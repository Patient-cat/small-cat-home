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
YELLOW_THRESHOLD = 0.65    # possible fall → yellow warning
RED_THRESHOLD = 0.85       # confirmed fall → red alert
FALL_CONSECUTIVE_FRAMES = 3     # consecutive frames to confirm red alert
YELLOW_HOLD_FRAMES = 10         # hold yellow warning for N frames
FALL_COOLDOWN_SECONDS = 10      # minimum gap between red alerts

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
FACE_SIMILARITY_THRESHOLD = 0.50
FACE_DET_SCORE_THRESHOLD = 0.30
FACE_AUTO_LEARN_THRESHOLD = 0.70
INSIGHTFACE_CTX_ID = -1

# ============================================================
# Tracking
# ============================================================
TRACK_MAX_LOST = 30             # frames before removing lost track
IOU_MATCH_MIN = 0.3             # minimum IoU to match detection to track

# ============================================================
# Video / Streaming
# ============================================================
YOLO_IMGSZ = 416
JPEG_QUALITY = 60
FRAME_QUEUE_SIZE = 2
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480

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
# Logging
# ============================================================
LOG_DIR = os.path.join(BASE_DIR, 'logs')
LOG_LEVEL = logging.INFO
LOG_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
