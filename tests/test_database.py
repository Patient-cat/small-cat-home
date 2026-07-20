"""Unit tests for database operations."""
import os
import sqlite3
import pytest
from models.database import get_db, db_connection, init_db


class TestDatabase:
    def setup_method(self):
        """Use temp file database for tests."""
        import tempfile
        import models.database as db_mod
        self._orig_path = db_mod.DB_PATH
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        db_mod.DB_PATH = self._tmp.name

    def teardown_method(self):
        import models.database as db_mod
        db_mod.DB_PATH = self._orig_path
        import os
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    def test_get_db_returns_connection(self):
        conn = get_db()
        assert conn is not None
        assert conn.execute('SELECT 1').fetchone()[0] == 1
        conn.close()

    def test_db_connection_context_manager(self):
        with db_connection() as conn:
            result = conn.execute('SELECT 1').fetchone()
            assert result[0] == 1

    def test_init_db_creates_tables(self):
        init_db()
        with db_connection() as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
        assert 'persons' in tables
        assert 'face_embeddings' in tables
        assert 'events' in tables
        assert 'users' in tables
        assert 'settings' in tables

    def test_init_db_bootstraps_admin(self):
        init_db()
        with db_connection() as conn:
            admin = conn.execute(
                "SELECT username, role FROM users WHERE role='admin'"
            ).fetchone()
        assert admin is not None
        assert admin['username'] == 'admin'

    def test_events_table_schema(self):
        init_db()
        with db_connection() as conn:
            cols = [c[1] for c in conn.execute('PRAGMA table_info(events)').fetchall()]
        assert 'elder_name' in cols
        assert 'confidence' in cols
        assert 'screenshot' in cols
        assert 'report' in cols
        assert 'permanent' in cols
