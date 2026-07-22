"""Ground hazard detection — YOLOv8n object detection + distance-based alerting."""
import time
import logging
from collections import deque
import numpy as np

import config as cfg

log = logging.getLogger('safesight')

# In-memory cache for risk levels (avoids DB query on every frame)
_risk_level_cache = {}  # {class_name: risk_level}
_risk_level_cache_loaded = False


def _load_risk_level_cache():
    """Load all risk level overrides from DB into memory (once at startup)."""
    global _risk_level_cache, _risk_level_cache_loaded
    if _risk_level_cache_loaded:
        return
    try:
        from models.database import db_connection
        with db_connection() as conn:
            rows = conn.execute("SELECT name, risk_level FROM custom_hazards").fetchall()
            for r in rows:
                _risk_level_cache[r['name']] = r['risk_level']
        _risk_level_cache_loaded = True
    except Exception as e:
        log.debug('Risk level cache load failed: %s', e)


def detect_ground_hazards(frame, model_ground):
    """Detect ground-level objects that could cause falls.

    Args:
        frame: BGR image from camera.
        model_ground: YOLO model instance (yolov8n).

    Returns:
        List of dicts with keys: cls, name, bbox, conf, center.
    """
    if model_ground is None:
        return []

    results = model_ground(frame, conf=cfg.GROUND_HAZARD_CONF, verbose=False)
    hazards = []
    target_classes = cfg.GROUND_HAZARD_TARGET_CLASSES

    for box in results[0].boxes:
        cls = int(box.cls[0])
        conf = float(box.conf[0])
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)

        if cls in target_classes:
            hazards.append({
                'cls': int(cls),
                'name': results[0].names[cls],
                'bbox': (int(x1), int(y1), int(x2), int(y2)),
                'conf': float(conf),
                'center': (int((x1 + x2) // 2), int((y1 + y2) // 2)),
            })
    return hazards


def check_person_distance(hazard_center, tracks):
    """Check closest person distance to a hazard.

    Args:
        hazard_center: (x, y) pixel coordinates of hazard center.
        tracks: dict of {track_id: track_data} from detection worker.

    Returns:
        (distance_pixels: float, person_name: str | None)
        Returns (9999, None) if no tracked persons.
    """
    if not tracks:
        return 9999, None

    min_dist = 9999
    closest_name = None

    for tid, t in tracks.items():
        bx1, by1, bx2, by2 = t['bbox']
        person_center = ((bx1 + bx2) // 2, (by1 + by2) // 2)

        dist = ((hazard_center[0] - person_center[0]) ** 2 +
                (hazard_center[1] - person_center[1]) ** 2) ** 0.5

        if dist < min_dist:
            min_dist = dist
            closest_name = t.get('name', '陌生人')

    return min_dist, closest_name


def _get_risk_level(class_name):
    """Get effective risk level for a class (cached in memory)."""
    import config as cfg
    _load_risk_level_cache()
    override_name = f'__override__:{class_name}'
    if override_name in _risk_level_cache:
        return _risk_level_cache[override_name]
    return cfg.HAZARD_CLASS_LEVELS.get(class_name, 'medium')


def _get_display_name(class_name):
    """Get custom display name for a class (cached in memory)."""
    _load_risk_level_cache()
    # Check cache for a non-override entry matching this category
    for key, val in _risk_level_cache.items():
        if not key.startswith('__override__') and val == class_name:
            return key
    return class_name


def process_ground_hazards(frame, model_ground, tracks, cam_id,
                           broadcast_fn, hazard_cooldown):
    """Run ground hazard detection and emit distance + risk-level alerts.

    Risk levels:
        - high   + close (<120px) → red alert
        - medium + close (<120px) → orange alert
        - low    + close (<120px) → yellow alert
        - any    + near  (<250px) → yellow alert (one level down)
        - ignore                 → never alert
    """
    hazards = detect_ground_hazards(frame, model_ground)

    for hazard in hazards:
        class_name = hazard['name']
        risk_level = _get_risk_level(class_name)

        if risk_level == 'ignore':
            continue

        dist, person_name = check_person_distance(hazard['center'], tracks)
        display_name = _get_display_name(class_name)

        # --- Approach velocity check ---
        # Track distance history per hazard; skip alert if person is moving away
        hist_key = f"{cam_id}_{class_name}_dist_hist"
        if hist_key not in hazard_cooldown:
            hazard_cooldown[hist_key] = deque(maxlen=5)
        dist_hist = hazard_cooldown[hist_key]
        dist_hist.append(dist)

        # Need at least 3 samples to judge direction
        is_approaching = True
        if len(dist_hist) >= 3:
            velocity = dist_hist[-1] - dist_hist[0]  # positive = moving away
            is_approaching = velocity < 5  # small threshold for noise

        # --- Alert level based on risk + distance ---
        alert_level = None
        if dist < cfg.GROUND_HAZARD_CLOSE:
            if risk_level == 'high':
                alert_level = 'red'
                alert_msg = f'高危障碍物：{display_name}，距离极近，请立即处理！'
            elif risk_level == 'medium':
                alert_level = 'orange'
                alert_msg = f'中危障碍物：{display_name}，距离很近，请注意！'
            else:
                alert_level = 'yellow'
                alert_msg = f'低危障碍物：{display_name}，较近，请注意避让'
        elif dist < cfg.GROUND_HAZARD_NEAR:
            alert_level = 'yellow'
            alert_msg = f'障碍物：{display_name}，较近，请注意避让'
        else:
            continue

        # Skip if no person nearby
        if dist >= cfg.GROUND_HAZARD_NEAR:
            continue

        # Skip if person is moving away from hazard
        if not is_approaching:
            continue

        # Cooldown check
        hazard_key = f"{cam_id}_{class_name}"
        now = time.time()
        if hazard_key in hazard_cooldown:
            if now - hazard_cooldown[hazard_key] < cfg.GROUND_HAZARD_COOLDOWN:
                continue

        # --- Risk score (distance × risk_weight) ---
        risk_weights = {'high': 1.0, 'medium': 0.7, 'low': 0.4}
        distance_factor = max(0, 1 - dist / cfg.GROUND_HAZARD_NEAR)
        risk_score = int(risk_weights.get(risk_level, 0.5) * distance_factor * 100)

        # Broadcast
        broadcast_fn({
            'type': alert_level,
            'level': 1 if alert_level == 'yellow' else 2,
            'message': alert_msg,
            'hazard_type': class_name,
            'display_name': display_name,
            'risk_level': risk_level,
            'person_nearby': person_name,
            'distance': int(dist),
            'risk_score': risk_score,
            'cam_id': cam_id,
            'bbox': list(hazard['bbox']),
        })

        # Queue DB write (non-blocking)
        try:
            from core.state import hazard_event_queue
            hazard_event_queue.put_nowait({
                'cam_id': cam_id, 'hazard_type': class_name,
                'display_name': display_name, 'risk_level': risk_level,
                'distance_px': int(dist), 'person_nearby': person_name,
                'alert_level': alert_level,
            })
        except Exception:
            pass  # Queue full, drop event

        hazard_cooldown[hazard_key] = now
        log.warning('Ground hazard: %s, distance=%dpx, risk=%d, level=%s, approaching=%s (cam %d)',
                    class_name, dist, risk_score, alert_level, is_approaching, cam_id)

    return hazards
