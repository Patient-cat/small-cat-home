<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/YOLOv8-Pose-orange.svg" alt="YOLOv8">
  <img src="https://img.shields.io/badge/Flask-3.0-green.svg" alt="Flask">
  <img src="https://img.shields.io/badge/license-MIT-brightgreen.svg" alt="License">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg" alt="Platform">
</p>

<h1 align="center">SafeSight</h1>
<h3 align="center">基于多模态 AI 的老年人跌倒监测及预警系统</h3>
<h3 align="center">Multimodal AI-Powered Elderly Fall Detection & Alert System</h3>

<p align="center">
  <b>中文</b> | <a href="#english">English</a>
</p>

---

## 项目简介

SafeSight 是一套**无感式、低成本、纯视觉**的老年人跌倒实时监测系统。系统以普通 RGB 摄像头为感知终端，综合运用 YOLOv8-Pose 姿态估计、多目标跟踪、InsightFace 人脸识别以及多特征概率融合的跌倒判定算法，实现 7×24 小时不间断的跌倒检测与即时告警。跌倒发生后，自动调用大语言模型生成结构化分析报告。

**核心亮点**：老人无需佩戴任何设备 | 普通摄像头即可运行 | 单台 PC 部署 | 多摄像头并发 | 人脸身份识别 | AI 辅助分析 | 纯本地运行保护隐私

> **Project Introduction**
>
> SafeSight is a non-intrusive, low-cost, vision-only real-time fall detection system for the elderly. Using standard RGB cameras, it combines YOLOv8-Pose pose estimation, multi-object tracking, InsightFace face recognition, and a multi-feature probabilistic fusion fall detection algorithm to provide 24/7 monitoring. When a fall is detected, the system instantly triggers multi-channel alerts and invokes an LLM to generate a structured analysis report.
>
> **Key Highlights**: No wearables required | Works with regular webcams | Single PC deployment | Multi-camera support | Face identification | AI-assisted analysis | Local processing for privacy

---

## 功能特性 | Features

- **多摄像头实时监控** | Multi-Camera Monitoring — 自适应网格布局，支持 USB / RTSP / 海康 NVR 多类视频源混合接入
- **姿态估计与跟踪** | Pose Estimation & Tracking — YOLOv8-Pose 检测 17 个关键点，IoU 多目标跟踪，跳帧策略保障 CPU 实时性
- **多特征融合跌倒判定** | Multi-Feature Fall Detection — 融合躯干角度、垂直速度、高宽比、角加速度、头脚差、着地检测、专用分类模型七维度，尺度归一化，Sigmoid 加权输出连续概率值 P_FALL
- **双级智能告警** | Two-Level Alerts — 🟡 黄色预警 P_FALL≥0.55「可能摔倒」闪烁提醒，🔴 红色告警 P_FALL≥0.75×2帧「确认摔倒」全屏弹窗+警报音+自动截图+AI分析
- **人脸识别与身份绑定** | Face Recognition — InsightFace ArcFace 512 维特征向量，余弦相似度匹配，在线自适应学习
- **AI 大模型分析** | LLM Analysis — 跌倒后异步调用 DeepSeek 生成结构化事故分析报告
- **系统登录鉴权** | Login System — Session 鉴权，所有页面/API/视频流需登录访问
- **视频模拟测试** | Simulated Testing — 上传视频文件替代摄像头进行完整流程验证，无需硬件即可演示
- **SSE + WebSocket 双通道推送** | Dual-Channel Push — 告警事件通过 SSE 和 WebSocket 同时推送，断线自动重连
- **摄像头管理** | Camera Management — ONVIF 自动扫描、NVR 多通道批量接入、RTSP 连接测试
- **跌倒历史与数据持久化** | History & Persistence — SQLite 存储事件记录+截图+AI 报告，支持永久标记和自动清理

---

## 快速开始 | Quick Start

### 环境要求 | Requirements

- Python 3.9+
- pip
- 一个摄像头（USB 摄像头 / RTSP 网络摄像头 / 或用于测试的 MP4 视频文件）
- **A webcam** (USB / RTSP / or an MP4 video file for testing)

