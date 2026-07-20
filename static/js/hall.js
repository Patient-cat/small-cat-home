// ============================================================
// Audio Engine
// ============================================================
var audioCtx = null, audioReady = false;
function initAudio() {
  if (audioReady) return;
  try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); audioReady = true; }
  catch (e) {}
}
document.body.addEventListener('click', initAudio, { once: true });

var _alarmTimer = null;
function playAlarm() {
  if (_alarmTimer) clearInterval(_alarmTimer);
  _alarmTimer = setInterval(function () {
    if (!audioCtx || !audioReady) return;
    var t = audioCtx.currentTime;
    var notes = [780, 980, 580, 780];
    for (var r = 0; r < 3; r++) {
      var t0 = t + r * 0.8;
      notes.forEach(function (f) {
        var o = audioCtx.createOscillator(), g = audioCtx.createGain();
        o.connect(g); g.connect(audioCtx.destination);
        o.type = 'sawtooth';
        o.frequency.setValueAtTime(f, t0);
        g.gain.setValueAtTime(0.18, t0);
        g.gain.exponentialRampToValueAtTime(0.01, t0 + 0.12);
        o.start(t0); o.stop(t0 + 0.12);
        t0 += 0.18;
      });
    }
  }, 2500);
}
function stopAlarm() {
  if (_alarmTimer) { clearInterval(_alarmTimer); _alarmTimer = null; }
}

// ============================================================
// Alert Handlers
// ============================================================
var _dismissedAt = 0;

function showFallOverlay(data) {
  if (Date.now() - _dismissedAt < 10000) return;
  initAudio();
  var name = data.name || '陌生人';
  document.getElementById('fallTitle').textContent = '🚨 ' + name + ' 确认摔倒！';
  document.getElementById('fallDetail').textContent =
    '老人：' + name + ' ｜ 置信度：' + Math.round((data.confidence || 0) * 100) + '% ｜ ' + (data.timestamp || '');
  document.getElementById('fallModal').classList.add('active');
  document.getElementById('warnFlash').classList.add('danger');
  playAlarm();
}

function dismissFall() {
  document.getElementById('fallModal').classList.remove('active');
  document.getElementById('warnFlash').classList.remove('danger');
  stopAlarm();
  _dismissedAt = Date.now();
}

function showWarning(data) {
  var el = document.getElementById('warnFlash');
  el.classList.add('warn');
  // Short soft beep
  if (audioCtx && audioReady) {
    var o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.connect(g); g.connect(audioCtx.destination);
    o.type = 'sine';
    o.frequency.setValueAtTime(660, audioCtx.currentTime);
    g.gain.setValueAtTime(0.08, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.25);
    o.start(); o.stop(audioCtx.currentTime + 0.25);
  }
  // Auto-dismiss after 3 seconds
  setTimeout(function () { el.classList.remove('warn'); }, 3000);
}

function showHazardAlert(data) {
  // Create hazard alert notification
  var alertDiv = document.createElement('div');
  alertDiv.className = 'hazard-alert';
  alertDiv.style.cssText = 'position:fixed;top:80px;right:20px;z-index:1000;background:rgba(17,24,32,0.95);border:1px solid #f59e0b;border-radius:12px;padding:16px 20px;backdrop-filter:blur(12px);box-shadow:0 8px 32px rgba(0,0,0,0.4);animation:slideIn 0.3s ease;max-width:350px;';

  alertDiv.innerHTML = '<div style="display:flex;align-items:center;gap:12px;">' +
    '<div style="font-size:24px;">⚠️</div>' +
    '<div>' +
    '<div style="font-weight:600;color:#f59e0b;margin-bottom:4px;">地面障碍物</div>' +
    '<div style="font-size:13px;color:#c8d2dc;">' + (data.message || '') + '</div>' +
    '<div style="font-size:11px;color:#60738a;margin-top:4px;">摄像头 ' + ((data.cam_id || 0) + 1) +
    (data.person_nearby ? ' · ' + data.person_nearby + ' 在附近' : '') + '</div>' +
    '</div></div>';

  document.body.appendChild(alertDiv);

  // Auto-remove after 5 seconds
  setTimeout(function() {
    alertDiv.style.opacity = '0';
    alertDiv.style.transform = 'translateX(100px)';
    alertDiv.style.transition = 'all 0.3s ease';
    setTimeout(function() { alertDiv.remove(); }, 300);
  }, 5000);
}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' || e.key === 'Enter') dismissFall();
});

