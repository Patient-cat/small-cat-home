"""Database connection management and schema initialization."""
import os
import sqlite3
import logging
from contextlib import contextmanager

import config as cfg

log = logging.getLogger('safesight')

DB_PATH = cfg.DB_PATH


def get_db():
    """Get a new database connection."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_connection():
    """Context manager for database connections."""
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize database schema and bootstrap default admin."""
    conn = get_db()
    conn.execute('CREATE TABLE IF NOT EXISTS persons (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                 'name TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    conn.execute('CREATE TABLE IF NOT EXISTS face_embeddings (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                 'person_id INTEGER NOT NULL, embedding_blob BLOB NOT NULL, '
                 'photo_path TEXT, det_score REAL DEFAULT 0.0, '
                 'created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, '
                 'FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_emb_pid ON face_embeddings(person_id)')
    conn.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                 'elder_name TEXT DEFAULT "陌生人", confidence REAL, screenshot TEXT, '
                 'report TEXT DEFAULT "", permanent INTEGER DEFAULT 0, '
                 'created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    # Add permanent column if missing (migration for existing DB)
    cols = [c[1] for c in conn.execute('PRAGMA table_info(events)').fetchall()]
    if 'permanent' not in cols:
        conn.execute('ALTER TABLE events ADD COLUMN permanent INTEGER DEFAULT 0')
    # Key-value settings table (setup wizard, etc.)
    conn.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    # Custom hazards table (user-defined risk levels and categories)
    conn.execute('CREATE TABLE IF NOT EXISTS custom_hazards ('
                 'id INTEGER PRIMARY KEY AUTOINCREMENT, '
                 'name TEXT NOT NULL, '
                 'category TEXT NOT NULL, '
                 'risk_level TEXT DEFAULT "medium", '
                 'created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    # Users table for auth
    conn.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                 'username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, '
                 'role TEXT NOT NULL DEFAULT "user", is_active INTEGER DEFAULT 1, '
                 'created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    conn.commit()
    # Bootstrap default admin if no users exist
    if conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
        from werkzeug.security import generate_password_hash
        admin_user = os.getenv('SAFESIGHT_USER', 'admin')
        admin_pass = os.getenv('SAFESIGHT_PASS', 'safesight2024')
        conn.execute('INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)',
                     (admin_user, generate_password_hash(admin_pass), 'admin'))
        conn.commit()
        log.info('Bootstrapped admin user: %s', admin_user)
    # Migrate legacy faces table if present
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='faces'")
    if cur.fetchone():
        _migrate_v2(conn)
    conn.close()
    log.info("faces.db initialized (v2)")


def _migrate_v2(conn):
    """Migrate legacy faces table to v2 schema (persons + face_embeddings)."""
    rows = conn.execute('SELECT name, embedding_blob, photo_path, created_at FROM faces').fetchall()
    for row in rows:
        cur = conn.execute('INSERT INTO persons (name, created_at) VALUES (?, ?)',
                           (row['name'], row['created_at']))
        conn.execute('INSERT INTO face_embeddings (person_id, embedding_blob, photo_path, created_at) '
                     'VALUES (?, ?, ?, ?)', (cur.lastrowid, row['embedding_blob'],
                                              row['photo_path'], row['created_at']))
    conn.execute('DROP TABLE IF EXISTS faces')
    conn.commit()
    log.info("Migrated %d records to v2", len(rows))