### 1. 克隆仓库 | Clone

```bash
git clone https://github.com/Patient-cat/small-cat-home.git
cd small-cat-home
```

### 2. 安装依赖 | Install Dependencies

```bash
pip install -r requirements.txt
```

> 依赖列表 | Dependencies: Flask, Flask-CORS, Flask-SocketIO, OpenCV, Ultralytics, InsightFace, ONVIF-Zeep, WSDiscovery

### 3. 启动服务 | Start Server

```bash
python app.py
# Windows 用户也可以双击 run.bat
# Windows users can also double-click run.bat
```

首次启动会自动下载 YOLOv8-Pose 模型（约 6.6 MB）和 InsightFace 模型。

> The first launch will auto-download the YOLOv8-Pose model (~6.6 MB) and InsightFace models.

### 4. 打开浏览器 | Open Browser

```
http://localhost:5001
```

### 5. （可选）启用 AI 分析 | (Optional) Enable AI Analysis

```bash
cp .env.example .env
# 编辑 .env 填入 DeepSeek API Key
# Edit .env and fill in your DeepSeek API Key
```

> AI 分析功能默认关闭，不配置 API Key 不影响系统正常使用。
> AI analysis is off by default. The system works normally without an API key.

---

## 部署指南 | Deployment Guide

### 场景 1：本地开发 / 演示 | Local Dev / Demo

直接用 USB 摄像头或上传测试视频：

```bash
pip install -r requirements.txt
python app.py
# 访问 http://localhost:5001
```

无需任何额外配置。

### 场景 2：多摄像头居家部署 | Home Deployment

准备一台闲置 PC 或 mini PC（N100/NUC 即可），连接 2-4 个 USB 摄像头：

1. 完成 1-3 步安装
2. 打开 `http://localhost:5001/cameras` 管理页面
3. 点击"手动添加摄像头"，填入摄像头索引（0, 1, 2...）
4. 重启服务
5. 监控主页即可看到所有摄像头画面

**硬件参考**：一台 Intel N100 mini PC（~500 元）+ 2 个 USB 摄像头（~100 元/个）= 约 700 元

### 场景 3：养老机构（海康 NVR 接入） | Nursing Home (Hikvision NVR)

利用已有的海康监控网络，通过 ONVIF 批量接入：

1. 确保服务器与 NVR 在同一局域网
2. 打开 `http://<服务器IP>:5001/cameras`
3. 点击"扫描网络摄像头"，等待发现 NVR 设备
4. 设置通道数量和码流类型
5. 点击"批量添加"，所有通道自动写入配置
6. 重启服务

> 注意：NVR 扫描依赖 `onvif-zeep` 和 `WSDiscovery` 两个包，已在 requirements.txt 中包含。
> Note: NVR scanning requires `onvif-zeep` and `WSDiscovery`, already included in requirements.txt.

### 场景 4：局域网内网访问 | LAN Access

服务默认监听 `0.0.0.0:5001`，同一局域网内的其他设备可通过服务器 IP 访问：

```
http://192.168.x.x:5001
```

如需长期运行，建议使用 `systemd`（Linux）或 `nssm`（Windows）将服务注册为系统服务。

---

## 系统架构 | Architecture

```
┌────────────────────────────────────────────────────────────┐
│                     Browser (HTML5 + JS)                    │
│   MJPEG Streams  │  SSE/WebSocket  │  REST API             │
├────────────────────────────────────────────────────────────┤
│                     Flask + SocketIO                        │
├──────────────┬──────────────┬──────────────┬───────────────┤
│ 采集线程 ×N   │ 检测线程 ×N   │ 告警广播      │ AI 分析线程池  │
│ Capture      │ Detection    │ Broadcast    │ AI Analysis   │
│ Thread ×N    │ Worker ×N    │              │ ThreadPool    │
├──────────────┴──────────────┴──────────────┴───────────────┤
│  YOLOv8-Pose  │  IoU Tracker  │  Fall Detector  │  InsightFace │
├────────────────────────────────────────────────────────────┤
│  USB Camera  │  RTSP Camera  │  Hikvision NVR  │  Video File  │
└────────────────────────────────────────────────────────────┘
```

