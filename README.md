# File Processing Pipeline

A web application for managing file processing workflows with customizable steps and user assignments.

## Features

- **User Authentication**: Secure login system with role-based access control
- **Customizable Process Steps**: Define global default steps and customize steps for each file
- **Visual Pipeline**: View file progress through a visual pipeline with status indicators
- **User Assignment**: Assign users to specific steps globally or per file
- **File Upload/Download**: Upload and download files at each step of the process
- **Status Tracking**: Track file status through the pipeline with automatic progression
- **Data Persistence**: All data is automatically saved to disk and loaded on startup

## Setup and Installation

1. Clone the repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Run the application:
   ```
   python app.py
   ```

## Usage

### Default Login

- Username: admin
- Password: admin

### Managing Process Steps

1. Log in as an administrator
2. Click "Manage Steps" in the header
3. Add, remove, rename, or reorder steps as needed

### Managing Users

1. Log in as an administrator
2. Click "Register User" in the header
3. Assign roles to users that will apply to all new files

### File Processing

1. Upload a new file from the main page
2. View the file's pipeline by clicking "View Pipeline"
3. Upload files or change status for steps you're authorized for
4. Download files from any step

### File-Specific Step Management

1. View a file's pipeline
2. Click "Manage Steps" to customize steps for this specific file
3. Add, remove, rename, or reorder steps as needed

### File-Specific User Assignment

1. View a file's pipeline
2. Click the "Users" button on any step to manage assigned users
3. Check/uncheck users to assign/unassign them from the step

## Data Storage

All data is stored in the `data` directory:
- `users.pkl`: User accounts and roles
- `steps.pkl`: Global process steps
- `step_assignments.pkl`: Global step assignments
- `files_db.pkl`: File information and history
- `backups/`: Automatic backups created when data changes

## License

This project is licensed under the MIT License - see the LICENSE file for details.
