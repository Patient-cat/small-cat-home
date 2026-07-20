"""Shared application state — thread-safe access to global variables."""
import queue
import threading

# ============================================================
# Alert queue (SSE + WebSocket)
# ============================================================
fall_queue = queue.Queue(maxsize=10)

# ============================================================
# Camera state
# ============================================================
CAMERAS = []                    # list of {id, source, name}
camera_names = {}               # {str(id): name}
camera_enabled = {}             # {cam_id: bool}
camera_caps = {}                # {cam_id: cv2.VideoCapture}
frame_queues = {}               # {cam_id: Queue}
latest_detections = {}          # {cam_id: dict}
detection_locks = {}            # {cam_id: Lock}
current_fps_list = {}           # {cam_id: float}
person_count_list = {}          # {cam_id: int}
last_p_fall_list = {}           # {cam_id: float}
video_buffers = {}              # {cam_id: VideoBuffer}

config_lock = threading.Lock()
state_lock = threading.Lock()
tracker_lock = threading.Lock()
tracker_states = {}             # {cam_key: {next_id, tracks}}

# ============================================================
# Detection state
# ============================================================
last_fall_time = 0.0
recognized_name = '陌生人'
alive = threading.Event()
alive.set()

# ============================================================
# AI toggle
# ============================================================
ai_toggle = True

# ============================================================
# Test mode state
# ============================================================
test_video_path = None
test_paused = False
test_eof = False
test_last_frame = None
test_saved_cam_states = {}

# ============================================================
# Models (loaded at startup, shared across threads)
# ============================================================
model = None                    # YOLOv8-pose
model_fd = None                 # Fall detection model
model_ground = None             # YOLOv8n for ground hazards
face_app = None                 # InsightFace
DEVICE = 'cpu'
