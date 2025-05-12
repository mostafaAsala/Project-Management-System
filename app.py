from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, session, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime
import uuid
import json
import copy

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload
app.secret_key = os.urandom(24)  # For session management
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Simple in-memory database for demonstration
files_db = {}
users_db = {
    'admin': {
        'password': generate_password_hash('admin'),
        'assigned_steps': ['intake', 'processing', 'validation', 'approval', 'final'],
        'is_admin': True,
        'roles': ['intake', 'processing', 'validation', 'approval', 'final']  # Global roles for new files
    }
}
steps = ["intake", "processing", "validation", "approval", "final"]
step_assignments = {
    'intake': ['admin'],
    'processing': ['admin'],
    'validation': ['admin'],
    'approval': ['admin'],
    'final': ['admin']
}

# Helper function to check if user is authorized for a step
def is_authorized_for_step(username, step, file_id=None):
    if username not in users_db:
        return False

    # Admins are authorized for all steps
    if users_db[username].get('is_admin', False):
        return True

    # If file_id is provided, check file-specific step assignments
    if file_id and file_id in files_db:
        file = files_db[file_id]
        if 'step_assignments' in file and step in file['step_assignments']:
            return username in file['step_assignments'][step]

    # Fall back to global roles if no file-specific assignment or not authorized
    return step in users_db[username].get('roles', [])

# Helper function to count files in each step
def count_files_in_steps():
    step_counts = {step: 0 for step in steps}

    for file_id, file in files_db.items():
        current_step = file.get('current_step')
        if current_step in step_counts:
            step_counts[current_step] += 1

    return step_counts

# Helper function to update the current step based on completed steps
def update_current_step(file_id):
    if file_id not in files_db:
        return

    file = files_db[file_id]
    file_steps = file.get('custom_steps', steps)

    # Create a dictionary to track the status of each step
    step_statuses = {}
    for s in file_steps:
        step_statuses[s] = 'Not Started'

    # Update statuses based on history
    for entry in file['history']:
        if entry['step'] in step_statuses:
            if entry.get('filename', '').startswith('Status update to '):
                status = entry['filename'].replace('Status update to ', '')
                step_statuses[entry['step']] = status
            elif entry.get('path'):  # If there's a file upload, mark as completed
                step_statuses[entry['step']] = 'Completed'

    # Find the first non-completed step
    next_step = None
    for s in file_steps:
        if step_statuses[s] != 'Completed':
            next_step = s
            break

    # If all steps are completed, set to the last step
    if next_step is None and file_steps:
        next_step = file_steps[-1]

    # Update the current step
    if next_step is not None:
        file['current_step'] = next_step

@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))

    return render_template('index.html',
                          files=files_db,
                          steps=steps,
                          username=session['username'],
                          is_admin=users_db.get(session['username'], {}).get('is_admin', False))

@app.route('/manage_steps')
def manage_steps():
    if 'username' not in session:
        return redirect(url_for('login'))

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        flash('Only administrators can manage process steps')
        return redirect(url_for('index'))

    # Count files in each step
    step_counts = count_files_in_steps()

    return render_template('manage_steps.html',
                          steps=steps,
                          step_counts=step_counts,
                          username=session['username'],
                          is_admin=True)

@app.route('/add_step', methods=['POST'])
def add_step():
    if 'username' not in session:
        return redirect(url_for('login'))

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        flash('Only administrators can manage process steps')
        return redirect(url_for('index'))

    step_name = request.form.get('step_name', '').strip().lower()
    step_position = request.form.get('step_position', 'end')

    if not step_name:
        flash('Step name cannot be empty')
        return redirect(url_for('manage_steps'))

    if step_name in steps:
        flash(f'Step "{step_name}" already exists')
        return redirect(url_for('manage_steps'))

    # Add step at specified position
    if step_position == 'start':
        steps.insert(0, step_name)
    elif step_position == 'end':
        steps.append(step_name)
    elif step_position.startswith('after_'):
        after_step = step_position[6:]
        if after_step in steps:
            index = steps.index(after_step)
            steps.insert(index + 1, step_name)
        else:
            steps.append(step_name)

    # Update step assignments
    step_assignments[step_name] = ['admin']

    # Update user assignments for admin
    users_db['admin']['assigned_steps'].append(step_name)

    flash(f'Step "{step_name}" added successfully')
    return redirect(url_for('manage_steps'))

