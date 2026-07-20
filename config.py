"""All configuration constants for the fall detection system."""
import os
import logging

# ============================================================
# Environment / .env
# ============================================================
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.isfile(env_path):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        pass

_load_dotenv()

# ============================================================
# Paths
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAMERA_CONFIG_FILE = os.path.join(BASE_DIR, 'cameras.json')
DB_PATH = os.path.join(BASE_DIR, 'faces.db')
STATIC_FALLS_DIR = os.path.join(BASE_DIR, 'static', 'falls')
STATIC_UPLOADS_DIR = os.path.join(BASE_DIR, 'static', 'uploads')
MODEL_POSE = 'yolov8m-pose.pt'
MODEL_FALL_DETECT = 'fall_detect.pt'

# ============================================================
# Fall Detection Thresholds
# ============================================================
YELLOW_THRESHOLD = 0.50    # possible fall → yellow warning
RED_THRESHOLD = 0.60       # confirmed fall → red alert
FALL_CONSECUTIVE_FRAMES = 2     # consecutive frames to confirm red alert
YELLOW_HOLD_FRAMES = 5          # hold yellow warning for N frames
FALL_COOLDOWN_SECONDS = 5       # minimum gap between red alerts

# ============================================================
# Fall Feature Weights (multi-feature fusion scoring)
# ============================================================
FEATURE_WEIGHT_ANGLE = 0.35     # torso tilt angle
FEATURE_WEIGHT_VELOCITY = 0.25  # vertical velocity of hip
FEATURE_WEIGHT_AR = 0.20        # aspect ratio (width/height)
FEATURE_WEIGHT_ACCEL = 0.12     # angular acceleration
FEATURE_WEIGHT_HF = 0.08        # head-foot Y distance
FEATURE_WEIGHT_GROUND = 0.05    # ground contact persistence
FEATURE_WEIGHT_FD = 0.10        # fall detection model bonus

# ============================================================
# Detection Timing
# ============================================================
DETECTION_INTERVAL = 3          # run YOLO every N frames
FACE_RECOGNITION_INTERVAL = 30  # run face rec every N frames
FACE_NAME_HOLD_FRAMES = 90      # hold name for N frames after last match
AUTO_LEARN_INTERVAL = 90        # frames between auto-learn embeddings

# ============================================================
# Face Recognition
# ============================================================
FACE_SIMILARITY_THRESHOLD = 0.35
FACE_DET_SCORE_THRESHOLD = 0.20
FACE_AUTO_LEARN_THRESHOLD = 0.55
INSIGHTFACE_CTX_ID = -1

# ============================================================
# Tracking
# ============================================================
TRACK_MAX_LOST = 30             # frames before removing lost track
IOU_MATCH_MIN = 0.3             # minimum IoU to match detection to track

# ============================================================
# Video / Streaming
# ============================================================
YOLO_IMGSZ = 640        # YOLO inference size (higher = more accurate, slower)
JPEG_QUALITY = 85       # MJPEG stream quality (60→85 much sharper)
FRAME_QUEUE_SIZE = 30
CAMERA_WIDTH = 960      # Camera capture width
CAMERA_HEIGHT = 540     # Camera capture height

# ============================================================
# Cleanup
# ============================================================
EVENT_RETENTION_DAYS = 7
CLEANUP_INTERVAL_SECONDS = 21600  # every 6 hours
FALL_SCREENSHOT_RETENTION_DAYS = 30

# ============================================================
# AI Analysis (DeepSeek / Gemini)
# ============================================================
AI_PROVIDER = os.getenv('AI_PROVIDER', 'deepseek')
AI_API_KEY = os.getenv('AI_API_KEY', '')
AI_MODEL = os.getenv('AI_MODEL', 'deepseek-chat')
AI_TIMEOUT = int(os.getenv('AI_TIMEOUT', '10'))
AI_ENABLED = bool(AI_API_KEY)

AI_ENDPOINTS = {
    'deepseek': 'https://api.deepseek.com/v1/chat/completions',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
}

def get_endpoint():
    base = AI_ENDPOINTS.get(AI_PROVIDER, AI_ENDPOINTS['deepseek'])
    if AI_PROVIDER == 'gemini':
        return base.format(model=AI_MODEL) + '?key=' + AI_API_KEY
    return base

# ============================================================
# Server
# ============================================================
SERVER_HOST = '0.0.0.0'
SERVER_PORT = 5001
DEBUG = False

# ============================================================
# Ground Hazard Detection
# ============================================================
GROUND_HAZARD_INTERVAL = 30     # run detection every N frames
GROUND_HAZARD_CONF = 0.25       # confidence threshold for object detection
GROUND_HAZARD_CLOSE = 120       # pixels — very close, orange alert
GROUND_HAZARD_NEAR = 250        # pixels — nearby, yellow alert
GROUND_HAZARD_COOLDOWN = 30     # seconds between repeated alerts
GROUND_HAZARD_TARGET_CLASSES = [24, 25, 26, 28, 39, 41, 45, 46, 47, 73, 76]
# COCO classes: backpack, umbrella, handbag, suitcase, bottle, cup,
#               knife, fork, spoon, book, scissors

# ============================================================
# Ground Hazard Risk Levels
# ============================================================
HAZARD_RISK_LEVELS = {
    'high':   {'color': (0, 0, 255),    'label': '高危', 'alert': True},
    'medium': {'color': (0, 165, 255),  'label': '中危', 'alert': True},
    'low':    {'color': (0, 255, 255),  'label': '低危', 'alert': True},
    'ignore': {'color': (128, 128, 128), 'label': '忽略', 'alert': False},
}

# Default risk level for each COCO class
HAZARD_CLASS_LEVELS = {
    # high risk — tripping hazards
    'backpack': 'medium', 'handbag': 'medium', 'suitcase': 'medium',
    # medium risk
    'umbrella': 'medium',
    # low risk — small objects
    'bottle': 'low', 'cup': 'low', 'book': 'low',
    'knife': 'medium', 'fork': 'low', 'spoon': 'low', 'scissors': 'medium',
}

# ============================================================
# Fall Detection Tuning
# ============================================================
FALL_DECAY_FACTOR = 0.85        # exponential decay for lost keypoints
FALL_STAGGER_DELAY = 0.1        # seconds stagger per camera at startup (0.3→0.1)
FALL_RECONNECT_DELAY = 0.2      # seconds between reconnect attempts (0.3→0.2)
FALL_FD_CONF_THRESHOLD = 0.3    # fall detection model confidence threshold
FALL_FD_IMGSZ = 320             # fall detection model input size

# ============================================================
# AI Analysis
# ============================================================
AI_MAX_TOKENS = 800
AI_TEMPERATURE = 0.3

# ============================================================
# Logging
# ============================================================
LOG_DIR = os.path.join(BASE_DIR, 'logs')
LOG_LEVEL = logging.INFO
LOG_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
