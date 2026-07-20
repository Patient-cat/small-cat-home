"""
Fall Detection Microservice — Multi-level alerts, SSE + WebSocket, REST API.
Supports iframe embedding and external system integration.
"""
import logging
import logging.handlers
import cv2
import numpy as np
import math
import sqlite3
import os
import json
import time
import queue
import threading
import base64
import requests
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, Response, render_template, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO
from ultralytics import YOLO

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
# Configuration — imported from config.py
# ============================================================
YELLOW_THRESHOLD = cfg.YELLOW_THRESHOLD
RED_THRESHOLD = cfg.RED_THRESHOLD
FALL_CONSECUTIVE_FRAMES = cfg.FALL_CONSECUTIVE_FRAMES
YELLOW_HOLD_FRAMES = cfg.YELLOW_HOLD_FRAMES
FALL_COOLDOWN_SECONDS = cfg.FALL_COOLDOWN_SECONDS
DETECTION_INTERVAL = cfg.DETECTION_INTERVAL

FACE_RECOGNITION_INTERVAL = cfg.FACE_RECOGNITION_INTERVAL
FACE_SIMILARITY_THRESHOLD = cfg.FACE_SIMILARITY_THRESHOLD
FACE_DET_SCORE_THRESHOLD = cfg.FACE_DET_SCORE_THRESHOLD
FACE_NAME_HOLD_FRAMES = cfg.FACE_NAME_HOLD_FRAMES
INSIGHTFACE_CTX_ID = cfg.INSIGHTFACE_CTX_ID

YOLO_IMGSZ = cfg.YOLO_IMGSZ
JPEG_QUALITY = cfg.JPEG_QUALITY

# ============================================================
# Flask + SocketIO Setup
# ============================================================
app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

os.makedirs(cfg.STATIC_FALLS_DIR, exist_ok=True)
os.makedirs(cfg.STATIC_UPLOADS_DIR, exist_ok=True)

# ============================================================
# Models & Camera config
# ============================================================
try:
    import torch
    DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'
except ImportError:
    DEVICE = 'cpu'

model = YOLO('yolov8m-pose.pt')
log.info('Pose model loaded, device: %s', DEVICE)
model_fd = None
_fd_path = os.path.join(os.path.dirname(__file__), 'fall_detect.pt')
if os.path.isfile(_fd_path):
    model_fd = YOLO(_fd_path)
    log.info('Fall detect model: loaded')

# Ground hazard detection model (lightweight YOLOv8n)
model_ground = None
try:
    model_ground = YOLO('yolov8n.pt')
    log.info('Ground hazard detection model loaded')
except Exception as e:
    log.warning('Ground hazard model not loaded: %s', e)

# Camera config — persisted to cameras.json, editable via /cameras page
CAMERA_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'cameras.json')


def _scan_usb_cameras(max_index=5):
    """Scan for available USB/built-in cameras. Returns list of working indices."""
    available = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            ok, _ = cap.read()
            cap.release()
            if ok:
                available.append(i)
        else:
            cap.release()
    return available


def _load_camera_config():
    """Load camera list + names from JSON. Deduplicates and validates."""
    cameras = []
    names = {}
    if os.path.isfile(CAMERA_CONFIG_FILE):
        try:
            with open(CAMERA_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                cameras = data.get('cameras', [])
                names = data.get('names', {})
        except Exception:
            pass

    # Deduplicate: remove cameras with same source, keep first one with a name
    seen_sources = {}
    clean_cameras = []
    removed_ids = set()
    for c in cameras:
        src = c['source']
        src_key = str(src) if isinstance(src, int) else src
        if src_key in seen_sources:
            removed_ids.add(str(c['id']))
            continue
        seen_sources[src_key] = c['id']
        clean_cameras.append(c)

    # Clean up orphan names
    valid_ids = {str(c['id']) for c in clean_cameras}
    names = {k: v for k, v in names.items() if k in valid_ids}

    if len(clean_cameras) < len(cameras):
        diff = len(cameras) - len(clean_cameras)
        log.info('Removed %d duplicate camera(s)', diff)
        cameras = clean_cameras
        _save_camera_config(cameras, names)

    return cameras, names


def _auto_scan_usb():
    """Background: scan USB cameras and add new ones. Runs after server starts."""
    import time as _time
    _time.sleep(3)  # wait for server to be ready
    usb_indices = _scan_usb_cameras()
    existing_sources = {c['source'] for c in CAMERAS if isinstance(c['source'], int)}
    new_id = max([c.get('id', -1) for c in CAMERAS] + [-1]) + 1
    added = 0
    for idx in usb_indices:
        if idx not in existing_sources:
            name = f'USB摄像头-{idx}'
            with config_lock:
                CAMERAS.append({'id': new_id, 'source': idx, 'name': name})
                camera_names[str(new_id)] = name
            _init_camera_pipeline(new_id)
            log.info('USB camera found: %s (index %d)', name, idx)
            new_id += 1
            added += 1
    if added:
        _save_camera_config(CAMERAS, camera_names)
        log.info('Auto-added %d USB camera(s)', added)


def _save_camera_config(cameras, names):
    with open(CAMERA_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump({'cameras': cameras, 'names': names}, f, ensure_ascii=False, indent=2)


CAMERAS, camera_names = _load_camera_config()

# Each camera gets its own pipeline
pipelines = {}
frame_queues = {}
latest_detections = {}
detection_locks = {}
current_fps_list = {}
person_count_list = {}
last_p_fall_list = {}
camera_enabled = {}  # id -> bool, whether camera is actively streaming
camera_caps = {}     # id -> cv2.VideoCapture, for graceful shutdown
test_video_path = None
test_video_lock = threading.Lock()
test_saved_cam_states = {}  # save camera states before test, restore after
test_paused = False
test_last_frame = None  # hold last frame when paused or EOF
test_eof = False

face_app = None
try:
    import insightface
    face_app = insightface.app.FaceAnalysis(name='buffalo_sc', providers=['CPUExecutionProvider'])
    face_app.prepare(ctx_id=INSIGHTFACE_CTX_ID)
    log.info("InsightFace buffalo_sc loaded (CPU)")
except Exception as e:
    log.warning("InsightFace init failed: %s", e)

# ============================================================
# Database
# ============================================================
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'faces.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_connection():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def _migrate_v2(conn):
    rows = conn.execute('SELECT name, embedding_blob, photo_path, created_at FROM faces').fetchall()
    for row in rows:
        cur = conn.execute('INSERT INTO persons (name, created_at) VALUES (?, ?)',
                           (row['name'], row['created_at']))
        conn.execute('INSERT INTO face_embeddings (person_id, embedding_blob, photo_path, created_at) '
                     'VALUES (?, ?, ?, ?)',
                     (cur.lastrowid, row['embedding_blob'], row['photo_path'], row['created_at']))
    conn.execute('DROP TABLE IF EXISTS faces')
    conn.commit()
    log.info("Migrated %d records to v2", len(rows))


def init_db():
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
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='faces'")
    if cur.fetchone():
        _migrate_v2(conn)
    conn.close()
    log.info("faces.db initialized (v2)")


init_db()
ai_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ai')
ai_toggle = True  # AI analysis on by default

# ---- Event cleanup: 7-day auto-delete, permanent events preserved ----
def cleanup_old_events():
    """Delete events older than 7 days that are not marked permanent."""
    try:
        with db_connection() as conn:
            rows = conn.execute(
                "SELECT id, screenshot FROM events WHERE permanent = 0 AND "
                "datetime(created_at) < datetime('now', '-7 days')"
            ).fetchall()
            for r in rows:
                if r['screenshot']:
                    fp = os.path.join(os.path.dirname(__file__), r['screenshot'].lstrip('/'))
                    if os.path.isfile(fp):
                        os.remove(fp)
            conn.execute(
                "DELETE FROM events WHERE permanent = 0 AND "
                "datetime(created_at) < datetime('now', '-7 days')"
            )
            conn.commit()
        if len(rows) > 0:
            log.info("Removed %d old event(s)", len(rows))
    except Exception as e:
        log.error("Cleanup error: %s", e)


def cleanup_old_screenshots():
    """Delete fall screenshots older than 30 days (DB records already cleaned)."""
    try:
        cutoff = datetime.now() - timedelta(days=cfg.FALL_SCREENSHOT_RETENTION_DAYS)
        falls_dir = cfg.STATIC_FALLS_DIR
        if not os.path.isdir(falls_dir):
            return
        removed = 0
        for fname in os.listdir(falls_dir):
            fp = os.path.join(falls_dir, fname)
            if not os.path.isfile(fp) or not fname.endswith('.jpg'):
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(fp))
            if mtime < cutoff:
                os.remove(fp)
                removed += 1
        if removed > 0:
            log.info('Cleaned %d old screenshot(s)', removed)
    except Exception as e:
        log.error('Screenshot cleanup error: %s', e)


def cleanup_loop():
    """Periodic cleanup every 6 hours."""
    cleanup_old_events()
    cleanup_old_screenshots()
    while alive.is_set():
        alive.wait(cfg.CLEANUP_INTERVAL_SECONDS)
        cleanup_old_events()
        cleanup_old_screenshots()

# ============================================================
# Global State
# ============================================================
fall_queue = queue.Queue(maxsize=10)
last_fall_time = 0
recognized_name = None
state_lock = threading.Lock()
config_lock = threading.Lock()
alive = threading.Event()
alive.set()

_auto_learn_history = {}
auto_learn_lock = threading.Lock()

# ============================================================
# Skeleton Drawing Data
# ============================================================
SKELETON_EDGES = [
    (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 6), (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
]
KP_COLORS = [
    (0, 255, 0), (0, 255, 0), (0, 255, 0), (0, 255, 0), (0, 255, 0),
    (255, 255, 0), (255, 255, 0), (0, 255, 255), (0, 255, 255),
    (0, 255, 255), (0, 255, 255), (255, 0, 255), (255, 0, 255),
    (255, 128, 0), (255, 128, 0), (255, 128, 0), (255, 128, 0),
]


# ============================================================
# Sigmoid helper
# ============================================================
def _sigmoid(x):
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


# ============================================================
# Face recognition helpers
# ============================================================
def extract_face_embedding(img_bgr):
    if face_app is None:
        return None, 0.0
    faces = face_app.get(img_bgr)
    if len(faces) == 0:
        return None, 0.0
    best = max(faces, key=lambda f: f.det_score)
    if best.det_score < FACE_DET_SCORE_THRESHOLD:
        return None, float(best.det_score)
    return best.embedding, float(best.det_score)


def _auto_learn(person_id, embedding, det_score, frame_no):
    with auto_learn_lock:
        last = _auto_learn_history.get(person_id, 0)
        if frame_no - last < 90:
            return
        _auto_learn_history[person_id] = frame_no
    with db_connection() as conn:
        conn.execute('INSERT INTO face_embeddings (person_id, embedding_blob, det_score) VALUES (?, ?, ?)',
                     (person_id, embedding.tobytes(), det_score))
        conn.commit()
    log.info("AutoLearn: new embedding for person_id=%d (det=%.2f)", person_id, det_score)


def recognize_face(frame_bgr, frame_no=0):
    if face_app is None:
        return None, 0.0
    lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    processed = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)

    faces = face_app.get(processed)
    if len(faces) == 0:
        return None, 0.0
    best = max(faces, key=lambda f: f.det_score)
    if best.det_score < FACE_DET_SCORE_THRESHOLD:
        return None, 0.0

    embedding = best.embedding
    emb_norm = np.linalg.norm(embedding)
    if emb_norm < 1e-10:
        return None, 0.0

    with db_connection() as conn:
        rows = conn.execute('''SELECT p.id AS person_id, p.name, e.embedding_blob
            FROM persons p JOIN face_embeddings e ON e.person_id = p.id''').fetchall()
    if len(rows) == 0:
        return None, 0.0

    person_best = {}
    for row in rows:
        db_emb = np.frombuffer(row['embedding_blob'], dtype=np.float32)
        db_norm = np.linalg.norm(db_emb)
        if db_norm < 1e-10:
            continue
        sim = float(np.dot(embedding, db_emb) / (emb_norm * db_norm))
        pid = row['person_id']
        if pid not in person_best or sim > person_best[pid][1]:
            person_best[pid] = (row['name'], sim)

    if not person_best:
        return None, 0.0
    best_person = max(person_best.values(), key=lambda x: x[1])
    if best_person[1] >= FACE_SIMILARITY_THRESHOLD:
        if best_person[1] >= 0.70:
            best_pid = max(person_best, key=lambda k: person_best[k][1])
            _auto_learn(best_pid, embedding, best.det_score, frame_no)
        return best_person[0], best_person[1]
    return None, best_person[1]


# ============================================================
# YOLO helpers
# ============================================================
# ============================================================
# Multi-person tracker (simple IoU-based, per-camera state)
# ============================================================
TRACK_MAX_LOST = 30    # frames before removing a lost track
IOU_MATCH_MIN = 0.3    # minimum IoU to consider a match
tracked_persons_by_cam = {}  # cam_id -> {track_id -> {...}}
next_track_id_by_cam = {}    # cam_id -> int
tracker_lock = threading.Lock()

PERSON_COLORS = [
    (0, 255, 0), (255, 128, 0), (0, 200, 255), (255, 0, 255),
    (255, 255, 0), (0, 255, 200), (200, 100, 255), (255, 200, 0),
]


def _iou(boxA, boxB):
    """Intersection-over-Union of two xyxy boxes."""
    xA = max(boxA[0], boxB[0]); yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2]); yB = min(boxA[3], boxB[3])
    inter = max(0, xB - xA) * max(0, yB - yA)
    areaA = max(1, (boxA[2] - boxA[0]) * (boxA[3] - boxA[1]))
    areaB = max(1, (boxB[2] - boxB[0]) * (boxB[3] - boxB[1]))
    return inter / float(areaA + areaB - inter)


