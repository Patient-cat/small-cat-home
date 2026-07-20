"""One-time script to fix imports from app -> core.state."""
import os

BASE = os.path.dirname(os.path.abspath(__file__))

replacements = {
    'api/cameras.py': [
        ('from app import CAMERAS, camera_names, camera_enabled, current_fps_list',
         'from core.state import CAMERAS, camera_names, camera_enabled, current_fps_list'),
        ('from app import CAMERAS, camera_names, _save_camera_config, _init_camera_pipeline',
         'from core.state import CAMERAS, camera_names\nfrom core.cam_config import _save_camera_config, _init_camera_pipeline'),
        ('from app import CAMERAS, camera_names, _save_camera_config',
         'from core.state import CAMERAS, camera_names\nfrom core.cam_config import _save_camera_config'),
        ('from app import camera_enabled',
         'from core.state import camera_enabled'),
        ('from app import camera_names, CAMERAS, _save_camera_config',
         'from core.state import CAMERAS, camera_names\nfrom core.cam_config import _save_camera_config'),
        ('from app import CAMERAS, _save_camera_config',
         'from core.state import CAMERAS\nfrom core.cam_config import _save_camera_config'),
        ('from app import CAMERAS, camera_names, _scan_usb_cameras, _save_camera_config, _init_camera_pipeline',
         'from core.state import CAMERAS, camera_names\nfrom core.cam_config import _save_camera_config, _init_camera_pipeline, _scan_usb_cameras'),
        ('from models.database import config_lock',
         'from core.state import config_lock'),
    ],
    'api/auth.py': [
        ('from app import _setup_complete',
         'from core.state import _setup_complete'),
    ],
    'api/pages.py': [
        ('from app import face_app',
         'from core.state import face_app'),
    ],
    'api/system.py': [
        ('from app import CAMERAS, camera_names, camera_enabled, current_fps_list, person_count_list, last_p_fall_list',
         'from core.state import CAMERAS, camera_names, camera_enabled, current_fps_list, person_count_list, last_p_fall_list'),
        ('from app import ai_toggle',
         'from core.state import ai_toggle'),
        ('import app as app_module',
         'from core.state import ai_toggle'),
        ('from app import fall_queue',
         'from core.state import fall_queue'),
    ],
    'core/tracking.py': [
        ('from app import tracker_states',
         'from core.state import tracker_states'),
    ],
    'core/face_recognition.py': [
        ('from app import face_app',
         'from core.state import face_app'),
    ],
}

for filepath, pairs in replacements.items():
    full = os.path.join(BASE, filepath)
    with open(full, 'r', encoding='utf-8') as f:
        content = f.read()
    changed = False
    for old, new in pairs:
        if old in content:
            content = content.replace(old, new, 1)
            print(f'{filepath}: replaced "{old[:60]}..."')
            changed = True
    if changed:
        with open(full, 'w', encoding='utf-8') as f:
            f.write(content)

print('Done')
