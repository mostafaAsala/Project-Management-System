import json
import os
import pickle
from datetime import datetime
import threading
import time

# Define data file paths
DATA_DIR = 'data'
USERS_FILE = os.path.join(DATA_DIR, 'users.pkl')
FILES_DB_FILE = os.path.join(DATA_DIR, 'files_db.pkl')
STEPS_FILE = os.path.join(DATA_DIR, 'steps.pkl')
STEP_ASSIGNMENTS_FILE = os.path.join(DATA_DIR, 'step_assignments.pkl')
CUSTOM_STEPS_FILE = os.path.join(DATA_DIR, 'custom_steps.pkl')
PROCESS_TYPES_FILE = os.path.join(DATA_DIR, 'process_types.pkl')
DEFAULT_ASSIGNED_TIMES_FILE = os.path.join(DATA_DIR, 'default_assigned_times.pkl')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')

# Ensure data directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# Global variables to track data changes
_data_changed = False
_save_lock = threading.Lock()
_auto_save_thread = None
_stop_auto_save = threading.Event()

def load_data():
    """
    Load all data from files.
    Returns a tuple of (users_db, files_db, steps, step_assignments, custom_steps_list, process_types)
    """
    users_db = load_users()
    files_db = load_files_db()
    steps_list = load_steps()
    step_assignments = load_step_assignments()
    custom_steps_list = load_custom_steps()
    process_types = load_process_types()

    return users_db, files_db, steps_list, step_assignments, custom_steps_list, process_types

