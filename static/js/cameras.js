let cameraOnMap = {};
let addMode = 'usb';
let scanResults = [];
let nvrConfigs = {};
let addingInProgress = false;

// ============================================================
// Camera list
// ============================================================
async function loadList() {
    const resp = await fetch('/api/cameras');
    const cams = await resp.json();
    document.getElementById('camList').innerHTML = cams.map(c => {
        if (cameraOnMap[c.id] === undefined) cameraOnMap[c.id] = c.enabled !== false;
        const on = cameraOnMap[c.id];
        const fps = c.fps || 0;
        const statusText = fps > 0 ? `运行中 (${fps}FPS)` : (on ? '等待连接' : '已关闭');
        const badgeClass = fps > 0 ? 'on' : (on ? '' : 'off');
        const badgeStyle = badgeClass ? `cam-badge ${badgeClass}` : 'cam-badge';
        return `<div class="cam-row" onclick="toggleCam(${c.id})">
            <div>
                <div class="name">${esc(c.name)} <span class="${badgeStyle}">${statusText}</span></div>
                <div class="src">${c.source}</div>
            </div>
            <div style="display:flex;gap:8px;">
                <button class="btn-roi" onclick="event.stopPropagation();openROIConfig(${c.id})" style="padding:5px 12px;font-size:12px;border:1px solid #4fc3f7;background:transparent;color:#4fc3f7;border-radius:12px;cursor:pointer;">配置区域</button>
                ${cams.length>1?`<button class="btn-delete" onclick="event.stopPropagation();deleteCam(${c.id})">删除</button>`:''}
            </div>
        </div>`;
    }).join('');
    document.getElementById('addSource').value = cams.length;
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function toggleCam(id) {
    try {
        const resp = await fetch(`/api/camera/${id}/toggle`, { method: 'POST' });
        const d = await resp.json();
        cameraOnMap[id] = d.enabled;
    } catch(e) {}
    loadList();
}

function setMode(mode) {
    addMode = mode;
    document.getElementById('modeUSB').classList.toggle('active', mode==='usb');
    document.getElementById('modeRTSP').classList.toggle('active', mode==='rtsp');
    const hint = document.getElementById('addHint');
    if (mode === 'usb') {
        document.getElementById('addSource').placeholder = '设备编号，如 0、1、2...';
        document.getElementById('addSource').value = '2';
        hint.textContent = '插入 USB 摄像头后，填下一个可用编号';
    } else {
        document.getElementById('addSource').placeholder = 'rtsp://admin:密码@IP:554/Streaming/Channels/101';
        document.getElementById('addSource').value = '';
        hint.innerHTML = '海康 NVR 格式: rtsp://admin:密码@IP:554/Streaming/Channels/101<br>通道1主码流=101, 子码流=102; 通道2=201... 或点击下方"扫描网络摄像头"自动发现';
    }
}

async function deleteCam(id) {
    if (!confirm('确定删除？需重启服务生效。')) return;
    const resp = await fetch(`/api/cameras?id=${id}`, { method:'DELETE' });
    const d = await resp.json();
    showToast(d.message, d.ok?'success':'error');
    if (d.ok) loadList();
}

async function addCamera() {
    let src = document.getElementById('addSource').value.trim();
    if (!src) return showToast('请填写设备来源', 'error');
    if (addMode === 'usb' && /^\d+$/.test(src)) src = parseInt(src);
    if (addMode === 'usb' && typeof src === 'number' && src < 0) return showToast('设备编号不能为负数', 'error');
    const resp = await fetch('/api/cameras', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({source:src})
    });
    const d = await resp.json();
    showToast(d.message, d.ok?'success':'error');
    if (d.ok) loadList();
}