@app.route('/rename_step', methods=['POST'])
def rename_step():
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        return jsonify({"success": False, "message": "Only administrators can manage process steps"}), 403

    data = request.json
    old_step = data.get('old_step')
    new_step = data.get('new_step', '').strip().lower()

    if not old_step or not new_step:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    if old_step not in steps:
        return jsonify({"success": False, "message": f"Step '{old_step}' not found"}), 404

    if new_step in steps:
        return jsonify({"success": False, "message": f"Step '{new_step}' already exists"}), 400

    # Rename step in steps list
    index = steps.index(old_step)
    steps[index] = new_step

    # Update step assignments
    step_assignments[new_step] = step_assignments.pop(old_step, [])

    # Update user assignments
    for username, user_data in users_db.items():
        if old_step in user_data.get('assigned_steps', []):
            user_data['assigned_steps'].remove(old_step)
            user_data['assigned_steps'].append(new_step)

    # Update files
    for file_id, file in files_db.items():
        if file.get('current_step') == old_step:
            file['current_step'] = new_step

        # Update history entries
        for entry in file.get('history', []):
            if entry.get('step') == old_step:
                entry['step'] = new_step

    return jsonify({"success": True})

@app.route('/remove_step', methods=['POST'])
def remove_step():
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        return jsonify({"success": False, "message": "Only administrators can manage process steps"}), 403

    data = request.json
    step = data.get('step')

    if not step:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    if step not in steps:
        return jsonify({"success": False, "message": f"Step '{step}' not found"}), 404

    # Check if any files are in this step
    for file_id, file in files_db.items():
        if file.get('current_step') == step:
            return jsonify({"success": False, "message": f"Cannot remove step '{step}' because it has files. Move files to another step first."}), 400

    # Remove step from steps list
    steps.remove(step)

    # Remove step from assignments
    if step in step_assignments:
        del step_assignments[step]

    # Remove step from user assignments
    for username, user_data in users_db.items():
        if step in user_data.get('assigned_steps', []):
            user_data['assigned_steps'].remove(step)

    # Remove step from file history (but keep the entries for record)

    return jsonify({"success": True})

@app.route('/move_step', methods=['POST'])
def move_step():
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        return jsonify({"success": False, "message": "Only administrators can manage process steps"}), 403

    data = request.json
    step = data.get('step')
    direction = data.get('direction')

    if not step or not direction:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    if step not in steps:
        return jsonify({"success": False, "message": f"Step '{step}' not found"}), 404

    index = steps.index(step)

    if direction == 'up':
        if index == 0:
            return jsonify({"success": False, "message": "Step is already at the top"}), 400
        steps[index], steps[index-1] = steps[index-1], steps[index]
    elif direction == 'down':
        if index == len(steps) - 1:
            return jsonify({"success": False, "message": "Step is already at the bottom"}), 400
        steps[index], steps[index+1] = steps[index+1], steps[index]
    else:
        return jsonify({"success": False, "message": "Invalid direction"}), 400

    return jsonify({"success": True})

