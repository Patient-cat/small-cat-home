"""Hazard management API routes — custom categories, risk levels, detection config."""
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