def load_custom_steps():
    """
    Load custom steps list from file.
    """
    if os.path.exists(CUSTOM_STEPS_FILE):
        try:
            with open(CUSTOM_STEPS_FILE, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            print(f"Error loading custom steps data: {e}")
            return []
    else:
        return []

def save_data(users_db, files_db, steps_list, step_assignments, custom_steps_list=None, process_types=None, default_assigned_times=None):
    """
    Save all data to files.
    """
    print("saving all data")
    save_users(users_db)
    save_files_db(files_db)
    save_steps(steps_list)
    save_step_assignments(step_assignments)
    if custom_steps_list is not None:
        save_custom_steps(custom_steps_list)
    if process_types is not None:
        save_process_types(process_types)
    if default_assigned_times is not None:
        save_default_assigned_times(default_assigned_times)
    create_backup()

def mark_data_changed():
    """
    Mark that data has been changed and needs to be saved.
    """
    global _data_changed
    _data_changed = True

def load_users():
    """
    Load users database from file.
    """
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            print(f"Error loading users data: {e}")
            return create_default_users()
    else:
        return create_default_users()

def save_users(users_db):
    """
    Save users database to file.
    """
    try:
        with open(USERS_FILE, 'wb') as f:
            pickle.dump(users_db, f)
    except Exception as e:
        print(f"Error saving users data: {e}")

def load_files_db():
    """
    Load files database from file.
    """
    if os.path.exists(FILES_DB_FILE):
        try:
            with open(FILES_DB_FILE, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            print(f"Error loading files data: {e}")
            return {}
    else:
        return {}

def save_files_db(files_db):
    """
    Save files database to file.
    """
    try:
        with open(FILES_DB_FILE, 'wb') as f:
            pickle.dump(files_db, f)
    except Exception as e:
        print(f"Error saving files data: {e}")

def load_steps():
    """
    Load steps list from file.
    """
    if os.path.exists(STEPS_FILE):
        try:
            with open(STEPS_FILE, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            print(f"Error loading steps data: {e}")
            return create_default_steps()
    else:
        return create_default_steps()

def save_steps(steps_list):
    """
    Save steps list to file.
    """
    try:
        with open(STEPS_FILE, 'wb') as f:
            pickle.dump(steps_list, f)
    except Exception as e:
        print(f"Error saving steps data: {e}")

def load_step_assignments():
    """
    Load step assignments from file.
    """
    if os.path.exists(STEP_ASSIGNMENTS_FILE):
        try:
            with open(STEP_ASSIGNMENTS_FILE, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            print(f"Error loading step assignments data: {e}")
            return create_default_step_assignments()
    else:
        return create_default_step_assignments()

def save_step_assignments(step_assignments):
    """
    Save step assignments to file.
    """
    try:
        with open(STEP_ASSIGNMENTS_FILE, 'wb') as f:
            pickle.dump(step_assignments, f)
    except Exception as e:
        print(f"Error saving step assignments data: {e}")



def save_custom_steps(custom_steps_list):
    """
    Save custom steps list to file.
    """
    try:
        with open(CUSTOM_STEPS_FILE, 'wb') as f:
            pickle.dump(custom_steps_list, f)
    except Exception as e:
        print(f"Error saving custom steps data: {e}")

def load_process_types():
    """
    Load process types list from file.
    """
    if os.path.exists(PROCESS_TYPES_FILE):
        try:
            with open(PROCESS_TYPES_FILE, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            print(f"Error loading process types data: {e}")
            return create_default_process_types()
    else:
        return create_default_process_types()

def save_process_types(process_types):
    """
    Save process types list to file.
    """
    try:
        with open(PROCESS_TYPES_FILE, 'wb') as f:
            pickle.dump(process_types, f)
    except Exception as e:
        print(f"Error saving process types data: {e}")

def load_default_assigned_times():
    """
    Load default assigned times for steps from file.
    """
    if os.path.exists(DEFAULT_ASSIGNED_TIMES_FILE):
        try:
            with open(DEFAULT_ASSIGNED_TIMES_FILE, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            print(f"Error loading default assigned times data: {e}")
            return {}
    else:
        return {}

def save_default_assigned_times(default_assigned_times):
    """
    Save default assigned times for steps to file.
    """
    try:
        with open(DEFAULT_ASSIGNED_TIMES_FILE, 'wb') as f:
            pickle.dump(default_assigned_times, f)
    except Exception as e:
        print(f"Error saving default assigned times data: {e}")

def create_default_process_types():
    """
    Create default process types list.
    """
    return ["PS", "Lam"]

def create_default_users():
    """
    Create default users database.
    """
    from werkzeug.security import generate_password_hash
    default_steps = create_default_steps()

    return {
        'admin': {
            'password': generate_password_hash('admin'),
            'assigned_steps': default_steps.copy(),
            'is_admin': True,
            'roles': default_steps.copy()
        }
    }

def create_default_steps():
    """
    Create default steps list.
    """
    return ["intake", "processing", "validation", "approval", "final"]

def create_default_step_assignments():
    """
    Create default step assignments.
    """
    default_steps = create_default_steps()
    step_assignments = {}

    for step in default_steps:
        step_assignments[step] = ['admin']

    return step_assignments

def create_backup():
    """
    Create a backup of all data files.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_folder = os.path.join(BACKUP_DIR, timestamp)
    os.makedirs(backup_folder, exist_ok=True)

    # Copy all data files to backup folder
    for file_path in [USERS_FILE, FILES_DB_FILE, STEPS_FILE, STEP_ASSIGNMENTS_FILE, CUSTOM_STEPS_FILE, PROCESS_TYPES_FILE]:
        if os.path.exists(file_path):
            try:
                filename = os.path.basename(file_path)
                backup_path = os.path.join(backup_folder, filename)
                with open(file_path, 'rb') as src, open(backup_path, 'wb') as dst:
                    dst.write(src.read())
            except Exception as e:
                print(f"Error creating backup for {file_path}: {e}")

def start_auto_save(users_db, files_db, steps_list, step_assignments, custom_steps_list=None, process_types=None, default_assigned_times=None, interval=60):
    """
    Start auto-save thread that saves data at regular intervals if changes were made.
    """
    global _auto_save_thread, _stop_auto_save

    if _auto_save_thread is not None and _auto_save_thread.is_alive():
        return  # Auto-save already running

    _stop_auto_save.clear()

    def auto_save_worker():
        global _data_changed

        while not _stop_auto_save.is_set():
            # Check if data has changed
            if _data_changed:
                with _save_lock:
                    save_data(users_db, files_db, steps_list, step_assignments, custom_steps_list, process_types, default_assigned_times)
                    _data_changed = False
                    print(f"Auto-saved data at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            # Wait for the next interval
            _stop_auto_save.wait(interval)

    _auto_save_thread = threading.Thread(target=auto_save_worker, daemon=True)
    _auto_save_thread.start()
    print(f"Auto-save started with {interval} second interval")

def stop_auto_save():
    """
    Stop the auto-save thread.
    """
    global _auto_save_thread, _stop_auto_save

    if _auto_save_thread is not None and _auto_save_thread.is_alive():
        _stop_auto_save.set()
        _auto_save_thread.join(timeout=5)
        print("Auto-save stopped")
