"""EZVIZ (萤石) Open Platform integration — token management, device access, alarm push."""
import time
import logging
import requests

import config as cfg

log = logging.getLogger('safesight')

_token_cache = {'token': None, 'expires_at': 0}


def get_access_token(force_refresh=False):
    """Get EZVIZ access token with caching (valid ~7 days).

    Returns cached token if still valid, otherwise refreshes from API.
    """
    now = time.time()

    # Use env token if available and not forcing refresh
    if cfg.EZVIZ_ACCESS_TOKEN and not force_refresh:
        return cfg.EZVIZ_ACCESS_TOKEN

    # Use cached token
    if _token_cache['token'] and now < _token_cache['expires_at'] and not force_refresh:
        return _token_cache['token']

    if not cfg.EZVIZ_ENABLED:
        log.warning('EZVIZ not configured (missing APP_KEY or APP_SECRET)')
        return None

    try:
        url = f'{cfg.EZVIZ_BASE_URL}/api/lapp/token/get'
        resp = requests.post(url, data={
            'appKey': cfg.EZVIZ_APP_KEY,
            'appSecret': cfg.EZVIZ_APP_SECRET,
        }, timeout=10)
        data = resp.json()

        if data.get('code') == 200:
            token_data = data['data']
            _token_cache['token'] = token_data['accessToken']
            _token_cache['expires_at'] = now + token_data.get('expireTime', 604800) - 300  # 5min early
            log.info('EZVIZ token refreshed, expires in %ds', token_data.get('expireTime', 0))
            return _token_cache['token']
        else:
            log.error('EZVIZ token error: %s', data.get('msg', 'unknown'))
            return None
    except Exception as e:
        log.error('EZVIZ token request failed: %s', e)
        return None


def _api_post(endpoint, extra_data=None):
    """Helper for EZVIZ API POST requests with auto token."""
    token = get_access_token()
    if not token:
        return {'ok': False, 'error': 'EZVIZ token unavailable'}

    url = f'{cfg.EZVIZ_BASE_URL}{endpoint}'
    data = {'accessToken': token}
    if extra_data:
        data.update(extra_data)

    try:
        resp = requests.post(url, data=data, timeout=10)
        result = resp.json()
        if result.get('code') == 200:
            return {'ok': True, 'data': result.get('data')}
        else:
            return {'ok': False, 'error': result.get('msg', 'API error')}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


# ============================================================
# Device Management
# ============================================================
def get_device_list():
    """Get all devices bound to the EZVIZ account."""
    return _api_post('/api/lapp/device/list', {'pageStart': 0, 'pageSize': 50})


def get_device_info(device_serial):
    """Get info for a specific device."""
    return _api_post('/api/lapp/device/info', {'deviceSerial': device_serial})


# ============================================================
# Video Streaming
# ============================================================
def get_live_url(device_serial, channel_no=1, protocol=2, quality=1):
    """Get live stream URL for a device.

    Args:
        device_serial: EZVIZ device serial number
        channel_no: channel number (default 1)
        protocol: 1=RTMP, 2=HLS, 3=FLV
        quality: 1=HD, 2=SD

    Returns:
        dict with 'ok' and 'url' or 'error'
    """
    return _api_post('/api/lapp/v2/live/address/get', {
        'deviceSerial': device_serial,
        'channelNo': channel_no,
        'protocol': protocol,
        'quality': quality,
    })


def get_playback_url(device_serial, channel_no=1, start_time='', end_time=''):
    """Get playback URL for recorded video."""
    return _api_post('/api/lapp/v2/api/video/address/get', {
        'deviceSerial': device_serial,
        'channelNo': channel_no,
        'startTime': start_time,
        'endTime': end_time,
    })


# ============================================================
# Alarm Push (SafeSight → EZVIZ)
# ============================================================
def push_alarm(device_serial, alarm_type, alarm_msg, channel_no=1):
    """Push an alarm event to EZVIZ platform.

    Args:
        device_serial: device to associate alarm with
        alarm_type: alarm type code (1=移动侦测, 2=遮挡报警, etc.)
        alarm_msg: alarm description text
        channel_no: channel number

    Returns:
        dict with 'ok' status
    """
    return _api_post('/api/lapp/alarm/device/send', {
        'deviceSerial': device_serial,
        'channelNo': channel_no,
        'alarmType': alarm_type,
        'alarmMessage': alarm_msg,
    })


def push_safesight_alert(alert_data, device_serial=None):
    """Push a SafeSight alert to EZVIZ platform.

    Maps SafeSight alert types to EZVIZ alarm types.
    """
    if not cfg.EZVIZ_ENABLED:
        return {'ok': False, 'error': 'EZVIZ not configured'}

    # Default to first available device if not specified
    if not device_serial:
        devices = get_device_list()
        if devices.get('ok') and devices['data']:
            device_serial = devices['data'][0].get('deviceSerial')
        else:
            return {'ok': False, 'error': 'No EZVIZ devices found'}

    msg = alert_data.get('message', 'SafeSight alert')
    alert_type = alert_data.get('type', 'yellow')

    # Map to EZVIZ alarm type
    ezviz_alarm_type = 1  # default: motion detection
    if alert_type == 'red':
        ezviz_alarm_type = 5  # emergency
    elif alert_type == 'orange':
        ezviz_alarm_type = 3  # intrusion
    elif alert_type == 'yellow':
        ezviz_alarm_type = 1  # motion

    return push_alarm(device_serial, ezviz_alarm_type, msg)


# ============================================================
# Screenshot Upload (SafeSight → EZVIZ Cloud)
# ============================================================
def upload_screenshot(image_path, device_serial=None):
    """Upload a screenshot to EZVIZ cloud storage."""
    if not cfg.EZVIZ_ENABLED:
        return {'ok': False, 'error': 'EZVIZ not configured'}

    token = get_access_token()
    if not token:
        return {'ok': False, 'error': 'Token unavailable'}

    if not device_serial:
        devices = get_device_list()
        if devices.get('ok') and devices['data']:
            device_serial = devices['data'][0].get('deviceSerial')

    try:
        url = f'{cfg.EZVIZ_BASE_URL}/api/lapp/alarm/manual/trigger'
        with open(image_path, 'rb') as f:
            resp = requests.post(url, data={
                'accessToken': token,
                'deviceSerial': device_serial,
            }, files={'file': f}, timeout=15)
        result = resp.json()
        return {'ok': result.get('code') == 200, 'data': result.get('data')}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
