"""
SafeSight — Elderly Fall Detection Microservice
Flask + SocketIO entry point with Blueprint registration.
"""
import os
import sys
import time
import queue
import signal
import logging
import logging.handlers
import threading
from concurrent.futures import ThreadPoolExecutor

import config as cfg

# ============================================================
# Logging
# ============================================================
os.makedirs(cfg.LOG_DIR, exist_ok=True)
log_file = os.path.join(cfg.LOG_DIR, 'safesight.log')
logging.basicConfig(
    level=cfg.LOG_LEVEL,
    format=cfg.LOG_FORMAT,
    datefmt=cfg.LOG_DATE_FORMAT,
    handlers=[
        logging.StreamHandler(),
        logging.handlers.TimedRotatingFileHandler(
            log_file, when='midnight', interval=1, backupCount=30, encoding='utf-8'
        ),
    ],
)
log = logging.getLogger('safesight')

# ============================================================
# Flask + SocketIO
# ============================================================
import secrets
from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

os.makedirs(cfg.STATIC_FALLS_DIR, exist_ok=True)
os.makedirs(cfg.STATIC_UPLOADS_DIR, exist_ok=True)

# ============================================================
# Register Blueprints
# ============================================================
from api.auth import auth_bp
from api.cameras import cameras_bp
from api.events import events_bp
from api.faces import faces_bp
from api.hazards import hazards_bp
from api.pages import pages_bp
from api.system import system_bp
from api.streaming import streaming_bp
from api.test_mode import test_bp

app.register_blueprint(auth_bp)
app.register_blueprint(cameras_bp)
app.register_blueprint(events_bp)
app.register_blueprint(faces_bp)
app.register_blueprint(hazards_bp)
app.register_blueprint(pages_bp)
app.register_blueprint(system_bp)
app.register_blueprint(streaming_bp)
app.register_blueprint(test_bp)

# Template context: inject user info for nav
@app.context_processor
def _inject_user():
    from flask import session as _s
    return dict(_user={'username': _s.get('username'), 'role': _s.get('role')}
                if _s.get('logged_in') else {})

# ============================================================
# Database
# ============================================================
from models.database import init_db
init_db()

# ============================================================
# Models
# ============================================================
import core.state as state

try:
    import torch
    state.DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'
except ImportError:
    state.DEVICE = 'cpu'

from ultralytics import YOLO

state.model = YOLO('yolov8m-pose.pt')
log.info('Pose model loaded, device: %s', state.DEVICE)

_fd_path = os.path.join(os.path.dirname(__file__), 'fall_detect_retrained.pt')
if os.path.isfile(_fd_path):
    state.model_fd = YOLO(_fd_path)
    log.info('Fall detect model: loaded')

try:
    state.model_ground = YOLO('yolov8n.pt')
    log.info('Ground hazard detection model loaded')
except Exception as e:
    log.warning('Ground hazard model not loaded: %s', e)

# InsightFace
try:
    from insightface.app import FaceAnalysis
    state.face_app = FaceAnalysis(name='buffalo_sc', providers=['CPUExecutionProvider'])
    state.face_app.prepare(ctx_id=cfg.INSIGHTFACE_CTX_ID, det_size=(640, 640))
    log.info('InsightFace buffalo_sc loaded (CPU)')
except Exception as e:
    log.warning('InsightFace not loaded: %s', e)
    state.face_app = None

# ============================================================
# Camera config
# ============================================================
from core.cam_config import _load_camera_config, _auto_scan_usb

state.CAMERAS, state.camera_names = _load_camera_config()
log.info('Loaded %d camera(s) from config', len(state.CAMERAS))

# ============================================================
# AI toggle
# ============================================================
from models.database import db_connection
with db_connection() as conn:
    row = conn.execute("SELECT value FROM settings WHERE key='setup_complete'").fetchone()
state.ai_toggle = True

# ============================================================
# Event cleanup
# ============================================================
def cleanup_loop():
    """Periodic cleanup of old events and screenshots."""
    import sqlite3
    while state.alive.is_set():
        try:
            with db_connection() as conn:
                conn.execute(
                    'DELETE FROM events WHERE permanent = 0 AND created_at < datetime("now", ?)',
                    (f'-{cfg.EVENT_RETENTION_DAYS} days',)
                )
                conn.commit()
            log.info('Cleanup: removed events older than %d days', cfg.EVENT_RETENTION_DAYS)
        except Exception as e:
            log.warning('Cleanup error: %s', e)
        time.sleep(cfg.CLEANUP_INTERVAL_SECONDS)

# ============================================================
# SocketIO events
# ============================================================
@socketio.on('connect')
def on_connect():
    log.debug('Client connected')

@socketio.on('disconnect')
def on_disconnect():
    log.debug('Client disconnected')

# ============================================================
# Main
# ============================================================
def initialize_camera_pipelines():
    """Set up per-camera state, start detection/cleanup/USB-scan threads."""
    import queue as q
    log.info("=" * 40)
    log.info("SafeSight v2.0 starting on http://localhost:%d", cfg.SERVER_PORT)
    for c in state.CAMERAS:
        name = state.camera_names.get(str(c['id']), f'摄像头{c["id"]+1}')
        log.info("  /video_feed/%d → %s (source=%s)", c['id'], name, c['source'])

    from core.video_buffer import VideoBuffer
    for c in state.CAMERAS:
        cid = c['id']
        state.frame_queues[cid] = q.Queue(maxsize=2)
        state.latest_detections[cid] = {
            'kp_xy': None, 'kp_conf': None, 'is_fall': False,
            'tracks': {}, 'fd_boxes': [],
        }
        state.detection_locks[cid] = threading.Lock()
        state.current_fps_list[cid] = 0
        state.person_count_list[cid] = 0
        state.last_p_fall_list[cid] = 0
        state.video_buffers[cid] = VideoBuffer(max_seconds=30, fps=15)

    from core.worker import detection_worker, ground_hazard_worker
    for c in state.CAMERAS:
        t = threading.Thread(target=detection_worker, args=(c['id'],), daemon=True,
                             name=f'detection-{c["id"]}')
        t.start()
        t_ground = threading.Thread(target=ground_hazard_worker, args=(c['id'],),
                                    daemon=True, name=f'ground-{c["id"]}')
        t_ground.start()

    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True, name='cleanup')
    cleanup_thread.start()
    usb_scan_thread = threading.Thread(target=_auto_scan_usb, daemon=True, name='usb-scan')
    usb_scan_thread.start()
    log.info("%d camera(s) + cleanup threads started", len(state.CAMERAS))


def _setup_signal_handlers():
    def _shutdown(sig, frame):
        log.info('Shutting down...')
        state.alive.clear()
        time.sleep(0.3)
        for cid, cap in list(state.camera_caps.items()):
            try:
                cap.release()
            except Exception:
                pass
        state.camera_caps.clear()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        os._exit(0)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)


if __name__ == '__main__':
    initialize_camera_pipelines()
    _setup_signal_handlers()
    socketio.run(app, host=cfg.SERVER_HOST, port=cfg.SERVER_PORT,
                 debug=False, allow_unsafe_werkzeug=True)