// ============================================================
// USB scan
// ============================================================
async function scanUSB() {
    const btn = document.getElementById('btnScanUSB');
    const status = document.getElementById('scanStatus');
    btn.disabled = true;
    btn.textContent = '扫描中...';
    status.textContent = '';
    try {
        const resp = await fetch('/api/cameras/scan-usb', { method: 'POST' });
        const data = await resp.json();
        btn.textContent = '扫描本地摄像头';
        btn.disabled = false;
        if (data.ok && data.found > 0) {
            status.textContent = `发现 ${data.found} 个本地摄像头` +
                (data.added.length > 0 ? `，已添加 ${data.added.length} 个` : '（已全部在列表中）');
            if (data.added.length > 0) {
                loadList(); // refresh the camera table
            }
        } else if (data.ok) {
            status.textContent = '未发现本地摄像头';
        } else {
            status.textContent = data.error || '扫描失败';
        }
    } catch (e) {
        btn.textContent = '扫描本地摄像头';
        btn.disabled = false;
        status.textContent = '扫描出错: ' + e.message;
    }
}

// Network scan
// ============================================================
async function scanNetwork() {
    const btn = document.getElementById('btnScan');
    const status = document.getElementById('scanStatus');
    const resultsDiv = document.getElementById('scanResults');

    btn.disabled = true;
    btn.textContent = '扫描中...';
    status.textContent = '正在搜索本地网络中的 ONVIF 设备...';
    resultsDiv.style.display = 'none';

    try {
        const resp = await fetch('/api/cameras/scan', { method: 'POST' });
        const data = await resp.json();
        btn.disabled = false;
        btn.textContent = '扫描网络摄像头';

        if (!data.ok) {
            if (data.error && data.error.includes('not installed')) {
                status.innerHTML = '需要安装 ONVIF 支持库: <code>pip install onvif-zeep</code>';
            } else {
                status.textContent = data.error || '扫描失败';
            }
            return;
        }

        scanResults = data.results || [];
        const count = scanResults.length;

        if (count === 0) {
            status.textContent = '未发现 ONVIF 设备。请确认 NVR/摄像头与服务器在同一局域网，且已开启 ONVIF。';
            return;
        }

        status.textContent = '';
        document.getElementById('scanCount').textContent = count;
        resultsDiv.style.display = 'block';
        renderDeviceCards();

    } catch (err) {
        btn.disabled = false;
        btn.textContent = '扫描网络摄像头';
        status.textContent = '网络错误: ' + err.message;
    }
}

function renderDeviceCards() {
    const list = document.getElementById('deviceList');
    list.innerHTML = scanResults.map((d, idx) => renderDeviceCard(d, idx)).join('');
}

function renderDeviceCard(d, idx) {
    const mfr = d.manufacturer || 'Unknown';
    const model = d.model || '';
    const isNvr = d.is_nvr;
    const hasCred = d.found_cred;
    const ip = d.ip;
    const channels = d.channels || [];
    const suggestedCh = d.suggested_channels || 1;

    // Store nvr config defaults
    if (!nvrConfigs[ip]) {
        nvrConfigs[ip] = { channels: suggestedCh, streamType: 'main' };
    }
    const cfg = nvrConfigs[ip];

    let html = `<div class="device-card" id="dev-${idx}">
        <div class="device-header">
            <div class="info">
                <span class="device-ip">${esc(ip)}</span>`;

    if (hasCred && isNvr) {
        html += `<span class="badge-nvr">NVR</span>`;
    } else if (hasCred && !isNvr) {
        html += `<span class="badge-ipc">IPC</span>`;
    }

    html += `<div class="device-meta">${esc(mfr)}${model ? ' — ' + esc(model) : ''}</div>`;
    html += `</div></div>`;

    // Credential input for failed-login devices
    if (!hasCred) {
        html += `<div class="device-creds">
            <input type="text" id="cred-user-${idx}" placeholder="用户名" value="admin">
            <input type="password" id="cred-pass-${idx}" placeholder="密码">
            <button class="btn-try-login" onclick="tryLogin(${idx})">尝试登录</button>
        </div>
        <div id="cred-status-${idx}" style="font-size:12px;color:var(--muted);margin-bottom:8px;"></div>`;
        // Generate URLs with custom credentials
        html += renderNvrConfig(idx, ip, cfg, suggestedCh, true);
    } else {
        // Show scan-discovered channels or generate URLs
        html += renderNvrConfig(idx, ip, cfg, suggestedCh, false);
    }

    html += `</div>`;
    return html;
}