// ============================================================
// SSE — Server-Sent Events for alerts
// ============================================================
function connectSSE() {
  var es = new EventSource('/events');
  es.onmessage = function (e) {
    if (!e.data) return;
    try {
      var d = JSON.parse(e.data);
      if (d.type === 'red' || d.type === 'fall' || d.level === 2) showFallOverlay(d);
      else if (d.type === 'yellow' || d.type === 'warning' || d.level === 1) {
        if (d.hazard_type) {
          showHazardAlert(d);
        } else {
          showWarning(d);
        }
      }
    } catch (_) {}
  };
  es.onerror = function () { es.close(); setTimeout(connectSSE, 3000); };
}
connectSSE();

// ============================================================
// Camera Grid State
// ============================================================
var cameras = [];
var _lastIds = '';
var _testActive = false;
var _testChanged = true;
var _showAll = false;
var _lastShowAll = false;
var _showOverlay = localStorage.getItem('showOverlay') !== '0';

document.addEventListener('DOMContentLoaded', function () {
  applyOverlay();
});

function toggleOverlay() {
  _showOverlay = !_showOverlay;
  localStorage.setItem('showOverlay', _showOverlay ? '1' : '0');
  applyOverlay();
  _testChanged = true;
  render();
}

function applyOverlay() {
  var btn = document.getElementById('btnOverlay');
  if (_showOverlay) {
    btn.textContent = '✓ 检测框已显示';
    btn.className = 'btn active';
  } else {
    btn.textContent = '显示检测框';
    btn.className = 'btn';
  }
}

function logout(){
  fetch('/api/logout', {method:'POST'}).then(function(){
    window.location.href='/login';
  });
}

var _retryCount = 0;
function loadAll() {
  fetch('/api/cameras').then(function (r) { return r.json(); }).then(function (list) {
    _retryCount = 0;
    cameras = list || [];
    render();
  }).catch(function () {
    _retryCount++;
    if (_retryCount < 10) setTimeout(loadAll, 1000);
    else document.getElementById('videoGrid').innerHTML =
      '<div class="empty-state"><div class="icon">⚠️</div><div class="title">无法连接服务器</div><div class="sub">请检查服务是否启动</div></div>';
  });
}

function render() {
  var ids = cameras.map(function (c) { return c.id + '|' + c.enabled; }).join(',');
  if (ids === _lastIds && !_testChanged && _showAll === _lastShowAll) {
    updateStats();
    return;
  }
  _lastIds = ids;
  _lastShowAll = _showAll;
  _testChanged = false;

  var visible = _showAll ? cameras : cameras.filter(function (c) { return c.enabled; });
  var grid = document.getElementById('videoGrid');
  var total = visible.length + 1;
  var cols = total <= 1 ? 1 : total <= 4 ? 2 : total <= 9 ? 3 : 4;
  grid.style.gridTemplateColumns = 'repeat(' + cols + ', 1fr)';

  var enabledCount = cameras.filter(function (c) { return c.enabled; }).length;
  document.getElementById('statusInfo').textContent =
    (_showAll ? '全部 ' : '已开启 ') + enabledCount + '/' + cameras.length + ' 路';

  // Build cards
  var html = '';

  // Test slot
  html += '<div class="cam-card test-slot' + (_testActive ? '' : ' disabled') + '" id="box-test">';
  if (_testActive) {
    html += '<img src="/test_feed' + (_showOverlay ? '?overlay=1' : '') + '" alt="模拟测试">';
  }
  html += '<div class="cam-bar"><span class="cam-name" style="color:#f59e0b;">🧪 模拟测试</span></div>';
  html += '<div class="cam-controls">';
  html += '<button class="cam-ctrl-btn" onclick="toggleFS(\'box-test\')" title="全屏">⛶</button>';
  html += '</div>';
  html += '<div class="test-actions">';
  html += '<button class="cam-ctrl-btn" onclick="uploadTestVideo()">' + (_testActive ? '更换' : '上传') + '</button>';
  if (_testActive) {
    html += '<button class="cam-ctrl-btn stop-btn" onclick="stopTest()">停止</button>';
  }
  html += '</div>';
  if (!_testActive) {
    html += '<div class="cam-placeholder"><div class="icon">🎬</div>上传视频模拟跌倒测试</div>';
  }
  html += '</div>';

  // Camera cards
  html += visible.map(function (c) {
    var on = c.enabled;
    var overlayUrl = _showOverlay ? '/debug' : '';
    return '<div class="cam-card' + (on ? '' : ' disabled') + '" id="box-' + c.id + '">' +
      (on ? '<img id="img-' + c.id + '" src="/video_feed/' + c.id + overlayUrl + '" alt="">' : '') +
      '<div class="cam-bar">' +
      '<span class="cam-name">' + esc(c.name) + '</span>' +
      '<span class="cam-stats"><span id="fps-' + c.id + '"></span></span>' +
      '</div>' +
      '<div class="cam-controls">' +
      '<button class="cam-ctrl-btn' + (on ? ' active' : '') + '" onclick="toggleCam(' + c.id + ')" title="' + (on ? '暂停' : '开启') + '">' + (on ? '⏸' : '▶') + '</button>' +
      '<button class="cam-ctrl-btn" onclick="toggleFS(\'box-' + c.id + '\')" title="全屏">⛶</button>' +
      '</div>' +
      (on ? '' : '<div class="cam-placeholder"><div class="icon">📷</div>已关闭 — 点击 ▶ 开启</div>') +
      '</div>';
  }).join('');

  grid.innerHTML = html;
}

