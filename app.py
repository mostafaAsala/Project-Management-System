import dateutil
from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, session, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime
import uuid
import json
import copy
import atexit
import zipfile
import tempfile
import threading
import time
import data_manager

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 16MB max upload
app.config['MAX_CONTENT_LENGTH'] = None  # No limit
app.secret_key = os.urandom(24)  # For session management
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.template_filter('strftime')
def _jinja2_filter_datetime(date, fmt=None):
    date = dateutil.parser.parse(date)
    native = date.replace(tzinfo=None)
    format='%Y-%m-%d, %H:%M'
    return native.strftime(format)


# Custom filter to convert string to datetime for time difference calculation
@app.template_filter('to_datetime')
def to_datetime(value):
    return datetime.fromisoformat(value)

# Load data from files
users_db, files_db, steps, step_assignments, custom_steps_list, process_types = data_manager.load_data()

# Initialize default assigned times for steps (in minutes)
default_assigned_times = data_manager.load_default_assigned_times()
if not default_assigned_times:
    default_assigned_times = {step: 0 for step in steps}  # Default to 0 minutes for all steps

# Initialize notifications database
notifications_db = data_manager.load_notifications()
if not notifications_db:
    notifications_db = {}  # Initialize empty notifications database

# Migrate existing files to add creation_time if missing
def migrate_files_creation_time():
    for _, file in files_db.items():
        if 'creation_time' not in file:
            # Use the first history entry timestamp as creation time if available
            if file.get('history') and len(file['history']) > 0:
                file['creation_time'] = file['history'][0]['timestamp']
            else:
                # If no history, use current time
                file['creation_time'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            # Mark data as changed
            data_manager.mark_data_changed()

# Run migration
migrate_files_creation_time()

# Start auto-save
data_manager.start_auto_save(users_db, files_db, steps, step_assignments, custom_steps_list, process_types, default_assigned_times, notifications_db, interval=30)

# Register function to save data when the application exits
def save_data_on_exit():
    data_manager.save_data(users_db, files_db, steps, step_assignments, custom_steps_list, process_types, default_assigned_times, notifications_db)
    data_manager.stop_auto_save()

# Function to manually save all data
def save_all_data():
    data_manager.save_data(users_db, files_db, steps, step_assignments, custom_steps_list, process_types, default_assigned_times, notifications_db)
    data_manager.create_backup()

atexit.register(save_data_on_exit)

# Notification scanner thread variables
notification_scanner_thread = None
stop_notification_scanner = False

def notification_scanner():
    """Background thread that scans for notification changes every 10 minutes"""
    global stop_notification_scanner
    print("[NOTIFICATION SCANNER] Starting notification scanner thread...")

    while not stop_notification_scanner:
        try:
            # Wait for 10 minutes (600 seconds)
            for _ in range(600):  # Check every second for stop signal
                if stop_notification_scanner:
                    break
                time.sleep(1)

            if not stop_notification_scanner:
                print("[NOTIFICATION SCANNER] Running periodic notification scan...")
                scan_and_update_file_notifications()

        except Exception as e:
            print(f"[NOTIFICATION SCANNER] Error in notification scanner: {e}")
            # Continue running even if there's an error
            time.sleep(60)  # Wait 1 minute before retrying

    print("[NOTIFICATION SCANNER] Notification scanner thread stopped")

def start_notification_scanner():
    """Start the notification scanner thread"""
    global notification_scanner_thread, stop_notification_scanner

    if notification_scanner_thread is None or not notification_scanner_thread.is_alive():
        stop_notification_scanner = False
        notification_scanner_thread = threading.Thread(target=notification_scanner, daemon=True)
        notification_scanner_thread.start()
        print("[NOTIFICATION SCANNER] Notification scanner thread started")

def stop_notification_scanner_thread():
    """Stop the notification scanner thread"""
    global stop_notification_scanner
    stop_notification_scanner = True
    if notification_scanner_thread and notification_scanner_thread.is_alive():
        notification_scanner_thread.join(timeout=5)
        print("[NOTIFICATION SCANNER] Notification scanner thread stopped")

# Register cleanup function
atexit.register(stop_notification_scanner_thread)

# Helper function to create a notification
def create_notification(username, notification_type, title, message, file_id=None, step=None):
    """Create a notification for a user"""
    if username not in notifications_db:
        notifications_db[username] = []

    notification = {
        'id': str(uuid.uuid4()),
        'type': notification_type,  # 'file_assigned', 'step_overdue', 'file_completed', etc.
        'title': title,
        'message': message,
        'file_id': file_id,
        'step': step,
        'timestamp': datetime.now().isoformat(),
        'read': False
    }

    notifications_db[username].append(notification)
    data_manager.mark_data_changed()
    return notification

# Helper function to get user notifications
def get_user_notifications(username, unread_only=False):
    """Get notifications for a user"""
    if username not in notifications_db:
        return []

    user_notifications = notifications_db[username]
    if unread_only:
        return [n for n in user_notifications if not n.get('read', False)]

    # Sort by timestamp (newest first)
    return sorted(user_notifications, key=lambda x: x['timestamp'], reverse=True)

# Helper function to mark notification as read
def mark_notification_read(username, notification_id):
    """Mark a notification as read"""
    if username not in notifications_db:
        return False
    print(notifications_db[username])
    print("notification id: ",notification_id)
    for notification in notifications_db[username]:
        if notification['id'] == notification_id:
            notification['read'] = True
            data_manager.mark_data_changed()
            
            print("All nots: ",notifications_db[username])
            print("Marked notification as read: ", notification)
            return True
    
    return False

# Helper function to create a unique notification key for file assignments
def get_notification_key(username, file_id, step):
    """Generate a unique key for file assignment notifications"""
    return f"{username}:{file_id}:{step}"

# Helper function to check if a notification already exists
def notification_exists(username, file_id, step):
    """Check if a notification already exists for this user, file, and step"""
    if username not in notifications_db:
        return False

    for notification in notifications_db[username]:
        if (notification.get('type') == 'file_assigned' and
            notification.get('file_id') == file_id and
            notification.get('step') == step):
            return True
    return False

# Helper function to remove notification for a specific file/step combination
def remove_file_notification(username, file_id, step):
    """Remove notification for a specific file/step combination"""
    if username not in notifications_db:
        return False

    initial_count = len(notifications_db[username])
    notifications_db[username] = [n for n in notifications_db[username]
                                if not (n.get('type') == 'file_assigned' and
                                       n.get('file_id') == file_id and
                                       n.get('step') == step)]

    removed = len(notifications_db[username]) < initial_count
    if removed:
        data_manager.mark_data_changed()
        print(f"[NOTIFICATION] Removed notification for user {username}, file {file_id}, step {step}")
    return removed

# Helper function to scan and update file notifications for all users
def scan_and_update_file_notifications():
    """Scan all files and update notifications based on current file positions"""
    print("\n[NOTIFICATION SCAN] Starting notification scan...")

    # Track current file positions for each user
    current_user_files = {}  # {username: {file_id: step}}

    # Build current state of files in user steps
    for username in users_db:
        user_roles = users_db[username].get('roles', [])
        user_custom_steps = users_db[username].get('custom_steps', [])
        user_steps = set(user_roles + user_custom_steps)
        current_user_files[username] = {}

        for file_id, file in files_db.items():
            current_step = file.get('current_step')

            # Check if user is assigned to current step
            if current_step in user_steps:
                # Check file-specific assignments if they exist
                file_assignments = file.get('step_assignments', {})
                if current_step in file_assignments:
                    if username in file_assignments[current_step]:
                        current_user_files[username][file_id] = current_step
                else:
                    # Fall back to global role assignment
                    current_user_files[username][file_id] = current_step

    # For each user, compare current state with existing notifications
    for username in users_db:
        if username not in notifications_db:
            notifications_db[username] = []

        # Get existing file assignment notifications
        existing_notifications = {}  # {file_id: step}
        for notification in notifications_db[username]:
            if notification.get('type') == 'file_assigned':
                file_id = notification.get('file_id')
                step = notification.get('step')
                if file_id and step:
                    existing_notifications[file_id] = step

        current_files = current_user_files.get(username, {})

        # Remove notifications for files no longer in user's steps
        for file_id, step in existing_notifications.items():
            if file_id not in current_files or current_files[file_id] != step:
                remove_file_notification(username, file_id, step)

        # Add notifications for new files in user's steps
        for file_id, step in current_files.items():
            if not notification_exists(username, file_id, step):
                file = files_db.get(file_id, {})
                create_notification(
                    username,
                    'file_assigned',
                    f'File in your step: {step}',
                    f'File "{file.get("original_filename", "Unknown")}" is currently in step "{step}" which is assigned to you.',
                    file_id=file_id,
                    step=step
                )
                print(f"[NOTIFICATION] Created new notification for user {username}, file {file_id}, step {step}")

    print("[NOTIFICATION SCAN] Notification scan completed")

# Helper function to trigger immediate notification scan
def trigger_notification_scan():
    """Trigger an immediate notification scan (used when files change steps)"""
    print("[NOTIFICATION] Triggering immediate notification scan due to file changes...")
    scan_and_update_file_notifications()

# Legacy function kept for backward compatibility but now uses the new scan system
def generate_user_file_notifications(username):
    """Generate notifications for files that are in the user's assigned steps (legacy function)"""
    # This function is now just a wrapper that triggers a full scan
    # to maintain backward compatibility with existing code
    scan_and_update_file_notifications()

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

    # Check if step is in user's custom steps
    if step in users_db[username].get('custom_steps', []):
        return True

    # Fall back to global roles if no file-specific assignment or not authorized
    return step in users_db[username].get('roles', [])

# Helper function to count files in each step
def count_files_in_steps():
    step_counts = {step: 0 for step in steps}
    for _, file in files_db.items():
        current_step = file.get('current_step')
        if current_step in step_counts:
            step_counts[current_step] += 1

    return step_counts

# Helper function to update the current step based on completed steps
def update_current_step(file_id):
    print(f"\n[EXECUTING] update_current_step({file_id}) - Updating current step")
    if file_id not in files_db:
        print(f"[STEP] File {file_id} not found in database, returning")
        return

    file = files_db[file_id]
    file_steps = file.get('custom_steps', steps)
    print(f"[STEP] File steps: {file_steps}")

    # Create a dictionary to track the status of each step
    step_statuses = {}
    for s in file_steps:
        step_statuses[s] = 'Not Started'
    print(f"[STEP] Initialized step statuses: {step_statuses}")

    # Create dictionaries to track the status, last update time, and user for each step
    step_last_updates = {}
    step_users = {}
    print(f"[STEP] Initialized tracking dictionaries")

    # Update statuses based on history
    print(f"[STEP] Updating statuses based on history entries: {len(file['history'])}")
    for entry in file['history']:
        if entry['step'] in step_statuses:
            print(f"[STEP] Processing history entry for step: {entry['step']}, timestamp: {entry['timestamp']}")
            # Update timestamp and user if this is a newer entry
            if entry['step'] not in step_last_updates or entry['timestamp'] > step_last_updates[entry['step']]:
                step_last_updates[entry['step']] = entry['timestamp']
                step_users[entry['step']] = entry['user']
                print(f"[STEP] Updated last update time for step {entry['step']} to {entry['timestamp']} by {entry['user']}")

            if entry.get('filename', '').startswith('Status update to '):
                status = entry['filename'].replace('Status update to ', '')
                step_statuses[entry['step']] = status
                print(f"[STEP] Updated status for step {entry['step']} to '{status}' based on status update entry")
            elif entry.get('path'):  # If there's a file upload, mark as in progress
                step_statuses[entry['step']] = 'In Progress'
                print(f"[STEP] Updated status for step {entry['step']} to 'In Progress' based on file upload")
            else:
                step_statuses[entry['step']] = 'Completed'
                print(f"[STEP] Updated status for step {entry['step']} to 'Completed' based on other entry type")

    # Ensure file has step_statuses dictionary
    if 'step_statuses' not in file:
        file['step_statuses'] = {}

    # Update the file's step_statuses with the calculated values
    # First, find the completion time for each step
    step_completion_times = {}

    # Sort all history entries by timestamp
    all_entries = file['history'].copy()
    all_entries.sort(key=lambda x: datetime.fromisoformat(x['timestamp']))

    # Find the completion time for each step
    for entry in all_entries:
        step = entry.get('step')
        if step in file_steps:
            # Check if this entry marks the step as completed
            if entry.get('filename', '').startswith('Status update to Completed'):
                step_completion_times[step] = entry['timestamp']

    # Calculate total time worked for each step based on previous step completion
    for i, s in enumerate(file_steps):
        total_time_minutes = 0

        # Get the current step status
        current_status = step_statuses.get(s, 'Not Started')

        # Find when this step started (when previous step was completed)
        start_time = None
        if i > 0:  # If not the first step
            prev_step = file_steps[i-1]
            prev_status = step_statuses.get(prev_step, 'Not Started')
            if prev_status == 'Completed' and prev_step in step_completion_times:
                start_time = datetime.fromisoformat(step_completion_times[prev_step])
            else:
                start_time = datetime.now()
            """if prev_step in step_completion_times:
                start_time = datetime.fromisoformat(step_completion_times[prev_step])"""
        else:  # For the first step, use the first history entry time
            if all_entries and all_entries[0]['step'] == s:
                start_time = datetime.fromisoformat(all_entries[0]['timestamp'])
        # If we have a start time, calculate the total time worked
        if start_time:
            if step_statuses[s] == 'Completed':
                end_time = datetime.fromisoformat(step_completion_times[s])
                total_time_minutes = (end_time - start_time).total_seconds() / 60
            elif current_status == 'In Progress':
                end_time = datetime.now()
                total_time_minutes = (end_time - start_time).total_seconds() / 60

            """if s in step_completion_times:  # If step is completed
                end_time = datetime.fromisoformat(step_completion_times[s])
                total_time_minutes = (end_time - start_time).total_seconds() / 60
            elif current_status == 'In Progress':  # If step is in progress
                end_time = datetime.now()
                total_time_minutes = (end_time - start_time).total_seconds() / 60
            """

        # If we don't have an existing entry, create a new one with default values
        if s not in file['step_statuses'] or not isinstance(file['step_statuses'][s], dict):
            file['step_statuses'][s] = {
                'status': step_statuses[s],
                'last_update': step_last_updates.get(s, None),
                'updated_by': step_users.get(s, None),
                'assigned_time': 0,  # Default assigned time
                'total_time_worked': int(total_time_minutes)
            }
        else:
            # Update the status if the entry already exists as a dictionary
            file['step_statuses'][s]['status'] = step_statuses[s]

            # Update total_time_worked
            file['step_statuses'][s]['total_time_worked'] = int(total_time_minutes)

            # Get assigned time (or default to 0)
            assigned_time = file['step_statuses'][s].get('assigned_time', 0)

            # Update is_overdue flag
            file['step_statuses'][s]['is_overdue'] = assigned_time > 0 and total_time_minutes > assigned_time

            # Update last_update and updated_by if we have newer information
            if s in step_last_updates:
                file['step_statuses'][s]['last_update'] = step_last_updates[s]
                file['step_statuses'][s]['updated_by'] = step_users[s]

    # Find the first non-completed step
    print(f"[STEP] Finding the first non-completed step")
    next_step = None
    for s in file_steps:
        if step_statuses[s] != 'Completed':
            next_step = s
            print(f"[STEP] Found first non-completed step: {next_step}")
            break

    # If all steps are completed, set to the last step
    if next_step is None and file_steps:
        next_step = file_steps[-1]
        print(f"[STEP] All steps completed, setting to last step: {next_step}")

    # Update the current step
    if next_step is not None and file['current_step'] != next_step:
        print(f"[STEP] Updating current step from {file['current_step']} to {next_step}")
        file['current_step'] = next_step
        # Mark data as changed
        data_manager.mark_data_changed()
        # Trigger notification scan since file changed steps
        trigger_notification_scan()
    else:
        print(f"[STEP] Current step remains unchanged: {file.get('current_step')}")

@app.route('/save_all_data', methods=['POST'])
def save_all_data_route():
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        return jsonify({"success": False, "message": "Only administrators can save all data"}), 403

    try:
        # Call the save_all_data function
        save_all_data()
        return jsonify({"success": True, "message": "All data saved successfully"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error saving data: {str(e)}"}), 500

@app.route('/api/notifications')
def get_notifications():
    """Get notifications for the current user"""
    if 'username' not in session:
        return jsonify({"error": "Not authenticated"}), 401

    username = session['username']
    unread_only = request.args.get('unread_only', 'false').lower() == 'true'

    # Notifications are now managed by the background scanner
    # No need to regenerate them on every API call

    notifications = get_user_notifications(username, unread_only)
    unread_count = len([n for n in get_user_notifications(username) if not n.get('read', False)])

    return jsonify({
        "notifications": notifications,
        "unread_count": unread_count
    })

@app.route('/api/notifications/mark_read', methods=['POST'])
def mark_notification_read_route():
    """Mark a notification as read"""
    if 'username' not in session:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.json
    notification_id = data.get('notification_id')

    if not notification_id:
        return jsonify({"error": "Missing notification_id"}), 400

    username = session['username']
    success = mark_notification_read(username, notification_id)

    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Notification not found"}), 404

@app.route('/api/notifications/mark_all_read', methods=['POST'])
def mark_all_notifications_read():
    """Mark all notifications as read for the current user"""
    if 'username' not in session:
        return jsonify({"error": "Not authenticated"}), 401

    username = session['username']

    if username in notifications_db:
        for notification in notifications_db[username]:
            notification['read'] = True
        data_manager.mark_data_changed()

    return jsonify({"success": True})

@app.route('/')
def index():
    print("\n[EXECUTING] index() - Main page route")
    if 'username' not in session:
        print("[STEP] User not in session, redirecting to login")
        return redirect(url_for('login'))

    username = session['username']
    print(f"[STEP] User {username} accessing main page")

    # Notifications are now managed by the background scanner
    # No need to regenerate them on every page load

    print("[STEP] Calculating current step time data for each file")
    # Calculate current step time data for each file
    current_step_times = {}
    for file_id, file in files_db.items():
        current_step = file.get('current_step')
        print(f"[STEP] Processing file {file_id} - Current step: {current_step}")
        if current_step and 'step_statuses' in file and current_step in file['step_statuses']:
            step_status = file['step_statuses'][current_step]
            if isinstance(step_status, dict):
                total_time_worked = step_status.get('total_time_worked', 0)
                assigned_time = step_status.get('assigned_time', 0)
                is_overdue = assigned_time > 0 and total_time_worked > assigned_time
                print(f"[STEP] File {file_id} - Step {current_step} - Time worked: {total_time_worked} min - Assigned time: {assigned_time} min - Overdue: {is_overdue}")

                current_step_times[file_id] = {
                    'total_time_worked': total_time_worked,
                    'assigned_time': assigned_time,
                    'is_overdue': is_overdue
                }
            else:
                # Handle legacy format
                print(f"[STEP] File {file_id} - Step {current_step} - Legacy format detected")
                current_step_times[file_id] = {
                    'total_time_worked': 0,
                    'assigned_time': 0,
                    'is_overdue': False
                }
        else:
            print(f"[STEP] File {file_id} - No current step or step status information")
            current_step_times[file_id] = {
                'total_time_worked': 0,
                'assigned_time': 0,
                'is_overdue': False
            }

    return render_template('index.html',
                          files=files_db,
                          steps=steps,
                          process_types=process_types,
                          current_step_times=current_step_times,
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
                          custom_steps=custom_steps_list,
                          default_assigned_times=default_assigned_times,
                          username=session['username'],
                          is_admin=True)

@app.route('/update_default_assigned_time', methods=['POST'])
def update_default_assigned_time():
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        return jsonify({"success": False, "message": "Only administrators can update default assigned times"}), 403

    data = request.json
    step = data.get('step')
    assigned_time = data.get('assigned_time')

    if not step or assigned_time is None:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    try:
        assigned_time = int(assigned_time)
        if assigned_time < 0:
            return jsonify({"success": False, "message": "Assigned time must be a non-negative integer"}), 400
    except ValueError:
        return jsonify({"success": False, "message": "Assigned time must be a valid integer"}), 400

    # Update the default assigned time for the step
    global default_assigned_times
    default_assigned_times[step] = assigned_time

    # Mark data as changed
    data_manager.mark_data_changed()

    return jsonify({"success": True})

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
    default_assigned_time = request.form.get('default_assigned_time', '00:00:00')

    if not step_name:
        flash('Step name cannot be empty')
        return redirect(url_for('manage_steps'))

    if step_name in steps:
        flash(f'Step "{step_name}" already exists')
        return redirect(url_for('manage_steps'))

    # Convert default assigned time from DD:HH:MM to minutes
    try:
        # Parse the DD:HH:MM format
        parts = default_assigned_time.split(':')
        if len(parts) != 3:
            raise ValueError("Invalid time format")

        days = int(parts[0])
        hours = int(parts[1])
        minutes = int(parts[2])

        # Validate ranges
        if hours >= 24 or minutes >= 60:
            raise ValueError("Invalid time values")

        # Convert to total minutes
        assigned_time_minutes = days * 24 * 60 + hours * 60 + minutes
    except ValueError:
        # If there's an error, default to 0 minutes
        assigned_time_minutes = 0

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

    # Add to custom steps list
    if step_name not in custom_steps_list:
        custom_steps_list.append(step_name)

    # Set default assigned time
    default_assigned_times[step_name] = assigned_time_minutes

    # Mark data as changed
    data_manager.mark_data_changed()

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
    for _, user_data in users_db.items():
        if old_step in user_data.get('assigned_steps', []):
            user_data['assigned_steps'].remove(old_step)
            user_data['assigned_steps'].append(new_step)

    # Update files
    for _, file in files_db.items():
        if file.get('current_step') == old_step:
            file['current_step'] = new_step

        # Update history entries
        for entry in file.get('history', []):
            if entry.get('step') == old_step:
                entry['step'] = new_step

    # Update custom steps list if needed
    if old_step in custom_steps_list:
        custom_steps_list.remove(old_step)
        custom_steps_list.append(new_step)

    # Update default assigned time
    if old_step in default_assigned_times:
        default_assigned_times[new_step] = default_assigned_times.pop(old_step)

    # Mark data as changed
    data_manager.mark_data_changed()

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
    for _, file in files_db.items():
        if file.get('current_step') == step:
            return jsonify({"success": False, "message": f"Cannot remove step '{step}' because it has files. Move files to another step first."}), 400

    # Remove step from steps list
    steps.remove(step)

    # Remove step from assignments
    if step in step_assignments:
        del step_assignments[step]

    # Remove step from user assignments
    for _, user_data in users_db.items():
        if step in user_data.get('assigned_steps', []):
            user_data['assigned_steps'].remove(step)

    # Remove step from custom steps list if it's there
    if step in custom_steps_list:
        custom_steps_list.remove(step)

    # Remove default assigned time for this step
    if step in default_assigned_times:
        del default_assigned_times[step]

    # Remove step from file history (but keep the entries for record)

    # Mark data as changed
    data_manager.mark_data_changed()

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

    # Mark data as changed
    data_manager.mark_data_changed()

    return jsonify({"success": True})

@app.route('/file/<file_id>')
def file_pipeline(file_id):
    print(f"\n[EXECUTING] file_pipeline({file_id}) - File pipeline view")
    if 'username' not in session:
        print("[STEP] User not in session, redirecting to login")
        return redirect(url_for('login'))

    if file_id not in files_db:
        print(f"[STEP] File {file_id} not found in database")
        flash('File not found')
        return redirect(url_for('index'))

    print(f"[STEP] Retrieving file data for {file_id}")
    file = files_db[file_id]
    # Use file's custom steps if available, otherwise use default steps
    file_steps = file.get('custom_steps', steps)
    print(f"[STEP] File steps: {file_steps}")

    # Ensure file has step_assignments
    if 'step_assignments' not in file:
        print(f"[STEP] Creating step assignments for file {file_id}")
        file['step_assignments'] = {}
        for s in file_steps:
            file['step_assignments'][s] = []
            for username, user_data in users_db.items():
                # Check if step is in user's roles or custom steps
                if s in user_data.get('roles', []) or s in user_data.get('custom_steps', []):
                    file['step_assignments'][s].append(username)
    else:
        print(f"[STEP] Using existing step assignments for file {file_id}")

    print(f"[STEP] Building step statuses for file {file_id}")
    # Get status for each step in the pipeline
    step_statuses = {}
    for step in file_steps:
        print(f"[STEP] Processing step: {step}")
        step_data = {
            'status': 'Not Started',
            'last_update': None,
            'user': None,
            'can_edit': is_authorized_for_step(session['username'], step, file_id)
        }
        print(f"[STEP] User {session['username']} can edit step {step}: {step_data['can_edit']}")

        # Use saved step status if available
        if 'step_statuses' in file and step in file['step_statuses']:
            if isinstance(file['step_statuses'][step], dict):
                # Use the stored dictionary values
                step_data['status'] = file['step_statuses'][step].get('status', 'Not Started')
                step_data['last_update'] = file['step_statuses'][step].get('last_update')
                step_data['user'] = file['step_statuses'][step].get('updated_by')
                step_data['assigned_time'] = file['step_statuses'][step].get('assigned_time', 0)
                step_data['total_time_worked'] = file['step_statuses'][step].get('total_time_worked', 0)
                step_data['is_overdue'] = file['step_statuses'][step].get('is_overdue', False)
            else:
                # For backward compatibility with old format where step_statuses just stored the status string
                step_data['status'] = file['step_statuses'][step]

                # Still need to get last_update and user from history
                for entry in file['history']:
                    if entry['step'] == step:
                        if step_data['last_update'] is None or entry['timestamp'] > step_data['last_update']:
                            step_data['last_update'] = entry['timestamp']
                            step_data['user'] = entry['user']
        else:
            # Fall back to calculating from history for backward compatibility
            # Check file history for this step
            for entry in file['history']:
                if entry['step'] == step:
                    if step_data['last_update'] is None or entry['timestamp'] > step_data['last_update']:
                        step_data['last_update'] = entry['timestamp']
                        step_data['user'] = entry['user']

                        # Check for explicit status updates
                        if entry.get('filename', '').startswith('Status update to '):
                            status = entry['filename'].replace('Status update to ', '')
                            step_data['status'] = status
                        elif entry.get('path'):  # If there's a file upload
                            step_data['status'] = 'In Progress'
                        else:
                            step_data['status'] = 'Completed'

            # Current step is in progress if not already completed
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

@app.route('/statistics')
def statistics():
    print("\n[EXECUTING] statistics() - Statistics page route")
    if 'username' not in session:
        print("[STEP] User not in session, redirecting to login")
        return redirect(url_for('login'))

    print("[STEP] Calculating comprehensive statistics")

    # Initialize statistics data
    stats = {
        'total_files': len(files_db),
        'total_users': len(users_db),
        'total_steps': len(steps),
        'user_stats': {},
        'step_stats': {},
        'supplier_stats': {},
        'process_type_stats': {},
        'overdue_stats': {
            'total_overdue_files': 0,
            'total_overdue_steps': 0,
            'overdue_by_step': {},
            'overdue_by_user': {}
        },
        'time_stats': {
            'total_time_worked': 0,
            'average_time_per_file': 0,
            'average_time_per_step': 0
        },
        'file_distribution': {
            'by_step': {},
            'by_status': {'Not Started': 0, 'In Progress': 0, 'Completed': 0}
        }
    }

    # Initialize step statistics
    for step in steps:
        stats['step_stats'][step] = {
            'total_files': 0,
            'completed_files': 0,
            'in_progress_files': 0,
            'not_started_files': 0,
            'total_time_worked': 0,
            'average_time': 0,
            'overdue_files': 0,
            'assigned_users': len(step_assignments.get(step, []))
        }
        stats['file_distribution']['by_step'][step] = 0
        stats['overdue_stats']['overdue_by_step'][step] = 0

    # Initialize user statistics
    for username in users_db:
        stats['user_stats'][username] = {
            'total_files_worked': 0,
            'total_time_worked': 0,
            'average_time_per_file': 0,
            'completed_steps': 0,
            'in_progress_steps': 0,
            'overdue_steps': 0,
            'assigned_steps': users_db[username].get('roles', [])
        }
        stats['overdue_stats']['overdue_by_user'][username] = 0

    # Process each file for statistics
    for file_id, file in files_db.items():
        current_step = file.get('current_step')
        supplier = file.get('supplier', 'Unknown')
        process_type = file.get('process_type', 'Unknown')

        # Supplier statistics
        if supplier not in stats['supplier_stats']:
            stats['supplier_stats'][supplier] = {
                'total_files': 0,
                'total_time_worked': 0,
                'average_time': 0,
                'overdue_files': 0
            }
        stats['supplier_stats'][supplier]['total_files'] += 1

        # Process type statistics
        if process_type not in stats['process_type_stats']:
            stats['process_type_stats'][process_type] = {
                'total_files': 0,
                'total_time_worked': 0,
                'average_time': 0
            }
        stats['process_type_stats'][process_type]['total_files'] += 1

        # File distribution by current step
        if current_step:
            stats['file_distribution']['by_step'][current_step] = stats['file_distribution']['by_step'].get(current_step, 0) + 1

        # Check if file has any overdue steps
        file_has_overdue = False

        # Process step statuses for this file
        step_statuses = file.get('step_statuses', {})
        for step, step_data in step_statuses.items():
            if isinstance(step_data, dict):
                status = step_data.get('status', 'Not Started')
                total_time_worked = step_data.get('total_time_worked', 0)
                is_overdue = step_data.get('is_overdue', False)
                updated_by = step_data.get('updated_by')

                # Update step statistics
                if step in stats['step_stats']:
                    stats['step_stats'][step]['total_files'] += 1
                    stats['step_stats'][step]['total_time_worked'] += total_time_worked

                    if status == 'Completed':
                        stats['step_stats'][step]['completed_files'] += 1
                        stats['file_distribution']['by_status']['Completed'] += 1
                    elif status == 'In Progress':
                        stats['step_stats'][step]['in_progress_files'] += 1
                        stats['file_distribution']['by_status']['In Progress'] += 1
                    else:
                        stats['step_stats'][step]['not_started_files'] += 1
                        stats['file_distribution']['by_status']['Not Started'] += 1

                    if is_overdue:
                        stats['step_stats'][step]['overdue_files'] += 1
                        stats['overdue_stats']['overdue_by_step'][step] += 1
                        stats['overdue_stats']['total_overdue_steps'] += 1
                        file_has_overdue = True

                # Update user statistics
                if updated_by and updated_by in stats['user_stats']:
                    stats['user_stats'][updated_by]['total_time_worked'] += total_time_worked

                    if status == 'Completed':
                        stats['user_stats'][updated_by]['completed_steps'] += 1
                    elif status == 'In Progress':
                        stats['user_stats'][updated_by]['in_progress_steps'] += 1

                    if is_overdue:
                        stats['user_stats'][updated_by]['overdue_steps'] += 1
                        stats['overdue_stats']['overdue_by_user'][updated_by] += 1

                # Update total time statistics
                stats['time_stats']['total_time_worked'] += total_time_worked

                # Update supplier time statistics
                stats['supplier_stats'][supplier]['total_time_worked'] += total_time_worked

                # Update process type time statistics
                stats['process_type_stats'][process_type]['total_time_worked'] += total_time_worked

        # Count files with overdue steps
        if file_has_overdue:
            stats['overdue_stats']['total_overdue_files'] += 1
            stats['supplier_stats'][supplier]['overdue_files'] += 1

    # Calculate averages
    if stats['total_files'] > 0:
        stats['time_stats']['average_time_per_file'] = stats['time_stats']['total_time_worked'] / stats['total_files']

    # Calculate step averages
    for step, step_data in stats['step_stats'].items():
        if step_data['total_files'] > 0:
            step_data['average_time'] = step_data['total_time_worked'] / step_data['total_files']

    # Calculate user averages
    for username, user_data in stats['user_stats'].items():
        files_worked = user_data['completed_steps'] + user_data['in_progress_steps']
        user_data['total_files_worked'] = files_worked
        if files_worked > 0:
            user_data['average_time_per_file'] = user_data['total_time_worked'] / files_worked

    # Calculate supplier averages
    for supplier, supplier_data in stats['supplier_stats'].items():
        if supplier_data['total_files'] > 0:
            supplier_data['average_time'] = supplier_data['total_time_worked'] / supplier_data['total_files']

    # Calculate process type averages
    for process_type, pt_data in stats['process_type_stats'].items():
        if pt_data['total_files'] > 0:
            pt_data['average_time'] = pt_data['total_time_worked'] / pt_data['total_files']

    print(f"[STEP] Statistics calculated - Total files: {stats['total_files']}, Total overdue files: {stats['overdue_stats']['total_overdue_files']}")

    return render_template('statistics.html',
                          stats=stats,
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

@app.route('/manage_users')
def manage_users():
    if 'username' not in session:
        return redirect(url_for('login'))

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        flash('Only administrators can manage users')
        return redirect(url_for('index'))

    return render_template('manage_users.html',
                          users=users_db,
                          username=session['username'],
                          is_admin=True)

@app.route('/manage_suppliers')
def manage_suppliers():
    if 'username' not in session:
        return redirect(url_for('login'))

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        flash('Only administrators can manage suppliers')
        return redirect(url_for('index'))

    # Count files per supplier
    suppliers = {}
    for _, file in files_db.items():
        supplier = file.get('supplier', 'unknown')
        if supplier in suppliers:
            suppliers[supplier] += 1
        else:
            suppliers[supplier] = 1

    return render_template('manage_suppliers.html',
                          suppliers=suppliers,
                          username=session['username'],
                          is_admin=True)

@app.route('/manage_process_types')
def manage_process_types():
    print("\n[EXECUTING] manage_process_types() - Managing process types")
    if 'username' not in session:
        print("[STEP] User not in session, redirecting to login")
        return redirect(url_for('login'))

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        print(f"[STEP] User {session['username']} is not an admin, redirecting to index")
        flash('Only administrators can manage process types')
        return redirect(url_for('index'))

    print(f"[STEP] Rendering manage process types page with {len(process_types)} process types")
    return render_template('manage_process_types.html',
                          process_types=process_types,
                          username=session['username'],
                          is_admin=True)

@app.route('/update_process_types', methods=['POST'])
def update_process_types():
    if 'username' not in session:
        return redirect(url_for('login'))

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        flash('Only administrators can manage process types')
        return redirect(url_for('index'))

    # Get the updated process types from the form
    new_process_types = request.form.getlist('process_types[]')

    # Filter out empty values
    new_process_types = [pt.strip() for pt in new_process_types if pt.strip()]

    # Ensure we have at least one process type
    if not new_process_types:
        flash('You must have at least one process type')
        return redirect(url_for('manage_process_types'))

    # Update the global process_types list
    global process_types
    process_types.clear()
    process_types.extend(new_process_types)

    # Mark data as changed
    data_manager.mark_data_changed()

    flash('Process types updated successfully')
    return redirect(url_for('manage_process_types'))

@app.route('/delete_supplier', methods=['POST'])
def delete_supplier():
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        return jsonify({"success": False, "message": "Only administrators can delete suppliers"}), 403

    data = request.json
    supplier = data.get('supplier')

    if not supplier:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    # Find all files for this supplier
    files_to_delete = []
    for file_id, file in files_db.items():
        if file.get('supplier') == supplier:
            files_to_delete.append(file_id)

    if not files_to_delete:
        return jsonify({"success": False, "message": "No files found for this supplier"}), 404

    # Delete all files for this supplier
    for file_id in files_to_delete:
        file = files_db[file_id]

        # Delete physical files from disk
        for entry in file.get('history', []):
            if entry.get('path') and os.path.exists(entry['path']):
                try:
                    os.remove(entry['path'])
                except Exception as e:
                    print(f"Error deleting file {entry['path']}: {e}")

        # Remove file from database
        del files_db[file_id]

    # Mark data as changed
    data_manager.mark_data_changed()

    return jsonify({"success": True})

@app.route('/delete_user', methods=['POST'])
def delete_user():
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        return jsonify({"success": False, "message": "Only administrators can delete users"}), 403

    data = request.json
    username_to_delete = data.get('username')

    if not username_to_delete:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    if username_to_delete not in users_db:
        return jsonify({"success": False, "message": "User not found"}), 404

    # Cannot delete admin user
    if username_to_delete == 'admin':
        return jsonify({"success": False, "message": "Cannot delete the admin user"}), 403

    # Cannot delete yourself
    if username_to_delete == session['username']:
        return jsonify({"success": False, "message": "Cannot delete your own account"}), 403

    # Remove user from global step assignments
    for _, users in step_assignments.items():
        if username_to_delete in users:
            users.remove(username_to_delete)

    # Remove user from file-specific step assignments
    for _, file in files_db.items():
        if 'step_assignments' in file:
            for _, users in file['step_assignments'].items():
                if username_to_delete in users:
                    users.remove(username_to_delete)

    # Delete the user
    del users_db[username_to_delete]

    # Mark data as changed
    data_manager.mark_data_changed()

    return jsonify({"success": True})

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

        # Get custom steps
        custom_steps = request.form.getlist('custom_steps')
        # Filter out empty values
        custom_steps = [step.strip().lower() for step in custom_steps if step.strip()]

        if username in users_db:
            flash('Username already exists')
        else:
            users_db[username] = {
                'password': generate_password_hash(password),
                'assigned_steps': assigned_roles.copy(),  # For backward compatibility
                'roles': assigned_roles,  # Global roles for new files
                'custom_steps': custom_steps,  # Store custom steps with the user
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
            for _, file in files_db.items():
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

            # Mark data as changed
            data_manager.mark_data_changed()

            flash('User registered successfully')
            return redirect(url_for('manage_users'))

    return render_template('register.html', steps=steps)

@app.route('/upload', methods=['POST'])
def upload_file():
    print("\n[EXECUTING] upload_file() - Uploading new file")
    if 'username' not in session:
        print("[STEP] User not in session, redirecting to login")
        return redirect(url_for('login'))

    if 'file' not in request.files:
        print("[STEP] No file part in request")
        flash('No file part')
        return redirect(url_for('index'))

    file = request.files['file']
    supplier = request.form.get('supplier', 'unknown')
    process_type = request.form.get('process_type', process_types[0] if process_types else 'unknown')
    step = request.form.get('step', steps[0])
    print(f"[STEP] Upload details - Supplier: {supplier}, Process Type: {process_type}, Step: {step}, Filename: {file.filename}")

    # Check if user is authorized for this step
    if not is_authorized_for_step(session['username'], step):
        print(f"[STEP] User {session['username']} not authorized for step {step}")
        flash(f'You are not authorized to upload files for the {step} step')
        return redirect(url_for('index'))

    if file.filename == '':
        print("[STEP] No selected file (empty filename)")
        flash('No selected file')
        return redirect(url_for('index'))

    file_id = request.form.get('file_id', str(uuid.uuid4()))
    filename = secure_filename(file.filename)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"[STEP] Generated file_id: {file_id}, Secured filename: {filename}, Timestamp: {timestamp}")

    # Save file with unique name
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{file_id}_{step}_{filename}")
    print(f"[STEP] Saving file to path: {file_path}")
    file.save(file_path)
    print(f"[STEP] File saved successfully")
    # Update database
    print(f"[STEP] Updating database for file_id: {file_id}")

    if file_id not in files_db:
        print(f"[STEP] Creating new file entry in database")
        if file_id == '':
            file_id = str(uuid.uuid4())
            print(f"[STEP] Generated new file_id: {file_id}")
        # Create a copy of the default steps for this file
        file_steps = steps.copy()
        print(f"[STEP] Using default steps for file: {file_steps}")

        # Create file-specific step assignments based on global roles and custom steps
        print(f"[STEP] Creating file-specific step assignments")
        file_step_assignments = {}
        for s in file_steps:
            file_step_assignments[s] = []
            for username, user_data in users_db.items():
                # Check if step is in user's roles or custom steps
                if s in user_data.get('roles', []) or s in user_data.get('custom_steps', []):
                    file_step_assignments[s].append(username)
            print(f"[STEP] Step '{s}' assigned to users: {file_step_assignments[s]}")

        # Initialize step_statuses for all steps
        print(f"[STEP] Initializing step statuses")
        file_step_statuses = {}
        for s in file_steps:
            # Use the default assigned time for this step if available
            default_time = default_assigned_times.get(s, 0)
            print(f"[STEP] Step '{s}' default assigned time: {default_time} minutes")

            file_step_statuses[s] = {
                'status': 'Not Started',
                'last_update': None,
                'updated_by': None,
                'assigned_time': default_time,  # Use the default assigned time from global settings
                'total_time_worked': 0,  # Initialize total time worked to 0
                'is_overdue': False  # Initialize overdue status to False
            }

        # Set the current step to In Progress
        if file_steps:
            print(f"[STEP] Setting first step '{file_steps[0]}' to In Progress")
            # Use the default assigned time for the first step if available
            default_time = default_assigned_times.get(file_steps[0], 0)

            file_step_statuses[file_steps[0]] = {
                'status': 'In Progress',
                'last_update': timestamp,
                'updated_by': session['username'],
                'assigned_time': default_time,  # Use the default assigned time from global settings
                'total_time_worked': 0,  # Initialize total time worked to 0
                'is_overdue': False  # Initialize overdue status to False
            }

        print(f"[STEP] Creating complete file entry in database")
        files_db[file_id] = {
            'supplier': supplier,
            'process_type': process_type,
            'original_filename': filename,
            'current_step': file_steps[0] if file_steps else step,  # Start with the first step by default
            'history': [],
            'custom_steps': file_steps,  # Add custom steps for this file
            'step_assignments': file_step_assignments,  # Add file-specific step assignments
            'step_statuses': file_step_statuses,  # Add step statuses
            'creation_time': timestamp  # Store creation time
        }
        print(f"[STEP] File entry created with current_step: {files_db[file_id]['current_step']}")

    print(f"[STEP] Adding history entry for step: {step}")
    files_db[file_id]['history'].append({
        'step': step,
        'timestamp': timestamp,
        'filename': filename,
        'path': file_path,
        'user': session['username']
    })

    print(f"[STEP] Setting current step to: {step}")
    files_db[file_id]['current_step'] = step

    # Mark data as changed
    print(f"[STEP] Marking data as changed")
    data_manager.mark_data_changed()

    # Trigger notification scan since new file was uploaded
    trigger_notification_scan()

    print(f"[STEP] Upload complete, redirecting to index")
    flash('File uploaded successfully')
    return redirect(url_for('index'))

@app.route('/upload_to_step', methods=['POST'])
def upload_to_step():
    print("\n[EXECUTING] upload_to_step() - Uploading file to specific step or updating status")
    if 'username' not in session:
        print("[STEP] User not in session, returning authentication error")
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    username = session['username']
    file_id = request.form.get('file_id')
    step = request.form.get('step')
    status = request.form.get('status')
    comment = request.form.get('comment', '')

    print(f"[STEP] Upload request - File: {file_id}, Step: {step}, Status: {status}, User: {username}")

    # Validate required inputs
    if not file_id or not step or not status:
        print("[STEP] Missing required parameters")
        return jsonify({"success": False, "message": "Missing required parameters (file_id, step, or status)"}), 400

    if file_id not in files_db:
        print(f"[STEP] File {file_id} not found in database")
        return jsonify({"success": False, "message": "File not found"}), 404

    # Check if user is authorized for this step
    if not is_authorized_for_step(username, step, file_id):
        print(f"[STEP] User {username} not authorized for step {step}")
        return jsonify({"success": False, "message": "Not authorized for this step"}), 403

    try:
        # Check if file was uploaded (optional)
        file_uploaded = False
        uploaded_file = None
        file_path = None
        unique_filename = None

        if 'file' in request.files and request.files['file'].filename != '':
            uploaded_file = request.files['file']
            file_uploaded = True
            print(f"[STEP] File will be uploaded: {uploaded_file.filename}")

            # Save the uploaded file
            filename = secure_filename(uploaded_file.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            unique_filename = f"{timestamp}_{filename}"

            # Create step directory if it doesn't exist
            step_dir = os.path.join(app.config['UPLOAD_FOLDER'], step.replace(' ', '_').lower())
            os.makedirs(step_dir, exist_ok=True)

            file_path = os.path.join(step_dir, unique_filename)
            uploaded_file.save(file_path)

            print(f"[STEP] File saved to: {file_path}")
        else:
            print(f"[STEP] No file uploaded - status-only update")

        # Update file database
        file_data = files_db[file_id]

        # Update step status
        if 'step_statuses' not in file_data:
            file_data['step_statuses'] = {}

        file_data['step_statuses'][step] = {
            'status': status,
            'last_update': datetime.now().isoformat(),
            'updated_by': username,
            'assigned_time': file_data.get('step_statuses', {}).get(step, {}).get('assigned_time', 0)
        }

        # Add to history
        if 'history' not in file_data:
            file_data['history'] = []

        if file_uploaded:
            # File upload entry
            history_entry = {
                'step': step,
                'status': status,
                'timestamp': datetime.now().isoformat(),
                'user': username,
                'filename': unique_filename,
                'path': file_path,
                'comment': comment
            }
        else:
            # Status-only update entry
            history_entry = {
                'step': step,
                'status': status,
                'timestamp': datetime.now().isoformat(),
                'user': username,
                'filename': f"Status update to {status}",
                'path': None,  # No file for status-only updates
                'comment': comment
            }

        file_data['history'].append(history_entry)

        # If status is completed, move to next step
        if status == 'Completed':
            file_steps = file_data.get('custom_steps', steps)
            current_step_index = file_steps.index(step)

            if current_step_index < len(file_steps) - 1:
                next_step = file_steps[current_step_index + 1]
                file_data['current_step'] = next_step

                # Initialize next step status
                if next_step not in file_data['step_statuses']:
                    file_data['step_statuses'][next_step] = {
                        'status': 'In Progress',
                        'last_update': datetime.now().isoformat(),
                        'assigned_time': default_assigned_times.get(next_step, 0)
                    }

                print(f"[STEP] File moved to next step: {next_step}")
            else:
                print(f"[STEP] File completed all steps")

        # Mark data as changed for auto-save
        data_manager.mark_data_changed()

        # Trigger notification scan since file step was updated
        trigger_notification_scan()

        success_message = f"Step '{step}' updated to '{status}'"
        if file_uploaded:
            success_message += " with file upload"

        print(f"[STEP] Successfully updated file {file_id} in step {step} with status {status}")

        return jsonify({
            "success": True,
            "message": success_message
        })

    except Exception as e:
        print(f"[STEP] Error updating step: {e}")
        return jsonify({"success": False, "message": f"Error updating step: {str(e)}"}), 500

@app.route('/download/<file_id>/<step>')
def download_file(file_id, step):
    print(f"\n[EXECUTING] download_file({file_id}, {step}) - Downloading file for step")
    if 'username' not in session:
        print("[STEP] User not in session, redirecting to login")
        return redirect(url_for('login'))

    if file_id not in files_db:
        print(f"[STEP] File {file_id} not found in database")
        flash('File not found')
        return redirect(url_for('index'))

    print(f"[STEP] Searching for file version in step: {step}")
    # Find the file version for the requested step
    for entry in reversed(files_db[file_id]['history']):
        if entry['step'] == step:
            print(f"[STEP] Found file version - Filename: {entry.get('filename')}, Path: {entry.get('path')}")
            if entry.get('path'):
                print(f"[STEP] Sending file: {entry['path']}")
                return send_file(entry['path'], as_attachment=True,
                                download_name=entry['filename'])
            else:
                print(f"[STEP] Entry has no path, might be a status update")

    print(f"[STEP] No file version found for step: {step}")
    flash('Version not found')
    return redirect(url_for('index'))

@app.route('/download_previous_step_files', methods=['POST'])
def download_previous_step_files():
    print(f"\n[EXECUTING] download_previous_step_files() - Downloading previous step files for user's assigned steps")
    if 'username' not in session:
        print("[STEP] User not in session, returning authentication error")
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    username = session['username']
    print(f"[STEP] Processing download request for user: {username}")

    # Get user's assigned steps
    user_roles = users_db.get(username, {}).get('roles', [])
    user_custom_steps = users_db.get(username, {}).get('custom_steps', [])
    user_assigned_steps = set(user_roles + user_custom_steps)

    print(f"[STEP] User {username} is assigned to steps: {user_assigned_steps}")

    if not user_assigned_steps:
        return jsonify({"success": False, "message": "User has no assigned steps"}), 400

    # Find all files that are currently in user's assigned steps
    files_in_user_steps = []
    for file_id, file in files_db.items():
        current_step = file.get('current_step')
        if current_step in user_assigned_steps:
            # Check file-specific assignments if they exist
            file_assignments = file.get('step_assignments', {})
            if current_step in file_assignments:
                # Check if user is specifically assigned to this file's current step
                if username in file_assignments[current_step]:
                    files_in_user_steps.append((file_id, file, current_step))
                    print(f"[STEP] File {file_id} is in user's assigned step '{current_step}' (file-specific assignment)")
            else:
                # Fall back to global role assignment
                files_in_user_steps.append((file_id, file, current_step))
                print(f"[STEP] File {file_id} is in user's assigned step '{current_step}' (global assignment)")

    print(f"[STEP] Found {len(files_in_user_steps)} files in user's assigned steps")

    if not files_in_user_steps:
        return jsonify({"success": False, "message": "No files found in your assigned steps"}), 404

    # Collect files from previous steps
    files_to_download = []

    for file_id, file, current_step in files_in_user_steps:
        file_steps = file.get('custom_steps', steps)

        print(f"[STEP] Processing file {file_id} - Current step: {current_step}")

        if not current_step or current_step not in file_steps:
            print(f"[STEP] File {file_id} has invalid current step, skipping")
            continue

        # Find the previous step
        current_step_index = file_steps.index(current_step)
        if current_step_index == 0:
            print(f"[STEP] File {file_id} is at the first step, no previous step available")
            continue

        previous_step = file_steps[current_step_index - 1]
        print(f"[STEP] Previous step for file {file_id}: {previous_step}")

        # Find the latest file from the previous step
        latest_file_entry = None
        for entry in reversed(file.get('history', [])):
            if entry.get('step') == previous_step and entry.get('path') and os.path.exists(entry['path']):
                latest_file_entry = entry
                break

        if latest_file_entry:
            files_to_download.append({
                'file_id': file_id,
                'original_filename': file.get('original_filename', 'unknown'),
                'supplier': file.get('supplier', 'unknown'),
                'process_type': file.get('process_type', 'unknown'),
                'current_step': current_step,
                'previous_step': previous_step,
                'entry': latest_file_entry
            })
            print(f"[STEP] Added file {file_id} from step {previous_step}: {latest_file_entry['filename']}")
        else:
            print(f"[STEP] No file found for previous step {previous_step} in file {file_id}")

    if not files_to_download:
        return jsonify({"success": False, "message": "No files found from previous steps"}), 404

    print(f"[STEP] Creating zip file with {len(files_to_download)} files")

    # Create a temporary zip file
    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    temp_zip_path = temp_zip.name
    temp_zip.close()

    try:
        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_info in files_to_download:
                entry = file_info['entry']
                file_path = entry['path']

                if os.path.exists(file_path):
                    # Create a meaningful filename for the zip organized by current step
                    current_step_clean = file_info['current_step'].replace(' ', '_').replace('/', '_')
                    previous_step_clean = file_info['previous_step'].replace(' ', '_').replace('/', '_')
                    supplier_clean = file_info['supplier'].replace(' ', '_').replace('/', '_')

                    # Organize files in folders by current step (the step user is working on)
                    zip_filename = f"{current_step_clean}/{supplier_clean}_{previous_step_clean}_{entry['filename']}"
                    zipf.write(file_path, zip_filename)
                    print(f"[STEP] Added to zip: {zip_filename}")
                else:
                    print(f"[STEP] File not found on disk: {file_path}")

        # Generate a descriptive filename for the download based on user's assigned steps
        user_steps_desc = []
        if user_assigned_steps:
            # Get unique current steps from the files we're processing
            current_steps_in_download = set()
            for file_info in files_to_download:
                current_steps_in_download.add(file_info['current_step'])

            if current_steps_in_download:
                user_steps_desc.append(f"user_{username}")
                if len(current_steps_in_download) == 1:
                    step_name = list(current_steps_in_download)[0].replace(' ', '_')
                    user_steps_desc.append(f"from_step_{step_name}")
                else:
                    user_steps_desc.append(f"from_{len(current_steps_in_download)}_steps")

        if user_steps_desc:
            zip_filename = f"previous_step_files_{'_'.join(user_steps_desc)}.zip"
        else:
            zip_filename = f"previous_step_files_user_{username}.zip"

        print(f"[STEP] Sending zip file: {zip_filename}")

        def remove_temp_file():
            try:
                os.unlink(temp_zip_path)
                print(f"[STEP] Cleaned up temporary file: {temp_zip_path}")
            except Exception as e:
                print(f"[STEP] Error cleaning up temporary file: {e}")

        # Send the file and schedule cleanup
        response = send_file(temp_zip_path, as_attachment=True, download_name=zip_filename)

        # Schedule the temp file for deletion after the response is sent
        # Note: This is a simple approach; in production, you might want a more robust cleanup mechanism
        import atexit
        atexit.register(remove_temp_file)

        return response

    except Exception as e:
        print(f"[STEP] Error creating zip file: {e}")
        # Clean up temp file on error
        try:
            os.unlink(temp_zip_path)
        except:
            pass
        return jsonify({"success": False, "message": f"Error creating zip file: {str(e)}"}), 500

@app.route('/download_single_previous_step_file', methods=['POST'])
def download_single_previous_step_file():
    print(f"\n[EXECUTING] download_single_previous_step_file() - Downloading previous step file for single file")
    if 'username' not in session:
        print("[STEP] User not in session, returning authentication error")
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    data = request.json
    file_id = data.get('file_id')

    print(f"[STEP] Received request for file: {file_id}")

    if not file_id:
        return jsonify({"success": False, "message": "No file ID specified"}), 400

    if file_id not in files_db:
        return jsonify({"success": False, "message": "File not found"}), 404

    file = files_db[file_id]
    current_step = file.get('current_step')
    file_steps = file.get('custom_steps', steps)

    print(f"[STEP] Processing file {file_id} - Current step: {current_step}")

    if not current_step or current_step not in file_steps:
        return jsonify({"success": False, "message": "File has invalid current step"}), 400

    # Find the previous step
    current_step_index = file_steps.index(current_step)
    if current_step_index == 0:
        return jsonify({"success": False, "message": "File is at the first step, no previous step available"}), 400

    previous_step = file_steps[current_step_index - 1]
    print(f"[STEP] Previous step for file {file_id}: {previous_step}")

    # Find the latest file from the previous step
    latest_file_entry = None
    for entry in reversed(file.get('history', [])):
        if entry.get('step') == previous_step and entry.get('path') and os.path.exists(entry['path']):
            latest_file_entry = entry
            break

    if not latest_file_entry:
        return jsonify({"success": False, "message": f"No file found for previous step '{previous_step}'"}), 404

    print(f"[STEP] Found file from step {previous_step}: {latest_file_entry['filename']}")

    try:
        # Generate a descriptive filename for the download
        supplier_clean = file.get('supplier', 'unknown').replace(' ', '_').replace('/', '_')
        previous_step_clean = previous_step.replace(' ', '_').replace('/', '_')
        original_filename = latest_file_entry['filename']

        download_filename = f"{supplier_clean}_{previous_step_clean}_{original_filename}"

        print(f"[STEP] Sending file: {latest_file_entry['path']} as {download_filename}")

        return send_file(latest_file_entry['path'], as_attachment=True, download_name=download_filename)

    except Exception as e:
        print(f"[STEP] Error sending file: {e}")
        return jsonify({"success": False, "message": f"Error downloading file: {str(e)}"}), 500

@app.route('/api/files')
def get_files():
    if 'username' not in session:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify(files_db)

@app.route('/delete_file', methods=['POST'])
def delete_file():
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        return jsonify({"success": False, "message": "Only administrators can delete files"}), 403

    data = request.json
    file_id = data.get('file_id')

    if not file_id:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    if file_id not in files_db:
        return jsonify({"success": False, "message": "File not found"}), 404

    # Get file info for cleanup
    file = files_db[file_id]

    # Delete physical files from disk
    for entry in file.get('history', []):
        if entry.get('path') and os.path.exists(entry['path']):
            try:
                os.remove(entry['path'])
            except Exception as e:
                print(f"Error deleting file {entry['path']}: {e}")

    # Remove file from database
    del files_db[file_id]

    # Mark data as changed
    data_manager.mark_data_changed()

    return jsonify({"success": True})

@app.route('/delete_step_file', methods=['POST'])
def delete_step_file():
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    # Check if user is admin
    if not users_db.get(session['username'], {}).get('is_admin', False):
        return jsonify({"success": False, "message": "Only administrators can delete files"}), 403

    data = request.json
    file_id = data.get('file_id')
    step = data.get('step')
    timestamp = data.get('timestamp')

    if not all([file_id, step, timestamp]):
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    if file_id not in files_db:
        return jsonify({"success": False, "message": "File not found"}), 404

    # Find the specific file entry in history
    file = files_db[file_id]
    entry_index = None

    for i, entry in enumerate(file.get('history', [])):
        if entry.get('step') == step and entry.get('timestamp') == timestamp:
            entry_index = i
            break

    if entry_index is None:
        return jsonify({"success": False, "message": "File version not found"}), 404

    # Get the entry to delete
    entry = file['history'][entry_index]

    # Delete physical file from disk if it exists
    if entry.get('path') and os.path.exists(entry['path']):
        try:
            os.remove(entry['path'])
        except Exception as e:
            print(f"Error deleting file {entry['path']}: {e}")

    # Remove entry from history
    file['history'].pop(entry_index)

    # Update current step if needed
    update_current_step(file_id)

    # Mark data as changed
    data_manager.mark_data_changed()

    return jsonify({"success": True})

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

@app.route('/api/step_times/<file_id>')
def get_step_times(file_id):
    """
    API endpoint to get the current total time worked for all steps of a file.
    This is used for real-time updates of the time worked display.
    """
    print(f"\n[EXECUTING] get_step_times({file_id}) - Getting step times for file")
    if 'username' not in session:
        print("[STEP] User not in session, returning authentication error")
        return jsonify({"error": "Not authenticated"}), 401

    if file_id not in files_db:
        print(f"[STEP] File {file_id} not found in database")
        return jsonify({"error": "File not found"}), 404

    file = files_db[file_id]
    file_steps = file.get('custom_steps', steps)
    print(f"[STEP] File steps: {file_steps}")

    # Calculate step times
    print(f"[STEP] Calculating step times for file {file_id}")
    step_times = {}

    # Sort all history entries by timestamp
    all_entries = file['history'].copy()
    all_entries.sort(key=lambda x: datetime.fromisoformat(x['timestamp']))

    # Find the completion time for each step
    step_completion_times = {}
    for entry in all_entries:
        step = entry.get('step')
        if step in file_steps:
            # Check if this entry marks the step as completed
            if entry.get('filename', '').startswith('Status update to Completed'):
                step_completion_times[step] = entry['timestamp']

    # Get current step statuses
    step_statuses = {}
    if 'step_statuses' in file:
        for step in file_steps:
            if step in file['step_statuses']:
                if isinstance(file['step_statuses'][step], dict):
                    step_statuses[step] = file['step_statuses'][step].get('status', 'Not Started')
                else:
                    step_statuses[step] = file['step_statuses'][step]
            else:
                step_statuses[step] = 'Not Started'

    # Calculate total time worked for each step
    for i, step in enumerate(file_steps):
        print(f"[STEP] Calculating time for step: {step} (index {i})")
        total_time_minutes = 0
        is_overdue = False
        assigned_time = 0

        # Get assigned time if available
        if 'step_statuses' in file and step in file['step_statuses'] and isinstance(file['step_statuses'][step], dict):
            assigned_time = file['step_statuses'][step].get('assigned_time', 0)
            print(f"[STEP] Step '{step}' assigned time: {assigned_time} minutes")

        # Find when this step started (when previous step was completed)
        start_time = None
        if i > 0:  # If not the first step
            prev_step = file_steps[i-1]
            if prev_step in step_completion_times:
                start_time = datetime.fromisoformat(step_completion_times[prev_step])
                print(f"[STEP] Step '{step}' start time based on previous step completion: {start_time}")
        else:  # For the first step, use the first history entry time
            if all_entries and all_entries[0]['step'] == step:
                start_time = datetime.fromisoformat(all_entries[0]['timestamp'])
                print(f"[STEP] First step '{step}' start time based on first history entry: {start_time}")

        # If we have a start time, calculate the total time worked
        if start_time:
            if step_statuses.get(step) == 'Completed' and step in step_completion_times:
                end_time = datetime.fromisoformat(step_completion_times[step])
                total_time_minutes = (end_time - start_time).total_seconds() / 60
                print(f"[STEP] Step '{step}' is completed - End time: {end_time}, Total time: {int(total_time_minutes)} minutes")
            elif step_statuses.get(step) == 'In Progress' or step_statuses.get(step) == 'Not Started':
                end_time = datetime.now()
                total_time_minutes = (end_time - start_time).total_seconds() / 60
                print(f"[STEP] Step '{step}' is in progress - Current time: {end_time}, Total time so far: {int(total_time_minutes)} minutes")
        else:
            print(f"[STEP] No start time found for step '{step}', cannot calculate time worked")

        # Check if overdue
        is_overdue = assigned_time > 0 and total_time_minutes > assigned_time
        if is_overdue:
            print(f"[STEP] Step '{step}' is OVERDUE - Worked: {int(total_time_minutes)} min, Assigned: {assigned_time} min")

        # Add to result
        step_times[step] = {
            'total_time_worked': int(total_time_minutes),
            'is_overdue': is_overdue
        }
        print(f"[STEP] Added time data for step '{step}': {step_times[step]}")
    return jsonify({"step_times": step_times})

@app.route('/update_status', methods=['POST'])
def update_status():
    print("\n[EXECUTING] update_status() - Updating step status")
    if 'username' not in session:
        print("[STEP] User not in session, returning authentication error")
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    data = request.json
    file_id = data.get('file_id')
    step = data.get('step')
    status = data.get('status')
    print(f"[STEP] Update status request - File ID: {file_id}, Step: {step}, Status: {status}")

    if not all([file_id, step, status]):
        print("[STEP] Missing required fields in request")
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    if file_id not in files_db:
        print(f"[STEP] File {file_id} not found in database")
        return jsonify({"success": False, "message": "File not found"}), 404

    # Check if step is valid for this file
    file_steps = files_db[file_id].get('custom_steps', steps)
    print(f"[STEP] File steps: {file_steps}")
    if step not in file_steps:
        print(f"[STEP] Step {step} is not valid for this file")
        return jsonify({"success": False, "message": "Invalid step for this file"}), 400

    if not is_authorized_for_step(session['username'], step, file_id):
        print(f"[STEP] User {session['username']} not authorized to update step {step}")
        return jsonify({"success": False, "message": "Not authorized to update this step"}), 403

    # Update the file status
    print(f"[STEP] Updating file status to: {status}")
    if status == 'Completed' or status == 'In Progress' or status == 'Not Started':
        # Add a status update entry to history
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        print(f"[STEP] Creating history entry with timestamp: {timestamp}")
        files_db[file_id]['history'].append({
            'step': step,
            'timestamp': timestamp,
            'filename': f"Status update to {status}",
            'path': None,  # No file for status updates
            'user': session['username']
        })

        # Ensure file has step_statuses dictionary
        if 'step_statuses' not in files_db[file_id]:
            print(f"[STEP] Creating step_statuses dictionary for file {file_id}")
            files_db[file_id]['step_statuses'] = {}

        # Ensure the step has a status entry as a dictionary
        if step not in files_db[file_id]['step_statuses'] or not isinstance(files_db[file_id]['step_statuses'][step], dict):
            print(f"[STEP] Creating new status entry for step {step}")
            files_db[file_id]['step_statuses'][step] = {
                'status': status,
                'last_update': timestamp,
                'updated_by': session['username'],
                'assigned_time': 0  # Default assigned time in minutes (0 means no time limit)
            }
        else:
            # Update the status, timestamp, and user
            print(f"[STEP] Updating existing status entry for step {step}")
            files_db[file_id]['step_statuses'][step]['status'] = status
            files_db[file_id]['step_statuses'][step]['last_update'] = timestamp
            files_db[file_id]['step_statuses'][step]['updated_by'] = session['username']

        # Get file's custom steps
        file_steps = files_db[file_id].get('custom_steps', steps)
        print(f"[STEP] File steps: {file_steps}")

        # If status is completed, update the current step
        if status == 'Completed' or True:
            print(f"[STEP] Status is '{status}', updating current step")
            # Find the last completed step and set current step to the next one
            update_current_step(file_id)

        # Mark data as changed
        print(f"[STEP] Marking data as changed")
        data_manager.mark_data_changed()

        # Trigger notification scan since file status was updated
        trigger_notification_scan()
        print(f"[STEP] Status update complete")

    return jsonify({"success": True})

@app.route('/update_step_assigned_time', methods=['POST'])
def update_step_assigned_time():
    if 'username' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    data = request.json
    file_id = data.get('file_id')
    step = data.get('step')
    assigned_time = data.get('assigned_time')

    if not all([file_id, step]) or assigned_time is None:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    try:
        assigned_time = int(assigned_time)
        if assigned_time < 0:
            return jsonify({"success": False, "message": "Assigned time must be a non-negative integer"}), 400
    except ValueError:
        return jsonify({"success": False, "message": "Assigned time must be a valid integer"}), 400

    if file_id not in files_db:
        return jsonify({"success": False, "message": "File not found"}), 404

    # Check if step is valid for this file
    file_steps = files_db[file_id].get('custom_steps', steps)
    if step not in file_steps:
        return jsonify({"success": False, "message": "Invalid step for this file"}), 400

    # Ensure file has step_statuses dictionary
    if 'step_statuses' not in files_db[file_id]:
        files_db[file_id]['step_statuses'] = {}

    # Ensure the step has a status entry as a dictionary
    if step not in files_db[file_id]['step_statuses'] or not isinstance(files_db[file_id]['step_statuses'][step], dict):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')

        # Calculate total time worked based on previous step completion
        file = files_db[file_id]
        file_steps = file.get('custom_steps', steps)

        # First, find the completion time for each step
        step_completion_times = {}

        # Sort all history entries by timestamp
        all_entries = file['history'].copy()
        all_entries.sort(key=lambda x: datetime.fromisoformat(x['timestamp']))

        # Find the completion time for each step
        for entry in all_entries:
            entry_step = entry.get('step')
            if entry_step in file_steps:
                # Check if this entry marks the step as completed
                if entry.get('filename', '').startswith('Status update to Completed'):
                    step_completion_times[entry_step] = entry['timestamp']

        # Get the index of the current step
        try:
            step_index = file_steps.index(step)
        except ValueError:
            step_index = -1

        total_time_minutes = 0

        # Get the current step status
        current_status = 'Not Started'
        if 'step_statuses' in file and step in file['step_statuses']:
            if isinstance(file['step_statuses'][step], dict):
                current_status = file['step_statuses'][step].get('status', 'Not Started')
            else:
                current_status = file['step_statuses'][step]

        # Find when this step started (when previous step was completed)
        start_time = None
        if step_index > 0:  # If not the first step
            prev_step = file_steps[step_index-1]
            if prev_step in step_completion_times:
                start_time = datetime.fromisoformat(step_completion_times[prev_step])
        else:  # For the first step, use the first history entry time
            if all_entries and all_entries[0]['step'] == step:
                start_time = datetime.fromisoformat(all_entries[0]['timestamp'])

        # If we have a start time, calculate the total time worked
        if start_time:
            if step in step_completion_times:  # If step is completed
                end_time = datetime.fromisoformat(step_completion_times[step])
                total_time_minutes = (end_time - start_time).total_seconds() / 60
            elif current_status == 'In Progress':  # If step is in progress
                end_time = datetime.now()
                total_time_minutes = (end_time - start_time).total_seconds() / 60

        files_db[file_id]['step_statuses'][step] = {
            'status': 'Not Started',
            'last_update': timestamp,
            'updated_by': session['username'],
            'assigned_time': assigned_time,
            'total_time_worked': int(total_time_minutes),
            'is_overdue': assigned_time > 0 and total_time_minutes > assigned_time
        }
    else:
        # Update the assigned time
        files_db[file_id]['step_statuses'][step]['assigned_time'] = assigned_time

        # Get the total time worked
        total_time_worked = files_db[file_id]['step_statuses'][step].get('total_time_worked', 0)

        # Update the is_overdue flag
        files_db[file_id]['step_statuses'][step]['is_overdue'] = assigned_time > 0 and total_time_worked > assigned_time

    # Mark data as changed
    data_manager.mark_data_changed()

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
            'user': None,
            'total_time_worked': 0
        }

        # Use saved step status if available
        if 'step_statuses' in file and step in file['step_statuses']:
            if isinstance(file['step_statuses'][step], dict):
                # Use the stored dictionary values
                step_data['status'] = file['step_statuses'][step].get('status', 'Not Started')
                step_data['last_update'] = file['step_statuses'][step].get('last_update')
                step_data['user'] = file['step_statuses'][step].get('updated_by')
                step_data['total_time_worked'] = file['step_statuses'][step].get('total_time_worked', 0)
                step_data['assigned_time'] = file['step_statuses'][step].get('assigned_time', 0)
                step_data['is_overdue'] = file['step_statuses'][step].get('is_overdue', False)
            else:
                # For backward compatibility with old format where step_statuses just stored the status string
                step_data['status'] = file['step_statuses'][step]

                # Still need to get last_update and user from history
                for entry in file['history']:
                    if entry['step'] == step:
                        if step_data['last_update'] is None or entry['timestamp'] > step_data['last_update']:
                            step_data['last_update'] = entry['timestamp']
                            step_data['user'] = entry['user']
        else:
            # Fall back to calculating from history for backward compatibility
            # Check file history for this step
            for entry in file['history']:
                if entry['step'] == step:
                    if step_data['last_update'] is None or entry['timestamp'] > step_data['last_update']:
                        step_data['last_update'] = entry['timestamp']
                        step_data['user'] = entry['user']

                        # Check for explicit status updates
                        if entry.get('filename', '').startswith('Status update to '):
                            status = entry['filename'].replace('Status update to ', '')
                            step_data['status'] = status
                        elif entry.get('path'):  # If there's a file upload
                            step_data['status'] = 'In Progress'
                        else:
                            step_data['status'] = 'Completed'

            # Current step is in progress if not already completed
            if file['current_step'] == step and step_data['status'] != 'Completed':
                step_data['status'] = 'In Progress'

        # If we don't have stored values for total_time_worked, assigned_time, or is_overdue,
        # make sure they have default values
        if 'total_time_worked' not in step_data:
            step_data['total_time_worked'] = 0

        if 'assigned_time' not in step_data:
            step_data['assigned_time'] = 0

        if 'is_overdue' not in step_data:
            step_data['is_overdue'] = False

        # Update the file's stored values to ensure they're up to date
        if 'step_statuses' in file and step in file['step_statuses'] and isinstance(file['step_statuses'][step], dict):
            file['step_statuses'][step]['total_time_worked'] = step_data['total_time_worked']
            file['step_statuses'][step]['assigned_time'] = step_data['assigned_time']
            file['step_statuses'][step]['is_overdue'] = step_data['is_overdue']

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

    # Ensure file has step_statuses dictionary
    if 'step_statuses' not in file:
        file['step_statuses'] = {}

    # Initialize the status for the new step with timestamp and user
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')

    # Use the default assigned time for this step if available
    default_time = default_assigned_times.get(step_name, 0)

    file['step_statuses'][step_name] = {
        'status': 'Not Started',
        'last_update': timestamp,
        'updated_by': session['username'],
        'assigned_time': default_time,  # Use the default assigned time from global settings
        'total_time_worked': 0,  # Initialize total time worked to 0
        'is_overdue': False  # Initialize overdue status to False
    }

    # Ensure file has step_assignments
    if 'step_assignments' not in file:
        file['step_assignments'] = {}

    # Initialize step assignments for the new step
    file['step_assignments'][step_name] = []

    # Automatically assign users who have this step in their custom steps
    for username, user_data in users_db.items():
        if step_name in user_data.get('custom_steps', []) or step_name in user_data.get('roles', []):
            file['step_assignments'][step_name].append(username)

    # Mark data as changed
    data_manager.mark_data_changed()

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

    # Update step_statuses if they exist
    if 'step_statuses' in file and old_step in file['step_statuses']:
        # Save the old status data
        old_status_data = file['step_statuses'][old_step]
        # Remove the old step
        del file['step_statuses'][old_step]
        # Add the new step with the old status data
        file['step_statuses'][new_step] = old_status_data

        # Update the last_update and updated_by if it's a dictionary
        if isinstance(file['step_statuses'][new_step], dict):
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
            file['step_statuses'][new_step]['last_update'] = timestamp
            file['step_statuses'][new_step]['updated_by'] = session['username']

    # Mark data as changed
    data_manager.mark_data_changed()

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

    # Remove step from step_statuses if it exists
    if 'step_statuses' in file and step in file['step_statuses']:
        del file['step_statuses'][step]

    # Mark data as changed
    data_manager.mark_data_changed()

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

    # Mark data as changed
    data_manager.mark_data_changed()

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

    # Reset step_statuses
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    files_db[file_id]['step_statuses'] = {}
    for s in steps:
        # Use the default assigned time for this step if available
        default_time = default_assigned_times.get(s, 0)

        files_db[file_id]['step_statuses'][s] = {
            'status': 'Not Started',
            'last_update': timestamp,
            'updated_by': session['username'],
            'assigned_time': default_time,  # Use the default assigned time from global settings
            'total_time_worked': 0,  # Initialize total time worked to 0
            'is_overdue': False  # Initialize overdue status to False
        }

    # Set current step to In Progress
    if steps:
        # Use the default assigned time for the first step if available
        default_time = default_assigned_times.get(steps[0], 0)

        files_db[file_id]['step_statuses'][steps[0]] = {
            'status': 'In Progress',
            'last_update': timestamp,
            'updated_by': session['username'],
            'assigned_time': default_time,  # Use the default assigned time from global settings
            'total_time_worked': 0,  # Initialize total time worked to 0
            'is_overdue': False  # Initialize overdue status to False
        }

    # Mark data as changed
    data_manager.mark_data_changed()

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
                if s in user_data.get('roles', []) or s in user_data.get('custom_steps', []):
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

    # Mark data as changed
    data_manager.mark_data_changed()

    # Trigger notification scan since step assignments were updated
    trigger_notification_scan()

    flash(f'Users for step "{step}" updated successfully')
    return redirect(url_for('file_pipeline', file_id=file_id))

@app.route('/api/file_info/<file_id>')
def get_file_info(file_id):
    """Get file information for the upload modal"""
    if 'username' not in session:
        return jsonify({"error": "Not authenticated"}), 401

    if file_id not in files_db:
        return jsonify({"success": False, "message": "File not found"}), 404

    file = files_db[file_id]
    return jsonify({
        "success": True,
        "file": {
            "original_filename": file.get('original_filename', 'Unknown'),
            "supplier": file.get('supplier', 'Unknown'),
            "process_type": file.get('process_type', 'Unknown'),
            "current_step": file.get('current_step', 'Unknown'),
            "step_statuses": file.get('step_statuses', {})
        }
    })



if __name__ == '__main__':
    # Start the notification scanner thread
    start_notification_scanner()

    # Run initial notification scan
    print("[STARTUP] Running initial notification scan...")
    scan_and_update_file_notifications()

    app.run(debug=True, host='0.0.0.0', port=5102)






