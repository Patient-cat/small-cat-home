"""System API routes — health check, AI toggle, SSE."""
import logging
from flask import Blueprint, Response, jsonify, request
from api.auth import login_required
from models.database import db_connection

log = logging.getLogger('safesight')

system_bp = Blueprint('system', __name__)


@system_bp.route('/api/health')
def api_health():
    """System health check."""
    from core.state import CAMERAS, camera_names, camera_enabled, current_fps_list, person_count_list, last_p_fall_list
    import config as cfg

    cameras = []
    for c in CAMERAS:
        cid = c['id']
        cameras.append({
            'id': int(cid),
            'name': camera_names.get(str(cid), f'摄像头{cid+1}'),
            'fps': float(current_fps_list.get(cid, 0)),
            'persons': int(person_count_list.get(cid, 0)),
            'p_fall': float(last_p_fall_list.get(cid, 0)),
        })

    return jsonify({
        'status': 'ok',
        'cameras': cameras,
        'name': 'SafeSight',
        'ai_enabled': cfg.AI_ENABLED,
        'ai_toggle': True,
    })


@system_bp.route('/api/toggle_ai', methods=['POST'])
@login_required
def api_toggle_ai():
    """Toggle AI analysis on/off."""
    from core.state import ai_toggle
    ai_toggle_val = not ai_toggle
    # Update the global state
    import core.state
    core.state.ai_toggle = ai_toggle_val
    return jsonify({'ok': True, 'ai_toggle': ai_toggle_val})


@system_bp.route('/events')
def sse_events():
    """Server-Sent Events stream for alerts."""
    from core.state import fall_queue
    import json
    import queue

    def stream():
        while True:
            try:
                data = fall_queue.get(timeout=1)
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"

    return Response(stream(), mimetype='text/event-stream')
