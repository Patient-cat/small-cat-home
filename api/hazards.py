"""Hazard management API routes — custom categories, risk levels, detection config."""
import os
import logging
from flask import Blueprint, request, jsonify
from api.auth import login_required
from models.database import db_connection

log = logging.getLogger('safesight')

hazards_bp = Blueprint('hazards', __name__)


@hazards_bp.route('/api/hazards/levels')
@login_required
def api_hazard_levels():
    """Return all available risk levels with their colors."""
    import config as cfg
    return jsonify(cfg.HAZARD_RISK_LEVELS)


@hazards_bp.route('/api/hazards/classes')
@login_required
def api_hazard_classes():
    """Return the default COCO class → risk level mapping."""
    import config as cfg
    return jsonify(cfg.HAZARD_CLASS_LEVELS)


@hazards_bp.route('/api/hazards/custom')
@login_required
def api_custom_hazards_list():
    """List all custom hazard definitions."""
    with db_connection() as conn:
        rows = conn.execute(
            'SELECT id, name, category, risk_level, created_at FROM custom_hazards ORDER BY id'
        ).fetchall()
    return jsonify([{
        'id': r['id'], 'name': r['name'], 'category': r['category'],
        'risk_level': r['risk_level'], 'created_at': r['created_at'],
    } for r in rows])


@hazards_bp.route('/api/hazards/custom', methods=['POST'])
@login_required
def api_custom_hazard_add():
    """Add a custom hazard definition."""
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    category = (data.get('category') or '').strip().lower()
    risk_level = (data.get('risk_level') or 'medium').strip().lower()

    if not name:
        return jsonify({'ok': False, 'error': '名称不能为空'}), 400
    if risk_level not in ('high', 'medium', 'low', 'ignore'):
        return jsonify({'ok': False, 'error': '风险等级无效'}), 400

    with db_connection() as conn:
        conn.execute(
            'INSERT INTO custom_hazards (name, category, risk_level) VALUES (?, ?, ?)',
            (name, category, risk_level)
        )
        conn.commit()
    log.info('Custom hazard added: %s (%s) = %s', name, category, risk_level)
    return jsonify({'ok': True, 'message': f'{name} 已添加'})


@hazards_bp.route('/api/hazards/custom/<int:hid>', methods=['PUT'])
@login_required
def api_custom_hazard_update(hid):
    """Update a custom hazard's risk level or name."""
    data = request.get_json(force=True) or {}
    updates = []
    params = []
    if 'name' in data:
        updates.append('name = ?')
        params.append(data['name'].strip())
    if 'risk_level' in data:
        if data['risk_level'] not in ('high', 'medium', 'low', 'ignore'):
            return jsonify({'ok': False, 'error': '风险等级无效'}), 400
        updates.append('risk_level = ?')
        params.append(data['risk_level'])
    if not updates:
        return jsonify({'ok': False, 'error': '无更新内容'}), 400
    params.append(hid)
    with db_connection() as conn:
        conn.execute(f'UPDATE custom_hazards SET {", ".join(updates)} WHERE id = ?', params)
        conn.commit()
    return jsonify({'ok': True, 'message': '已更新'})


@hazards_bp.route('/api/hazards/custom/<int:hid>', methods=['DELETE'])
@login_required
def api_custom_hazard_delete(hid):
    """Delete a custom hazard definition."""
    with db_connection() as conn:
        conn.execute('DELETE FROM custom_hazards WHERE id = ?', (hid,))
        conn.commit()
    return jsonify({'ok': True, 'message': '已删除'})


