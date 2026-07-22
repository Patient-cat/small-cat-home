"""Collision probability predictor — rule-based risk assessment for person-obstacle interaction."""
import math
import logging
from collections import defaultdict, deque

import config as cfg

log = logging.getLogger('safesight')

# Per-track position history for velocity calculation
_position_history = defaultdict(lambda: deque(maxlen=10))


def _distance(p1, p2):
    """Euclidean distance between two (x, y) points."""
    return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)


def _velocity(position_history):
    """Calculate velocity vector from position history.

    Returns:
        (speed, dx, dy) or (0, 0, 0) if insufficient data
    """
    if len(position_history) < 2:
        return 0, 0, 0

    p1 = position_history[-2]
    p2 = position_history[-1]
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    speed = math.sqrt(dx**2 + dy**2)
    return speed, dx, dy


def _angle_between(v1, v2):
    """Angle in degrees between two vectors."""
    dot = v1[0]*v2[0] + v1[1]*v2[1]
    mag1 = math.sqrt(v1[0]**2 + v1[1]**2)
    mag2 = math.sqrt(v2[0]**2 + v2[1]**2)
    if mag1 < 0.01 or mag2 < 0.01:
        return 90.0  # perpendicular if zero velocity
    cos_angle = max(-1, min(1, dot / (mag1 * mag2)))
    return math.degrees(math.acos(cos_angle))


def calculate_collision_probability(person_pos, person_velocity, obstacle_pos, obstacle_radius=30):
    """Calculate collision probability between a person and an obstacle.

    Args:
        person_pos: (x, y) pixel position of person center
        person_velocity: (vx, vy) velocity vector
        obstacle_pos: (x, y) pixel position of obstacle center
        obstacle_radius: approximate radius of obstacle in pixels

    Returns:
        dict with probability (0-100), risk_level, and breakdown
    """
    max_dist = cfg.COLLISION_MAX_DISTANCE  # pixels
    max_speed = cfg.COLLISION_MAX_SPEED   # px/frame

    # Step 1: Distance factor
    dist = _distance(person_pos, obstacle_pos)
    distance_factor = max(0, 1 - dist / max_dist)

    # Step 2: Direction alignment factor
    speed, vx, vy = person_velocity
    to_obstacle = (obstacle_pos[0] - person_pos[0], obstacle_pos[1] - person_pos[1])
    velocity_vec = (vx, vy)

    if speed > 0.5:  # only consider direction if moving
        angle = _angle_between(velocity_vec, to_obstacle)
        direction_factor = max(0, 1 - angle / 90.0)
    else:
        direction_factor = 0  # not moving = no collision risk

    # Step 3: Speed factor
    speed_factor = min(1, speed / max_speed)

    # Step 4: Proximity bonus (very close = higher risk regardless of direction)
    proximity_bonus = max(0, 1 - dist / 100) * 0.3

    # Step 5: Combined probability
    probability = min(100, int(
        (distance_factor * 0.4 + direction_factor * 0.3 + speed_factor * 0.2 + proximity_bonus) * 100
    ))

    # Risk level
    if probability >= 70:
        risk_level = 'high'
    elif probability >= 40:
        risk_level = 'medium'
    elif probability >= 20:
        risk_level = 'low'
    else:
        risk_level = 'safe'

    return {
        'probability': probability,
        'risk_level': risk_level,
        'distance': round(dist, 1),
        'speed': round(speed, 1),
        'angle': round(_angle_between(velocity_vec, to_obstacle), 1) if speed > 0.5 else 90,
        'distance_factor': round(distance_factor, 3),
        'direction_factor': round(direction_factor, 3),
        'speed_factor': round(speed_factor, 3),
    }


def process_collision_prediction(tracks, hazards, cam_id):
    """Run collision prediction for all tracked persons against all hazards.

    Args:
        tracks: dict of {track_id: track_data} from detection worker
        hazards: list of hazard dicts from ground hazard detection
        cam_id: camera ID

    Returns:
        list of collision predictions with risk levels
    """
    predictions = []

    for tid, t in tracks.items():
        # Get person center from bbox
        bx1, by1, bx2, by2 = t['bbox']
        person_center = ((bx1 + bx2) // 2, (by1 + by2) // 2)

        # Update position history
        pos_key = f"{cam_id}_{tid}"
        _position_history[pos_key].append(person_center)
        velocity = _velocity(_position_history[pos_key])

        for hazard in hazards:
            hazard_center = hazard['center']

            result = calculate_collision_probability(
                person_center, velocity, hazard_center
            )

            if result['probability'] >= 20:  # only report non-trivial risks
                predictions.append({
                    'person_id': tid,
                    'person_name': t.get('name', '陌生人'),
                    'hazard_type': hazard['name'],
                    'hazard_center': hazard_center,
                    'person_center': person_center,
                    'cam_id': cam_id,
                    **result
                })

    return predictions
