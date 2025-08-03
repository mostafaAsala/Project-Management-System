#!/usr/bin/env python3
"""
Test script for the new notification system
"""

import sys
import os
import time
from datetime import datetime

# Add the current directory to the path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the app modules
import data_manager

def test_notification_system():
    """Test the notification system functionality"""
    print("=== Testing Notification System ===")
    
    # Load existing data
    print("\n1. Loading existing data...")
    users_db = data_manager.load_users()
    files_db = data_manager.load_files_db()
    notifications_db = data_manager.load_notifications()
    
    print(f"   - Users: {len(users_db)}")
    print(f"   - Files: {len(files_db)}")
    print(f"   - Notifications: {len(notifications_db)}")
    
    # Show current notifications
    print("\n2. Current notifications:")
    for username, user_notifications in notifications_db.items():
        file_assigned_count = len([n for n in user_notifications if n.get('type') == 'file_assigned'])
        total_count = len(user_notifications)
        print(f"   - {username}: {file_assigned_count} file_assigned / {total_count} total")
        
        # Show details of file_assigned notifications
        for notification in user_notifications:
            if notification.get('type') == 'file_assigned':
                file_id = notification.get('file_id')
                step = notification.get('step')
                file_name = files_db.get(file_id, {}).get('original_filename', 'Unknown')
                print(f"     * File: {file_name} (ID: {file_id}) in step: {step}")
    
    # Show files and their current steps
    print("\n3. Current file positions:")
    for file_id, file in files_db.items():
        current_step = file.get('current_step')
        filename = file.get('original_filename', 'Unknown')
        print(f"   - {filename} (ID: {file_id}): {current_step}")
    
    # Show user step assignments
    print("\n4. User step assignments:")
    for username, user_data in users_db.items():
        roles = user_data.get('roles', [])
        custom_steps = user_data.get('custom_steps', [])
        all_steps = set(roles + custom_steps)
        print(f"   - {username}: {list(all_steps)}")
    
    print("\n=== Test Complete ===")

if __name__ == "__main__":
    test_notification_system()