@hazards_bp.route('/api/hazards/class-level', methods=['POST'])
@login_required
def api_update_class_level():
    """Update risk level for a COCO class (stored in DB as a custom_hazard with empty name)."""
    data = request.get_json(force=True) or {}
    category = (data.get('category') or '').strip().lower()
    risk_level = (data.get('risk_level') or '').strip().lower()
    if not category:
        return jsonify({'ok': False, 'error': '类别不能为空'}), 400
    if risk_level not in ('high', 'medium', 'low', 'ignore'):
        return jsonify({'ok': False, 'error': '风险等级无效'}), 400

    # Store as a custom hazard with name="__override__:<category>"
    override_name = f'__override__:{category}'
    with db_connection() as conn:
        existing = conn.execute(
            'SELECT id FROM custom_hazards WHERE name = ?', (override_name,)
        ).fetchone()
        if existing:
            conn.execute('UPDATE custom_hazards SET risk_level = ? WHERE id = ?',
                         (risk_level, existing['id']))
        else:
            conn.execute(
                'INSERT INTO custom_hazards (name, category, risk_level) VALUES (?, ?, ?)',
                (override_name, category, risk_level)
            )
        conn.commit()
    log.info('Class level updated: %s → %s', category, risk_level)
    return jsonify({'ok': True, 'message': f'{category} 等级已更新为 {risk_level}'})


@hazards_bp.route('/api/hazards/get-risk-level')
@login_required
def api_get_risk_level_for_class():
    """Get the effective risk level for a given class name (checks DB overrides first)."""
    category = (request.args.get('category') or '').strip().lower()
    if not category:
        return jsonify({'ok': False, 'error': 'category 参数缺失'}), 400

    import config as cfg

    # Check DB override first
    override_name = f'__override__:{category}'
    with db_connection() as conn:
        row = conn.execute(
            'SELECT risk_level FROM custom_hazards WHERE name = ?', (override_name,)
        ).fetchone()
    if row:
        return jsonify({'ok': True, 'category': category, 'risk_level': row['risk_level']})

    # Fall back to config default
    default = cfg.HAZARD_CLASS_LEVELS.get(category, 'medium')
    return jsonify({'ok': True, 'category': category, 'risk_level': default})


@hazards_bp.route('/api/hazards/resolve')
@login_required
def api_resolve_hazard():
    """Resolve a detected hazard name to its effective risk level and display name.

    Checks custom_hazards DB first, then config defaults.
    """
    class_name = (request.args.get('name') or '').strip().lower()
    if not class_name:
        return jsonify({'ok': False, 'error': 'name 参数缺失'}), 400

    import config as cfg

    # 1. Check custom hazard (user-defined label)
    with db_connection() as conn:
        row = conn.execute(
            'SELECT name, risk_level FROM custom_hazards WHERE category = ? AND name NOT LIKE "__override__:%" LIMIT 1',
            (class_name,)
        ).fetchone()
    if row:
        return jsonify({
            'ok': True, 'class': class_name,
            'display_name': row['name'],
            'risk_level': row['risk_level'],
        })

    # 2. Check class-level override
    override_name = f'__override__:{class_name}'
    with db_connection() as conn:
        row = conn.execute(
            'SELECT risk_level FROM custom_hazards WHERE name = ?', (override_name,)
        ).fetchone()
    if row:
        return jsonify({
            'ok': True, 'class': class_name,
            'display_name': class_name,
            'risk_level': row['risk_level'],
        })

    # 3. Default from config
    default = cfg.HAZARD_CLASS_LEVELS.get(class_name, 'medium')
    return jsonify({
        'ok': True, 'class': class_name,
        'display_name': class_name,
        'risk_level': default,
    })


# ============================================================
# Hazard Event History
# ============================================================
@hazards_bp.route('/api/hazard-events')
@login_required
def api_hazard_events():
    """List recent hazard alert events."""
    limit = request.args.get('limit', 50, type=int)
    resolved = request.args.get('resolved', None)
    with db_connection() as conn:
        if resolved is not None:
            rows = conn.execute(
                'SELECT * FROM hazard_events WHERE resolved = ? ORDER BY id DESC LIMIT ?',
                (int(resolved), min(limit, 200))
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM hazard_events ORDER BY id DESC LIMIT ?',
                (min(limit, 200),)
            ).fetchall()
    return jsonify([dict(r) for r in rows])


