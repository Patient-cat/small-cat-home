"""Detection worker and ground hazard worker threads."""
import time
import queue
import threading
import logging
import numpy as np

import config as cfg
from core.state import (
    frame_queues, latest_detections, detection_locks,
    current_fps_list, person_count_list, last_p_fall_list,
    camera_enabled, alive, tracker_lock, tracker_states,
    model, model_fd, model_ground, face_app, DEVICE,
    fall_queue, last_fall_time, recognized_name, state_lock,
    video_buffers,
    camera_names, camera_caps,
)
from core.tracking import all_persons, _match_or_create_tracks, _iou
from core.fall_detection import check_fall
from core.ground_hazard import process_ground_hazards
from models.database import db_connection

log = logging.getLogger('safesight')

# Config aliases
YELLOW_THRESHOLD = cfg.YELLOW_THRESHOLD
RED_THRESHOLD = cfg.RED_THRESHOLD
FALL_CONSECUTIVE_FRAMES = cfg.FALL_CONSECUTIVE_FRAMES
YELLOW_HOLD_FRAMES = cfg.YELLOW_HOLD_FRAMES
FALL_COOLDOWN_SECONDS = cfg.FALL_COOLDOWN_SECONDS
DETECTION_INTERVAL = cfg.DETECTION_INTERVAL
FACE_RECOGNITION_INTERVAL = cfg.FACE_RECOGNITION_INTERVAL
IOU_MATCH_MIN = cfg.IOU_MATCH_MIN


def broadcast_alert(event_data):
    """Push to SSE queue and WebSocket broadcast."""
    try:
        fall_queue.put_nowait(event_data)
    except queue.Full:
        pass
    # SocketIO emit handled by app's socketio instance — imported lazily
    try:
        from core.state import socketio
        socketio.emit('alert', event_data)
    except Exception as e:
        log.debug('SocketIO emit failed: %s', e)


def _run_fall_detection(frame, ld):
    """Run fall detection model, return (fd_boxes, fd_cls_conf)."""
    if model_fd is None:
        return [], 0.0

    fd_res = model_fd(frame, imgsz=cfg.FALL_FD_IMGSZ, conf=cfg.FALL_FD_CONF_THRESHOLD,
                      verbose=False, device=DEVICE)[0]
    fd_boxes = []
    fd_cls_conf = 0.0

    if fd_res.boxes is not None:
        for box in fd_res.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
            cls = int(box.cls[0])
            conf = float(box.conf[0])
            fd_boxes.append({'bbox': (x1, y1, x2, y2), 'is_fall': cls == 0, 'conf': conf})
    elif fd_res.probs is not None:
        probs = fd_res.probs
        fall_idx = 1 if 'fall' in (model_fd.names or {}).get(1, '').lower() else 0
        fd_cls_conf = float(probs.top1conf) if probs.top1 == fall_idx else 1.0 - float(probs.top1conf)
        h, w = frame.shape[:2]
        if fd_cls_conf > cfg.FALL_FD_CONF_THRESHOLD:
            fd_boxes.append({'bbox': (0, 0, w, h), 'is_fall': True, 'conf': fd_cls_conf})

    if fd_boxes or not ld.get('fd_boxes'):
        ld['fd_boxes'] = fd_boxes
    ld['_fd_boxes_cache'] = fd_boxes
    ld['_fd_cls_conf'] = fd_cls_conf
    return fd_boxes, fd_cls_conf


