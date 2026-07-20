"""Camera configuration management — load/save/init cameras."""
import os
import json
import logging
import threading

import config as cfg
from core.state import CAMERAS, camera_names, camera_enabled, frame_queues
from core.state import latest_detections, detection_locks, current_fps_list
from core.state import person_count_list, last_p_fall_list, config_lock

log = logging.getLogger('safesight')

CAMERA_CONFIG_FILE = cfg.CAMERA_CONFIG_FILE


def _scan_usb_cameras(max_index=5):
    """Scan for available USB/built-in cameras. Returns list of working indices."""
    import cv2
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
        except (json.JSONDecodeError, IOError) as e:
            log.warning('Failed to load camera config: %s', e)

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


def _save_camera_config(cameras, names):
    """Persist camera config to JSON file."""
    with config_lock:
        with open(CAMERA_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'cameras': cameras, 'names': names}, f, ensure_ascii=False, indent=2)


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


def _init_camera_pipeline(cam_id):
    """Initialize per-camera processing infrastructure for a new camera."""
    from core.state import tracker_states
    from core.worker import detection_worker, ground_hazard_worker

    if cam_id not in frame_queues:
        frame_queues[cam_id] = __import__('queue').Queue(maxsize=2)
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
