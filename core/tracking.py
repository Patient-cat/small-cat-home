"""Multi-person IoU-based tracker."""
import numpy as np
import config as cfg


def _iou(boxA, boxB):
    """Compute Intersection over Union for two (x1,y1,x2,y2) boxes."""
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    inter = max(0, xB - xA) * max(0, yB - yA)
    if inter == 0:
        return 0.0
    areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
    return inter / (areaA + areaB - inter + 1e-6)


def _get_tracker_state(cam_key):
    """Return (next_id, tracks_dict) for a camera."""
    from app import tracker_states
    if cam_key not in tracker_states:
        tracker_states[cam_key] = {'next_id': 0, 'tracks': {}}
    return tracker_states[cam_key]


def _match_or_create_tracks(detections, frame_no, cam_key):
    """Match detections to existing tracks via IoU, or create new tracks.

    Args:
        detections: list of dicts with keys 'bbox', 'kp', 'kp_conf'.
        frame_no: current frame number.
        cam_key: camera identifier string.

    Returns:
        dict of {track_id: track_data}
    """
    state = _get_tracker_state(cam_key)
    tracks = state['tracks']

    # Build IoU cost matrix
    det_boxes = [d['bbox'] for d in detections]
    track_ids = list(tracks.keys())
    track_boxes = [tracks[tid]['bbox'] for tid in track_ids]

    matched_det = set()
    matched_trk = set()

    if det_boxes and track_boxes:
        iou_matrix = np.zeros((len(det_boxes), len(track_boxes)))
        for i, db in enumerate(det_boxes):
            for j, tb in enumerate(track_boxes):
                iou_matrix[i, j] = _iou(db, tb)

        # Greedy matching (highest IoU first)
        while True:
            if iou_matrix.size == 0:
                break
            max_iou = iou_matrix.max()
            if max_iou < cfg.IOU_MATCH_MIN:
                break
            idx = np.unravel_index(iou_matrix.argmax(), iou_matrix.shape)
            di, ti = idx[0], idx[1]
            tid = track_ids[ti]
            det = detections[di]

            # Update existing track
            tracks[tid]['bbox'] = det['bbox']
            tracks[tid]['kp'] = det['kp']
            tracks[tid]['kp_conf'] = det['kp_conf']
            tracks[tid]['lost'] = 0

            # Append to hip history
            hip = det['kp'][11:13]  # left hip, right hip
            if hip is not None and len(hip) == 2:
                hip_center = ((hip[0][0] + hip[1][0]) / 2, (hip[0][1] + hip[1][1]) / 2)
                tracks[tid].setdefault('hip_history', []).append(hip_center)
                tracks[tid]['hip_history'] = tracks[tid]['hip_history'][-30:]

            # Append to angle history
            kp, kp_conf = det['kp'], det['kp_conf']
            ls, rs = kp[5], kp[6]
            lh, rh = kp[11], kp[12]
            if all(c > 0.3 for c in [kp_conf[5], kp_conf[6], kp_conf[11], kp_conf[12]]):
                import math
                ms = ((ls[0]+rs[0])/2, (ls[1]+rs[1])/2)
                mh = ((lh[0]+rh[0])/2, (lh[1]+rh[1])/2)
                dx, dy = mh[0]-ms[0], mh[1]-ms[1]
                angle = math.degrees(math.atan2(abs(dx), abs(dy)+1e-6))
                tracks[tid].setdefault('angle_history', []).append(angle)
                tracks[tid]['angle_history'] = tracks[tid]['angle_history'][-30:]

            matched_det.add(di)
            matched_trk.add(ti)

            # Zero out matched row/column
            iou_matrix[di, :] = 0
            iou_matrix[:, ti] = 0

    # Create new tracks for unmatched detections
    for i, det in enumerate(detections):
        if i in matched_det:
            continue
        tid = state['next_id']
        state['next_id'] += 1
        color = _track_color(tid)
        tracks[tid] = {
            'bbox': det['bbox'],
            'kp': det['kp'],
            'kp_conf': det['kp_conf'],
            'lost': 0,
            'name': None,
            'color': color,
            'fall_counter': 0,
            'last_p_fall': 0.0,
            'ground_contact_frames': 0,
            'hip_history': [],
            'angle_history': [],
            'fd_fall_conf': 0.0,
        }

    # Increment lost counter for unmatched tracks, remove stale ones
    stale = []
    for i, tid in enumerate(track_ids):
        if i not in matched_trk:
            tracks[tid]['lost'] += 1
            if tracks[tid]['lost'] > cfg.TRACK_MAX_LOST:
                stale.append(tid)
    for tid in stale:
        del tracks[tid]

    return tracks


def _track_color(track_id):
    """Generate a consistent color for a track ID."""
    palette = [
        (46, 204, 113), (52, 152, 219), (155, 89, 182),
        (241, 196, 15), (230, 126, 34), (231, 76, 60),
        (26, 188, 156), (192, 57, 43), (142, 68, 173),
        (44, 62, 80), (127, 140, 141), (211, 84, 0),
    ]
    return palette[track_id % len(palette)]


def all_persons(result):
    """Extract person detections from YOLO pose result.

    Returns:
        list of dicts: [{bbox, kp, kp_conf}, ...]
    """
    persons = []
    if result.keypoints is None or result.boxes is None:
        return persons

    kp_xy = result.keypoints.xy.cpu().numpy()
    kp_conf = result.keypoints.conf.cpu().numpy()
    boxes = result.boxes.xyxy.cpu().numpy()

    for i in range(len(boxes)):
        cls = int(result.boxes.cls[i]) if result.boxes.cls is not None else 0
        if cls != 0:  # class 0 = person
            continue
        persons.append({
            'bbox': tuple(boxes[i].astype(int)),
            'kp': kp_xy[i],
            'kp_conf': kp_conf[i],
        })
    return persons
