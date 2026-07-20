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


def process_ground_hazards(frame, model_ground, tracks, cam_id,
                           broadcast_fn, hazard_cooldown):
    """Run ground hazard detection and emit distance-based alerts.

    Alert levels:
        - distance < CLOSE (120px):  orange alert — "距离很近，请立即注意"
        - distance < NEAR  (250px):  yellow alert  — "障碍物较近，请注意"

    Args:
        frame: current camera frame.
        model_ground: YOLO model for object detection.
        tracks: current tracked persons dict.
        cam_id: camera ID.
        broadcast_fn: callable(event_data) for alerts.
        hazard_cooldown: dict for tracking alert cooldowns.

    Returns:
        List of detected hazards (for overlay drawing).
    """
    hazards = detect_ground_hazards(frame, model_ground)

    for hazard in hazards:
        dist, person_name = check_person_distance(hazard['center'], tracks)

        # Determine alert level based on distance
        alert_level = None
        if dist < cfg.GROUND_HAZARD_CLOSE:
            alert_level = 'orange'
            alert_msg = f'地面障碍物：{hazard["name"]}，距离很近，请立即注意！'
        elif dist < cfg.GROUND_HAZARD_NEAR:
            alert_level = 'yellow'
            alert_msg = f'地面障碍物：{hazard["name"]}，较近，请注意避让'
        else:
            continue  # Too far, no alert

        # Cooldown check
        hazard_key = f"{cam_id}_{hazard['name']}"
        now = time.time()
        if hazard_key in hazard_cooldown:
            if now - hazard_cooldown[hazard_key] < cfg.GROUND_HAZARD_COOLDOWN:
                continue

        # Broadcast
        broadcast_fn({
            'type': alert_level,
            'level': 1 if alert_level == 'yellow' else 2,
            'message': alert_msg,
            'hazard_type': hazard['name'],
            'person_nearby': person_name,
            'distance': int(dist),
            'cam_id': cam_id,
            'bbox': list(hazard['bbox']),
        })

        hazard_cooldown[hazard_key] = now
        log.warning('Ground hazard: %s, distance=%dpx, level=%s (cam %d)',
                    hazard['name'], dist, alert_level, cam_id)

    return hazards
