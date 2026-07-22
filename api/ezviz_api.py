"""EZVIZ (萤石) API routes — device list, live URL, alarm push, webhook callback."""
import logging
from flask import Blueprint, request, jsonify
from api.auth import login_required
import config as cfg

log = logging.getLogger('safesight')

ezviz_bp = Blueprint('ezviz', __name__)


@ezviz_bp.route('/api/ezviz/status')
@login_required
def api_ezviz_status():
    """Check EZVIZ integration status."""
    from core.ezviz import get_access_token
    token = get_access_token()
    return jsonify({
        'enabled': cfg.EZVIZ_ENABLED,
        'has_token': token is not None,
        'app_key': cfg.EZVIZ_APP_KEY[:8] + '...' if cfg.EZVIZ_APP_KEY else '',
    })


@ezviz_bp.route('/api/ezviz/token/refresh', methods=['POST'])
@login_required
def api_ezviz_refresh_token():
    """Force refresh the EZVIZ access token."""
    from core.ezviz import get_access_token
    token = get_access_token(force_refresh=True)
    if token:
        return jsonify({'ok': True, 'token': token[:20] + '...'})
    return jsonify({'ok': False, 'error': 'Failed to refresh token'}), 500


@ezviz_bp.route('/api/ezviz/devices')
@login_required
def api_ezviz_devices():
    """List all devices bound to the EZVIZ account."""
    from core.ezviz import get_device_list
    result = get_device_list()
    return jsonify(result)


@ezviz_bp.route('/api/ezviz/live-url')
@login_required
def api_ezviz_live_url():
    """Get live stream URL for a device."""
    from core.ezviz import get_live_url
    serial = request.args.get('serial', '')
    channel = request.args.get('channel', 1, type=int)
    if not serial:
        return jsonify({'ok': False, 'error': 'serial parameter required'}), 400
    result = get_live_url(serial, channel_no=channel)
    return jsonify(result)


@ezviz_bp.route('/api/ezviz/push-alarm', methods=['POST'])
@login_required
def api_ezviz_push_alarm():
    """Push a test alarm to EZVIZ platform."""
    from core.ezviz import push_safesight_alert
    data = request.get_json(force=True) or {}
    alert_data = {
        'type': data.get('type', 'yellow'),
        'message': data.get('message', 'SafeSight test alarm'),
    }
    result = push_safesight_alert(alert_data, device_serial=data.get('device_serial'))
    return jsonify(result)


@ezviz_bp.route('/api/ezviz/alarm/callback', methods=['POST'])
def ezviz_alarm_callback():
    """Receive alarm callbacks from EZVIZ platform (webhook).

    Configure this URL in EZVIZ console to receive device alarms.
    """
    try:
        data = request.json or {}
        device_serial = data.get('deviceSerial', '')
        alarm_type = data.get('alarmType', 0)
        alarm_time = data.get('alarmTime', '')
        channel_no = data.get('channelNo', 1)

        log.info('EZVIZ alarm received: device=%s type=%d time=%s',
                 device_serial, alarm_type, alarm_time)

        # Store in hazard_events table
        from models.database import db_connection
        with db_connection() as conn:
            conn.execute(
                'INSERT INTO hazard_events (cam_id, hazard_type, display_name, risk_level, '
                'distance_px, person_nearby, alert_level) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (channel_no, f'ezviz_{alarm_type}', f'EZVIZ报警(type={alarm_type})',
                 'medium', 0, None, 'yellow')
            )
            conn.commit()

        # Broadcast via SSE/WebSocket
        from core.worker import broadcast_alert
        broadcast_alert({
            'type': 'yellow',
            'level': 1,
            'message': f'萤石设备报警：{device_serial}',
            'hazard_type': f'ezviz_{alarm_type}',
            'cam_id': channel_no,
            'source': 'ezviz',
        })

        return jsonify({'code': 200, 'msg': 'success'})
    except Exception as e:
        log.error('EZVIZ callback error: %s', e)
        return jsonify({'code': 500, 'msg': str(e)})
