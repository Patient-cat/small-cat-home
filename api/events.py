"""Event management API routes."""
import logging
from flask import Blueprint, request, jsonify
from api.auth import login_required
from models.database import db_connection

log = logging.getLogger('safesight')

events_bp = Blueprint('events', __name__)


@events_bp.route('/api/events')
@login_required
def api_events():
    """List fall events."""
    limit = request.args.get('limit', 100, type=int)
    limit = min(limit, 500)
    with db_connection() as conn:
        rows = conn.execute(
            'SELECT id, elder_name, confidence, screenshot, report, permanent, created_at '
            'FROM events ORDER BY id DESC LIMIT ?', (limit,)
        ).fetchall()
    events = []
    for r in rows:
        events.append({
            'id': r['id'],
            'elder_name': r['elder_name'],
            'confidence': r['confidence'],
            'screenshot': r['screenshot'],
            'report': r['report'],
            'has_report': bool(r['report']),
            'report_summary': (r['report'] or '')[:100],
            'permanent': bool(r['permanent']),
            'created_at': r['created_at'],
        })
    return jsonify(events)


@events_bp.route('/api/events/<int:event_id>')
@login_required
def api_event_detail(event_id):
    """Get single event detail."""
    with db_connection() as conn:
        row = conn.execute(
            'SELECT id, elder_name, confidence, screenshot, report, created_at '
            'FROM events WHERE id = ?', (event_id,)
        ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': '事件不存在'}), 404
    return jsonify({
        'id': row['id'], 'elder_name': row['elder_name'],
        'confidence': row['confidence'], 'screenshot': row['screenshot'],
        'report': row['report'], 'created_at': row['created_at'],
    })


@events_bp.route('/api/events/<int:event_id>', methods=['DELETE'])
@login_required
def api_delete_event(event_id):
    """Delete a single event."""
    with db_connection() as conn:
        conn.execute('DELETE FROM events WHERE id = ?', (event_id,))
        conn.commit()
    return jsonify({'ok': True, 'message': '事件已删除'})


@events_bp.route('/api/events/delete_all', methods=['POST'])
@login_required
def api_delete_all_events():
    """Delete all non-permanent events."""
    with db_connection() as conn:
        result = conn.execute('DELETE FROM events WHERE permanent = 0')
        conn.commit()
    return jsonify({'ok': True, 'message': f'已删除 {result.rowcount} 条事件'})


@events_bp.route('/api/events/<int:event_id>/permanent', methods=['POST'])
@login_required
def api_toggle_permanent(event_id):
    """Toggle event permanent flag."""
    with db_connection() as conn:
        row = conn.execute('SELECT permanent FROM events WHERE id = ?', (event_id,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': '事件不存在'}), 404
        new_val = 0 if row['permanent'] else 1
        conn.execute('UPDATE events SET permanent = ? WHERE id = ?', (new_val, event_id))
        conn.commit()
    return jsonify({'ok': True, 'permanent': bool(new_val)})


@events_bp.route('/api/latest_report')
@login_required
def api_latest_report():
    """Get the latest AI analysis report."""
    with db_connection() as conn:
        row = conn.execute(
            'SELECT id, elder_name, confidence, report, created_at '
            'FROM events WHERE report != "" ORDER BY id DESC LIMIT 1'
        ).fetchone()
    if not row:
        return jsonify(None)
    return jsonify({
        'id': row['id'], 'elder_name': row['elder_name'],
        'confidence': row['confidence'], 'report': row['report'],
        'created_at': row['created_at'], 'pending': False,
    })