@app.route('/file/<file_id>')
def file_pipeline(file_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    if file_id not in files_db:
        flash('File not found')
        return redirect(url_for('index'))

    file = files_db[file_id]

    # Use file's custom steps if available, otherwise use default steps
    file_steps = file.get('custom_steps', steps)

    # Ensure file has step_assignments
    if 'step_assignments' not in file:
        file['step_assignments'] = {}
        for s in file_steps:
            file['step_assignments'][s] = []
            for username, user_data in users_db.items():
                if s in user_data.get('roles', []):
                    file['step_assignments'][s].append(username)

    # Get status for each step in the pipeline
    step_statuses = {}
    for step in file_steps:
        step_data = {
            'status': 'Not Started',
            'last_update': None,
            'user': None,
            'can_edit': is_authorized_for_step(session['username'], step, file_id)
        }

        # Check file history for this step
        for entry in file['history']:
            if entry['step'] == step:
                if step_data['last_update'] is None or entry['timestamp'] > step_data['last_update']:
                    step_data['last_update'] = entry['timestamp']
                    step_data['user'] = entry['user']
                    step_data['status'] = 'Completed'

        # Current step is in progress
        if file['current_step'] == step and step_data['status'] != 'Completed':
            step_data['status'] = 'In Progress'

        step_statuses[step] = step_data

    return render_template('file_pipeline.html',
                          file=file,
                          file_id=file_id,
                          steps=file_steps,
                          step_statuses=step_statuses,
                          username=session['username'],
                          is_admin=users_db.get(session['username'], {}).get('is_admin', False))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if username in users_db and check_password_hash(users_db[username]['password'], password):
            session['username'] = username
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'username' not in session or not users_db.get(session['username'], {}).get('is_admin', False):
        flash('Only administrators can register new users')
        return redirect(url_for('login'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        assigned_roles = request.form.getlist('assigned_roles')  # These are global roles
        is_admin = 'is_admin' in request.form

        if username in users_db:
            flash('Username already exists')
        else:
            users_db[username] = {
                'password': generate_password_hash(password),
                'assigned_steps': assigned_roles.copy(),  # For backward compatibility
                'roles': assigned_roles,  # Global roles for new files
                'is_admin': is_admin
            }

            # Update global step assignments
            for step in steps:
                if step in assigned_roles:
                    if username not in step_assignments[step]:
                        step_assignments[step].append(username)
                elif username in step_assignments[step]:
                    step_assignments[step].remove(username)

            # Update file-specific step assignments for all existing files
            for file_id, file in files_db.items():
                if 'step_assignments' not in file:
                    # Create step assignments if they don't exist
                    file['step_assignments'] = {}
                    for s in file.get('custom_steps', steps):
                        file['step_assignments'][s] = []

                # Add user to their assigned roles in each file
                for s in file.get('custom_steps', steps):
                    if s in assigned_roles:
                        if s not in file['step_assignments']:
                            file['step_assignments'][s] = []
                        if username not in file['step_assignments'][s]:
                            file['step_assignments'][s].append(username)

            flash('User registered successfully')
            return redirect(url_for('index'))

    return render_template('register.html', steps=steps)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'username' not in session:
        return redirect(url_for('login'))

    if 'file' not in request.files:
        flash('No file part')
        return redirect(url_for('index'))

    file = request.files['file']
    supplier = request.form.get('supplier', 'unknown')
    step = request.form.get('step', steps[0])

    # Check if user is authorized for this step
    if not is_authorized_for_step(session['username'], step):
        flash(f'You are not authorized to upload files for the {step} step')
        return redirect(url_for('index'))

    if file.filename == '':
        flash('No selected file')
        return redirect(url_for('index'))

    file_id = request.form.get('file_id', str(uuid.uuid4()))
    filename = secure_filename(file.filename)
    timestamp = datetime.now().isoformat()

    # Save file with unique name
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{file_id}_{step}_{filename}")
    file.save(file_path)

    # Update database
    if file_id not in files_db:
        # Create a copy of the default steps for this file
        file_steps = steps.copy()

        # Create file-specific step assignments based on global roles
        file_step_assignments = {}
        for s in file_steps:
            file_step_assignments[s] = []
            for username, user_data in users_db.items():
                if s in user_data.get('roles', []):
                    file_step_assignments[s].append(username)

        files_db[file_id] = {
            'supplier': supplier,
            'original_filename': filename,
            'current_step': file_steps[0] if file_steps else step,  # Start with the first step by default
            'history': [],
            'custom_steps': file_steps,  # Add custom steps for this file
            'step_assignments': file_step_assignments  # Add file-specific step assignments
        }

    files_db[file_id]['history'].append({
        'step': step,
        'timestamp': timestamp,
        'filename': filename,
        'path': file_path,
        'user': session['username']
    })

    files_db[file_id]['current_step'] = step

    flash('File uploaded successfully')
    return redirect(url_for('index'))

@app.route('/upload_to_step', methods=['POST'])
def upload_to_step():
    if 'username' not in session:
        return redirect(url_for('login'))

    if 'file' not in request.files:
        flash('No file part')
        return redirect(url_for('index'))

    file = request.files['file']
    file_id = request.form.get('file_id')
    step = request.form.get('step')
    comment = request.form.get('comment', '')

    # Validate inputs
    if not file_id or not step:
        flash('Missing required parameters')
        return redirect(url_for('index'))

    if file_id not in files_db:
        flash('File not found')
        return redirect(url_for('index'))

    # Check if user is authorized for this step
    if not is_authorized_for_step(session['username'], step, file_id):
        flash(f'You are not authorized to upload files for the {step} step')
        return redirect(url_for('file_pipeline', file_id=file_id))

    if file.filename == '':
        flash('No selected file')
        return redirect(url_for('file_pipeline', file_id=file_id))

    filename = secure_filename(file.filename)
    timestamp = datetime.now().isoformat()

    # Save file with unique name
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{file_id}_{step}_{timestamp.replace(':', '_').replace('.', '_')}_{filename}")
    file.save(file_path)

    # Add entry to history
    files_db[file_id]['history'].append({
        'step': step,
        'timestamp': timestamp,
        'filename': filename,
        'path': file_path,
        'user': session['username'],
        'comment': comment
    })

    # Update the current step based on completed steps
    update_current_step(file_id)

    flash('File uploaded successfully')
    return redirect(url_for('file_pipeline', file_id=file_id))

@app.route('/download/<file_id>/<step>')
def download_file(file_id, step):
    if 'username' not in session:
        return redirect(url_for('login'))

    if file_id not in files_db:
        flash('File not found')
        return redirect(url_for('index'))

    # Find the file version for the requested step
    for entry in reversed(files_db[file_id]['history']):
        if entry['step'] == step:
            return send_file(entry['path'], as_attachment=True,
                            download_name=entry['filename'])

    flash('Version not found')
    return redirect(url_for('index'))

@app.route('/api/files')
def get_files():
    if 'username' not in session:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify(files_db)

@app.route('/api/file_versions/<file_id>/<step>')
def get_file_versions(file_id, step):
    if 'username' not in session:
        return jsonify({"error": "Not authenticated"}), 401

    if file_id not in files_db:
        return jsonify({"error": "File not found"}), 404

    versions = []
    for entry in files_db[file_id]['history']:
        if entry['step'] == step and entry.get('path') is not None:  # Only include actual files, not status updates
            version_data = {
                'file_id': file_id,
                'step': step,
                'filename': entry['filename'],
                'timestamp': entry['timestamp'],
                'user': entry['user']
            }

            # Add comment if it exists
            if 'comment' in entry:
                version_data['comment'] = entry['comment']

            versions.append(version_data)

    return jsonify({"versions": versions})

@app.route('/update_status', methods=['POST'])
def update_status():
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    data = request.json
    file_id = data.get('file_id')
    step = data.get('step')
    status = data.get('status')

    if not all([file_id, step, status]):
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    if file_id not in files_db:
        return jsonify({"success": False, "message": "File not found"}), 404

    # Check if step is valid for this file
    file_steps = files_db[file_id].get('custom_steps', steps)
    if step not in file_steps:
        return jsonify({"success": False, "message": "Invalid step for this file"}), 400

    if not is_authorized_for_step(session['username'], step, file_id):
        return jsonify({"success": False, "message": "Not authorized to update this step"}), 403

    # Update the file status
    if status == 'Completed' or status == 'In Progress':
        # Add a status update entry to history
        timestamp = datetime.now().isoformat()
        files_db[file_id]['history'].append({
            'step': step,
            'timestamp': timestamp,
            'filename': f"Status update to {status}",
            'path': None,  # No file for status updates
            'user': session['username']
        })

        # Get file's custom steps
        file_steps = files_db[file_id].get('custom_steps', steps)

        # If status is completed, update the current step
        if status == 'Completed':
            # Find the last completed step and set current step to the next one
            update_current_step(file_id)

    return jsonify({"success": True})

# File-specific step management routes
@app.route('/manage_file_steps/<file_id>')
def manage_file_steps(file_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    if file_id not in files_db:
        flash('File not found')
        return redirect(url_for('index'))

    file = files_db[file_id]

    # Use file's custom steps if available, otherwise use default steps
    file_steps = file.get('custom_steps', steps.copy())

    # If file doesn't have custom steps yet, add them
    if 'custom_steps' not in file:
        file['custom_steps'] = file_steps

    # Get status for each step
    step_statuses = {}
    for step in file_steps:
        step_data = {
            'status': 'Not Started',
            'last_update': None,
            'user': None
        }

        # Check file history for this step
        for entry in file['history']:
            if entry['step'] == step:
                if step_data['last_update'] is None or entry['timestamp'] > step_data['last_update']:
                    step_data['last_update'] = entry['timestamp']
                    step_data['user'] = entry['user']
                    step_data['status'] = 'Completed'

        # Current step is in progress
        if file['current_step'] == step and step_data['status'] != 'Completed':
            step_data['status'] = 'In Progress'

        step_statuses[step] = step_data

    # Get available steps that aren't already in the file's steps
    available_steps = [step for step in steps if step not in file_steps]

    return render_template('manage_file_steps.html',
                          file=file,
                          file_id=file_id,
                          file_steps=file_steps,
                          step_statuses=step_statuses,
                          available_steps=available_steps,
                          username=session['username'],
                          is_admin=users_db.get(session['username'], {}).get('is_admin', False))

@app.route('/add_file_step/<file_id>', methods=['POST'])
def add_file_step(file_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    if file_id not in files_db:
        flash('File not found')
        return redirect(url_for('index'))

    step_name = request.form.get('step_name', '').strip().lower()
    step_position = request.form.get('step_position', 'end')

    if not step_name:
        flash('Step name cannot be empty')
        return redirect(url_for('manage_file_steps', file_id=file_id))

    file = files_db[file_id]

    # Ensure file has custom_steps
    if 'custom_steps' not in file:
        file['custom_steps'] = steps.copy()

    # Check if step already exists in file's steps
    if step_name in file['custom_steps']:
        flash(f'Step "{step_name}" already exists in this file')
        return redirect(url_for('manage_file_steps', file_id=file_id))

    # Add step at specified position
    if step_position == 'start':
        file['custom_steps'].insert(0, step_name)
    elif step_position == 'end':
        file['custom_steps'].append(step_name)
    elif step_position.startswith('after_'):
        after_step = step_position[6:]
        if after_step in file['custom_steps']:
            index = file['custom_steps'].index(after_step)
            file['custom_steps'].insert(index + 1, step_name)
        else:
            file['custom_steps'].append(step_name)

    flash(f'Step "{step_name}" added successfully')
    return redirect(url_for('manage_file_steps', file_id=file_id))

@app.route('/rename_file_step/<file_id>', methods=['POST'])
def rename_file_step(file_id):
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    if file_id not in files_db:
        return jsonify({"success": False, "message": "File not found"}), 404

    data = request.json
    old_step = data.get('old_step')
    new_step = data.get('new_step', '').strip().lower()

    if not old_step or not new_step:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    file = files_db[file_id]

    # Ensure file has custom_steps
    if 'custom_steps' not in file:
        file['custom_steps'] = steps.copy()

    if old_step not in file['custom_steps']:
        return jsonify({"success": False, "message": f"Step '{old_step}' not found in this file"}), 404

    if new_step in file['custom_steps']:
        return jsonify({"success": False, "message": f"Step '{new_step}' already exists in this file"}), 400

    # Rename step in file's steps list
    index = file['custom_steps'].index(old_step)
    file['custom_steps'][index] = new_step

    # Update current step if needed
    if file['current_step'] == old_step:
        file['current_step'] = new_step

    # Update history entries for this file
    for entry in file['history']:
        if entry['step'] == old_step:
            entry['step'] = new_step

    return jsonify({"success": True})

@app.route('/remove_file_step/<file_id>', methods=['POST'])
def remove_file_step(file_id):
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    if file_id not in files_db:
        return jsonify({"success": False, "message": "File not found"}), 404

    data = request.json
    step = data.get('step')

    if not step:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    file = files_db[file_id]

    # Ensure file has custom_steps
    if 'custom_steps' not in file:
        file['custom_steps'] = steps.copy()

    if step not in file['custom_steps']:
        return jsonify({"success": False, "message": f"Step '{step}' not found in this file"}), 404

    # Check if this is the current step
    if file['current_step'] == step:
        return jsonify({"success": False, "message": f"Cannot remove step '{step}' because it is the current step"}), 400

    # Check if this step has completed status
    has_completed = False
    for entry in file['history']:
        if entry['step'] == step:
            has_completed = True
            break

    if has_completed:
        return jsonify({"success": False, "message": f"Cannot remove step '{step}' because it has completed entries"}), 400

    # Remove step from file's steps list
    file['custom_steps'].remove(step)

    return jsonify({"success": True})

@app.route('/move_file_step/<file_id>', methods=['POST'])
def move_file_step(file_id):
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    if file_id not in files_db:
        return jsonify({"success": False, "message": "File not found"}), 404

    data = request.json
    step = data.get('step')
    direction = data.get('direction')

    if not step or not direction:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    file = files_db[file_id]

    # Ensure file has custom_steps
    if 'custom_steps' not in file:
        file['custom_steps'] = steps.copy()

    if step not in file['custom_steps']:
        return jsonify({"success": False, "message": f"Step '{step}' not found in this file"}), 404

    index = file['custom_steps'].index(step)

    if direction == 'up':
        if index == 0:
            return jsonify({"success": False, "message": "Step is already at the top"}), 400
        file['custom_steps'][index], file['custom_steps'][index-1] = file['custom_steps'][index-1], file['custom_steps'][index]
    elif direction == 'down':
        if index == len(file['custom_steps']) - 1:
            return jsonify({"success": False, "message": "Step is already at the bottom"}), 400
        file['custom_steps'][index], file['custom_steps'][index+1] = file['custom_steps'][index+1], file['custom_steps'][index]
    else:
        return jsonify({"success": False, "message": "Invalid direction"}), 400

    return jsonify({"success": True})

@app.route('/reset_file_steps/<file_id>')
def reset_file_steps(file_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    if file_id not in files_db:
        flash('File not found')
        return redirect(url_for('index'))

    # Reset to default steps
    files_db[file_id]['custom_steps'] = steps.copy()

    flash('Process steps have been reset to the default')
    return redirect(url_for('manage_file_steps', file_id=file_id))

@app.route('/api/step_users/<file_id>/<step>')
def get_step_users(file_id, step):
    if 'username' not in session:
        return jsonify({"error": "Not authenticated"}), 401

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        return jsonify({"error": "Only administrators can manage users"}), 403

    if file_id not in files_db:
        return jsonify({"error": "File not found"}), 404

    file = files_db[file_id]

    # Use file's custom steps if available
    file_steps = file.get('custom_steps', steps)

    if step not in file_steps:
        return jsonify({"error": "Step not found"}), 404

    # Ensure file has step_assignments
    if 'step_assignments' not in file:
        file['step_assignments'] = {}
        for s in file_steps:
            file['step_assignments'][s] = []
            for username, user_data in users_db.items():
                if s in user_data.get('roles', []):
                    file['step_assignments'][s].append(username)

    # If this step doesn't have assignments yet, initialize it
    if step not in file['step_assignments']:
        file['step_assignments'][step] = []

    # Get all users and mark which ones are assigned to this step
    users_list = []
    for username, user_data in users_db.items():
        users_list.append({
            'username': username,
            'assigned': username in file['step_assignments'].get(step, [])
        })

    return jsonify({"users": users_list})

@app.route('/manage_step_users/<file_id>', methods=['POST'])
def manage_step_users(file_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        flash('Only administrators can manage users')
        return redirect(url_for('file_pipeline', file_id=file_id))

    if file_id not in files_db:
        flash('File not found')
        return redirect(url_for('index'))

    step = request.form.get('step')
    assigned_users = request.form.getlist('assigned_users')

    file = files_db[file_id]

    # Use file's custom steps if available
    file_steps = file.get('custom_steps', steps)

    if step not in file_steps:
        flash('Step not found')
        return redirect(url_for('file_pipeline', file_id=file_id))

    # Ensure file has step_assignments
    if 'step_assignments' not in file:
        file['step_assignments'] = {}

    # Update step assignments
    file['step_assignments'][step] = assigned_users

    flash(f'Users for step "{step}" updated successfully')
    return redirect(url_for('file_pipeline', file_id=file_id))

if __name__ == '__main__':
    app.run(debug=True)