def _process_pose_and_tracking(frame, det_frame_count, cam_id, ld, dl):
    """Run pose detection, face recognition, tracking, and fall scoring."""
    global recognized_name

    results = model(frame, imgsz=cfg.YOLO_IMGSZ, conf=0.5, verbose=False, device=DEVICE)
    result = results[0]
    detections = all_persons(result)

    with tracker_lock:
        tracks = _match_or_create_tracks(detections, det_frame_count, cam_id)
        person_count_list[cam_id] = len(tracks)

    # Per-person face recognition
    if face_app is not None and det_frame_count % FACE_RECOGNITION_INTERVAL == 0:
        from core.face_recognition import recognize_face
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

    # Sync recognized_name
    named_tracks = [(t.get('name'), t.get('last_p_fall', 0))
                    for t in tracks.values() if t.get('name')]
    with state_lock:
        if named_tracks:
            recognized_name = max(named_tracks, key=lambda x: x[1])[0]

    # Score each person
    max_p_fall = 0.0
    any_is_fall = False
    fd_cache = ld.get('_fd_boxes_cache', [])
    fd_cls = ld.get('_fd_cls_conf', 0.0)

    for tid, t in tracks.items():
        kp, kp_conf = t['kp'], t['kp_conf']
        track_fd_conf = fd_cls
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
            prev = t.get('last_p_fall', 0.0)
            p_fall_val = prev * cfg.FALL_DECAY_FACTOR
            t['last_p_fall'] = p_fall_val
            t['ground_contact_frames'] = max(0, t.get('ground_contact_frames', 0) - 1)
        else:
            p_fall_val = info.get('p_fall', 0.0)
            t['last_p_fall'] = p_fall_val
            if info.get('p_ground', 0) > 0.5:
                t['ground_contact_frames'] = t.get('ground_contact_frames', 0) + 1
            else:
                t['ground_contact_frames'] = max(0, t.get('ground_contact_frames', 0) - 1)

        if p_fall_val > max_p_fall:
            max_p_fall = p_fall_val
        if is_fall_now:
            t['fall_counter'] += 1
        else:
            t['fall_counter'] = 0
        if t['fall_counter'] >= FALL_CONSECUTIVE_FRAMES:
            any_is_fall = True

    last_p_fall_list[cam_id] = max_p_fall

    # Publish for drawing
    best_t = max(tracks.values(), key=lambda t: t.get('last_p_fall', 0)) if tracks else None
    with dl:
        ld['kp_xy'] = best_t['kp'] if best_t else None
        ld['kp_conf'] = best_t['kp_conf'] if best_t else None
        ld['is_fall'] = any_is_fall
        ld['tracks'] = tracks

    return tracks, max_p_fall, any_is_fall


def _handle_alerts(cam_id, max_p_fall, any_is_fall, tracks, frame, warn_hold):
    """Handle yellow/red fall alerts. Returns updated warn_hold."""
    global last_fall_time, recognized_name

    # Level 1: Yellow alert
    if YELLOW_THRESHOLD <= max_p_fall < RED_THRESHOLD:
        warn_hold = YELLOW_HOLD_FRAMES
    else:
        warn_hold = max(0, warn_hold - 1)
    if warn_hold > 0 and not any_is_fall:
        broadcast_alert({'type': 'yellow', 'level': 1,
                         'message': '⚠️ 可能摔倒', 'p_fall': max_p_fall, 'cam_id': cam_id})

    # Level 2: Red alert
    now = time.time()
    for tid, t in tracks.items():
        if t['fall_counter'] >= FALL_CONSECUTIVE_FRAMES:
            t['fall_counter'] = 0
            if (now - last_fall_time) > FALL_COOLDOWN_SECONDS:
                last_fall_time = now
                pname = t.get('name') or '陌生人'
                with state_lock:
                    saved_name = recognized_name
                    recognized_name = pname
                _trigger_fall_event(frame, {'p_fall': t.get('last_p_fall', 0.75),
                                            'angle': 0, 'velocity': 0, 'ar': 0,
                                            'angle_accel': 0, 'p_angle': 0,
                                            'p_vel': 0, 'p_ar': 0,
                                            'p_accel': 0, 'p_hf': 0,
                                            'p_ground': 0, 'p_fd': 0,
                                            'cam_id': cam_id})
                with state_lock:
                    recognized_name = saved_name
                t['fall_counter'] = 0

    return warn_hold


def _trigger_fall_event(frame, info):
    """Save screenshot, write event to DB, queue AI analysis."""
    import cv2
    from datetime import datetime

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    fname = f'fall_{ts}.jpg'
    fpath = f'static/falls/{fname}'
    cv2.imwrite(fpath, frame)

    with state_lock:
        name = recognized_name

    with db_connection() as conn:
        cur = conn.execute(
            'INSERT INTO events (elder_name, confidence, screenshot) VALUES (?, ?, ?)',
            (name, info['p_fall'], f'/{fpath}')
        )
        conn.commit()
        event_id = cur.lastrowid

    log.warning('FALL EVENT #%d: %s (P=%.2f, cam=%d)',
                event_id, name, info['p_fall'], info.get('cam_id', 0))

    broadcast_alert({
        'type': 'red', 'level': 2,
        'name': name,
        'confidence': info['p_fall'],
        'message': f'确认摔倒！{name}',
        'timestamp': ts,
        'screenshot': f'/{fpath}',
        'event_id': event_id,
    })

    # Queue AI analysis
    from concurrent.futures import ThreadPoolExecutor
    ai_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ai')
    ai_executor.submit(_analyze_fall_image, fpath, event_id)


