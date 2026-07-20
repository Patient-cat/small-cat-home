"""Authentication routes — login, logout, register, user management."""
import sqlite3
import logging
from flask import Blueprint, request, jsonify, session, redirect, url_for
from functools import wraps

from models.database import db_connection

log = logging.getLogger('safesight')

auth_bp = Blueprint('auth', __name__)


def login_required(f):
    """Decorator: redirect to /login if not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth.login_page'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator: return 403 if not admin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            return jsonify({'ok': False, 'error': '需要管理员权限'}), 403
        return f(*args, **kwargs)
    return decorated


@auth_bp.route('/login')
def login_page():
    """Login page."""
    if session.get('logged_in'):
        from app import _setup_complete
        if not _setup_complete():
            return redirect(url_for('auth.wizard_page'))
        return redirect(url_for('pages.hall'))
    from flask import render_template
    return render_template('login.html')


@auth_bp.route('/api/login', methods=['POST'])
def api_login():
    """Authenticate user against DB, set session."""
    from werkzeug.security import check_password_hash
    from app import _setup_complete

    data = request.get_json(force=True) or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '')
    if not username or not password:
        return jsonify({'ok': False, 'error': '请输入用户名和密码'}), 400

    with db_connection() as conn:
        row = conn.execute(
            'SELECT id, username, password_hash, role, is_active FROM users WHERE username = ?',
            (username,)
        ).fetchone()

    if not row or not check_password_hash(row['password_hash'], password):
        log.warning('Failed login attempt for "%s"', username)
        return jsonify({'ok': False, 'error': '用户名或密码错误'}), 401
    if not row['is_active']:
        return jsonify({'ok': False, 'error': '账号已被禁用，请联系管理员'}), 403

    session['logged_in'] = True
    session['username'] = row['username']
    session['role'] = row['role']
    session['user_id'] = row['id']
    log.info('User "%s" (%s) logged in', username, row['role'])
    redirect_to = '/wizard' if not _setup_complete() else '/hall'
    return jsonify({'ok': True, 'redirect': redirect_to})


@auth_bp.route('/api/logout', methods=['POST'])
def api_logout():
    """Clear session."""
    session.clear()
    return jsonify({'ok': True})


@auth_bp.route('/register_user')
def register_user_page():
    """User self-registration page (no auth required)."""
    if session.get('logged_in'):
        return redirect(url_for('pages.hall'))
    from flask import render_template
    return render_template('register_user.html')


@auth_bp.route('/api/register', methods=['POST'])
def api_register():
    """Create a new user account (role=user)."""
    from werkzeug.security import generate_password_hash as _gh

    data = request.get_json(force=True) or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '')
    if not username or not password:
        return jsonify({'ok': False, 'error': '用户名和密码不能为空'}), 400
    if len(username) < 2 or len(username) > 32:
        return jsonify({'ok': False, 'error': '用户名长度需在2-32个字符之间'}), 400
    if len(password) < 6:
        return jsonify({'ok': False, 'error': '密码长度不能少于6位'}), 400

    with db_connection() as conn:
        try:
            conn.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                         (username, _gh(password)))
            conn.commit()
            log.info('New user registered: %s', username)
            return jsonify({'ok': True, 'message': '注册成功，请登录'})
        except sqlite3.IntegrityError:
            return jsonify({'ok': False, 'error': '用户名已存在'}), 409


@auth_bp.route('/users')
@login_required
@admin_required
def users_page():
    """Admin user management page."""
    from flask import render_template
    return render_template('users.html')


@auth_bp.route('/api/users')
@login_required
@admin_required
def api_users():
    """List all users."""
    with db_connection() as conn:
        rows = conn.execute(
            'SELECT id, username, role, is_active, created_at FROM users ORDER BY id'
        ).fetchall()
    users = [{'id': r['id'], 'username': r['username'], 'role': r['role'],
              'is_active': bool(r['is_active']), 'created_at': r['created_at']} for r in rows]
    return jsonify({'ok': True, 'users': users, 'current_user_id': session.get('user_id')})


@auth_bp.route('/api/users/<int:uid>', methods=['DELETE'])
@login_required
@admin_required
def api_delete_user(uid):
    """Delete a user. Admin cannot delete themselves."""
    if uid == session.get('user_id'):
        return jsonify({'ok': False, 'error': '不能删除自己'}), 400
    with db_connection() as conn:
        row = conn.execute('SELECT role FROM users WHERE id = ?', (uid,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': '用户不存在'}), 404
        conn.execute('DELETE FROM users WHERE id = ?', (uid,))
        conn.commit()
    log.info('Admin "%s" deleted user id=%d', session.get('username'), uid)
    return jsonify({'ok': True, 'message': '用户已删除'})


@auth_bp.route('/api/users/<int:uid>/toggle', methods=['POST'])
@login_required
@admin_required
def api_toggle_user(uid):
    """Toggle user active/inactive. Admin cannot disable themselves."""
    if uid == session.get('user_id'):
        return jsonify({'ok': False, 'error': '不能禁用自己'}), 400
    with db_connection() as conn:
        row = conn.execute('SELECT id, is_active FROM users WHERE id = ?', (uid,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': '用户不存在'}), 404
        new_val = 0 if row['is_active'] else 1
        conn.execute('UPDATE users SET is_active = ? WHERE id = ?', (new_val, uid))
        conn.commit()
    action = '禁用' if new_val == 0 else '启用'
    log.info('Admin "%s" %s user id=%d', session.get('username'), action, uid)
    return jsonify({'ok': True, 'is_active': bool(new_val), 'message': f'用户已{action}'})


# ============================================================
# Setup Wizard
# ============================================================
def _setup_complete():
    """Check if setup wizard has been completed."""
    from models.database import get_db
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key='setup_complete'").fetchone()
    conn.close()
    return row and row['value'] == 'true'


@auth_bp.route('/wizard')
@login_required
def wizard_page():
    """First-run setup wizard. Redirect to /hall if already set up."""
    if _setup_complete():
        return redirect(url_for('pages.hall'))
    from flask import render_template
    return render_template('wizard.html')


@auth_bp.route('/api/setup/admin', methods=['POST'])
@login_required
def api_setup_admin():
    """Change the default admin password during setup."""
    from werkzeug.security import generate_password_hash

    data = request.get_json(force=True) or {}
    new_pass = (data.get('password') or '').strip()
    if len(new_pass) < 6:
        return jsonify({'ok': False, 'error': '密码长度不能少于6位'}), 400
    if session.get('role') != 'admin':
        return jsonify({'ok': False, 'error': '需要管理员权限'}), 403

    with db_connection() as conn:
        admin = conn.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
        if admin:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                         (generate_password_hash(new_pass), admin['id']))
            conn.commit()
    log.info('Admin password updated via setup wizard')
    return jsonify({'ok': True})


@auth_bp.route('/api/setup/complete', methods=['POST'])
@login_required
def api_setup_complete():
    """Mark setup as complete and enable all cameras."""
    with db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                     ('setup_complete', 'true'))
        conn.commit()
    log.info('Setup marked complete')
    return jsonify({'ok': True})