每路摄像头独立运行：采集 → 帧队列（Queue, max=60）→ YOLO 检测（每 3 帧）→ 跌倒判定 → 结果回写帧队列 → MJPEG 编码输出。人脸识别每 30 帧执行一次，AI 分析在跌倒触发后异步执行。

> Each camera runs independently: Capture → Frame Queue (max=60) → YOLO Detection (every 3rd frame) → Fall Judgment → MJPEG encode. Face recognition runs every 30th frame. AI analysis is triggered asynchronously after a fall event.

---

## 项目结构 | Project Structure

```
├── app.py                  # 主服务入口（Flask + SocketIO + 多线程）
│                           # Main server (Flask + SocketIO + multi-threading)
├── config.py               # AI 大模型配置 / LLM config (DeepSeek/Gemini)
├── init_db.py              # 数据库初始化 / Database initialization
├── requirements.txt        # Python 依赖列表
├── run.bat                 # Windows 一键启动脚本
├── .env.example            # API Key 配置模板
├── cameras.json            # 摄像头配置文件 / Camera configuration
├── faces.db                # SQLite 数据库（自动创建）/ SQLite DB (auto-created)
├── yolov8n-pose.pt         # YOLOv8 姿态估计模型（自动下载）
├── templates/
│   ├── index.html          # 实时监控主页 / Live monitoring dashboard
│   ├── register.html       # 人脸注册（三步向导）/ Face registration wizard
│   ├── manage.html         # 人员管理 / Person management
│   ├── history.html        # 跌倒事件历史 / Fall event history
│   ├── cameras.html        # 摄像头管理 / Camera management
│   └── test.html           # 视频模拟测试 / Video simulation test
└── static/
    ├── falls/              # 跌倒截图存档 / Fall screenshots
    └── uploads/            # 人脸注册照片 / Face registration photos
```

---

## API 参考 | API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | 服务状态 | Health check — returns `{status, cameras[{id,name,fps,persons,p_fall}], name, ai_enabled}` |
| GET | `/api/events?limit=100` | 事件列表 | Event list — returns array of `{id, elder_name, confidence, screenshot, created_at, has_report}` |
| GET | `/api/events/<id>` | 事件详情 | Event detail — includes full AI report |
| DELETE | `/api/events/<id>` | 删除事件 | Delete event |
| PUT | `/api/events/<id>/permanent` | 标记永久保留 | Toggle permanent flag |
| GET | `/api/latest_report` | 最新 AI 报告 | Latest AI report |
| GET | `/api/faces` | 已注册人员列表 | Registered persons |
| POST | `/api/register_face` | 注册人脸 | Register face — `{name, image_base64}` |
| DELETE | `/api/faces/<id>` | 删除人员 | Delete person |
| PUT | `/api/faces/<id>/photo` | 追加人脸照片 | Add face photo (multipart) |
| GET | `/api/cameras` | 摄像头列表 | Camera list with FPS |
| POST | `/api/cameras` | 添加摄像头 | Add camera(s) — `{source, name}` or `{cameras: [...]}` |
| DELETE | `/api/cameras/<id>` | 删除摄像头 | Delete camera |
| POST | `/api/cameras/scan` | ONVIF 扫描 | Scan network for ONVIF devices |
| POST | `/api/cameras/generate_urls` | 生成 NVR 通道 URL | Generate RTSP URLs — `{ip, user, password, channels}` |
| POST | `/api/cameras/test` | 测试 RTSP 连接 | Test connection — `{source}` |
| POST | `/api/toggle_ai` | 开关 AI 分析 | Toggle AI analysis |
| POST | `/test` | 上传测试视频 | Upload test video (multipart) |
| GET | `/test_feed` | 测试视频 MJPEG 流 | Test video feed |
| GET | `/events` | SSE 告警推送 | SSE alert stream |
| WS | `/ws` | WebSocket 告警推送 | WebSocket alert stream |

