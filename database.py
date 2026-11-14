"""SQLite helpers for the Personal Study Library.

Every query in the application goes through `query()` or `execute()`,
which always use parameterized SQL (the `?` placeholders), so user input
is never pasted directly into SQL strings.
"""
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS subjects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    icon        TEXT    NOT NULL DEFAULT 'book',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_opened TEXT
);

CREATE TABLE IF NOT EXISTS units (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS resources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id       INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    type          TEXT    NOT NULL CHECK (type IN
                    ('pdf', 'ppt', 'document', 'video', 'link', 'image', 'ai', 'note')),
    subtype       TEXT    NOT NULL DEFAULT '',
    title         TEXT    NOT NULL,
    description   TEXT    NOT NULL DEFAULT '',
    url           TEXT,
    file_path     TEXT,
    original_name TEXT,
    file_size     INTEGER,
    content       TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_opened   TEXT
);

CREATE TABLE IF NOT EXISTS favorites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (user_id, resource_id)
);

CREATE INDEX IF NOT EXISTS idx_subjects_user    ON subjects(user_id);
CREATE INDEX IF NOT EXISTS idx_units_subject    ON units(subject_id, position);
CREATE INDEX IF NOT EXISTS idx_resources_unit   ON resources(unit_id);
CREATE INDEX IF NOT EXISTS idx_resources_opened ON resources(last_opened);
CREATE INDEX IF NOT EXISTS idx_favorites_user   ON favorites(user_id);
"""


def connect(path):
    """Open a connection with foreign keys (and cascading deletes) enabled."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path):
    """Create all tables if they do not exist yet. Safe to call on every start."""
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_db():
    """Return one connection per request (stored on Flask's `g`)."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, params=(), one=False):
    """Run a SELECT and return a list of rows (or a single row / None)."""
    cur = get_db().execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, params=()):
    """Run an INSERT / UPDATE / DELETE, commit, and return the new row id."""
    db = get_db()
    cur = db.execute(sql, params)
    db.commit()
    return cur.lastrowid


def execute_many(sql, seq_of_params):
    db = get_db()
    db.executemany(sql, seq_of_params)
    db.commit()

