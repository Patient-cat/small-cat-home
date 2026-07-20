"""Multi-signal risk scoring — combines gait, hazard, approach velocity, and history."""
import time
import logging
from collections import defaultdict

import config as cfg

log = logging.getLogger('safesight')

# In-memory store for per-camera, per-person risk state
_risk_state = {}  # key: (cam_id, person_name) -> {gait_samples, last_update}


def calculate_risk_score(gait_score=0, hazard_score=0, approach_score=0, history_score=0):
    """
    Weighted risk score (0-100).

    Weights:
    - gait:     30% — step speed decline, trunk sway increase
    - hazard:   25% — distance × risk_level of nearby obstacles
    - approach: 25% — approaching velocity toward hazard
    - history:  20% — historical hazard frequency in this area

    Returns: int 0-100
    """
    weights = [0.30, 0.25, 0.25, 0.20]
    scores = [gait_score, hazard_score, approach_score, history_score]
    return min(100, max(0, int(sum(w * s for w, s in zip(weights, scores)))))


def get_risk_color(score):
    """Map risk score to RGB color tuple for drawing."""
    if score >= 70:
        return (0, 0, 255)      # red
    elif score >= 50:
        return (0, 165, 255)    # orange
    elif score >= 30:
        return (0, 255, 255)    # yellow
    else:
        return (0, 255, 0)      # green


def get_risk_label(score):
    """Map risk score to label string."""
    if score >= 70:
        return '高风险'
    elif score >= 50:
        return '中风险'
    elif score >= 30:
        return '低风险'
    else:
        return '正常'


def calculate_hazard_score(dist, risk_level):
    """Score 0-100 based on obstacle distance and risk level."""
    risk_weights = {'high': 1.0, 'medium': 0.7, 'low': 0.4, 'ignore': 0}
    distance_factor = max(0, 1 - dist / cfg.GROUND_HAZARD_NEAR)
    return int(risk_weights.get(risk_level, 0.5) * distance_factor * 100)


def calculate_approach_score(dist_history):
    """Score 0-100 based on how fast person is approaching a hazard.

    dist_history: deque of recent distance measurements (newest last).
    Higher score = faster approach.
    """
    if len(dist_history) < 3:
        return 0
    velocity = dist_history[-1] - dist_history[0]  # negative = approaching
    if velocity >= 0:
        return 0  # moving away or static
    # Map velocity to 0-100 (faster approach = higher score)
    # -50px/frame is very fast approach, -5 is slow
    speed = abs(velocity)
    return min(100, int(speed * 2))


def calculate_history_score(event_count, days=7):
    """Score 0-100 based on historical hazard frequency in the area."""
    if days <= 0:
        return 0
    events_per_day = event_count / days
    # More than 5 events/day = high risk
    return min(100, int(events_per_day * 20))