### 外部集成示例 | Integration Examples

**iframe 嵌入 Embed**：
```html
<img src="http://localhost:5001/video_feed/0" width="640" height="480">
```

**WebSocket 接收告警 Receive Alerts**：
```javascript
const ws = new WebSocket('ws://localhost:5001/ws');
ws.onmessage = (e) => {
  const alert = JSON.parse(e.data);
  // { type: "fall", level: 2, name: "张三", confidence: 0.85, timestamp: "..." }
};
```

**SSE 接收告警 (备选 Alternative)**：
```javascript
const es = new EventSource('http://localhost:5001/events');
es.onmessage = (e) => {
  const alert = JSON.parse(e.data); // 格式同上 Same format
};
```

---

## 告警系统 | Alert System

| Level | Trigger | Visual | Audio | Duration |
|-------|---------|--------|-------|----------|
| 🟡 **Yellow** 可能摔倒 | P_FALL ≥ 0.55 | Yellow flashing overlay 黄色闪烁 | Soft beep 轻柔提示音 | Auto-dismiss 3s |
| 🔴 **Red** 确认摔倒 | P_FALL ≥ 0.75 × 2 frames | Full-screen red modal 全屏红色弹窗 | Continuous alarm 持续警报 | Manual dismiss 人工确认 |

> P_FALL 由七个特征加权融合：躯干角度 (30%) + 垂直速度 (22%) + 宽高比 (18%) + 角加速度 (10%) + 着地检测 (10%) + 头脚差 (5%) + 分类模型 (5%)，尺度归一化，经双层 Sigmoid 映射。
> 红色告警触发后自动截图保存、写入数据库、异步调用 DeepSeek AI 分析。10 秒冷却期避免重复触发。

---

## 常见问题 | FAQ

**Q: 启动后报错 "PytorchStreamReader failed"？**
> YOLO 模型文件下载不完整。删除 `yolov8n-pose.pt` 后重新启动，系统会自动重新下载。
> Delete `yolov8n-pose.pt` and restart — the model will auto re-download.

**Q: 摄像头没有画面？**
> 1. 确保摄像头未被其他程序占用 2. 检查 `cameras.json` 中的 source 索引是否正确 3. 尝试删除 `cameras.json` 中的多余摄像头，只保留实际连接的
> 1. Ensure the camera is not in use by another app 2. Check `cameras.json` source index 3. Try removing unused cameras from config

**Q: ONVIF 扫描提示需要安装支持库？**
> 手动安装：`pip install onvif-zeep WSDiscovery`，然后重启服务。
> Run `pip install onvif-zeep WSDiscovery` and restart.

**Q: 如何提升检测精度？**
> 将 `app.py` 第 65 行 `model = YOLO('yolov8n-pose.pt')` 改为 `yolov8s-pose.pt`（首次启动自动下载，约 13MB），精度提升约 10%，CPU 推理约 50ms/帧仍可实时运行。
> Change line 65 in `app.py` to `yolov8s-pose.pt` for ~10% better accuracy (~13MB, ~50ms/frame on CPU).

**Q: 可以部署到树莓派吗？**
> YOLOv8n-pose 在树莓派 5 上推理约 100-150ms/帧，可运行但帧率较低。建议使用 Intel N100 或更高性能的 mini PC 以获得流畅体验。
> Runs on Raspberry Pi 5 at ~100-150ms/frame. For smooth experience, recommend Intel N100 or better.

**Q: 摄像头配置修改后需要重启吗？**
> 是的。摄像头配置写入 `cameras.json` 后需要重启 `python app.py` 才能生效。
> Yes. Camera config changes require restarting the server.

**Q: 如何贡献代码？**
> 欢迎提 Issue 和 PR！Please feel free to open Issues and PRs!

---

## License

MIT License — 详见 [LICENSE](LICENSE) 文件。

---

<p align="center">
  <sub>Built with ❤️ for elderly care | 为智慧养老而生</sub>
</p>
