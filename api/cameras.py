"""Camera management API routes."""
import logging
from flask import Blueprint, request, jsonify
from api.auth import login_required

log = logging.getLogger('safesight')

cameras_bp = Blueprint('cameras', __name__)


@cameras_bp.route('/api/cameras', methods=['GET'])
@login_required
def api_cameras_list():
    """List all cameras."""
    from core.state import CAMERAS, camera_names, camera_enabled, current_fps_list
    return jsonify([{
        'id': int(c['id']),
        'source': c['source'],
        'name': camera_names.get(str(c['id']), f'摄像头{c["id"]+1}'),
        'enabled': bool(camera_enabled.get(c['id'], False)),
        'fps': float(current_fps_list.get(c['id'], 0)),
    } for c in CAMERAS])


@cameras_bp.route('/api/cameras', methods=['POST'])
@login_required
def api_cameras_add():
    """Add a camera (single or batch)."""
    from core.state import CAMERAS, camera_names
    from core.cam_config import _save_camera_config, _init_camera_pipeline
    from core.state import config_lock

    data = request.get_json(force=True) or {}

    # Batch add
    if 'cameras' in data and isinstance(data['cameras'], list):
        added = []
        used_ids = {c['id'] for c in CAMERAS}
        for cam_data in data['cameras']:
            source = cam_data.get('source')
            if source is None:
                continue
            new_id = 0
            while new_id in used_ids:
                new_id += 1
            used_ids.add(new_id)
            with config_lock:
                CAMERAS.append({'id': new_id, 'source': source})
                if cam_data.get('name'):
                    camera_names[str(new_id)] = cam_data['name'].strip()
            _init_camera_pipeline(new_id)
            added.append({'id': new_id, 'source': source})
        _save_camera_config(CAMERAS, camera_names)
        return jsonify({'ok': True, 'message': f'已添加 {len(added)} 个摄像头', 'added': added})

    # Single add
    source = data.get('source')
    if source is None:
        return jsonify({'ok': False, 'error': 'source is required (int for USB, str for RTSP)'}), 400
    used_ids = {c['id'] for c in CAMERAS}
    new_id = 0
    while new_id in used_ids:
        new_id += 1
    with config_lock:
        CAMERAS.append({'id': new_id, 'source': source})
        if data.get('name'):
            camera_names[str(new_id)] = data['name'].strip()
    _init_camera_pipeline(new_id)
    _save_camera_config(CAMERAS, camera_names)
    return jsonify({'ok': True, 'message': f'摄像头 {new_id} 已添加', 'id': new_id})


@cameras_bp.route('/api/cameras', methods=['DELETE'])
@login_required
def api_cameras_delete():
    """Delete a camera by ID."""
    from core.state import CAMERAS, camera_names
    from core.cam_config import _save_camera_config
    from core.state import config_lock

    cam_id = request.args.get('id', type=int)
    if cam_id is None:
        return jsonify({'ok': False, 'error': '?id= required'}), 400
    idx = next((i for i, c in enumerate(CAMERAS) if c['id'] == cam_id), None)
    if idx is None:
        return jsonify({'ok': False, 'error': '摄像头不存在'}), 404
    with config_lock:
        CAMERAS.pop(idx)
        camera_names.pop(str(cam_id), None)
    _save_camera_config(CAMERAS, camera_names)
    return jsonify({'ok': True, 'message': f'摄像头 {cam_id} 已删除，重启后生效'})


@cameras_bp.route('/api/camera/<int:cam_id>/toggle', methods=['POST'])
@login_required
def api_toggle_camera(cam_id):
    """Toggle camera on/off."""
    from core.state import camera_enabled
    if cam_id not in camera_enabled:
        return jsonify({'ok': False, 'error': '摄像头不存在'}), 404
    camera_enabled[cam_id] = not camera_enabled[cam_id]
    return jsonify({'ok': True, 'enabled': camera_enabled[cam_id]})


@cameras_bp.route('/api/cameras/disable-all', methods=['POST'])
@login_required
def api_disable_all_cameras():
    """Disable all cameras."""
    from core.state import camera_enabled
    count = sum(1 for v in camera_enabled.values() if v)
    for cid in camera_enabled:
        camera_enabled[cid] = False
    return jsonify({'ok': True, 'disabled': count})


@cameras_bp.route('/api/camera/<int:cam_id>/rename', methods=['POST'])
@login_required
def api_rename_camera(cam_id):
    """Rename a camera."""
    from core.state import CAMERAS, camera_names
    from core.cam_config import _save_camera_config
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name is required'}), 400
    camera_names[str(cam_id)] = name
    _save_camera_config(CAMERAS, camera_names)
    return jsonify({'ok': True, 'name': name})