function updateStats() {
  cameras.forEach(function (c) {
    if (!c.enabled) return;
    var el = document.getElementById('fps-' + c.id);
    if (!el) return;
    var pf = c.fps > 0 ? (c.p_fall || 0) : 0;
    var cls = pf >= 0.75 ? 'danger' : pf >= 0.55 ? 'warn' : 'live';
    var dot = '<span class="pulse ' + cls + '"></span>';
    el.innerHTML = dot + 'FPS:' + Math.round(c.fps) + ' P:' + pf.toFixed(2);
  });
}

// ============================================================
// Camera Toggle
// ============================================================
function toggleCam(id) {
  var btn = document.querySelector('#box-' + id + ' .cam-ctrl-btn');
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  fetch('/api/camera/' + id + '/toggle', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (d) {
    if (!d.ok) return;
    var c = cameras.find(function (c) { return c.id === id; });
    if (c) c.enabled = d.enabled;
    render();
  }).catch(function () {}).finally(function () {
    if (btn) btn.disabled = false;
  });
}

function disableAllCams() {
  if (!confirm('确定要停止所有摄像头监控吗？')) return;
  fetch('/api/cameras/disable-all', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (d) {
    if (!d.ok) return;
    cameras.forEach(function (c) { c.enabled = false; });
    render();
  });
}

// ============================================================
// Test Video
// ============================================================
function uploadTestVideo() { document.getElementById('testFileInput').click(); }
function doUploadTest() {
  var file = document.getElementById('testFileInput').files[0];
  if (!file) return;
  var fd = new FormData(); fd.append('video', file);
  fetch('/test', { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(function (d) {
    if (d.ok) { _testActive = true; _testChanged = true; render(); }
    else alert('上传失败: ' + (d.error || ''));
  });
}
function stopTest() {
  fetch('/test/reset').then(function (r) { return r.json(); }).then(function (d) {
    _testActive = false; _testChanged = true; render();
  });
}

// ============================================================
// Show All Toggle
// ============================================================
function toggleShowAll() {
  _showAll = !_showAll;
  _testChanged = true;
  var btn = document.getElementById('btnShowAll');
  btn.textContent = _showAll ? '仅已开启' : '显示全部';
  if (_showAll) { btn.className = 'btn accent'; }
  else { btn.className = 'btn'; }
  render();
}

// ============================================================
// Fullscreen
// ============================================================
function toggleFS(id) {
  var el = document.getElementById(id);
  if (!el) return;
  document.querySelectorAll('.cam-card.fullscreen').forEach(function (other) {
    if (other.id !== id) other.classList.remove('fullscreen');
  });
  el.classList.toggle('fullscreen');
}
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') {
    document.querySelectorAll('.cam-card.fullscreen').forEach(function (el) {
      el.classList.remove('fullscreen');
    });
  }
});

// ============================================================
// Helpers
// ============================================================
function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

// ============================================================
// Init
// ============================================================
function pollStats() {
  fetch('/api/cameras').then(function (r) { return r.json(); }).then(function (list) {
    if (!list) return;
    cameras = list;
    updateStats();
  }).catch(function () {});
}
loadAll();
setInterval(pollStats, 3000);
</script>
</body>
