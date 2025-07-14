#!/usr/bin/env python3
"""
Test script to verify the notification system is working correctly.
"""

import requests
import json

# Test configuration
BASE_URL = "http://localhost:5102"
USERNAME = "admin"
PASSWORD = "admin"

def login():
    """Login and get session cookies"""
    session = requests.Session()
    
    # Get login page first
    response = session.get(f"{BASE_URL}/login")
    if response.status_code != 200:
        print(f"Failed to get login page: {response.status_code}")
        return None
    
    # Login
    login_data = {
        'username': USERNAME,
        'password': PASSWORD
    }
    
    response = session.post(f"{BASE_URL}/login", data=login_data)
    if response.status_code == 200 and "Welcome" in response.text:
        print("✓ Login successful")
        return session
    else:
        print(f"✗ Login failed: {response.status_code}")
        return None

def test_notifications_api(session):
    """Test the notifications API endpoints"""
    print("\n--- Testing Notifications API ---")
    
    # Test getting notifications
    response = session.get(f"{BASE_URL}/api/notifications")
    if response.status_code == 200:
        data = response.json()
        print(f"✓ Get notifications successful")
        print(f"  - Unread count: {data.get('unread_count', 0)}")
        print(f"  - Total notifications: {len(data.get('notifications', []))}")
        
        # Print first few notifications
        notifications = data.get('notifications', [])
        for i, notification in enumerate(notifications[:3]):
            print(f"  - Notification {i+1}: {notification.get('title', 'No title')}")
            print(f"    Message: {notification.get('message', 'No message')}")
            print(f"    Read: {notification.get('read', False)}")
            print(f"    File ID: {notification.get('file_id', 'None')}")
        
        return notifications
    else:
        print(f"✗ Get notifications failed: {response.status_code}")
        return []

def test_mark_notification_read(session, notifications):
    """Test marking a notification as read"""
    if not notifications:
        print("No notifications to test marking as read")
        return
    
    print("\n--- Testing Mark Notification as Read ---")
    
    # Find an unread notification
    unread_notification = None
    for notification in notifications:
        if not notification.get('read', False):
            unread_notification = notification
            break
    
    if not unread_notification:
        print("No unread notifications found")
        return
    
    # Mark as read
    mark_read_data = {
        'notification_id': unread_notification['id']
    }
    
    response = session.post(f"{BASE_URL}/api/notifications/mark_read", 
                          json=mark_read_data,
                          headers={'Content-Type': 'application/json'})
    
    if response.status_code == 200:
        data = response.json()
        if data.get('success'):
            print(f"✓ Successfully marked notification as read: {unread_notification['title']}")
        else:
            print(f"✗ Failed to mark notification as read: {data}")
    else:
        print(f"✗ Mark as read failed: {response.status_code}")

def test_mark_all_read(session):
    """Test marking all notifications as read"""
    print("\n--- Testing Mark All Notifications as Read ---")
    
    response = session.post(f"{BASE_URL}/api/notifications/mark_all_read",
                          headers={'Content-Type': 'application/json'})
    
    if response.status_code == 200:
        data = response.json()
        if data.get('success'):
            print("✓ Successfully marked all notifications as read")
        else:
            print(f"✗ Failed to mark all notifications as read: {data}")
    else:
        print(f"✗ Mark all as read failed: {response.status_code}")

def main():
    """Main test function"""
    print("=== Notification System Test ===")
    
    # Login
    session = login()
    if not session:
        print("Cannot proceed without login")
        return
    
    # Test notifications API
    notifications = test_notifications_api(session)
    
    # Test marking single notification as read
    test_mark_notification_read(session, notifications)
    
    # Test marking all notifications as read
    test_mark_all_read(session)
    
    # Test notifications API again to see changes
    print("\n--- Testing Notifications API After Changes ---")
    test_notifications_api(session)
    
    print("\n=== Test Complete ===")

if __name__ == "__main__":
    main()
