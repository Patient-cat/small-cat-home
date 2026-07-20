"""Ground hazard detection — YOLOv8n object detection + ROI + proximity check."""
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


def check_person_nearby(hazard_center, tracks, threshold=None):
    """Check if any tracked person is near a hazard.

    Args:
        hazard_center: (x, y) pixel coordinates of hazard center.
        tracks: dict of {track_id: track_data} from detection worker.
        threshold: pixel distance threshold (default from config).

    Returns:
        (is_nearby: bool, person_name: str | None)
    """
    if threshold is None:
        threshold = cfg.GROUND_HAZARD_PROXIMITY

    for tid, t in tracks.items():
        bx1, by1, bx2, by2 = t['bbox']
        person_center = ((bx1 + bx2) // 2, (by1 + by2) // 2)

        dist = ((hazard_center[0] - person_center[0]) ** 2 +
                (hazard_center[1] - person_center[1]) ** 2) ** 0.5

        if dist < threshold:
            return True, t.get('name', '陌生人')
    return False, None


def is_in_roi(bbox, roi_polygon, frame_w=1, frame_h=1):
    """Check if object center is within the walking region ROI.

    Args:
        bbox: (x1, y1, x2, y2) bounding box in pixel coordinates.
        roi_polygon: list of [x, y] normalized coordinates (0-1), or empty.
        frame_w: frame width in pixels (for normalization).
        frame_h: frame height in pixels (for normalization).

    Returns:
        True if object is in ROI (or no ROI configured).
    """
    if not roi_polygon:
        return True

    # Normalize bbox center to 0-1 range
    cx = ((bbox[0] + bbox[2]) / 2) / frame_w
    cy = ((bbox[1] + bbox[3]) / 2) / frame_h

    # Point-in-polygon test (ray casting algorithm)
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


def process_ground_hazards(frame, model_ground, tracks, cam_id,
                           get_roi_fn, broadcast_fn, hazard_cooldown):
    """Run ground hazard detection and emit alerts.

    Args:
        frame: current camera frame.
        model_ground: YOLO model for object detection.
        tracks: current tracked persons dict.
        cam_id: camera ID.
        get_roi_fn: callable(cam_id) -> list of ROI points.
        broadcast_fn: callable(event_data) for alerts.
        hazard_cooldown: dict for tracking alert cooldowns.

    Returns:
        List of detected hazards (for overlay drawing).
    """
    hazards = detect_ground_hazards(frame, model_ground)
    roi = get_roi_fn(cam_id)

    for hazard in hazards:
        if not is_in_roi(hazard['bbox'], roi):
            continue

        nearby, person_name = check_person_nearby(hazard['center'], tracks)

        if nearby:
            hazard_key = f"{cam_id}_{hazard['name']}"
            now = time.time()
            if hazard_key in hazard_cooldown:
                if now - hazard_cooldown[hazard_key] < cfg.GROUND_HAZARD_COOLDOWN:
                    continue

            broadcast_fn({
                'type': 'yellow',
                'level': 1,
                'message': f'地面障碍物：{hazard["name"]}，请注意避让',
                'hazard_type': hazard['name'],
                'person_nearby': person_name,
                'cam_id': cam_id,
                'bbox': list(hazard['bbox']),
            })

            hazard_cooldown[hazard_key] = now
            log.warning('Ground hazard: %s near %s (cam %d)',
                        hazard['name'], person_name, cam_id)

    return hazards