function renderNvrConfig(idx, ip, cfg, suggestedCh, isNvr) {
    let html = '';

    // Channel config for NVR or multi-channel
    html += `<div class="nvr-config">
        <label>通道数:</label>
        <input type="number" min="1" max="64" value="${cfg.channels}"
               onchange="updateNvrCfg('${ip}','channels',this.value)">
        <label>码流:</label>
        <select onchange="updateNvrCfg('${ip}','streamType',this.value)">
            <option value="main" ${cfg.streamType==='main'?'selected':''}>主码流</option>
            <option value="sub" ${cfg.streamType==='sub'?'selected':''}>子码流</option>
            <option value="both" ${cfg.streamType==='both'?'selected':''}>主+子</option>
        </select>
    </div>`;

    // URL preview area
    html += `<div class="url-list" id="urlList-${idx}"></div>`;

    // Actions
    html += `<div class="device-actions">
        <button class="btn-action secondary" onclick="previewUrls(${idx}, '${ip}')">预览 URL</button>
        <button class="btn-action secondary" onclick="testFirstUrl(${idx}, '${ip}')">测试连接</button>
        <button class="btn-action primary" onclick="addAllChannels(${idx}, '${ip}')">批量添加 (${cfg.channels}路)</button>
    </div>
    <div id="testStatus-${idx}" style="font-size:12px;margin-top:6px;min-height:18px;"></div>`;

    return html;
}

function updateNvrCfg(ip, key, value) {
    if (!nvrConfigs[ip]) nvrConfigs[ip] = { channels: 4, streamType: 'main' };
    if (key === 'channels') nvrConfigs[ip].channels = Math.max(1, Math.min(64, parseInt(value) || 1));
    else nvrConfigs[ip][key] = value;
}

// ============================================================
// URL generation & preview
// ============================================================
async function generateUrls(device, channels, streamType) {
    const user = device.user || 'admin';
    const password = device.password || '';
    const ip = device.ip;
    const payload = { ip, user, password, port: 554, channels, stream_type: streamType };

    try {
        const resp = await fetch('/api/cameras/generate_urls', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await resp.json();
        if (data.ok) return data.urls;
        showToast('生成 URL 失败: ' + (data.error || '未知错误'), 'error');
        return [];
    } catch (err) {
        showToast('请求失败: ' + err.message, 'error');
        return [];
    }
}

async function previewUrls(idx, ip) {
    const device = scanResults[idx];
    if (!device) return;
    const cfg = nvrConfigs[ip] || { channels: 4, streamType: 'main' };
    const urlList = document.getElementById('urlList-' + idx);

    // If already open, just close
    if (urlList.classList.contains('open')) {
        urlList.classList.remove('open');
        return;
    }

    urlList.innerHTML = '<div style="color:var(--muted);font-size:12px;">生成中...</div>';
    urlList.classList.add('open');

    const urls = await generateUrls(device, cfg.channels, cfg.streamType);
    if (urls.length === 0) {
        urlList.innerHTML = '<div style="color:var(--red);font-size:12px;">生成失败</div>';
        return;
    }

    urlList.innerHTML = urls.map((u, ui) => `
        <div class="url-item">
            <span class="ch-label">CH${u.channel} ${u.stream}</span>
            <span class="ch-url">${esc(u.url)}</span>
            <span class="ch-test" onclick="testSingleUrl('${esc(u.url).replace(/'/g,"\\'")}', this)" title="测试此路">测试</span>
        </div>
    `).join('');
}

async function testConnection(source, statusEl) {
    if (!statusEl) return;
    statusEl.innerHTML = '<span class="status-dot testing"></span> 测试中...';

    try {
        const t0 = Date.now();
        const resp = await fetch('/api/cameras/test', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source: source })
        });
        const data = await resp.json();
        const elapsed = Date.now() - t0;

        if (data.connected) {
            statusEl.innerHTML = `<span class="status-dot connected"></span> 连接成功 — ${data.resolution || '?'} — ${data.latency_ms || elapsed}ms`;
        } else {
            statusEl.innerHTML = `<span class="status-dot failed"></span> 连接失败: ${data.error || '超时'}`;
        }
    } catch (err) {
        statusEl.innerHTML = `<span class="status-dot failed"></span> 测试异常: ${err.message}`;
    }
}