@hazards_bp.route('/api/hazard-events', methods=['POST'])
@login_required
def api_hazard_event_add():
    """Record a new hazard alert event (called by worker)."""
    data = request.get_json(force=True) or {}
    with db_connection() as conn:
        conn.execute(
            'INSERT INTO hazard_events (cam_id, hazard_type, display_name, risk_level, '
            'distance_px, person_nearby, alert_level) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (data.get('cam_id'), data.get('hazard_type'), data.get('display_name'),
             data.get('risk_level'), data.get('distance'), data.get('person_nearby'),
             data.get('alert_level'))
        )
        conn.commit()
    return jsonify({'ok': True})


@hazards_bp.route('/api/hazard-events/latest-clip/<int:cam_id>')
@login_required
def api_hazard_latest_clip(cam_id):
    """Return the path to the latest hazard clip for a camera."""
    import glob
    clip_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'clips')
    pattern = os.path.join(clip_dir, f'hazard_cam{cam_id}_*.mp4')
    clips = sorted(glob.glob(pattern), reverse=True)
    if clips:
        rel = os.path.relpath(clips[0], os.path.dirname(os.path.dirname(__file__)))
        return jsonify({'ok': True, 'path': '/' + rel.replace('\\', '/')})
    return jsonify({'ok': False, 'error': '暂无回放视频'})


@hazards_bp.route('/api/hazard-events/<int:eid>/resolve', methods=['POST'])
@login_required
def api_hazard_event_resolve(eid):
    """Mark a hazard event as resolved."""
    with db_connection() as conn:
        conn.execute('UPDATE hazard_events SET resolved = 1 WHERE id = ?', (eid,))
        conn.commit()
    return jsonify({'ok': True, 'message': '已标记为已处理'})


@hazards_bp.route('/api/hazard-events/<int:eid>', methods=['DELETE'])
@login_required
def api_hazard_event_delete(eid):
    """Delete a hazard event."""
    with db_connection() as conn:
        conn.execute('DELETE FROM hazard_events WHERE id = ?', (eid,))
        conn.commit()
    return jsonify({'ok': True, 'message': '已删除'})


@hazards_bp.route('/api/hazard-events/stats')
@login_required
def api_hazard_event_stats():
    """Aggregated stats for charts: by camera, by type, by hour, by day."""
    days = request.args.get('days', 30, type=int)
    with db_connection() as conn:
        by_cam = conn.execute(
            'SELECT cam_id, COUNT(*) as cnt FROM hazard_events '
            'WHERE created_at > datetime("now", ?) GROUP BY cam_id ORDER BY cnt DESC',
            (f'-{days} days',)
        ).fetchall()

        by_type = conn.execute(
            'SELECT hazard_type, COUNT(*) as cnt FROM hazard_events '
            'WHERE created_at > datetime("now", ?) GROUP BY hazard_type ORDER BY cnt DESC',
            (f'-{days} days',)
        ).fetchall()

        by_hour = conn.execute(
            'SELECT strftime("%H", created_at) as hour, COUNT(*) as cnt FROM hazard_events '
            'WHERE created_at > datetime("now", ?) GROUP BY hour ORDER BY hour',
            (f'-{days} days',)
        ).fetchall()

        by_level = conn.execute(
            'SELECT risk_level, COUNT(*) as cnt FROM hazard_events '
            'WHERE created_at > datetime("now", ?) GROUP BY risk_level',
            (f'-{days} days',)
        ).fetchall()

        daily = conn.execute(
            'SELECT date(created_at) as day, COUNT(*) as cnt FROM hazard_events '
            'WHERE created_at > datetime("now", ?) GROUP BY day ORDER BY day',
            (f'-{days} days',)
        ).fetchall()

    return jsonify({
        'by_camera': [{'cam_id': r['cam_id'], 'count': r['cnt']} for r in by_cam],
        'by_type': [{'type': r['hazard_type'], 'count': r['cnt']} for r in by_type],
        'by_hour': [{'hour': r['hour'], 'count': r['cnt']} for r in by_hour],
        'by_level': [{'level': r['risk_level'], 'count': r['cnt']} for r in by_level],
        'daily': [{'date': r['day'], 'count': r['cnt']} for r in daily],
    })