def _analyze_fall_image(screenshot_path, event_id):
    """Text-based AI analysis using event data."""
    import requests as req

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
        import json
        if cfg.AI_PROVIDER == 'gemini':
            payload = {'contents': [{'parts': [{'text': text_prompt}]}]}
            headers = {'Content-Type': 'application/json'}
            endpoint = cfg.get_endpoint()
        else:
            payload = {'model': cfg.AI_MODEL, 'messages': [{'role': 'user', 'content': text_prompt}],
                       'max_tokens': cfg.AI_MAX_TOKENS, 'temperature': cfg.AI_TEMPERATURE}
            headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {cfg.AI_API_KEY}'}
            endpoint = cfg.get_endpoint()

        resp = req.post(endpoint, json=payload, headers=headers, timeout=cfg.AI_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        report = data['candidates'][0]['content']['parts'][0]['text'] if cfg.AI_PROVIDER == 'gemini' \
            else data['choices'][0]['message']['content']

        with db_connection() as conn:
            conn.execute('UPDATE events SET report = ? WHERE id = ?', (report, event_id))
            conn.commit()
        log.info('AI report saved for event #%d', event_id)
    except Exception as e:
        log.error('AI analysis failed for event #%d: %s', event_id, e)


def detection_worker(cam_id):
    """Per-camera detection worker thread."""
    det_frame_count = 0
    warn_hold = 0
    fq = frame_queues[cam_id]
    dl = detection_locks[cam_id]
    ld = latest_detections[cam_id]

    while alive.is_set():
        try:
            frame = fq.get(timeout=1)
        except queue.Empty:
            continue

        det_frame_count += 1

        # Fall detection model (less frequent)
        if model_fd is not None and det_frame_count % 15 == 0:
            _run_fall_detection(frame, ld)

        # Pose detection + tracking + alerts
        if det_frame_count % DETECTION_INTERVAL == 0:
            tracks, max_p_fall, any_is_fall = _process_pose_and_tracking(
                frame, det_frame_count, cam_id, ld, dl)
            warn_hold = _handle_alerts(cam_id, max_p_fall, any_is_fall, tracks, frame, warn_hold)
        else:
            time.sleep(0.002)


def ground_hazard_worker(cam_id):
    """Worker thread for ground hazard detection."""
    frame_count = 0
    hazard_cooldown = {}
    last_frame = None

    while alive.is_set():
        if not camera_enabled.get(cam_id, False):
            time.sleep(1)
            continue

        fq = frame_queues.get(cam_id)
        if fq is None:
            time.sleep(0.5)
            continue

        try:
            frame = fq.get(timeout=0.5)
            last_frame = frame
        except queue.Empty:
            frame = last_frame

        if frame is None:
            continue

        frame_count += 1

        if frame_count % cfg.GROUND_HAZARD_INTERVAL == 0:
            with detection_locks[cam_id]:
                tracks = latest_detections[cam_id].get('tracks', {})

            hazards = process_ground_hazards(
                frame, model_ground, tracks, cam_id,
                broadcast_alert, hazard_cooldown)

            # Collision prediction
            collision_predictions = []
            if hazards and tracks:
                from core.collision_predictor import process_collision_prediction
                collision_predictions = process_collision_prediction(tracks, hazards, cam_id)

                # Alert on high collision probability
                for pred in collision_predictions:
                    if pred['probability'] >= cfg.COLLISION_HIGH_THRESHOLD:
                        broadcast_alert({
                            'type': 'red',
                            'level': 2,
                            'message': f'碰撞风险！{pred["person_name"]} 可能撞到 {pred["hazard_type"]}（{pred["probability"]}%）',
                            'hazard_type': pred['hazard_type'],
                            'person_nearby': pred['person_name'],
                            'collision_probability': pred['probability'],
                            'cam_id': cam_id,
                        })
                    elif pred['probability'] >= cfg.COLLISION_MEDIUM_THRESHOLD:
                        broadcast_alert({
                            'type': 'orange',
                            'level': 1,
                            'message': f'注意：{pred["person_name"]} 正在靠近 {pred["hazard_type"]}',
                            'hazard_type': pred['hazard_type'],
                            'person_nearby': pred['person_name'],
                            'collision_probability': pred['probability'],
                            'cam_id': cam_id,
                        })

            # Save video clip on high-risk alert
            if hazards:
                for h in hazards:
                    rl = h.get('_alert_risk_level', '')
                    if rl in ('high', 'medium'):
                        from core.video_buffer import VideoBuffer, generate_clip_path
                        vbuf = video_buffers.get(cam_id)
                        if vbuf:
                            clip_path = generate_clip_path(cam_id, 'hazard')
                            vbuf.save_clip_async(clip_path, before_sec=15, after_sec=5)
                        break

            with detection_locks[cam_id]:
                latest_detections[cam_id]['ground_hazards'] = hazards
                latest_detections[cam_id]['collision_predictions'] = collision_predictions

        time.sleep(0.03)
