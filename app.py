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
        'is_admin': True
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
def is_authorized_for_step(username, step):
    if username not in users_db:
        return False
    if users_db[username].get('is_admin', False):
        return True
    return step in users_db[username].get('assigned_steps', [])

# Helper function to count files in each step
def count_files_in_steps():
    step_counts = {step: 0 for step in steps}

    for file_id, file in files_db.items():
        current_step = file.get('current_step')
        if current_step in step_counts:
            step_counts[current_step] += 1

    return step_counts

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

    # Get status for each step in the pipeline
    step_statuses = {}
    for step in steps:
        step_data = {
            'status': 'Not Started',
            'last_update': None,
            'user': None,
            'can_edit': is_authorized_for_step(session['username'], step)
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
                          steps=steps,
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
        assigned_steps = request.form.getlist('assigned_steps')
        is_admin = 'is_admin' in request.form

        if username in users_db:
            flash('Username already exists')
        else:
            users_db[username] = {
                'password': generate_password_hash(password),
                'assigned_steps': assigned_steps,
                'is_admin': is_admin
            }

            # Update step assignments
            for step in steps:
                if step in assigned_steps:
                    if username not in step_assignments[step]:
                        step_assignments[step].append(username)
                elif username in step_assignments[step]:
                    step_assignments[step].remove(username)

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
        files_db[file_id] = {
            'supplier': supplier,
            'original_filename': filename,
            'current_step': step,
            'history': []
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
    if not is_authorized_for_step(session['username'], step):
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

    # Update current step if this is the current step
    if files_db[file_id]['current_step'] == step:
        # If the step is completed, move to the next step
        current_index = steps.index(step)
        if current_index < len(steps) - 1:
            files_db[file_id]['current_step'] = steps[current_index + 1]

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

    if step not in steps:
        return jsonify({"success": False, "message": "Invalid step"}), 400

    if not is_authorized_for_step(session['username'], step):
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

        # If completed, move to next step
        if status == 'Completed' and files_db[file_id]['current_step'] == step:
            current_index = steps.index(step)
            if current_index < len(steps) - 1:
                files_db[file_id]['current_step'] = steps[current_index + 1]

    return jsonify({"success": True})

if __name__ == '__main__':
    app.run(debug=True)