@cameras_bp.route('/api/cameras/<int:cam_id>/roi', methods=['GET', 'POST'])
@login_required
def api_camera_roi(cam_id):
    """Get or set walking region ROI for a camera."""
    from core.state import CAMERAS
    from core.cam_config import _save_camera_config

    if request.method == 'GET':
        roi = next((c.get('walk_roi', []) for c in CAMERAS if c['id'] == cam_id), [])
        return jsonify({'ok': True, 'roi': roi})
    else:
        data = request.get_json()
        roi = data.get('roi', [])
        for c in CAMERAS:
            if c['id'] == cam_id:
                c['walk_roi'] = roi
                break
        _save_camera_config(CAMERAS, {})
        return jsonify({'ok': True})


@cameras_bp.route('/api/cameras/scan-usb', methods=['POST'])
@login_required
def api_cameras_scan_usb():
    """Scan for available USB/built-in cameras and auto-add them."""
    from core.state import CAMERAS, camera_names
    from core.cam_config import _save_camera_config, _init_camera_pipeline, _scan_usb_cameras
    from core.state import config_lock

    usb_indices = _scan_usb_cameras(max_index=5)
    existing_sources = {c['source'] for c in CAMERAS if isinstance(c['source'], int)}
    new_id = max([c.get('id', -1) for c in CAMERAS] + [-1]) + 1
    added = []
    for idx in usb_indices:
        if idx not in existing_sources:
            name = f'USB摄像头-{idx}'
            with config_lock:
                CAMERAS.append({'id': new_id, 'source': idx, 'name': name})
                camera_names[str(new_id)] = name
            _init_camera_pipeline(new_id)
            added.append({'id': new_id, 'source': idx, 'name': name})
            new_id += 1
    if added:
        _save_camera_config(CAMERAS, camera_names)
    return jsonify({'ok': True, 'added': added, 'found': len(usb_indices)})


@cameras_bp.route('/api/cameras/scan', methods=['POST'])
@login_required
def api_cameras_scan():
    """Auto-discover ONVIF cameras on the local network."""
    results = []
    DEFAULT_CREDS = [
        ('admin', 'admin12345'), ('admin', 'admin'), ('admin', '12345'),
        ('admin', 'Hik12345'), ('admin', 'hikvision'), ('admin', 'password'),
    ]
    NVR_KEYWORDS = ['hikvision', 'hik', 'nvr', 'dvr', 'ds-76', 'ds-77', 'ds-96']

    def _is_nvr(mfr, model):
        text = f'{mfr or ""} {model or ""}'.lower()
        return any(kw in text for kw in NVR_KEYWORDS)

    try:
        from onvif import ONVIFCamera
        from wsdiscovery import WSDiscovery
        wsd = WSDiscovery()
        wsd.start()
        services = wsd.searchServices(timeout=5)
        wsd.stop()

        for svc in services:
            xaddrs = svc.getXAddrs()
            if not xaddrs:
                continue
            ip = xaddrs[0].split('//')[1].split(':')[0].split('/')[0]
            found_cred = False
            for user, pwd in DEFAULT_CREDS:
                try:
                    cam = ONVIFCamera(ip, 80, user, pwd)
                    dev = cam.devicemgmt
                    mfr, model = dev.GetManufacturer(), dev.GetModel()
                    nvr = _is_nvr(mfr, model)
                    media = cam.create_media_service()
                    profiles = media.GetProfiles()
                    channels = []
                    first_rtsp = None
                    for idx, profile in enumerate(profiles):
                        try:
                            uri = media.GetStreamUri({
                                'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}},
                                'ProfileToken': profile.token
                            })
                            url = uri.Uri.replace('rtsp://', f'rtsp://{user}:{pwd}@')
                            channels.append({'token': profile.token, 'url': url, 'channel_hint': idx + 1})
                            if first_rtsp is None:
                                first_rtsp = url
                        except (IOError, RuntimeError) as e:
                            log.debug('ONVIF stream URI failed for %s: %s', ip, e)
                            continue
                    suggested = len(channels) if nvr and len(channels) > 1 else (4 if nvr else 1)
                    results.append({
                        'ip': ip, 'manufacturer': mfr, 'model': model,
                        'rtsp': first_rtsp, 'user': user, 'password': pwd,
                        'found_cred': True, 'is_nvr': nvr,
                        'suggested_channels': suggested, 'channels': channels,
                    })
                    found_cred = True
                    break
                except (IOError, RuntimeError, OSError) as e:
                    log.debug('ONVIF connection failed for %s with %s/%s: %s', ip, user, pwd, e)
                    continue
            if not found_cred:
                results.append({
                    'ip': ip, 'manufacturer': 'Unknown (need password)',
                    'model': '', 'rtsp': None, 'user': 'admin',
                    'password': '', 'found_cred': False,
                    'is_nvr': False, 'suggested_channels': 1, 'channels': [],
                })
    except ImportError:
        return jsonify({'ok': False, 'error': 'ONVIF库未安装，请在终端执行: pip install onvif-zeep WSDiscovery'}), 500
    except Exception as e:
        log.warning('ONVIF scan error: %s', e)
        return jsonify({'ok': True, 'results': results, 'note': str(e)})

    return jsonify({'ok': True, 'results': results})