function testSingleUrl(url, el) {
    const parent = el.parentElement;
    const statusSpan = document.createElement('span');
    statusSpan.style.fontSize = '10px';
    statusSpan.style.marginLeft = '6px';
    parent.appendChild(statusSpan);
    el.remove();
    testConnection(url, statusSpan);
}

async function testFirstUrl(idx, ip) {
    const device = scanResults[idx];
    if (!device) return;
    const cfg = nvrConfigs[ip] || { channels: 4, streamType: 'main' };
    const statusEl = document.getElementById('testStatus-' + idx);

    const urls = await generateUrls(device, Math.min(cfg.channels, 1), cfg.streamType);
    if (urls.length === 0) {
        statusEl.innerHTML = '<span class="status-dot failed"></span> 无法生成测试 URL';
        return;
    }
    await testConnection(urls[0].url, statusEl);
}

// ============================================================
// Batch add
// ============================================================
async function addAllChannels(idx, ip) {
    if (addingInProgress) return;
    const device = scanResults[idx];
    if (!device) return;
    const cfg = nvrConfigs[ip] || { channels: 4, streamType: 'main' };

    if (!confirm(`确定添加 ${cfg.channels} 个通道吗？重启服务后方可生效。`)) return;
    addingInProgress = true;

    const urls = await generateUrls(device, cfg.channels, cfg.streamType);
    if (urls.length === 0) {
        showToast('无法生成 URL', 'error');
        addingInProgress = false;
        return;
    }

    const cameras = urls.map(u => ({
        source: u.url,
        name: `NVR-${ip}-CH${u.channel}${u.stream === 'sub' ? '-sub' : ''}`
    }));

    try {
        const resp = await fetch('/api/cameras', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cameras: cameras })
        });
        const data = await resp.json();
        if (data.ok) {
            showToast(data.message, 'success');
            loadList();
            // Mark card as added
            const card = document.getElementById('dev-' + idx);
            if (card) {
                const header = card.querySelector('.device-header');
                if (header) {
                    const mark = document.createElement('span');
                    mark.className = 'added-mark';
                    mark.textContent = '已添加';
                    header.appendChild(mark);
                }
            }
        } else {
            showToast(data.error || '添加失败', 'error');
        }
    } catch (err) {
        showToast('请求失败: ' + err.message, 'error');
    }
    addingInProgress = false;
}

// ============================================================
// Credential retry
// ============================================================
async function tryLogin(idx) {
    const user = document.getElementById('cred-user-' + idx).value.trim();
    const pass = document.getElementById('cred-pass-' + idx).value.trim();
    const statusEl = document.getElementById('cred-status-' + idx);
    if (!user || !pass) {
        statusEl.textContent = '请输入用户名和密码';
        return;
    }
    const device = scanResults[idx];
    if (!device) return;

    statusEl.textContent = '测试中...';
    // Try via generate_urls — if credentials work, URL should be valid
    const testUrl = `rtsp://${encodeURIComponent(user)}:${encodeURIComponent(pass)}@${device.ip}:554/Streaming/Channels/101`;
    await testConnection(testUrl, statusEl);
}

function showToast(text, type) {
    const t = document.getElementById('toast');
    t.textContent = text; t.className = 'toast '+type; t.style.display = 'block';
    setTimeout(() => t.style.display = 'none', 3000);
}

// ============================================================
// ROI (Walking Region) Configuration
// ============================================================
let roiPoints = [];
let isDrawingROI = false;
let currentROICamId = null;
let roiDrawMode = 'rect'; // 'rect' or 'polygon'
let rectStart = null; // {x, y} for rect mode
let rectPreview = null; // {x1,y1,x2,y2} for live preview

