# Personal Study Library

A lightweight personal study organizer web application built with Python Flask, SQLite, and vanilla HTML5, CSS3, and JavaScript.

## Features

- **Authentication**: Simple session-based registration, login, and logout with hashed passwords.
- **Dashboard**: Overview cards, quick statistics, live search across subjects, recently opened resources, and favorites.
- **Subject Management**: Create, rename, delete, and personalize subjects with icons and descriptions.
- **Unit Management**: Organize subjects into units with drag-and-drop or button reordering and quick navigation.
- **Resource Management**:
  - **PDFs**: Upload, in-browser preview, and download.
  - **Presentations**: PPT and PPTX upload and download.
  - **Documents**: DOC, DOCX, and TXT upload (with in-browser text viewer for TXT files).
  - **Videos**: YouTube video links (embedded playback) and external video URLs.
  - **Web Links**: Custom web URLs with custom titles.
  - **Images**: JPG, PNG, and WEBP image uploads with preview and thumbnails.
  - **AI Links**: Save ChatGPT, Gemini, and other AI chat URLs.
  - **Notes**: Create, edit, and export text notes directly within the browser.
- **Views & Filtering**: Switch between Grid View and List View, with instant type-based filters.
- **Security**: Strict user data isolation, parameter-bound SQLite queries, CSRF token protection, and filename sanitization.

## Running the Application

```cmd
python -m venv venv

venv\Scripts\activate

pip install -r requirements.txt

python app.py
```

Then open your browser at:
`http://127.0.0.1:5000`