def _get_tracker_state(cam_key):
    """Get or initialize per-camera tracking state."""
    if cam_key not in tracked_persons_by_cam:
        tracked_persons_by_cam[cam_key] = {}
        next_track_id_by_cam[cam_key] = 0
    return tracked_persons_by_cam[cam_key], next_track_id_by_cam[cam_key]


def _match_or_create_tracks(detections, frame_no, cam_key):
    """
    detections: list of (bbox_xyxy, kp_xy, kp_conf)
    cam_key: unique key for this camera (to isolate tracking state)
    """
    tracked, nid_ref = _get_tracker_state(cam_key)
    next_id = nid_ref
    matched_tids = set()
    new_tracks = {}

    for det_bbox, kp, kp_conf in detections:
        best_tid, best_iou = None, 0
        for tid, t in tracked.items():
            if tid in matched_tids:
                continue
            iou = _iou(det_bbox, t['bbox'])
            if iou > best_iou:
                best_iou = iou; best_tid = tid
        if best_tid is not None and best_iou >= IOU_MATCH_MIN:
            tid = best_tid; matched_tids.add(tid)
            t = tracked[tid]
            t['bbox'] = det_bbox; t['last_seen'] = frame_no
            t['kp'] = kp; t['kp_conf'] = kp_conf
            new_tracks[tid] = t
        else:
            tid = next_id; next_id += 1
            color = PERSON_COLORS[tid % len(PERSON_COLORS)]
            new_tracks[tid] = {
                'bbox': det_bbox, 'last_seen': frame_no,
                'kp': kp, 'kp_conf': kp_conf,
                'hip_history': deque(maxlen=5),
                'angle_history': deque(maxlen=6),
                'fall_counter': 0, 'name': None, 'color': color,
                'fd_fall_conf': 0.0, 'ground_contact_frames': 0,
                'last_p_fall': 0.0,
            }

    next_track_id_by_cam[cam_key] = next_id

    # Remove stale tracks
    stale = [tid for tid in tracked if frame_no - tracked[tid]['last_seen'] > TRACK_MAX_LOST]
    for tid in stale:
        del tracked[tid]

    tracked.clear()
    tracked.update(new_tracks)
    return new_tracks


def all_persons(result):
    """Extract all detected persons from YOLO result. Returns list of (bbox, kp_xy, kp_conf)."""
    if result.keypoints is None or len(result.keypoints) == 0:
        return []
    kps = result.keypoints.xy.cpu().numpy()
    confs = result.keypoints.conf.cpu().numpy()
    if result.boxes is not None and len(result.boxes) > 0:
        boxes = result.boxes.xyxy.cpu().numpy()
    else:
        # Fallback: use keypoint min/max as approximate box
        boxes = np.array([[k[:, 0].min(), k[:, 1].min(), k[:, 0].max(), k[:, 1].max()] for k in kps])
    return [(tuple(boxes[i]), kps[i], confs[i]) for i in range(len(kps))]


# ============================================================
# Probabilistic Fall Detection (multi-feature fusion)
# ============================================================
def check_fall(kp_xy, kp_conf, hip_hist, angle_hist,
               fd_fall_conf=0.0, ground_contact_frames=0):
    """
    7-feature fall detection with scale normalization.
    Returns (is_fall, info_dict). When keypoints are insufficient, returns (False, None) —
    caller should decay last_p_fall to handle occlusion.
    """
    def k(idx):
        return (kp_xy[idx], kp_conf[idx]) if kp_conf[idx] > 0.5 else None

    ls = k(5); rs = k(6); lh = k(11); rh = k(12)
    nose = k(0); l_ankle = k(15); r_ankle = k(16)
    l_knee = k(13); r_knee = k(14)

    shoulders = [p[0] for p in (ls, rs) if p is not None]
    hips = [p[0] for p in (lh, rh) if p is not None]
    if len(shoulders) < 1 or len(hips) < 1:
        return False, None

    sx = np.mean([p[0] for p in shoulders]); sy = np.mean([p[1] for p in shoulders])
    hx = np.mean([p[0] for p in hips]); hy = np.mean([p[1] for p in hips])

    # Scale reference: torso length (rigid segment, stable whether standing or fallen)
    torso_len = math.dist((sx, sy), (hx, hy)) if (abs(sx - hx) + abs(sy - hy)) > 1e-6 else 60
    body_height = torso_len * 2.5 + 1

    # ---- Feature 1: torso angle (unchanged, inherently scale-invariant) ----
    dx = abs(hx - sx); dy = abs(hy - sy)
    angle = math.degrees(math.atan2(dy, dx)) if (dx + dy) > 1e-6 else 90.0
    p_angle = _sigmoid((45 - angle) / 12.0)

    # ---- Feature 2: vertical velocity (scale-normalized) ----
    hip_hist.append(hy)
    velocity = 0.0
    if len(hip_hist) >= 3:
        vel_px = (hip_hist[-1] - hip_hist[0]) / (len(hip_hist) - 1)
        velocity = vel_px / body_height
    p_vel = _sigmoid((velocity - 0.06) / 0.04)

    # ---- Feature 3: aspect ratio (unchanged) ----
    all_x = [p[0] for p in shoulders + hips]; all_y = [p[1] for p in shoulders + hips]
    bw = max(all_x) - min(all_x) + 1; bh = max(all_y) - min(all_y) + 1
    ar = bw / bh if bh > 0 else 0.5
    p_ar = _sigmoid((ar - 0.8) * 6.0)

    # ---- Feature 4: angular acceleration (FIXED: true 2nd derivative) ----
    angle_hist.append(angle)
    angle_accel = 0.0
    if len(angle_hist) >= 5:
        v_now = angle_hist[-1] - angle_hist[-2]
        v_prev = angle_hist[-3] - angle_hist[-4]
        angle_accel = abs(v_now - v_prev) / 2.0
    p_accel = _sigmoid((angle_accel - 4.0) / 3.0)

    # ---- Feature 5: head-foot Y diff (scale-normalized) ----
    p_hf = 0.5; hf_ratio = 0.5
    if nose is not None:
        ankles_feet = [p[0] for p in (l_ankle, r_ankle) if p is not None]
        if ankles_feet:
            ankle_y = np.mean([p[1] for p in ankles_feet])
            hf_ratio = abs(ankle_y - nose[0][1]) / body_height
            p_hf = _sigmoid((0.60 - hf_ratio) / 0.15)

    # ---- Feature 6: ground contact (hip proximity to lowest visible point) ----
    foot_ys = [p[1] for p in (l_ankle, r_ankle, l_knee, r_knee) if p is not None]
    ground_y = max(foot_ys) if foot_ys else hy + body_height * 0.6
    hip_to_ground = max(0.0, ground_y - hy) / body_height
    p_ground = _sigmoid((0.15 - hip_to_ground) / 0.08)

    # ---- Feature 7: model_fd confidence (secondary model opinion) ----
    p_fd = float(fd_fall_conf)

    # ---- Weighted fusion ----
    P_FALL = (
        p_angle * 0.30 +
        p_vel * 0.22 +
        p_ar * 0.18 +
        p_accel * 0.10 +
        p_hf * 0.05 +
        p_ground * 0.10 +
        p_fd * 0.05
    )
    P_FALL = _sigmoid((P_FALL - 0.50) * 6.0)

    is_fall = P_FALL >= RED_THRESHOLD
    return is_fall, {
        'p_fall': round(P_FALL, 3), 'angle': round(angle, 1),
        'velocity': round(velocity, 4), 'ar': round(ar, 2),
        'angle_accel': round(angle_accel, 1),
        'p_angle': round(p_angle, 2), 'p_vel': round(p_vel, 2),
        'p_ar': round(p_ar, 2), 'p_accel': round(p_accel, 2),
        'p_hf': round(p_hf, 2), 'p_ground': round(p_ground, 2),
        'p_fd': round(p_fd, 2),
    }


