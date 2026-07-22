"""Drawing overlay functions for video frames."""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import config as cfg
from core.state import current_fps_list, person_count_list, last_p_fall_list, camera_names

YELLOW_THRESHOLD = cfg.YELLOW_THRESHOLD
RED_THRESHOLD = cfg.RED_THRESHOLD

# Load Chinese font for PIL rendering
_FONT_PATH = None
for fp in [
    'C:/Windows/Fonts/msyh.ttc',
    'C:/Windows/Fonts/simhei.ttf',
    '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
]:
    import os
    if os.path.isfile(fp):
        _FONT_PATH = fp
        break


def _put_chinese(frame, text, position, font_size=20, color=(255, 255, 255)):
    """Draw Chinese text on frame using PIL (OpenCV putText doesn't support CJK)."""
    if _FONT_PATH is None:
        # Fallback to ASCII if no CJK font found
        cv2.putText(frame, text.encode('ascii', 'replace').decode(), position,
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        return frame

    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    font = ImageFont.truetype(_FONT_PATH, font_size)
    draw.text(position, text, font=font, fill=color)
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

# COCO skeleton edges
SKELETON_EDGES = [
    (0, 1), (0, 2), (1, 3), (2, 4),
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
]


def draw_tracking_overlay(frame, tracks):
    """Draw skeleton, bounding boxes, and labels for tracked persons."""
    for tid, t in tracks.items():
        kp, kp_conf = t.get('kp'), t.get('kp_conf')
        if kp is None or kp_conf is None:
            continue
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
        lx = max(0, bx)
        ly = max(20, by - 8)
        # Use PIL for Chinese text rendering
        frame[:] = _put_chinese(frame.copy(), label, (lx, ly), font_size=18, color=color)


def draw_fall_boxes(frame, fd_boxes):
    """Draw fall detection model boxes."""
    for fb in fd_boxes:
        x1, y1, x2, y2 = fb['bbox']
        fd_conf = fb['conf']
        if fb['is_fall']:
            c = (0, 0, 255)
            label = 'FALL ' + str(int(fd_conf * 100)) + '%'
        else:
            c = (0, 255, 0)
            label = 'SAFE ' + str(int(fd_conf * 100)) + '%'
        cv2.rectangle(frame, (x1, y1), (x2, y2), c, 2)
        cv2.putText(frame, label, (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, c, 2)


def draw_ground_hazards(frame, ground_hazards):
    """Draw ground hazard detection boxes with risk-level colors."""
    import config as cfg
    for hazard in ground_hazards:
        x1, y1, x2, y2 = hazard['bbox']
        class_name = hazard['name']
        risk_level = 'medium'  # default
        try:
            from core.ground_hazard import _get_risk_level
            risk_level = _get_risk_level(class_name)
        except Exception:
            pass
        risk_info = cfg.HAZARD_RISK_LEVELS.get(risk_level, cfg.HAZARD_RISK_LEVELS['medium'])
        color = risk_info['color']
        label_text = risk_info['label']
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"[{label_text}] {class_name} {hazard['conf']:.2f}"
        cv2.putText(frame, label, (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)


def draw_hud(frame, cam_id):
    """Draw HUD overlay (FPS, person count, P_FALL, collision risk)."""
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

    # Collision risk indicator
    from core.state import latest_detections
    predictions = latest_detections.get(cam_id, {}).get('collision_predictions', [])
    if predictions:
        max_prob = max(p['probability'] for p in predictions)
        risk_color = (0, 255, 0) if max_prob < 40 else (0, 165, 255) if max_prob < 70 else (0, 0, 255)
        cv2.putText(frame, f"COLLISION: {max_prob}%", (10, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, risk_color, 1)
