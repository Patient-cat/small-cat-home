"""Fall detection logic — probabilistic scoring with multi-feature fusion."""
import math
import config as cfg


def _sigmoid(x):
    """Numerically stable sigmoid."""
    if x >= 0:
        z = math.exp(-x)
        return 1 / (1 + z)
    z = math.exp(x)
    return z / (1 + z)


def check_fall(kp_xy, kp_conf, hip_hist, angle_hist,
               fd_fall_conf=0.0, ground_contact_frames=0):
    """Evaluate fall probability from pose keypoints + history.

    Returns (is_fall: bool, info: dict | None).
    info is None when keypoints are too sparse to evaluate.
    """
    def k(idx):
        """Return (x, y) or None if keypoint confidence too low."""
        if kp_conf[idx] < 0.3:
            return None
        return kp_xy[idx]

    # Torso angle: vector from mid-shoulder (5+6)/2 to mid-hip (11+12)/2
    ls, rs = k(5), k(6)
    lh, rh = k(11), k(12)
    if not (ls and rs and lh and rh):
        return False, None

    mid_shoulder = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
    mid_hip = ((lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2)
    dx = mid_hip[0] - mid_shoulder[0]
    dy = mid_hip[1] - mid_shoulder[1]
    angle = math.degrees(math.atan2(abs(dx), abs(dy) + 1e-6))  # 0=upright, 90=horizontal

    # Vertical velocity of hip center
    velocity = 0.0
    if len(hip_hist) >= 2:
        prev = hip_hist[-2]
        velocity = mid_hip[1] - prev[1]  # positive = moving down

    # Aspect ratio of bounding box around keypoints
    all_kps = [kp_xy[i] for i in range(17) if kp_conf[i] > 0.3]
    if len(all_kps) < 5:
        return False, None
    xs = [p[0] for p in all_kps]
    ys = [p[1] for p in all_kps]
    bw = max(xs) - min(xs)
    bh = max(ys) - min(ys)
    ar = bw / (bh + 1e-6)  # wide & short → high AR → likely fallen

    # Angular acceleration
    angle_accel = 0.0
    if len(angle_hist) >= 2:
        angle_accel = angle - angle_hist[-2]

    # Head–foot Y distance (small when curled up / fallen)
    head_y = None
    for idx in [0, 1, 2, 3, 4]:  # nose, eyes, ears
        if kp_conf[idx] > 0.3:
            head_y = kp_xy[idx][1]
            break
    foot_y = None
    for idx in [15, 16]:  # ankles
        if kp_conf[idx] > 0.3:
            foot_y = max(foot_y or 0, kp_xy[idx][1])
    hf_diff = (foot_y - head_y) if (head_y is not None and foot_y is not None) else 200

    # ---- Probabilistic scoring ----
    p_angle = _sigmoid((angle - 50) / 8)           # angle > 50° → high
    p_vel = _sigmoid((velocity - 15) / 5)           # fast downward
    p_ar = _sigmoid((ar - 1.2) / 0.3)               # wide aspect ratio
    p_accel = _sigmoid((abs(angle_accel) - 10) / 5) # sudden rotation
    p_hf = _sigmoid((100 - hf_diff) / 20)           # small head-foot gap

    # Ground contact persistence bonus
    p_ground = _sigmoid((ground_contact_frames - 5) / 3)

    # Fall detection model bonus
    p_fd = min(fd_fall_conf, 1.0) if fd_fall_conf > 0 else 0.0

    p_raw = (p_angle * cfg.FEATURE_WEIGHT_ANGLE +
             p_vel * cfg.FEATURE_WEIGHT_VELOCITY +
             p_ar * cfg.FEATURE_WEIGHT_AR +
             p_accel * cfg.FEATURE_WEIGHT_ACCEL +
             p_hf * cfg.FEATURE_WEIGHT_HF +
             p_ground * cfg.FEATURE_WEIGHT_GROUND +
             p_fd * cfg.FEATURE_WEIGHT_FD)

    p_fall = _sigmoid((p_raw - 0.5) / 0.15)

    is_fall = p_fall >= cfg.RED_THRESHOLD

    return is_fall, {
        'angle': round(angle, 1),
        'velocity': round(velocity, 1),
        'ar': round(ar, 2),
        'angle_accel': round(angle_accel, 1),
        'hf_diff': round(hf_diff, 1),
        'p_fall': round(p_fall, 3),
        'p_angle': round(p_angle, 2),
        'p_vel': round(p_vel, 2),
        'p_ar': round(p_ar, 2),
        'p_accel': round(p_accel, 2),
        'p_hf': round(p_hf, 2),
        'p_ground': round(p_ground, 2),
        'p_fd': round(p_fd, 2),
    }
