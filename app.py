from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, session, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime
import uuid

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

@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))

    # Get process overview data
    process_overview = []
    for step in steps:
        step_data = {
            'name': step,
            'status': 'Not Started',
            'last_update': None,
            'assigned_users': step_assignments[step],
            'can_edit': is_authorized_for_step(session['username'], step)
        }

        # Check if any files are at this step
        for file_id, file in files_db.items():
            for entry in file['history']:
                if entry['step'] == step:
                    if step_data['last_update'] is None or entry['timestamp'] > step_data['last_update']:
                        step_data['last_update'] = entry['timestamp']
                        step_data['status'] = 'Completed'

            if file['current_step'] == step:
                step_data['status'] = 'In Progress'

        process_overview.append(step_data)

    return render_template('index.html',
                          files=files_db,
                          steps=steps,
                          process_overview=process_overview,
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

if __name__ == '__main__':
    app.run(debug=True)