function openROIConfig(camId) {
    currentROICamId = camId;
    roiPoints = [];
    isDrawingROI = true;
    rectStart = null;
    rectPreview = null;

    const modal = document.createElement('div');
    modal.id = 'roiModal';
    modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:1000;display:flex;align-items:center;justify-content:center;';

    modal.innerHTML = `
        <div style="background:#111922;border:1px solid #1e2d3d;border-radius:12px;padding:24px;max-width:700px;width:90%;">
            <h3 style="color:#4fc3f7;margin-bottom:12px;">配置行走区域 - 摄像头 ${camId + 1}</h3>
            <div style="display:flex;gap:8px;margin-bottom:12px;">
                <button id="modeRect" onclick="setROIMode('rect')" style="padding:6px 14px;border-radius:6px;font-size:12px;cursor:pointer;border:1px solid #00d4aa;background:#00d4aa;color:#000;font-weight:600;">矩形模式</button>
                <button id="modePoly" onclick="setROIMode('polygon')" style="padding:6px 14px;border-radius:6px;font-size:12px;cursor:pointer;border:1px solid #5a7a8a;background:transparent;color:#c8d8e0;">多边形模式</button>
            </div>
            <p id="roiHint" style="color:#5a7a8a;font-size:12px;margin-bottom:10px;">拖拽画一个矩形区域，只有区域内的物体才会触发告警。</p>
            <div style="position:relative;width:100%;aspect-ratio:4/3;background:#000;border:1px solid #1e2d3d;">
                <img id="roiVideo" src="/video_feed/${camId}" style="width:100%;height:100%;object-fit:contain;display:block;" />
                <canvas id="roiCanvas" width="640" height="480" style="position:absolute;top:0;left:0;width:100%;height:100%;cursor:crosshair;"></canvas>
            </div>
            <div style="display:flex;gap:10px;margin-top:16px;justify-content:flex-end;">
                <button onclick="clearROI()" style="padding:8px 16px;background:transparent;color:#f44336;border:1px solid #f44336;border-radius:6px;cursor:pointer;">清除</button>
                <button onclick="closeROIConfig()" style="padding:8px 16px;background:transparent;color:#c8d8e0;border:1px solid #1e2d3d;border-radius:6px;cursor:pointer;">取消</button>
                <button onclick="saveROI()" style="padding:8px 16px;background:#4caf50;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:bold;">保存</button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    loadExistingROI(camId);
    setupCanvasHandlers();
}

function setROIMode(mode) {
    roiDrawMode = mode;
    roiPoints = [];
    rectStart = null;
    rectPreview = null;
    drawROI();
    document.getElementById('modeRect').style.cssText = mode === 'rect'
        ? 'padding:6px 14px;border-radius:6px;font-size:12px;cursor:pointer;border:1px solid #00d4aa;background:#00d4aa;color:#000;font-weight:600;'
        : 'padding:6px 14px;border-radius:6px;font-size:12px;cursor:pointer;border:1px solid #5a7a8a;background:transparent;color:#c8d8e0;';
    document.getElementById('modePoly').style.cssText = mode === 'polygon'
        ? 'padding:6px 14px;border-radius:6px;font-size:12px;cursor:pointer;border:1px solid #00d4aa;background:#00d4aa;color:#000;font-weight:600;'
        : 'padding:6px 14px;border-radius:6px;font-size:12px;cursor:pointer;border:1px solid #5a7a8a;background:transparent;color:#c8d8e0;';
    document.getElementById('roiHint').textContent = mode === 'rect'
        ? '拖拽画一个矩形区域，只有区域内的物体才会触发告警。'
        : '点击画面添加顶点（至少3个），双击完成。只有区域内的物体才会触发告警。';
    drawROI();
}

function setupCanvasHandlers() {
    const canvas = document.getElementById('roiCanvas');
    if (!canvas) return;

    // Rect mode: mousedown → mousemove → mouseup
    canvas.addEventListener('mousedown', function(e) {
        if (roiDrawMode !== 'rect') return;
        const rect = canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) / canvas.width;
        const y = (e.clientY - rect.top) / canvas.height;
        rectStart = {x, y};
        rectPreview = null;
    });

    canvas.addEventListener('mousemove', function(e) {
        if (roiDrawMode !== 'rect' || !rectStart) return;
        const rect = canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) / canvas.width;
        const y = (e.clientY - rect.top) / canvas.height;
        rectPreview = {
            x1: Math.min(rectStart.x, x), y1: Math.min(rectStart.y, y),
            x2: Math.max(rectStart.x, x), y2: Math.max(rectStart.y, y)
        };
        drawROI();
    });

    canvas.addEventListener('mouseup', function(e) {
        if (roiDrawMode !== 'rect' || !rectStart) return;
        if (rectPreview && (rectPreview.x2 - rectPreview.x1) > 0.02 && (rectPreview.y2 - rectPreview.y1) > 0.02) {
            roiPoints = [
                [rectPreview.x1, rectPreview.y1],
                [rectPreview.x2, rectPreview.y1],
                [rectPreview.x2, rectPreview.y2],
                [rectPreview.x1, rectPreview.y2]
            ];
        }
        rectStart = null;
        rectPreview = null;
        drawROI();
    });

    // Polygon mode: click to add points, double-click to finish
    canvas.addEventListener('click', function(e) {
        if (roiDrawMode !== 'polygon') return;
        const rect = canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) / canvas.width;
        const y = (e.clientY - rect.top) / canvas.height;
        roiPoints.push([x, y]);
        drawROI();
    });

    canvas.addEventListener('dblclick', function(e) {
        if (roiDrawMode !== 'polygon') return;
        if (roiPoints.length >= 3) {
            drawROI(); // Finalize polygon
        }
    });
}

async function loadExistingROI(camId) {
    try {
        const resp = await fetch(`/api/cameras/${camId}/roi`);
        const data = await resp.json();
        if (data.ok && data.roi && data.roi.length > 0) {
            roiPoints = data.roi;
            drawROI();
        }
    } catch(e) {}
}

function drawROI() {
    const canvas = document.getElementById('roiCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw rect preview during drag
    if (rectPreview) {
        const x = rectPreview.x1 * canvas.width;
        const y = rectPreview.y1 * canvas.height;
        const w = (rectPreview.x2 - rectPreview.x1) * canvas.width;
        const h = (rectPreview.y2 - rectPreview.y1) * canvas.height;
        ctx.strokeStyle = '#00d4aa';
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 3]);
        ctx.strokeRect(x, y, w, h);
        ctx.setLineDash([]);
        ctx.fillStyle = 'rgba(0, 212, 170, 0.1)';
        ctx.fillRect(x, y, w, h);
        return;
    }

    // Draw points only (< 3 points in polygon mode)
    if (roiPoints.length < 3) {
        roiPoints.forEach((p, i) => {
            const px = p[0] * canvas.width;
            const py = p[1] * canvas.height;
            ctx.beginPath();
            ctx.arc(px, py, 5, 0, Math.PI * 2);
            ctx.fillStyle = '#00d4aa';
            ctx.fill();
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = 1;
            ctx.stroke();
        });
        return;
    }

    // Draw filled polygon
    ctx.beginPath();
    ctx.moveTo(roiPoints[0][0] * canvas.width, roiPoints[0][1] * canvas.height);
    for (let i = 1; i < roiPoints.length; i++) {
        ctx.lineTo(roiPoints[i][0] * canvas.width, roiPoints[i][1] * canvas.height);
    }
    ctx.closePath();
    ctx.strokeStyle = '#00d4aa';
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = 'rgba(0, 212, 170, 0.15)';
    ctx.fill();

    // Draw vertex points
    roiPoints.forEach((p, i) => {
        const px = p[0] * canvas.width;
        const py = p[1] * canvas.height;
        ctx.beginPath();
        ctx.arc(px, py, 5, 0, Math.PI * 2);
        ctx.fillStyle = '#00d4aa';
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1;
        ctx.stroke();
    });
}

function clearROI() {
    roiPoints = [];
    drawROI();
}

function closeROIConfig() {
    isDrawingROI = false;
    currentROICamId = null;
    const modal = document.getElementById('roiModal');
    if (modal) modal.remove();
}

async function saveROI() {
    if (roiPoints.length < 3) {
        showToast('至少需要3个点形成闭合区域', 'error');
        return;
    }

    try {
        const resp = await fetch(`/api/cameras/${currentROICamId}/roi`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({roi: roiPoints})
        });
        const data = await resp.json();
        if (data.ok) {
            showToast('行走区域保存成功', 'success');
            closeROIConfig();
        } else {
            showToast('保存失败', 'error');
        }
    } catch(e) {
        showToast('保存失败: ' + e.message, 'error');
    }
}

loadList();
