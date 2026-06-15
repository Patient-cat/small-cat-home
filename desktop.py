"""
SafeSight Desktop Launcher
PyWebView native window + Flask backend + system tray.
"""
import os
import sys
import time
import threading
import logging

# Signal desktop mode to config.py BEFORE importing app modules
os.environ['SAFESIGHT_DESKTOP'] = '1'

log = logging.getLogger('desktop')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

# Ensure working dir is the script's directory (handles PyInstaller bundles)
if getattr(sys, 'frozen', False):
    os.chdir(sys._MEIPASS)
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))


def start_flask():
    """Start the Flask+SocketIO server in a daemon thread."""
    from app import initialize_camera_pipelines, _setup_signal_handlers, socketio, app as flask_app
    import config as cfg
    initialize_camera_pipelines()
    _setup_signal_handlers()
    socketio.run(flask_app, host=cfg.SERVER_HOST, port=cfg.SERVER_PORT,
                 debug=False, allow_unsafe_werkzeug=True)


def wait_for_server(url, timeout=30):
    """Poll /api/health until the server is ready."""
    import requests
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                log.info('Server ready at %s', url)
                return True
        except Exception:
            pass
        time.sleep(0.5)
    log.error('Server did not start within %ds', timeout)
    return False


class DesktopAPI:
    """JS <-> Python bridge exposed to the webview window."""

    def get_app_path(self):
        return os.path.dirname(os.path.abspath(sys.argv[0]))

    def get_version(self):
        return '2.0.0'

    def quit_app(self):
        log.info('Quit requested from JS')
        os._exit(0)


def _shutdown():
    """Release cameras and CUDA before exit."""
    log.info('Shutting down desktop app...')
    try:
        from app import alive, camera_caps
        alive.clear()
        for cap in list(camera_caps.values()):
            try:
                cap.release()
            except Exception:
                pass
        camera_caps.clear()
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    os._exit(0)


def main():
    server_url = 'http://127.0.0.1:5001'

    # Start Flask in background thread
    t = threading.Thread(target=start_flask, daemon=True, name='flask-server')
    t.start()

    if not wait_for_server(server_url + '/api/health'):
        print('ERROR: Server failed to start. Check logs for details.')
        sys.exit(1)

    # Open native window
    try:
        import webview
    except ImportError:
        print('pywebview not installed. Run: pip install pywebview')
        print('Falling back to browser — open ' + server_url)
        try:
            import webbrowser
            webbrowser.open(server_url)
        except Exception:
            pass
        # Keep running until interrupted
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            _shutdown()
        return

    api = DesktopAPI()

    # Build tray icon path
    ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'logo.png')
    if not os.path.isfile(ico_path):
        ico_path = None

    window = webview.create_window(
        title='SafeSight 跌倒监测系统',
        url=server_url,
        width=1280,
        height=800,
        min_size=(960, 640),
        resizable=True,
        js_api=api,
    )

    log.info('Desktop window opened at %s', server_url)
    webview.start(debug=False, private_mode=False)
    _shutdown()


if __name__ == '__main__':
    main()
