# -*- mode: python ; coding: utf-8 -*-
# SafeSight PyInstaller spec — produces dist/safesight/
# Build: pyinstaller safesight.spec

import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# Collect insightface model data
insightface_datas = []
try:
    import insightface
    insight_dir = os.path.dirname(insightface.__file__)
    # Collect buffalo_sc or buffalo_l models if present
    for model_name in ['buffalo_sc', 'buffalo_l', 'buffalo_s']:
        model_path = os.path.join(insight_dir, 'model_zoo', model_name)
        if os.path.isdir(model_path):
            insightface_datas.append((model_path, os.path.join('insightface', 'model_zoo', model_name)))
    # Also check user home .insightface
    home_insight = os.path.join(os.path.expanduser('~'), '.insightface', 'models')
    if os.path.isdir(home_insight):
        for model_name in ['buffalo_sc', 'buffalo_l', 'buffalo_s']:
            model_path = os.path.join(home_insight, model_name)
            if os.path.isdir(model_path):
                insightface_datas.append((model_path, os.path.join('insightface', 'models', model_name)))
except ImportError:
    pass

a = Analysis(
    ['desktop.py'],  # entry point
    pathex=[],
    binaries=[],
    datas=[
        # Templates
        ('templates/*.html', 'templates'),
        # Static assets
        ('static/logo.png', 'static'),
        ('static/test_video.mp4', 'static'),
        ('static/test_video_20260614_233940.mp4', 'static'),
        ('static/falls', 'static/falls'),
        ('static/uploads', 'static/uploads'),
        # Model files (relative to project root)
        ('yolov8m-pose.pt', '.'),
        ('fall_detect.pt', '.'),
        # Config
        ('.env.example', '.'),
    ] + insightface_datas,
    hiddenimports=[
        # Flask ecosystem
        'flask', 'flask_cors', 'flask_socketio',
        'engineio', 'engineio.async_drivers.threading',
        'socketio', 'socketio.server', 'socketio.namespace',
        'werkzeug', 'werkzeug.security', 'werkzeug.debug',
        # Jinja2
        'jinja2', 'jinja2.ext',
        # CV / ML
        'cv2', 'cv2.gapi',
        'numpy', 'numpy.core', 'numpy.linalg',
        'PIL', 'PIL.Image',
        'ultralytics', 'ultralytics.nn', 'ultralytics.engine',
        'torch', 'torchvision',
        'insightface', 'insightface.model_zoo', 'insightface.app',
        'insightface.data', 'insightface.utils',
        'onnxruntime', 'onnxruntime.capi',
        # ONVIF
        'onvif', 'wsdiscovery',
        # Utilities
        'requests', 'dotenv', 'webview', 'webview.platforms.winforms',
        # Python stdlib (explicit for safety)
        'logging', 'logging.handlers', 'queue', 'threading',
        'concurrent.futures', 'collections', 'base64', 'secrets',
        'signal', 'urllib.parse', 'math', 'json', 'time', 'datetime',
        'hashlib', 'hmac', 'sqlite3', 'os', 'sys', 'io',
        'contextlib', 'functools', 'itertools',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'scipy', 'pandas',
        'notebook', 'jupyter', 'IPython',
        'PyQt5', 'PySide2', 'PySide6', 'wx',
        'kivy', 'toga',
        'test', 'unittest', 'pytest',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='safesight',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['torch_cpu.dll', 'torch_python.dll', 'onnxruntime.dll', 'opencv_world*.dll'],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='x86_64',
    codesign_identity=None,
    entitlements_file=None,
    icon='static/logo.png',
)