# ============================================================
# Alert broadcast (SSE + WebSocket)
# ============================================================
def broadcast_alert(event_data):
    """Push to SSE queue and WebSocket broadcast."""
    try:
        fall_queue.put_nowait(event_data)
    except queue.Full:
        pass
    try:
        socketio.emit('alert', event_data)
    except Exception:
        pass


# ============================================================
# Ground Hazard Detection
# ============================================================
def detect_ground_hazards(frame):
    """Detect ground-level objects that could cause falls."""
    if model_ground is None:
        return []

    results = model_ground(frame, conf=0.5, verbose=False)
    hazards = []

    # COCO classes: 24=backpack, 39=bottle, 41=cup, 73=book
    target_classes = [24, 39, 41, 73]

    for box in results[0].boxes:
        cls = int(box.cls[0])
        conf = float(box.conf[0])
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)

        if cls in target_classes:
            hazards.append({
                'cls': cls,
                'name': results[0].names[cls],
                'bbox': (x1, y1, x2, y2),
                'conf': conf,
                'center': ((x1+x2)//2, (y1+y2)//2)
            })
    return hazards


def check_person_nearby(hazard_center, tracks, threshold=200):
    """Check if any tracked person is near a hazard."""
    for tid, t in tracks.items():
        bx1, by1, bx2, by2 = t['bbox']
        person_center = ((bx1+bx2)//2, (by1+by2)//2)

        dist = ((hazard_center[0] - person_center[0])**2 +
                (hazard_center[1] - person_center[1])**2)**0.5

        if dist < threshold:
            return True, t.get('name', '陌生人')
    return False, None


def is_in_roi(bbox, roi_polygon):
    """Check if object is within the walking region ROI."""
    if not roi_polygon:
        return True  # No ROI configured = everything is in region

    cx, cy = (bbox[0]+bbox[2])//2, (bbox[1]+bbox[3])//2

    # Point-in-polygon test
    n = len(roi_polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = roi_polygon[i]
        xj, yj = roi_polygon[j]
        if ((yi > cy) != (yj > cy)) and (cx < (xj - xi) * (cy - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def get_camera_roi(cam_id):
    """Get walking region ROI for a camera."""
    for c in CAMERAS:
        if c['id'] == cam_id:
            return c.get('walk_roi', [])
    return []


def set_camera_roi(cam_id, roi):
    """Set walking region ROI for a camera."""
    for c in CAMERAS:
        if c['id'] == cam_id:
            c['walk_roi'] = roi
            break
    _save_camera_config(CAMERAS, camera_names)


def ground_hazard_worker(cam_id):
    """Worker thread for ground hazard detection."""
    frame_count = 0
    hazard_cooldown = {}

    while alive.is_set():
        if not camera_enabled.get(cam_id, False):
            time.sleep(1)
            continue

        try:
            frame = frame_queues[cam_id].get(timeout=1)
        except queue.Empty:
            continue

        frame_count += 1

        # Run every 30 frames (~1 second)
        if frame_count % 30 == 0:
            hazards = detect_ground_hazards(frame)

            # Get current tracked persons
            with detection_locks[cam_id]:
                tracks = latest_detections[cam_id].get('tracks', {})

            # Get ROI configuration
            roi = get_camera_roi(cam_id)

            for hazard in hazards:
                # Check if in ROI
                if not is_in_roi(hazard['bbox'], roi):
                    continue

                # Check if person is nearby
                nearby, person_name = check_person_nearby(
                    hazard['center'], tracks, threshold=200
                )

                if nearby:
                    # Check cooldown (prevent repeated alerts)
                    hazard_key = f"{cam_id}_{hazard['name']}"
                    now = time.time()
                    if hazard_key in hazard_cooldown:
                        if now - hazard_cooldown[hazard_key] < 30:
                            continue

                    # Broadcast alert
                    broadcast_alert({
                        'type': 'yellow',
                        'level': 1,
                        'message': f'地面障碍物：{hazard["name"]}，请注意避让',
                        'hazard_type': hazard['name'],
                        'person_nearby': person_name,
                        'cam_id': cam_id,
                        'bbox': list(hazard['bbox'])
                    })

                    hazard_cooldown[hazard_key] = now
                    log.warning('Ground hazard: %s near %s (cam %d)',
                               hazard['name'], person_name, cam_id)

            # Update latest_detections for drawing
            with detection_locks[cam_id]:
                latest_detections[cam_id]['ground_hazards'] = hazards

        time.sleep(0.03)


# ============================================================
# AI Analysis
# ============================================================
AI_PROMPT = """分析这张老年人跌倒图片，输出以下内容（用中文）：
1）可能原因（环境因素 / 健康因素 / 动作因素）
2）风险评估（高 / 中 / 低）
3）急救建议
4）是否需要呼叫家属（是 / 否，并说明理由）"""


def analyze_fall_image(screenshot_path, event_id):
    """Text-based AI analysis using event data (works with all LLMs including deepseek-chat)."""
    if not cfg.AI_ENABLED:
        log.info("AI skipped (no API key) for event #%d", event_id)
        return

    with db_connection() as conn:
        row = conn.execute(
            'SELECT elder_name, confidence, created_at FROM events WHERE id = ?', (event_id,)
        ).fetchone()
    if row is None:
        return

    text_prompt = (
        f"你是一个老年人跌倒监测AI助手。刚刚发生了一起跌倒事件，请基于以下信息分析：\n\n"
        f"老人姓名：{row['elder_name']}\n"
        f"跌倒概率（P_FALL）：{row['confidence']:.0%}\n"
        f"时间：{row['created_at']}\n\n"
        f"请输出：\n"
        f"1）可能原因（环境因素 / 健康因素 / 动作因素）\n"
        f"2）风险评估（高 / 中 / 低）\n"
        f"3）急救建议\n"
        f"4）是否需要呼叫家属（是 / 否，并说明理由）\n"
        f"5）预防建议"
    )

    try:
        if cfg.AI_PROVIDER == 'gemini':
            payload = {'contents': [{'parts': [{'text': text_prompt}]}]}
            headers = {'Content-Type': 'application/json'}
            endpoint = cfg.get_endpoint()
        else:
            payload = {'model': cfg.AI_MODEL, 'messages': [{'role': 'user', 'content': text_prompt}],
                       'max_tokens': 800, 'temperature': 0.3}
            headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {cfg.AI_API_KEY}'}
            endpoint = cfg.get_endpoint()

        resp = requests.post(endpoint, json=payload, headers=headers, timeout=cfg.AI_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        report = data['candidates'][0]['content']['parts'][0]['text'] if cfg.AI_PROVIDER == 'gemini' \
            else data['choices'][0]['message']['content']

        with db_connection() as conn:
            conn.execute('UPDATE events SET report = ? WHERE id = ?', (report, event_id))
            conn.commit()
        log.info('AI report saved for event #%d: %.200s', event_id, report)
    except requests.exceptions.Timeout:
        log.warning("AI timeout (%ds) for event #%d", cfg.AI_TIMEOUT, event_id)
    except Exception as e:
        log.error("AI failed for event #%d: %s", event_id, e)


# ============================================================
# Fall Event Trigger
# ============================================================
def trigger_fall_event(frame, info):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    filename = f"fall_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    filepath = os.path.join('static', 'falls', filename)
    cv2.imwrite(filepath, frame)

    p_fall = info.get('p_fall', 0.5) if info else 0.5
    with state_lock:
        fall_name = recognized_name if recognized_name else '陌生人'
    screenshot_url = f'/static/falls/{filename}'

    with db_connection() as conn:
        cur = conn.execute('INSERT INTO events (elder_name, confidence, screenshot) VALUES (?, ?, ?)',
                           (fall_name, p_fall, screenshot_url))
        event_id = cur.lastrowid
        conn.commit()

    if cfg.AI_ENABLED and ai_toggle:
        ai_executor.submit(analyze_fall_image, screenshot_url, event_id)

    event_data = {'type': 'red', 'level': 2, 'name': fall_name, 'confidence': p_fall,
                  'message': '🚨 确认摔倒！请立即处理', 'timestamp': ts,
                  'screenshot': screenshot_url, 'event_id': event_id}
    broadcast_alert(event_data)

    if info:
        log.warning('FALL: %s person=%s P_FALL=%s angle=%s vel=%s ar=%s', ts, fall_name,
              f"angle={info['angle']}° vel={info['velocity']}  ar={info['ar']}  "
              f"p=[a:{info['p_angle']} v:{info['p_vel']} r:{info['p_ar']} "
              f"aa:{info['p_accel']} hf:{info['p_hf']} g:{info.get('p_ground','?')} "
              f"fd:{info.get('p_fd','?')}]  event_id={event_id}")


# ============================================================
# Detection Worker Thread
# ============================================================
def detection_worker(cam_id):
    """Per-camera detection worker — reads from frame_queues[cam_id], updates per-camera state."""
    det_frame_count = 0
    warn_hold = 0
    fq = frame_queues[cam_id]
    dl = detection_locks[cam_id]
    ld = latest_detections[cam_id]
    global last_fall_time, recognized_name

    while alive.is_set():
        try:
            frame = fq.get(timeout=1)
        except queue.Empty:
            continue

        det_frame_count += 1

        # Fall detection model — run less often to save CPU
        # Handles both detection models (boxes) and classification models (probs)
        if model_fd is not None and det_frame_count % 15 == 0:
            fd_res = model_fd(frame, imgsz=320, conf=0.5, verbose=False, device=DEVICE)[0]
            fd_boxes = []
            fd_cls_conf = 0.0  # classification confidence for fall class

            if fd_res.boxes is not None:
                # Detection model: extract bounding boxes
                for box in fd_res.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                    cls = int(box.cls[0])
                    conf = float(box.conf[0])
                    fd_boxes.append({'bbox': (x1, y1, x2, y2), 'is_fall': cls == 0, 'conf': conf})
            elif fd_res.probs is not None:
                # Classification model: get fall probability
                probs = fd_res.probs
                fall_idx = 1 if 'fall' in (model_fd.names or {}).get(1, '').lower() else 0
                fd_cls_conf = float(probs.top1conf) if probs.top1 == fall_idx else 1.0 - float(probs.top1conf)
                # Pseudo-box: full frame, so IoU matching still works
                h, w = frame.shape[:2]
                if fd_cls_conf > 0.3:
                    fd_boxes.append({'bbox': (0, 0, w, h), 'is_fall': True, 'conf': fd_cls_conf})

            if fd_boxes or not ld.get('fd_boxes'):
                ld['fd_boxes'] = fd_boxes
            ld['_fd_boxes_cache'] = fd_boxes
            ld['_fd_cls_conf'] = fd_cls_conf

        # YOLO pose detection
        if det_frame_count % DETECTION_INTERVAL == 0:
            results = model(frame, imgsz=YOLO_IMGSZ, conf=0.5, verbose=False, device=DEVICE)
            result = results[0]
            detections = all_persons(result)

            with tracker_lock:
                tracks = _match_or_create_tracks(detections, det_frame_count, cam_id)
                person_count_list[cam_id] = len(tracks)

            # Per-person face recognition
            if face_app is not None and det_frame_count % FACE_RECOGNITION_INTERVAL == 0:
                for tid, t in tracks.items():
                    if t.get('name') is None:
                        bx, by = max(0, int(t['bbox'][0])-20), max(0, int(t['bbox'][1])-40)
                        bw2 = min(frame.shape[1], int(t['bbox'][2])+20)
                        bh2 = min(frame.shape[0], int(t['bbox'][3])+10)
                        face_crop = frame[by:bh2, bx:bw2]
                        if face_crop.size > 0:
                            name, _ = recognize_face(face_crop, det_frame_count)
                            if name is not None:
                                t['name'] = name
                        break

            # Sync recognized_name from highest-confidence named track
            named_tracks = [(t.get('name'), t.get('last_p_fall', 0))
                            for t in tracks.values() if t.get('name')]
            with state_lock:
                if named_tracks:
                    recognized_name = max(named_tracks, key=lambda x: x[1])[0]

            # Process each tracked person
            max_p_fall = 0.0; any_is_fall = False
            fd_cache = ld.get('_fd_boxes_cache', [])
            fd_cls = ld.get('_fd_cls_conf', 0.0)  # classification model global conf
            for tid, t in tracks.items():
                kp, kp_conf = t['kp'], t['kp_conf']

                # Match model_fd boxes to this track via IoU
                track_fd_conf = fd_cls  # start with classification model's global conf
                if fd_cache:
                    tb = t['bbox']
                    for fb in fd_cache:
                        if _iou(tb, fb['bbox']) >= IOU_MATCH_MIN and fb['is_fall']:
                            track_fd_conf = max(track_fd_conf, fb['conf'])
                t['fd_fall_conf'] = track_fd_conf

                is_fall_now, info = check_fall(
                    kp, kp_conf, t['hip_history'], t['angle_history'],
                    fd_fall_conf=track_fd_conf,
                    ground_contact_frames=t.get('ground_contact_frames', 0))

                if info is None:
                    # Keypoints lost (partial occlusion) — exponential decay of last P_FALL
                    prev = t.get('last_p_fall', 0.0)
                    p_fall_val = prev * 0.85
                    t['last_p_fall'] = p_fall_val
                    t['ground_contact_frames'] = max(0, t.get('ground_contact_frames', 0) - 1)
                else:
                    p_fall_val = info.get('p_fall', 0.0)
                    t['last_p_fall'] = p_fall_val
                    # Track ground contact persistence
                    if info.get('p_ground', 0) > 0.5:
                        t['ground_contact_frames'] = t.get('ground_contact_frames', 0) + 1
                    else:
                        t['ground_contact_frames'] = max(0, t.get('ground_contact_frames', 0) - 1)

                if p_fall_val > max_p_fall: max_p_fall = p_fall_val
                if is_fall_now: t['fall_counter'] += 1
                else: t['fall_counter'] = 0
                if t['fall_counter'] >= FALL_CONSECUTIVE_FRAMES:
                    any_is_fall = True

            last_p_fall_list[cam_id] = max_p_fall

            # Level 1: Yellow alert — possible fall
            if YELLOW_THRESHOLD <= max_p_fall < RED_THRESHOLD:
                warn_hold = YELLOW_HOLD_FRAMES
            else:
                warn_hold = max(0, warn_hold - 1)
            if warn_hold > 0 and not any_is_fall:
                broadcast_alert({'type': 'yellow', 'level': 1,
                                 'message': '⚠️ 可能摔倒', 'p_fall': max_p_fall, 'cam_id': cam_id})

            # Level 2: Red alert — confirmed fall
            now = time.time()
            for tid, t in tracks.items():
                if t['fall_counter'] >= FALL_CONSECUTIVE_FRAMES:
                    t['fall_counter'] = 0
                    if (now - last_fall_time) > FALL_COOLDOWN_SECONDS:
                        last_fall_time = now
                        pname = t.get('name') or '陌生人'
                        with state_lock:
                            saved_name = recognized_name; recognized_name = pname
                        trigger_fall_event(frame, {'p_fall': t.get('last_p_fall', 0.75),
                                                    'angle': 0, 'velocity': 0, 'ar': 0,
                                                    'angle_accel': 0, 'p_angle': 0,
                                                    'p_vel': 0, 'p_ar': 0,
                                                    'p_accel': 0, 'p_hf': 0,
                                                    'p_ground': 0, 'p_fd': 0,
                                                    'cam_id': cam_id})
                        with state_lock:
                            recognized_name = saved_name
                        t['fall_counter'] = 0

            # Publish detection for drawing
            best_t = max(tracks.values(), key=lambda t: t.get('last_p_fall', 0)) if tracks else None
            with dl:
                ld['kp_xy'] = best_t['kp'] if best_t else None
                ld['kp_conf'] = best_t['kp_conf'] if best_t else None
                ld['is_fall'] = any_is_fall
                ld['tracks'] = tracks
        else:
            time.sleep(0.002)


# ============================================================
# MJPEG Stream Generator (reads camera, feeds detection, draws overlays)
# ============================================================
def open_camera(source):
    cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(source)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def open_rtsp_camera(source, cam_id):
    """Open RTSP camera with timeout and staggered delay."""
    import time as _time
    _time.sleep(cam_id * 0.3)  # 0.3s stagger per camera
    # Set RTSP timeout via env var (5 seconds)
    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp|timeout;5000000'
    cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FPS, 60)
    # Apply timeouts (OpenCV 4.5+)
    for prop, val in [(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000),
                      (cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)]:
        try:
            cap.set(prop, val)
        except Exception:
            pass
    if not cap.isOpened():
        log.warning('RTSP camera %d failed to open: %s', cam_id, source)
        cap.release()
        return None
    return cap


def generate_frames(cam_id, show_overlay=True):
    """MJPEG stream generator for a specific camera."""
    fq = frame_queues[cam_id]
    dl = detection_locks[cam_id]
    ld = latest_detections[cam_id]
    fc_start = time.time(); fc_count = 0; fail_count = 0
    cam = None

    while alive.is_set():
        # Disabled camera — show placeholder
        if not camera_enabled.get(cam_id, False):
            if cam is not None:
                cam.release(); cam = None
                camera_caps.pop(cam_id, None)
            blank = np.zeros((360, 480, 3), dtype=np.uint8)
            cv2.putText(blank, f'{camera_names.get(str(cam_id), "Camera")} 已关闭', (40, 190),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (128, 128, 128), 2)
            _, buf = cv2.imencode('.jpg', blank, [cv2.IMWRITE_JPEG_QUALITY, 30])
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
            time.sleep(0.5)
            continue

        if cam is None:
            src = CAMERAS[cam_id]['source']
            if isinstance(src, str):
                cam = open_rtsp_camera(src, cam_id)
            else:
                cam = open_camera(src)
            camera_caps[cam_id] = cam

        if cam is not None:
            success, frame = cam.read()
        else:
            success = False; frame = None

        if not success:
            fail_count += 1
            if fail_count > 30:
                if cam is not None:
                    log.warning('Camera cam_id=%d reconnecting after %d failures', cam_id, fail_count)
                    cam.release(); cam = None
                fail_count = 0
            # Yield a "connecting" placeholder so browser doesn't hang
            cam_name = camera_names.get(str(cam_id), f'摄像头{cam_id+1}')
            blank = np.zeros((360, 480, 3), dtype=np.uint8)
            cv2.putText(blank, f'{cam_name} 连接中...', (60, 190),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (128, 160, 200), 1)
            cv2.putText(blank, f'尝试 {fail_count}/30', (100, 220),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 120, 140), 1)
            _, buf = cv2.imencode('.jpg', blank, [cv2.IMWRITE_JPEG_QUALITY, 30])
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
            time.sleep(0.3); continue
        fail_count = 0

        # Downscale 4K to 1080p for faster encoding + sharper browser rendering
        h, w = frame.shape[:2]
        if w > 1920:
            scale = 1920 / w
            frame = cv2.resize(frame, (1920, int(h * scale)))

        fc_count += 1
        now = time.time(); elapsed = now - fc_start
        if elapsed >= 1.0:
            current_fps_list[cam_id] = round(fc_count / elapsed, 1)
            fc_count = 0; fc_start = now

        # Feed detection worker
        if fq.full():
            try: fq.get_nowait()
            except queue.Empty: pass
        fq.put(frame.copy())

        # Draw all tracked persons (only when overlay enabled)
        if show_overlay:
            with dl:
                tracks = ld.get('tracks', {})

            for tid, t in tracks.items():
                kp, kp_conf = t.get('kp'), t.get('kp_conf')
                if kp is None or kp_conf is None: continue
                is_fall_person = t.get('fall_counter', 0) > 0
                color = t.get('color', (0, 255, 0))
                pname = t.get('name') or f'ID:{tid}'
                p_fall_val = t.get('last_p_fall', 0)

                for a, b in SKELETON_EDGES:
                    if kp_conf[a] > 0.5 and kp_conf[b] > 0.5:
                        c = (0, 0, 255) if is_fall_person else color
                        cv2.line(frame, (int(kp[a][0]), int(kp[a][1])),
                                 (int(kp[b][0]), int(kp[b][1])), c, 2)
                for i in range(len(kp)):
                    if kp_conf[i] > 0.5:
                        cx, cy = int(kp[i][0]), int(kp[i][1])
                        c = (0, 0, 255) if is_fall_person else color
                        cv2.circle(frame, (cx, cy), 4, c, -1)
                        cv2.circle(frame, (cx, cy), 5, (255, 255, 255), 1)

                bx, by = int(t['bbox'][0]), int(t['bbox'][1])
                bw = int(t['bbox'][2] - t['bbox'][0])
                bh = int(t['bbox'][3] - t['bbox'][1])
                cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), color, 2)
                label = f'{pname} | {p_fall_val:.2f}'
                (lw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
                lx = max(0, bx + int((bw - lw) / 2))
                ly = max(20, by - 8)
                cv2.putText(frame, label, (lx, ly), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

            # Draw fall detection boxes from model_fd
            fd_boxes = ld.get('fd_boxes', [])
            for fb in fd_boxes:
                x1, y1, x2, y2 = fb['bbox']
                fd_conf = fb['conf']
                if fb['is_fall']:
                    c = (0, 0, 255); label = 'FALL ' + str(int(fd_conf * 100)) + '%'
                else:
                    c = (0, 255, 0); label = 'SAFE ' + str(int(fd_conf * 100)) + '%'
                cv2.rectangle(frame, (x1, y1), (x2, y2), c, 2)
                cv2.putText(frame, label, (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, c, 2)

            # Draw ground hazard detections
            ground_hazards = ld.get('ground_hazards', [])
            for hazard in ground_hazards:
                x1, y1, x2, y2 = hazard['bbox']
                color = (0, 165, 255)  # Orange
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                label = f"{hazard['name']} {hazard['conf']:.2f}"
                cv2.putText(frame, label, (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            # HUD
            fps = current_fps_list.get(cam_id, 0)
            pers = person_count_list.get(cam_id, 0)
            pf = last_p_fall_list.get(cam_id, 0)
            cam_name = camera_names.get(str(cam_id), f'摄像头{cam_id+1}')
            cv2.putText(frame, f"{cam_name} | FPS: {fps} | Persons: {pers}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            p_color = (0, 255, 0) if pf < YELLOW_THRESHOLD else (
                (0, 0, 255) if pf >= RED_THRESHOLD else (0, 165, 255))
            cv2.putText(frame, f"P_FALL: {pf:.2f}", (10, 55),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, p_color, 1)

        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')


# ============================================================
# Auth
# ============================================================
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))


def login_required(f):
    """Decorator: redirect to /login if not authenticated."""
    from functools import wraps
    from flask import session, redirect, url_for
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator: return 403 if not admin."""
    from functools import wraps
    from flask import session, jsonify as _jsonify
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            return _jsonify({'ok': False, 'error': '需要管理员权限'}), 403
        return f(*args, **kwargs)
    return decorated


@app.route('/login')
def login_page():
    """Login page."""
    from flask import session, redirect, url_for
    if session.get('logged_in'):
        return redirect(url_for('hall'))
    return render_template('login.html')


@app.route('/api/login', methods=['POST'])
def api_login():
    """Authenticate user against DB, set session."""
    from flask import session
    data = request.get_json(force=True) or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '')
    if not username or not password:
        return jsonify({'ok': False, 'error': '请输入用户名和密码'}), 400
    conn = get_db()
    row = conn.execute('SELECT id, username, password_hash, role, is_active FROM users WHERE username = ?',
                       (username,)).fetchone()
    conn.close()
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
    return jsonify({'ok': True, 'redirect': '/hall'})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    """Clear session."""
    from flask import session
    session.clear()
    return jsonify({'ok': True})

# ---- Template context: inject user info for nav ----
@app.context_processor
def _inject_user():
    from flask import session as _s
    return dict(_user={'username': _s.get('username'), 'role': _s.get('role')}
                if _s.get('logged_in') else {})


# ============================================================
# User Registration & Management
# ============================================================
@app.route('/register_user')
def register_user_page():
    """User self-registration page (no auth required)."""
    from flask import session, redirect, url_for
    if session.get('logged_in'):
        return redirect(url_for('hall'))
    return render_template('register_user.html')


@app.route('/api/register', methods=['POST'])
def api_register():
    """Create a new user account (role=user)."""
    data = request.get_json(force=True) or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '')
    if not username or not password:
        return jsonify({'ok': False, 'error': '用户名和密码不能为空'}), 400
    if len(username) < 2 or len(username) > 32:
        return jsonify({'ok': False, 'error': '用户名长度需在2-32个字符之间'}), 400
    if len(password) < 6:
        return jsonify({'ok': False, 'error': '密码长度不能少于6位'}), 400
    from werkzeug.security import generate_password_hash as _gh
    conn = get_db()
    try:
        conn.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                     (username, _gh(password)))
        conn.commit()
        log.info('New user registered: %s', username)
        return jsonify({'ok': True, 'message': '注册成功，请登录'})
    except sqlite3.IntegrityError:
        return jsonify({'ok': False, 'error': '用户名已存在'}), 409
    finally:
        conn.close()


@app.route('/users')
@login_required
@admin_required
def users_page():
    """Admin user management page."""
    return render_template('users.html')


@app.route('/api/users')
@login_required
@admin_required
def api_users():
    """List all users."""
    from flask import session
    conn = get_db()
    rows = conn.execute('SELECT id, username, role, is_active, created_at FROM users ORDER BY id').fetchall()
    conn.close()
    users = [{'id': r['id'], 'username': r['username'], 'role': r['role'],
              'is_active': bool(r['is_active']), 'created_at': r['created_at']} for r in rows]
    return jsonify({'ok': True, 'users': users,
                    'current_user_id': session.get('user_id')})


@app.route('/api/users/<int:uid>', methods=['DELETE'])
@login_required
@admin_required
def api_delete_user(uid):
    """Delete a user. Admin cannot delete themselves."""
    from flask import session
    if uid == session.get('user_id'):
        return jsonify({'ok': False, 'error': '不能删除自己'}), 400
    conn = get_db()
    row = conn.execute('SELECT role FROM users WHERE id = ?', (uid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'ok': False, 'error': '用户不存在'}), 404
    conn.execute('DELETE FROM users WHERE id = ?', (uid,))
    conn.commit()
    conn.close()
    log.info('Admin "%s" deleted user id=%d', session.get('username'), uid)
    return jsonify({'ok': True, 'message': '用户已删除'})


@app.route('/api/users/<int:uid>/toggle', methods=['POST'])
@login_required
@admin_required
def api_toggle_user(uid):
    """Toggle user active/inactive. Admin cannot disable themselves."""
    from flask import session
    if uid == session.get('user_id'):
        return jsonify({'ok': False, 'error': '不能禁用自己'}), 400
    conn = get_db()
    row = conn.execute('SELECT id, is_active FROM users WHERE id = ?', (uid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'ok': False, 'error': '用户不存在'}), 404
    new_val = 0 if row['is_active'] else 1
    conn.execute('UPDATE users SET is_active = ? WHERE id = ?', (new_val, uid))
    conn.commit()
    conn.close()
    action = '禁用' if new_val == 0 else '启用'
    log.info('Admin "%s" %s user id=%d', session.get('username'), action, uid)
    return jsonify({'ok': True, 'is_active': bool(new_val), 'message': f'用户已{action}'})


# ============================================================
# Routes — Pages
# ============================================================
@app.route('/')
@login_required
def index():
    return render_template('index.html')


@app.route('/hall')
@login_required
def hall():
    return render_template('hall.html')


@app.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    if request.method == 'GET':
        return render_template('register.html')
    if face_app is None:
        return jsonify({'ok': False, 'error': 'InsightFace 未加载'}), 500
    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({'ok': False, 'error': '请输入姓名'}), 400
    photos = request.files.getlist('photo')
    photos = [f for f in photos if f and f.filename]
    if len(photos) == 0:
        return jsonify({'ok': False, 'error': '请上传至少一张照片'}), 400

    conn = get_db()
    row = conn.execute('SELECT id FROM persons WHERE name = ?', (name,)).fetchone()
    person_id = row['id'] if row else conn.execute(
        'INSERT INTO persons (name) VALUES (?)', (name,)).lastrowid
    saved = 0; errors = []
    for pf in photos:
        fn = pf.filename.lower()
        if not (fn.endswith('.jpg') or fn.endswith('.jpeg') or fn.endswith('.png')):
            errors.append(f'{pf.filename}: 格式不支持'); continue
        try:
            fb = pf.read(); nparr = np.frombuffer(fb, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None: errors.append(f'{pf.filename}: 无法解码'); continue
            emb, score = extract_face_embedding(img)
            if emb is None: errors.append(f'{pf.filename}: 未检测到人脸 (det={score:.2f})'); continue
            sn = "".join(c for c in name if c.isalnum() or c in ('_', '-', '一-鿿'))
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            pfn = f"{sn}_{ts}_{saved}.jpg"; pp = os.path.join('static', 'uploads', pfn)
            cv2.imwrite(pp, img)
            conn.execute('INSERT INTO face_embeddings (person_id, embedding_blob, photo_path, det_score) '
                         'VALUES (?, ?, ?, ?)', (person_id, emb.tobytes(), f'/static/uploads/{pfn}', score))
            saved += 1
        except Exception as e:
            errors.append(f'{pf.filename}: {str(e)}')
    try:
        conn.commit()
    finally:
        conn.close()
    if saved == 0:
        return jsonify({'ok': False, 'error': f'全部失败: {"; ".join(errors[-3:])}'}), 400
    return jsonify({'ok': True, 'message': f'{name} 注册成功！已保存 {saved} 个面部嵌入',
                    'person_id': person_id, 'saved': saved, 'errors': errors[:3]})


@app.route('/manage')
@login_required
def manage():
    return render_template('manage.html')


@app.route('/history')
@login_required
def history():
    return render_template('history.html')


# ============================================================
# Routes — Streaming & WebSocket
# ============================================================
@app.route('/video_feed')
@login_required
def video_feed():
    return Response(generate_frames(0, show_overlay=False), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/video_feed/<int:cam_id>')
@login_required
def video_feed_cam(cam_id):
    if cam_id < 0 or cam_id >= len(CAMERAS):
        return 'Camera not found', 404
    return Response(generate_frames(cam_id, show_overlay=False), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/video_feed/<int:cam_id>/debug')
@login_required
def video_feed_cam_debug(cam_id):
    if cam_id < 0 or cam_id >= len(CAMERAS):
        return 'Camera not found', 404
    return Response(generate_frames(cam_id, show_overlay=True), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/capture_frame')
@login_required
def capture_frame():
    """Capture a frame from a specific camera's queue. ?cam_id=N (default 0)."""
    cam_id = request.args.get('cam_id', 0, type=int)
    fq = frame_queues.get(cam_id)
    if fq is None:
        return Response(b'', status=503)
    try:
        frame = fq.get(timeout=3)
    except queue.Empty:
        return Response(b'', status=503)
    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return Response(buf.tobytes(), mimetype='image/jpeg')


@app.route('/api/camera/<int:cam_id>/toggle', methods=['POST'])
@login_required
def api_toggle_camera(cam_id):
    camera_enabled[cam_id] = not camera_enabled.get(cam_id, False)
    return jsonify({'ok': True, 'enabled': camera_enabled[cam_id]})


@app.route('/api/cameras/disable-all', methods=['POST'])
@login_required
def api_disable_all_cameras():
    """Disable all cameras — called when user leaves monitoring page."""
    count = 0
    for c in CAMERAS:
        cid = c['id']
        if camera_enabled.get(cid, False):
            camera_enabled[cid] = False
            count += 1
    if count > 0:
        log.info('Auto-disabled %d camera(s) on page leave', count)
    return jsonify({'ok': True, 'disabled': count})


@app.route('/api/camera/<int:cam_id>/rename', methods=['POST'])
@login_required
def api_rename_camera(cam_id):
    data = request.get_json(force=True) or {}
    new_name = (data.get('name') or '').strip()
    if not new_name:
        return jsonify({'ok': False, 'error': '名称不能为空'}), 400
    with config_lock:
        camera_names[str(cam_id)] = new_name
    _save_camera_config(CAMERAS, camera_names)
    return jsonify({'ok': True, 'name': new_name})


@app.route('/cameras')
@login_required
def cameras_page():
    return render_template('cameras.html')


def _init_camera_pipeline(cam_id):
    """Initialize per-camera processing infrastructure for a new camera."""
    if cam_id not in frame_queues:
        frame_queues[cam_id] = queue.Queue(maxsize=2)
    if cam_id not in latest_detections:
        latest_detections[cam_id] = {'kp_xy': None, 'kp_conf': None, 'is_fall': False, 'tracks': {}, 'fd_boxes': []}
    if cam_id not in detection_locks:
        detection_locks[cam_id] = threading.Lock()
    current_fps_list[cam_id] = 0
    person_count_list[cam_id] = 0
    last_p_fall_list[cam_id] = 0
    camera_enabled[cam_id] = False

    # Start detection worker thread
    t = threading.Thread(target=detection_worker, args=(cam_id,), daemon=True,
                         name=f'detection-{cam_id}')
    t.start()

    # Start ground hazard detection thread
    t_ground = threading.Thread(target=ground_hazard_worker, args=(cam_id,),
                                daemon=True, name=f'ground-{cam_id}')
    t_ground.start()


@app.route('/api/cameras/scan-usb', methods=['POST'])
@login_required
def api_cameras_scan_usb():
    """Scan for available USB/built-in cameras and auto-add them."""
    global CAMERAS, camera_names
    usb_indices = _scan_usb_cameras(max_index=5)
    existing_sources = {c['source'] for c in CAMERAS if isinstance(c['source'], int)}
    new_id = max([c.get('id', -1) for c in CAMERAS] + [-1]) + 1
    added = []
    for idx in usb_indices:
        if idx not in existing_sources:
            name = f'USB摄像头-{idx}'
            with config_lock:
                CAMERAS.append({'id': new_id, 'source': idx, 'name': name})
                camera_names[str(new_id)] = name
            _init_camera_pipeline(new_id)
            added.append({'id': new_id, 'source': idx, 'name': name})
            new_id += 1
    if added:
        _save_camera_config(CAMERAS, camera_names)
    return jsonify({'ok': True, 'added': added, 'found': len(usb_indices)})


@app.route('/api/cameras/scan', methods=['POST'])
@login_required
def api_cameras_scan():
    """Auto-discover ONVIF cameras on the local network with NVR channel enumeration."""
    results = []

    # Common Hikvision default passwords
    DEFAULT_CREDS = [
        ('admin', 'admin12345'),
        ('admin', 'admin'),
        ('admin', '12345'),
        ('admin', 'Hik12345'),
        ('admin', 'hikvision'),
        ('admin', 'password'),
    ]

    NVR_KEYWORDS = ['hikvision', 'hik', 'nvr', 'dvr', 'ds-76', 'ds-77', 'ds-96',
                    'ds-71', 'ds-72', 'ds-73', 'ds-81', 'ds-91']

    def _is_nvr(mfr, model):
        text = f'{mfr or ""} {model or ""}'.lower()
        return any(kw in text for kw in NVR_KEYWORDS)

    try:
        from onvif import ONVIFCamera

        # WS-Discovery scan
        from wsdiscovery import WSDiscovery
        wsd = WSDiscovery()
        wsd.start()
        services = wsd.searchServices(timeout=5)
        wsd.stop()

        for svc in services:
            xaddrs = svc.getXAddrs()
            if not xaddrs:
                continue
            addr = xaddrs[0]
            ip = addr.split('://')[1].split(':')[0] if '://' in addr else addr.split(':')[0]

            # Try common credentials
            found_cred = None
            for user, pwd in DEFAULT_CREDS:
                try:
                    cam = ONVIFCamera(ip, 80, user, pwd)
                    info = cam.devicemgmt.GetDeviceInformation()
                    found_cred = (user, pwd)
                    mfr = info.Manufacturer
                    model = info.Model
                    nvr = _is_nvr(mfr, model)

                    # Enumerate all ONVIF profiles (each = one channel on NVRs)
                    profiles = cam.media.GetProfiles()
                    channels = []
                    first_rtsp = None
                    for idx, profile in enumerate(profiles):
                        try:
                            uri = cam.media.GetStreamUri({
                                'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}},
                                'ProfileToken': profile.token,
                            })
                            url = uri.Uri.replace('//', f'//{user}:{pwd}@')
                            channels.append({
                                'token': profile.token,
                                'url': url,
                                'channel_hint': idx + 1,
                            })
                            if first_rtsp is None:
                                first_rtsp = url
                        except Exception:
                            continue

                    suggested = len(channels) if nvr and len(channels) > 1 else (4 if nvr else 1)

                    results.append({
                        'ip': ip, 'manufacturer': mfr, 'model': model,
                        'rtsp': first_rtsp, 'user': user, 'password': pwd,
                        'found_cred': True,
                        'is_nvr': nvr,
                        'suggested_channels': suggested,
                        'channels': channels,
                    })
                    break
                except Exception:
                    continue

            if not found_cred:
                results.append({
                    'ip': ip, 'manufacturer': 'Unknown (need password)',
                    'model': '', 'rtsp': None, 'user': 'admin',
                    'password': '', 'found_cred': False,
                    'is_nvr': False, 'suggested_channels': 1, 'channels': [],
                })

    except ImportError:
        return jsonify({'ok': False, 'error': 'ONVIF库未安装，请在终端执行: pip install onvif-zeep WSDiscovery', 'results': []}), 500
    except Exception as e:
        return jsonify({'ok': True, 'results': results, 'note': f'Partial scan: {str(e)[:200]}'})

    return jsonify({'ok': True, 'results': results, 'count': len(results)})


@app.route('/api/cameras/generate_urls', methods=['POST'])
@login_required
def api_generate_urls():
    """Generate Hikvision NVR RTSP URLs from a template."""
    import urllib.parse
    data = request.get_json(force=True) or {}
    ip = (data.get('ip') or '').strip()
    user = (data.get('user') or 'admin').strip()
    password = (data.get('password') or '').strip()
    port = data.get('port', 554)
    channels = max(1, min(64, data.get('channels', 4)))
    stream_type = data.get('stream_type', 'main')

    if not ip:
        return jsonify({'ok': False, 'error': 'ip is required'}), 400

    # URL-encode credentials to handle @, :, /, % in password
    encoded_user = urllib.parse.quote(user, safe='')
    encoded_pass = urllib.parse.quote(password, safe='')

    suffix_map = {'main': 1, 'sub': 2}
    suffixes = []
    if stream_type == 'both':
        suffixes = [1, 2]
    else:
        suffixes = [suffix_map.get(stream_type, 1)]

    urls = []
    for ch in range(1, channels + 1):
        for sfx in suffixes:
            stream_label = 'main' if sfx == 1 else 'sub'
            channel_code = ch * 100 + sfx
            url = f'rtsp://{encoded_user}:{encoded_pass}@{ip}:{port}/Streaming/Channels/{channel_code}'
            urls.append({
                'channel': ch,
                'url': url,
                'stream': stream_label,
                'channel_code': channel_code,
            })

    return jsonify({'ok': True, 'urls': urls, 'count': len(urls)})


@app.route('/api/cameras/test', methods=['POST'])
@login_required
def api_cameras_test():
    """Test whether an RTSP URL is reachable by attempting a quick connection."""
    data = request.get_json(force=True) or {}
    source = data.get('source')
    if not source:
        return jsonify({'ok': False, 'error': 'source is required'}), 400

    import time as _time
    cap = None
    try:
        t0 = _time.time()
        cap = cv2.VideoCapture(source)
        # Short timeouts — not all backends respect these, but worth setting
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
        success, frame = cap.read()
        elapsed = round((_time.time() - t0) * 1000)

        if success and frame is not None:
            h, w = frame.shape[:2]
            return jsonify({
                'ok': True, 'connected': True,
                'resolution': f'{w}x{h}',
                'latency_ms': elapsed,
            })
        else:
            return jsonify({'ok': True, 'connected': False, 'error': '无法读取视频帧'})
    except Exception as e:
        return jsonify({'ok': True, 'connected': False, 'error': str(e)[:200]})
    finally:
        if cap is not None:
            cap.release()


@app.route('/api/cameras', methods=['GET', 'POST', 'DELETE'])
@login_required
def api_cameras():
    """GET: list all. POST: add. DELETE: remove (with id param)."""
    global CAMERAS
    if request.method == 'GET':
        return jsonify([{
            'id': c['id'], 'source': c['source'],
            'name': camera_names.get(str(c['id']), f'摄像头{c["id"]+1}'),
            'enabled': camera_enabled.get(c['id'], False),
            'fps': current_fps_list.get(c['id'], 0),
        } for c in CAMERAS])

    elif request.method == 'POST':
        data = request.get_json(force=True) or {}

        # Batch add: {"cameras": [{"source": "...", "name": "..."}, ...]}
        if 'cameras' in data and isinstance(data['cameras'], list):
            added = []
            used_ids = {c['id'] for c in CAMERAS}
            for cam_data in data['cameras']:
                source = cam_data.get('source')
                if source is None:
                    continue
                new_id = 0
                while new_id in used_ids:
                    new_id += 1
                used_ids.add(new_id)
                with config_lock:
                    CAMERAS.append({'id': new_id, 'source': source})
                    if cam_data.get('name'):
                        camera_names[str(new_id)] = cam_data['name'].strip()
                _init_camera_pipeline(new_id)
                added.append({'id': new_id, 'source': source})
            _save_camera_config(CAMERAS, camera_names)
            return jsonify({'ok': True, 'message': f'已添加 {len(added)} 个摄像头', 'added': added})

        # Single add
        source = data.get('source')
        if source is None:
            return jsonify({'ok': False, 'error': 'source is required (int for USB, str for RTSP)'}), 400
        used_ids = {c['id'] for c in CAMERAS}
        new_id = 0
        while new_id in used_ids:
            new_id += 1
        with config_lock:
            CAMERAS.append({'id': new_id, 'source': source})
            if data.get('name'):
                camera_names[str(new_id)] = data['name'].strip()
        _init_camera_pipeline(new_id)
        _save_camera_config(CAMERAS, camera_names)
        return jsonify({'ok': True, 'message': f'摄像头 {new_id} 已添加', 'id': new_id})

    elif request.method == 'DELETE':
        cam_id = request.args.get('id', type=int)
        if cam_id is None:
            return jsonify({'ok': False, 'error': '?id= required'}), 400
        idx = next((i for i, c in enumerate(CAMERAS) if c['id'] == cam_id), None)
        if idx is None:
            return jsonify({'ok': False, 'error': '摄像头不存在'}), 404
        with config_lock:
            CAMERAS.pop(idx)
            camera_names.pop(str(cam_id), None)
        _save_camera_config(CAMERAS, camera_names)
        return jsonify({'ok': True, 'message': f'摄像头 {cam_id} 已删除，重启后生效'})


@app.route('/api/cameras/<int:cam_id>/roi', methods=['GET', 'POST'])
@login_required
def api_camera_roi(cam_id):
    """Get or set walking region ROI for a camera."""
    if request.method == 'GET':
        roi = get_camera_roi(cam_id)
        return jsonify({'ok': True, 'roi': roi})
    else:
        data = request.get_json()
        roi = data.get('roi', [])
        set_camera_roi(cam_id, roi)
        return jsonify({'ok': True})


@app.route('/events')
def sse_events():
    def stream():
        while True:
            try:
                data = fall_queue.get(timeout=1)
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"
    return Response(stream(), mimetype='text/event-stream')


@socketio.on('connect')
def on_connect():
    log.debug("WebSocket client connected")


@socketio.on('disconnect')
def on_disconnect():
    log.debug("WebSocket client disconnected")


# ============================================================
# Routes — REST API
# ============================================================
@app.route('/api/health')
@login_required
def api_health():
    return jsonify({
        'status': 'ok',
        'cameras': [{
            'id': c['id'],
            'name': camera_names.get(str(c['id']), '摄像头' + str(c['id'] + 1)),
            'fps': current_fps_list.get(c['id'], 0),
            'persons': person_count_list.get(c['id'], 0),
            'p_fall': last_p_fall_list.get(c['id'], 0),
        } for c in CAMERAS],
        'name': recognized_name,
        'ai_enabled': cfg.AI_ENABLED and ai_toggle,
        'ai_toggle': ai_toggle,
    })


@app.route('/api/toggle_ai', methods=['POST'])
@login_required
def api_toggle_ai():
    global ai_toggle
    ai_toggle = not ai_toggle
    return jsonify({'ok': True, 'ai_toggle': ai_toggle})


@app.route('/api/events')
@login_required
def api_events():
    limit = request.args.get('limit', 100, type=int)
    with db_connection() as conn:
        rows = conn.execute(
            'SELECT id, elder_name, confidence, screenshot, report, permanent, created_at '
            'FROM events ORDER BY id DESC LIMIT ?', (min(limit, 500),)).fetchall()
    return jsonify([{
        'id': r['id'], 'elder_name': r['elder_name'], 'confidence': r['confidence'],
        'screenshot': r['screenshot'],
        'report': r['report'] if r['report'] else '',
        'has_report': bool(r['report']),
        'report_summary': r['report'][:100] if r['report'] else '',
        'permanent': bool(r['permanent']),
        'created_at': r['created_at'],
    } for r in rows])


@app.route('/api/events/<int:event_id>', methods=['DELETE'])
@login_required
def api_delete_event(event_id):
    with db_connection() as conn:
        row = conn.execute('SELECT id, screenshot FROM events WHERE id = ?', (event_id,)).fetchone()
        if row is None:
            return jsonify({'ok': False, 'error': '事件不存在'}), 404
        if row['screenshot']:
            fp = os.path.join(os.path.dirname(__file__), row['screenshot'].lstrip('/'))
            if os.path.isfile(fp): os.remove(fp)
        conn.execute('DELETE FROM events WHERE id = ?', (event_id,))
        conn.commit()
    return jsonify({'ok': True, 'message': f'事件 #{event_id} 已删除'})


@app.route('/api/events/delete_all', methods=['POST'])
@login_required
def api_delete_all_events():
    with db_connection() as conn:
        rows = conn.execute('SELECT screenshot FROM events').fetchall()
        for r in rows:
            if r['screenshot']:
                fp = os.path.join(os.path.dirname(__file__), r['screenshot'].lstrip('/'))
                if os.path.isfile(fp): os.remove(fp)
        conn.execute('DELETE FROM events')
        conn.commit()
    return jsonify({'ok': True, 'message': '所有跌倒事件已清空'})


@app.route('/api/events/<int:event_id>/permanent', methods=['POST'])
@login_required
def api_toggle_permanent(event_id):
    with db_connection() as conn:
        row = conn.execute('SELECT id, permanent FROM events WHERE id = ?', (event_id,)).fetchone()
        if row is None:
            return jsonify({'ok': False, 'error': '事件不存在'}), 404
        new_val = 0 if row['permanent'] else 1
        conn.execute('UPDATE events SET permanent = ? WHERE id = ?', (new_val, event_id))
        conn.commit()
    return jsonify({'ok': True, 'permanent': bool(new_val),
                    'message': '已标记为永久保存' if new_val else '已取消永久保存'})


@app.route('/api/events/<int:event_id>')
@login_required
def api_event_detail(event_id):
    with db_connection() as conn:
        row = conn.execute(
            'SELECT id, elder_name, confidence, screenshot, report, created_at '
            'FROM events WHERE id = ?', (event_id,)).fetchone()
    if row is None:
        return jsonify({'ok': False, 'error': '事件不存在'}), 404
    return jsonify({
        'id': row['id'], 'elder_name': row['elder_name'], 'confidence': row['confidence'],
        'screenshot': row['screenshot'],
        'report': row['report'] if row['report'] else 'AI 分析中...',
        'created_at': row['created_at'],
    })


@app.route('/api/latest_report')
@login_required
def api_latest_report():
    with db_connection() as conn:
        row = conn.execute(
            "SELECT id, elder_name, confidence, report, created_at FROM events "
            "WHERE report != '' ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            row2 = conn.execute(
                'SELECT id, elder_name, confidence, created_at FROM events ORDER BY id DESC LIMIT 1'
            ).fetchone()
            if row2:
                return jsonify({'id': row2['id'], 'elder_name': row2['elder_name'],
                                'confidence': row2['confidence'], 'created_at': row2['created_at'],
                                'report': 'AI 分析中，请稍候...', 'pending': True})
            return jsonify(None)
    return jsonify({'id': row['id'], 'elder_name': row['elder_name'],
                    'confidence': row['confidence'], 'report': row['report'],
                    'created_at': row['created_at'], 'pending': False})


@app.route('/api/register_face', methods=['POST'])
@login_required
def api_register_face():
    """Register face via REST API: {"name":"李四","image_base64":"..."}"""
    if face_app is None:
        return jsonify({'ok': False, 'error': 'InsightFace 未加载'}), 500
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    img_b64 = (data.get('image_base64') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name 字段为空'}), 400
    if not img_b64:
        return jsonify({'ok': False, 'error': 'image_base64 字段为空'}), 400
    try:
        img_bytes = base64.b64decode(img_b64)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({'ok': False, 'error': '无法解码 base64 图片'}), 400
        emb, score = extract_face_embedding(img)
        if emb is None:
            return jsonify({'ok': False, 'error': f'未检测到人脸 (det={score:.2f})'}), 400

        conn = get_db()
        try:
            row = conn.execute('SELECT id FROM persons WHERE name = ?', (name,)).fetchone()
            person_id = row['id'] if row else conn.execute(
                'INSERT INTO persons (name) VALUES (?)', (name,)).lastrowid
            sn = "".join(c for c in name if c.isalnum() or c in ('_', '-', '一-鿿'))
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            pfn = f"{sn}_{ts}_api.jpg"; pp = os.path.join('static', 'uploads', pfn)
            cv2.imwrite(pp, img)
            conn.execute('INSERT INTO face_embeddings (person_id, embedding_blob, photo_path, det_score) '
                         'VALUES (?, ?, ?, ?)', (person_id, emb.tobytes(), f'/static/uploads/{pfn}', score))
            conn.commit()
        finally:
            conn.close()
        return jsonify({'ok': True, 'message': f'{name} 已注册', 'person_id': person_id,
                        'det_score': round(score, 3), 'photo_url': f'/static/uploads/{pfn}'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/faces')
@login_required
def api_faces():
    with db_connection() as conn:
        rows = conn.execute('''SELECT p.id, p.name, p.created_at, COUNT(e.id) AS embedding_count
            FROM persons p LEFT JOIN face_embeddings e ON e.person_id = p.id
            GROUP BY p.id ORDER BY p.id DESC''').fetchall()
        result = []
        for r in rows:
            photos = conn.execute(
                'SELECT photo_path FROM face_embeddings WHERE person_id = ? ORDER BY id', (r['id'],)
            ).fetchall()
            result.append({
                'id': r['id'], 'name': r['name'],
                'embedding_count': r['embedding_count'],
                'created_at': r['created_at'],
                'photos': [p['photo_path'] for p in photos],
            })
    return jsonify(result)


@app.route('/api/faces/<int:face_id>', methods=['DELETE'])
@login_required
def api_delete_face(face_id):
    with db_connection() as conn:
        row = conn.execute('SELECT id, name FROM persons WHERE id = ?', (face_id,)).fetchone()
        if row is None:
            return jsonify({'ok': False, 'error': '记录不存在'}), 404
        photos = conn.execute('SELECT photo_path FROM face_embeddings WHERE person_id = ?',
                              (face_id,)).fetchall()
        for p in photos:
            if p['photo_path']:
                fp = os.path.join(os.path.dirname(__file__), p['photo_path'].lstrip('/'))
                if os.path.isfile(fp): os.remove(fp)
        conn.execute('DELETE FROM persons WHERE id = ?', (face_id,))
        conn.commit()
    return jsonify({'ok': True, 'message': f"已删除 {row['name']} 及其所有面部记录"})


@app.route('/api/faces/<int:face_id>/photo', methods=['PUT'])
@login_required
def api_add_face_photo(face_id):
    """Add face photos to existing person (multi-file or multi-blob)."""
    if face_app is None:
        return jsonify({'ok': False, 'error': 'InsightFace 未加载'}), 500

    photos = request.files.getlist('photo')
    photos = [f for f in photos if f and f.filename]
    if len(photos) == 0:
        return jsonify({'ok': False, 'error': '请上传至少一张照片'}), 400

    conn = get_db()
    row = conn.execute('SELECT id, name FROM persons WHERE id = ?', (face_id,)).fetchone()
    if row is None:
        conn.close(); return jsonify({'ok': False, 'error': '人员记录不存在'}), 404

    saved = 0; errors = []
    for pf in photos:
        fn = pf.filename.lower()
        if not (fn.endswith('.jpg') or fn.endswith('.jpeg') or fn.endswith('.png')):
            errors.append(f'{pf.filename}: 格式不支持'); continue
        try:
            fb = pf.read(); nparr = np.frombuffer(fb, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None: errors.append(f'{pf.filename}: 无法解码'); continue
            emb, score = extract_face_embedding(img)
            if emb is None: errors.append(f'{pf.filename}: 未检测到人脸 (det={score:.2f})'); continue
            sn = "".join(c for c in row['name'] if c.isalnum() or c in ('_', '-', '一-鿿'))
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            pfn = f"{sn}_{ts}_{saved}.jpg"
            pp = os.path.join('static', 'uploads', pfn); cv2.imwrite(pp, img)
            conn.execute('INSERT INTO face_embeddings (person_id, embedding_blob, photo_path, det_score) '
                         'VALUES (?, ?, ?, ?)', (face_id, emb.tobytes(), f'/static/uploads/{pfn}', score))
            saved += 1
        except Exception as e:
            errors.append(f'{pf.filename}: {str(e)}')
    try:
        conn.commit()
    finally:
        conn.close()
    if saved == 0:
        return jsonify({'ok': False, 'error': f'全部失败: {"; ".join(errors[-3:])}'}), 400
    msg = f"已为 {row['name']} 添加 {saved} 个面部嵌入"
    if errors: msg += f'（{len(errors)} 张跳过）'
    return jsonify({'ok': True, 'message': msg, 'saved': saved, 'errors': errors[:3]})


# ============================================================
# Test mode
# ============================================================
@app.route('/test', methods=['GET', 'POST'])
@login_required
def test_page():
    global test_video_path, test_paused, test_eof, test_last_frame
    if request.method == 'POST':
        test_paused = False
        test_eof = False
        test_last_frame = None
        vf = request.files.get('video')
        if not vf or vf.filename == '':
            return jsonify({'ok': False, 'error': '请选择视频文件'}), 400
        # Use unique filename so the test_feed generator detects the change
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        vp = os.path.join('static', f'test_video_{ts}.mp4')
        vf.save(vp)
        # Clean up old test videos
        import glob as _glob
        for old in _glob.glob(os.path.join('static', 'test_video_*.mp4')):
            if old != vp:
                try:
                    os.remove(old)
                except OSError:
                    pass
        with test_video_lock:
            # If there's an old generator running, signal it to stop
            old_vp = test_video_path
            test_video_path = vp
            # Save and disable all cameras
            test_saved_cam_states.clear()
            for c in CAMERAS:
                cid = c['id']
                test_saved_cam_states[cid] = camera_enabled.get(cid, False)
                camera_enabled[cid] = False
        log.info("Test: video uploaded, cameras disabled")
        return jsonify({'ok': True, 'message': '视频已加载，摄像头已关闭'})
    return render_template('test.html')


@app.route('/test/reset')
@login_required
def test_reset():
    global test_video_path, test_paused, test_eof, test_last_frame
    test_paused = False
    test_eof = False
    test_last_frame = None
    with test_video_lock:
        test_video_path = None
        # Restore saved camera states
        for cid, state in test_saved_cam_states.items():
            camera_enabled[cid] = state
        test_saved_cam_states.clear()
    log.info("Test: reset, cameras restored")
    return jsonify({'ok': True, 'message': '测试结束，摄像头已恢复'})


@app.route('/test/pause', methods=['POST'])
@login_required
def test_pause():
    global test_paused
    test_paused = not test_paused
    return jsonify({'ok': True, 'paused': test_paused})


@app.route('/test/state')
@login_required
def test_state():
    global test_paused, test_eof
    return jsonify({'ok': True, 'paused': test_paused, 'eof': test_eof, 'has_video': test_video_path is not None})


@app.route('/test_feed')
@login_required
def test_feed():
    """MJPEG stream for uploaded test video. ?overlay=1 to show detection overlay."""
    show_overlay = request.args.get('overlay', '0') == '1'
    def gen():
        global test_video_path, test_paused, test_last_frame, test_eof
        while alive.is_set():
            vp = None
            with test_video_lock:
                vp = test_video_path
            if vp is None:
                blank = np.zeros((360, 480, 3), dtype=np.uint8)
                cv2.putText(blank, '请上传测试视频', (80, 190),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (128, 128, 128), 2)
                _, buf = cv2.imencode('.jpg', blank)
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
                time.sleep(0.5)
                continue

            test_last_frame = None
            test_eof = False
            cap = cv2.VideoCapture(vp)
            while alive.is_set():
                vp2 = None
                with test_video_lock:
                    vp2 = test_video_path
                if vp2 != vp:
                    break  # video changed or reset

                if test_paused:
                    # Paused: just keep showing the last frame
                    if test_last_frame is not None:
                        _, buf = cv2.imencode('.jpg', test_last_frame,
                                              [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
                    time.sleep(0.1)
                    continue

                if test_eof:
                    # Video ended: keep showing last frame
                    if test_last_frame is not None:
                        _, buf = cv2.imencode('.jpg', test_last_frame,
                                              [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
                    time.sleep(0.2)
                    continue

                success, frame = cap.read()
                if not success:
                    test_eof = True
                    continue

                # Run detection on frame
                results = model(frame, imgsz=YOLO_IMGSZ, conf=0.5, verbose=False, device=DEVICE)
                res = results[0]
                dets = all_persons(res)

                # Match tracks
                with tracker_lock:
                    tracks = _match_or_create_tracks(dets, int(cap.get(cv2.CAP_PROP_POS_FRAMES)), '__test__')

                max_p_fall = 0.0; any_fall = False
                for tid, t in tracks.items():
                    kp, kp_conf = t.get('kp'), t.get('kp_conf')
                    if kp is None or kp_conf is None:
                        continue
                    is_fall, info = check_fall(
                        kp, kp_conf, t['hip_history'], t['angle_history'],
                        fd_fall_conf=0.0,
                        ground_contact_frames=t.get('ground_contact_frames', 0))

                    if info is None:
                        prev = t.get('last_p_fall', 0.0)
                        pf = prev * 0.85
                        t['last_p_fall'] = pf
                        t['ground_contact_frames'] = max(0, t.get('ground_contact_frames', 0) - 1)
                    else:
                        pf = info.get('p_fall', 0)
                        t['last_p_fall'] = pf
                        if info.get('p_ground', 0) > 0.5:
                            t['ground_contact_frames'] = t.get('ground_contact_frames', 0) + 1
                        else:
                            t['ground_contact_frames'] = max(0, t.get('ground_contact_frames', 0) - 1)

                    if pf > max_p_fall: max_p_fall = pf
                    if is_fall:
                        t['fall_counter'] += 1
                    else:
                        t['fall_counter'] = 0
                    if t['fall_counter'] >= FALL_CONSECUTIVE_FRAMES:
                        any_fall = True
                        t['fall_counter'] = 0

                    if show_overlay:
                        color = (0, 0, 255) if t['fall_counter'] > 0 else t.get('color', (0, 255, 0))
                        # Draw bounding box
                        bx, by = int(t['bbox'][0]), int(t['bbox'][1])
                        bw = int(t['bbox'][2] - t['bbox'][0])
                        bh = int(t['bbox'][3] - t['bbox'][1])
                        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), color, 2)
                        # Draw skeleton
                        for a, b in SKELETON_EDGES:
                            if kp_conf[a] > 0.5 and kp_conf[b] > 0.5:
                                cv2.line(frame, (int(kp[a][0]), int(kp[a][1])),
                                         (int(kp[b][0]), int(kp[b][1])), color, 2)
                        for i in range(len(kp)):
                            if kp_conf[i] > 0.5:
                                cv2.circle(frame, (int(kp[i][0]), int(kp[i][1])), 4, color, -1)
                        # Label on bounding box
                        pname = t.get('name') or ''
                        label = f'{pname} | {pf:.2f}' if pname else f'{pf:.2f}'
                        (lw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
                        lx = max(0, bx + int((bw - lw) / 2))
                        ly = max(20, by - 8)
                        cv2.putText(frame, label, (lx, ly), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

                if show_overlay:
                    # HUD
                    cv2.putText(frame, f'Test Mode | Persons: {len(tracks)} | P_FALL: {max_p_fall:.2f}',
                                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                (0, 0, 255) if max_p_fall >= RED_THRESHOLD else (0, 255, 0), 2)
                    if any_fall:
                        cv2.putText(frame, 'FALL DETECTED!', (10, 60),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 3)

                if any_fall:
                    # Test mode: save to DB + broadcast alert + trigger AI if enabled
                    now = time.time()
                    if not hasattr(gen, '_last_fall') or (now - gen._last_fall) > FALL_COOLDOWN_SECONDS:
                        gen._last_fall = now
                        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                        fn = os.path.join('static', 'falls', f'test_fall_{ts}.jpg')
                        cv2.imwrite(fn, frame)
                        with db_connection() as conn:
                            cur = conn.execute('INSERT INTO events (elder_name, confidence, screenshot) VALUES (?, ?, ?)',
                                               ('测试跌倒', max_p_fall, f'/static/falls/test_fall_{ts}.jpg'))
                            eid = cur.lastrowid
                            conn.commit()
                        # Broadcast alert via SSE + WebSocket so frontend plays alarm
                        event_data = {
                            'type': 'fall', 'level': 2, 'name': '测试跌倒',
                            'confidence': round(max_p_fall, 3),
                            'timestamp': ts, 'screenshot': f'/static/falls/test_fall_{ts}.jpg',
                            'event_id': eid,
                        }
                        broadcast_alert(event_data)
                        if cfg.AI_ENABLED and ai_toggle:
                            ai_executor.submit(analyze_fall_image, f'/static/falls/test_fall_{ts}.jpg', eid)
                        log.warning('Test: fall detected + alert, event #%d', eid)
                test_last_frame = frame.copy()
                test_eof = False
                _, buf = cv2.imencode('.jpg', frame,
                                      [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')

            cap.release()
            with test_video_lock:
                if test_video_path == vp:
                    test_video_path = None
                    for cid, state in test_saved_cam_states.items():
                        camera_enabled[cid] = state
                    test_saved_cam_states.clear()
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')


# ============================================================
# Main
# ============================================================
if __name__ == '__main__':
    
    log.info("=" * 40)
    log.info("SafeSight v2.0 starting on http://localhost:%d", cfg.SERVER_PORT)
    for c in CAMERAS:
        name = camera_names.get(str(c['id']), f'摄像头{c["id"]+1}')
        log.info("  /video_feed/%d → %s (source=%s)", c['id'], name, c['source'])
    

    # Init per-camera state
    for c in CAMERAS:
        cid = c['id']
        frame_queues[cid] = queue.Queue(maxsize=2)
        latest_detections[cid] = {'kp_xy': None, 'kp_conf': None, 'is_fall': False, 'tracks': {}, 'fd_boxes': []}
        detection_locks[cid] = threading.Lock()
        current_fps_list[cid] = 0
        person_count_list[cid] = 0
        last_p_fall_list[cid] = 0

    # Start detection threads per camera
    for c in CAMERAS:
        t = threading.Thread(target=detection_worker, args=(c['id'],), daemon=True,
                             name=f'detection-{c["id"]}')
        t.start()
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True, name='cleanup')
    cleanup_thread.start()
    usb_scan_thread = threading.Thread(target=_auto_scan_usb, daemon=True, name='usb-scan')
    usb_scan_thread.start()
    log.info("%d camera(s) + cleanup threads started", len(CAMERAS))

    import signal as _signal
    def _shutdown(sig, frame):
        log.info('Shutting down...')
        alive.clear()
        time.sleep(0.3)
        for cid, cap in list(camera_caps.items()):
            try: cap.release()
            except: pass
        camera_caps.clear()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except: pass
        os._exit(0)
    _signal.signal(_signal.SIGINT, _shutdown)
    _signal.signal(_signal.SIGTERM, _shutdown)

    socketio.run(app, host='0.0.0.0', port=5001, debug=False, allow_unsafe_werkzeug=True)
