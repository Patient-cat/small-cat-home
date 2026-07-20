"""Video streaming routes — MJPEG feeds and frame capture."""
import time
import queue
import logging
import cv2
import numpy as np
from flask import Blueprint, Response, request
from api.auth import login_required
from core.state import (
    CAMERAS, camera_names, camera_enabled, camera_caps,
    frame_queues, latest_detections, detection_locks,
    current_fps_list, alive, test_video_path, test_paused,
    test_eof, test_last_frame, test_saved_cam_states, DEVICE,
)
import config as cfg

log = logging.getLogger('safesight')

streaming_bp = Blueprint('streaming', __name__)


def open_camera(source):
    cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(source)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.CAMERA_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def open_rtsp_camera(source, cam_id):
    """Open RTSP camera with timeout and staggered delay."""
    import time as _time
    _time.sleep(cam_id * cfg.FALL_STAGGER_DELAY)
    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp|timeout;5000000'
    cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FPS, 60)
    for prop, val in [(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000),
                      (cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)]:
        try:
            cap.set(prop, val)
        except cv2.error:
            pass
    if not cap.isOpened():
        log.warning('RTSP camera %d failed to open: %s', cam_id, source)
        cap.release()
        return None
    return cap


def generate_frames(cam_id, show_overlay=True):
    """MJPEG stream generator for a specific camera."""
    import os
    from core.draw import draw_tracking_overlay, draw_fall_boxes, draw_ground_hazards, draw_hud

    fq = frame_queues[cam_id]
    dl = detection_locks[cam_id]
    ld = latest_detections[cam_id]
    fc_start = time.time()
    fc_count = 0
    fail_count = 0
    cam = None

    while alive.is_set():
        # Disabled camera
        if not camera_enabled.get(cam_id, False):
            if cam is not None:
                cam.release()
                cam = None
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
            success = False
            frame = None

        if not success:
            fail_count += 1
            if fail_count > 30:
                if cam is not None:
                    log.warning('Camera cam_id=%d reconnecting after %d failures', cam_id, fail_count)
                    cam.release()
                    cam = None
                fail_count = 0
            cam_name = camera_names.get(str(cam_id), f'摄像头{cam_id+1}')
            blank = np.zeros((360, 480, 3), dtype=np.uint8)
            cv2.putText(blank, f'{cam_name} 连接中...', (60, 190),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (128, 160, 200), 1)
            cv2.putText(blank, f'尝试 {fail_count}/30', (100, 220),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 120, 140), 1)
            _, buf = cv2.imencode('.jpg', blank, [cv2.IMWRITE_JPEG_QUALITY, 30])
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
            time.sleep(cfg.FALL_RECONNECT_DELAY)
            continue
        fail_count = 0

        # Downscale 4K
        h, w = frame.shape[:2]
        if w > 1920:
            scale = 1920 / w
            frame = cv2.resize(frame, (1920, int(h * scale)))

        fc_count += 1
        now = time.time()
        elapsed = now - fc_start
        if elapsed >= 1.0:
            current_fps_list[cam_id] = round(fc_count / elapsed, 1)
            fc_count = 0
            fc_start = now

        # Feed detection worker
        if fq.full():
            try:
                fq.get_nowait()
            except queue.Empty:
                pass
        fq.put(frame.copy())

        # Draw overlays
        if show_overlay:
            with dl:
                tracks = ld.get('tracks', {})
            draw_tracking_overlay(frame, tracks)
            draw_fall_boxes(frame, ld.get('fd_boxes', []))
            draw_ground_hazards(frame, ld.get('ground_hazards', []))
            draw_hud(frame, cam_id)

        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, cfg.JPEG_QUALITY])
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')


@streaming_bp.route('/video_feed')
@login_required
def video_feed():
    return Response(generate_frames(0, show_overlay=False),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@streaming_bp.route('/video_feed/<int:cam_id>')
@login_required
def video_feed_cam(cam_id):
    if cam_id < 0 or cam_id >= len(CAMERAS):
        return 'Camera not found', 404
    return Response(generate_frames(cam_id, show_overlay=False),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@streaming_bp.route('/video_feed/<int:cam_id>/debug')
@login_required
def video_feed_cam_debug(cam_id):
    if cam_id < 0 or cam_id >= len(CAMERAS):
        return 'Camera not found', 404
    return Response(generate_frames(cam_id, show_overlay=True),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@streaming_bp.route('/capture_frame')
@login_required
def capture_frame():
    """Capture a single JPEG frame. ?cam_id=N (default 0)."""
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
