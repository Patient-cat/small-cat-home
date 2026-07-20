"""Ground hazard detection — YOLOv8n object detection + distance-based alerting."""
import time
import logging
import numpy as np

import config as cfg

log = logging.getLogger('safesight')


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
                'cls': cls,
                'name': results[0].names[cls],
                'bbox': (x1, y1, x2, y2),
                'conf': conf,
                'center': ((x1 + x2) // 2, (y1 + y2) // 2),
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
    """Get effective risk level for a class, checking DB overrides first."""
    import config as cfg
    from models.database import db_connection

    override_name = f'__override__:{class_name}'
    with db_connection() as conn:
        row = conn.execute(
            'SELECT risk_level FROM custom_hazards WHERE name = ?', (override_name,)
        ).fetchone()
    if row:
        return row['risk_level']
    return cfg.HAZARD_CLASS_LEVELS.get(class_name, 'medium')


def _get_display_name(class_name):
    """Get custom display name for a class, or fall back to class_name."""
    from models.database import db_connection
    with db_connection() as conn:
        row = conn.execute(
            'SELECT name FROM custom_hazards WHERE category = ? AND name NOT LIKE "__override__:%" LIMIT 1',
            (class_name,)
        ).fetchone()
    return row['name'] if row else class_name


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

        # Skip ignored classes
        if risk_level == 'ignore':
            continue

        dist, person_name = check_person_distance(hazard['center'], tracks)

        # Determine alert level based on risk level + distance
        import config as cfg
        risk_info = cfg.HAZARD_RISK_LEVELS.get(risk_level, cfg.HAZARD_RISK_LEVELS['medium'])
        display_name = _get_display_name(class_name)

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
            continue  # Too far, no alert

        # Cooldown check
        hazard_key = f"{cam_id}_{class_name}"
        now = time.time()
        if hazard_key in hazard_cooldown:
            if now - hazard_cooldown[hazard_key] < cfg.GROUND_HAZARD_COOLDOWN:
                continue

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
            'cam_id': cam_id,
            'bbox': list(hazard['bbox']),
        })

        hazard_cooldown[hazard_key] = now
        log.warning('Ground hazard: %s, distance=%dpx, level=%s (cam %d)',
                    hazard['name'], dist, alert_level, cam_id)

    return hazards
