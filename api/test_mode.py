"""Test mode routes — upload and playback test videos."""
import os
import time
import threading
import logging
import cv2
import numpy as np
from flask import Blueprint, render_template, request, jsonify, Response
from api.auth import login_required
from core.state import (
    camera_enabled, test_video_path, test_paused, test_eof,
    test_last_frame, test_saved_cam_states, alive, DEVICE,
)
import config as cfg

log = logging.getLogger('safesight')

test_bp = Blueprint('test', __name__)
test_video_lock = threading.Lock()


@test_bp.route('/test', methods=['GET', 'POST'])
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
        from datetime import datetime
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        ext = os.path.splitext(vf.filename)[1].lower()
        if ext not in ('.mp4', '.avi', '.mov', '.mkv', '.webm'):
            return jsonify({'ok': False, 'error': '格式不支持'}), 400
        save_path = os.path.join(cfg.STATIC_UPLOADS_DIR, f'test_video_{ts}{ext}')
        vf.save(save_path)
        # Disable live cameras
        for cid in list(camera_enabled.keys()):
            test_saved_cam_states[cid] = camera_enabled[cid]
            camera_enabled[cid] = False
        with test_video_lock:
            test_video_path = save_path
        return jsonify({'ok': True, 'message': '视频已上传，开始播放'})
    return render_template('test.html')


@test_bp.route('/test/reset')
@login_required
def test_reset():
    global test_video_path, test_paused, test_eof, test_last_frame
    with test_video_lock:
        test_video_path = None
        test_paused = False
        test_eof = False
        test_last_frame = None
    for cid, state in test_saved_cam_states.items():
        camera_enabled[cid] = state
    test_saved_cam_states.clear()
    return jsonify({'ok': True, 'message': '测试已重置'})


@test_bp.route('/test/pause', methods=['POST'])
@login_required
def test_pause():
    global test_paused
    test_paused = not test_paused
    return jsonify({'ok': True, 'paused': test_paused})


@test_bp.route('/test/state')
@login_required
def test_state():
    return jsonify({'paused': test_paused, 'eof': test_eof, 'path': test_video_path})


@test_bp.route('/test_feed')
@login_required
def test_feed():
    """MJPEG stream for uploaded test video."""
    from ultralytics import YOLO
    show_overlay = request.args.get('overlay', '0') == '1'
    from core.state import model as pose_model, model_fd

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
            if not cap.isOpened():
                blank = np.zeros((360, 480, 3), dtype=np.uint8)
                cv2.putText(blank, '视频打开失败', (80, 190),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                _, buf = cv2.imencode('.jpg', blank)
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
                break

            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            delay = 1.0 / fps
            det_count = 0
            tracks = {}
            from core.tracking import all_persons, _match_or_create_tracks
            from core.state import tracker_lock

            while alive.is_set():
                if test_paused:
                    if test_last_frame is not None:
                        _, buf = cv2.imencode('.jpg', test_last_frame, [cv2.IMWRITE_JPEG_QUALITY, cfg.JPEG_QUALITY])
                        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
                    time.sleep(0.05)
                    continue

                ok, frame = cap.read()
                if not ok:
                    test_eof = True
                    break
                test_last_frame = frame.copy()

                if show_overlay and pose_model is not None:
                    det_count += 1
                    if det_count % cfg.DETECTION_INTERVAL == 0:
                        results = pose_model(frame, imgsz=cfg.YOLO_IMGSZ, conf=0.5, verbose=False, device=DEVICE)
                        detections = all_persons(results[0])
                        with tracker_lock:
                            tracks = _match_or_create_tracks(detections, det_count, 'test')

                    from core.draw import draw_tracking_overlay, draw_hud
                    draw_tracking_overlay(frame, tracks)
                    draw_hud(frame, 0)

                _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, cfg.JPEG_QUALITY])
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
                time.sleep(delay)

            cap.release()
            if test_eof:
                blank = np.zeros((360, 480, 3), dtype=np.uint8)
                cv2.putText(blank, '播放结束', (120, 190),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (128, 128, 128), 2)
                _, buf = cv2.imencode('.jpg', blank)
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
                time.sleep(1)

    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')
