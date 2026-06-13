# Safesight 开发文档

> 基于多模态AI的老年人跌倒风险识别及预警系统  
> v2.0 — 多摄像头多人追踪版  
> 最后更新: 2026-06-09

---

## 目录

1. [项目架构](#1-项目架构)
2. [环境与启动](#2-环境与启动)
3. [核心管线](#3-核心管线)
4. [模型与算法](#4-模型与算法)
5. [API 参考](#5-api-参考)
6. [数据库](#6-数据库)
7. [前端页面](#7-前端页面)
8. [配置与调优](#8-配置与调优)
9. [海康摄像头接入指南](#9-海康摄像头接入指南)
10. [故障排查](#10-故障排查)
11. [已知限制](#11-已知限制)

---

## 1. 项目架构

### 1.1 技术栈

| 层次 | 技术 |
|------|------|
| 后端框架 | Flask + Flask-SocketIO + Flask-CORS |
| 姿态检测 | YOLOv8n-pose (Ultralytics) |
| 跌倒分类 | 自训练 YOLOv8n (fall_detect.pt) |
| 人脸识别 | InsightFace buffalo_sc (ONNX) |
| AI 分析 | DeepSeek / Gemini API |
| 数据库 | SQLite 3 |
| 视频流 | MJPEG over HTTP |
| 实时推送 | Server-Sent Events + WebSocket |

### 1.2 线程模型

```
主进程 (Flask + SocketIO)
├─ Camera-0  capture (generate_frames)  ← MJPEG 流生成
│            ├─ cam.read() → frame.copy() → frame_queues[cid]
│            ├─ 绘制骨骼/HUD overlay
│            └─ JPEG 编码 → yield MJPEG
│
├─ Camera-0  detection_worker            ← 姿态检测线程
│            ├─ 从 frame_queues[cid] 取帧
│            ├─ 每 3 帧: YOLOv8-pose 推理
│            ├─ IoU 多人追踪
│            ├─ 每 15 帧: fall_detect 模型
│            ├─ 每 30 帧: InsightFace 人脸识别
│            └─ check_fall() 跌到概率计算
│
├─ Camera-1  capture + detection_worker  ← 每路独立
├─ ...
├─ Camera-N  capture + detection_worker
│
├─ cleanup_loop  (每 6 小时清理 7 天前事件)
├─ usb_scan      (启动时自动扫描 USB 摄像头)
└─ ai_executor   (ThreadPoolExecutor, max_workers=2)
```

### 1.3 关键同步原语

| 锁 | 作用域 |
|----|--------|
| `detection_locks[cam_id]` | 保护 `latest_detections[cam_id]` 读写 |
| `tracker_lock` | 保护 `tracked_persons` 全局字典 |
| `state_lock` | 保护 `recognized_name` |
| `config_lock` | 保护 `CAMERAS` 列表和 `camera_names` 字典 |
| `auto_learn_lock` | 保护 `_auto_learn_history` |
| `test_video_lock` | 保护测试视频路径 |

### 1.4 重要全局结构

```python
CAMERAS = [{id, source, name}, ...]          # 从 cameras.json 加载
frame_queues[cam_id] = Queue(maxsize=2)      # 捕获→检测的帧队列
latest_detections[cam_id] = {kp_xy, kp_conf, is_fall, tracks, fd_boxes}
camera_caps[cam_id] = cv2.VideoCapture       # 用于优雅退出释放资源
tracked_persons = {track_id: {bbox, kp, ...}}# IoU 追踪状态
```

---

## 2. 环境与启动

### 2.1 依赖

```bash
pip install -r requirements.txt
```

核心依赖: `ultralytics>=8.0`, `opencv-python>=4.8`, `flask`, `flask-socketio`, `insightface>=0.7`

**注意**: PyTorch 不在 requirements.txt 中，需根据 CUDA 版本手动安装。

### 2.2 数据库初始化

```bash
python init_db.py
```

或首次运行 `app.py` 时自动初始化。

### 2.3 启动服务

```bash
python app.py
# Windows: 双击 run.bat
```

服务绑定 `http://0.0.0.0:5001`

### 2.4 目录结构

```
Safesight/
├── app.py              # 主程序 (~1800 行)
├── config.py           # AI API 配置
├── cameras.json        # 摄像头列表
├── faces.db            # SQLite 数据库
├── fall_detect.pt      # 自训练跌倒检测模型
├── yolov8n-pose.pt     # YOLOv8 姿态估计
├── templates/
│   ├── index.html      # 首页仪表盘
│   ├── hall.html       # 监控大厅（多路画面）
│   ├── cameras.html    # 摄像头管理
│   ├── register.html   # 人脸注册
│   ├── manage.html     # 人员管理
│   ├── history.html    # 跌倒历史
│   └── test.html       # 视频模拟测试
├── static/
│   ├── falls/          # 跌倒截图
│   └── uploads/        # 人脸注册照片
└── DEV.md              # 本文档
```

---

## 3. 核心管线

### 3.1 帧处理流程

```
cam.read()                          # RTSP/USB 捕获
  → frame.copy() → fq.put(copy)    # 复制后给检测线程（避免数据竞争）
  → 从 latest_detections 取追踪结果
  → 画骨骼线 + 关键点 + 人名 + P_FALL
  → 画 fall_detect 框
  → 画 HUD（FPS/人数/P_FALL）
  → cv2.imencode JPEG
  → yield MJPEG 帧
```

### 3.2 检测流程

```
fq.get() 取帧
  → 每 3 帧:  YOLOv8-pose(imgsz=416, device=DEVICE)
  → 每 15 帧: fall_detect(imgsz=320, device=DEVICE)
  → 每 30 帧: InsightFace 人脸识别（仅未命名的人）
  → IoU 匹配追踪 (IOU_MATCH_MIN=0.3, TRACK_MAX_LOST=30)
  → check_fall() 概率融合
  → 更新 latest_detections
  → alert level 1 (warning) / level 2 (fall)
```

### 3.3 跌倒检测算法 `check_fall()`

五特征加权融合，每帧独立计算：

| 特征 | 权重 | 计算方式 |
|------|------|----------|
| 躯干角度 | 35% | 肩膀中心↔髋部中心的垂直偏离角 |
| 垂直速度 | 25% | 5 帧滑动窗口髋部 Y 位移 |
| 宽高比 | 20% | 肩髋包围盒宽/高 |
| 角加速度 | 12% | 4 帧窗口角度变化率 |
| 头脚 Y 差 | 8% | 鼻尖↔脚踝的距离 |

每个特征 → sigmoid 归一化 → 加权求和 → 二次 sigmoid → **P_FALL (0–1)**

```
Level 1 (warning): 0.55 ≤ P_FALL < 0.75
Level 2 (fall):    P_FALL ≥ 0.75 连续 2 帧
```

### 3.4 人脸识别流程

```
crop 人物上半身 → CLAHE 预处理 → InsightFace 提取 embedding
  → 与 faces.db 中所有人脸 embedding 余弦相似度
  → 最高分 ≥ 0.50: 匹配
  → 最高分 ≥ 0.70: 自动录入为新 embedding (AutoLearn)
  → 名字持有 90 帧后失效
```

---

## 4. 模型与算法

### 4.1 YOLOv8n-pose

- 文件: `yolov8n-pose.pt`
- 参数量: ~5.3M
- 推理尺寸: `imgsz=416`
- 输出: 17 个 COCO 关键点 + 置信度
- 设备: 自动检测 CUDA → CPU 回退

### 4.2 fall_detect.pt

- 文件: `fall_detect.pt`
- 类型: YOLOv8n 分类/检测
- 推理尺寸: `imgsz=320`
- 输出: `is_fall` 布尔 + 置信度
- 频率: 每 15 帧

### 4.3 InsightFace buffalo_sc

- 后端: ONNX Runtime (CPUExecutionProvider)
- 检测器: SCRFD
- 识别模型: ArcFace
- 频率: 每 30 帧

### 4.4 IoU 多人追踪

- 匹配阈值: `IOU_MATCH_MIN=0.3`
- 丢失容忍: `TRACK_MAX_LOST=30` 帧
- 每人维护独立 `hip_history` / `angle_history` deque

---

## 5. API 参考

### 5.1 视频流

| 端点 | 方法 | 说明 |
|------|------|------|
| `/video_feed/<cam_id>` | GET | MJPEG 实时流 |
| `/test_feed` | GET | 测试视频 MJPEG 流 |
| `/capture_frame` | GET | 摄像头 0 单帧截图 |

### 5.2 摄像头管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/cameras` | GET | 列出所有摄像头(含 fps/enabled) |
| `/api/cameras` | POST | 添加摄像头 `{source, name?}` |
| `/api/cameras` | DELETE | 删除 `?id=` |
| `/api/camera/<id>/toggle` | POST | 开关摄像头 |
| `/api/camera/<id>/rename` | POST | 重命名 `{name}` |
| `/api/cameras/scan` | POST | ONVIF 网络扫描 |
| `/api/cameras/scan-usb` | POST | USB 摄像头扫描 |
| `/api/cameras/generate_urls` | POST | 生成海康 RTSP URL |
| `/api/cameras/test` | POST | 测试 RTSP URL 连通性 |

### 5.3 实时事件

| 端点 | 方法 | 说明 |
|------|------|------|
| `/events` | GET | SSE 事件流 |
| WebSocket `/` | WS | 跌倒/警告推送 |

事件格式:
```json
{
  "type": "fall",          // "fall" | "warning"
  "level": 2,              // 1=warning, 2=fall
  "name": "张三",
  "confidence": 0.85,
  "timestamp": "2026-06-09 14:30:00",
  "screenshot": "/static/falls/fall_20260609_143000.jpg",
  "event_id": 42,
  "cam_id": 0
}
```

### 5.4 跌倒事件

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/events` | GET | 事件列表 `?limit=100` |
| `/api/events/<id>` | GET | 事件详情 |
| `/api/events/<id>` | DELETE | 删除事件 |
| `/api/events/<id>/permanent` | POST | 标记/取消永久保存 |
| `/api/events/delete_all` | POST | 清空所有事件 |
| `/api/latest_report` | GET | 最近 AI 报告 |

### 5.5 人脸管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/faces` | GET | 人员列表 |
| `/api/faces/<id>` | DELETE | 删除人员及面部 |
| `/api/faces/<id>/photo` | PUT | 追加面部照片 |
| `/api/register_face` | POST | REST API 注册 `{name, image_base64}` |
| `/register` | GET/POST | Web 页面注册 |

### 5.6 系统

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/toggle_ai` | POST | 开关 AI 分析 |

### 5.7 测试

| 端点 | 方法 | 说明 |
|------|------|------|
| `/test` | GET/POST | 测试页面 / 上传视频 |
| `/test/reset` | GET | 停止测试恢复摄像头 |
| `/test/pause` | POST | 暂停/继续 |
| `/test/state` | GET | 测试状态查询 |

---

## 6. 数据库

### 6.1 Schema

```sql
-- 人员
CREATE TABLE persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 面部特征
CREATE TABLE face_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    embedding_blob BLOB NOT NULL,      -- numpy float32 序列化
    photo_path TEXT,
    det_score REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 跌倒事件
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_name TEXT DEFAULT '陌生人',
    confidence REAL,                   -- P_FALL 值
    screenshot TEXT,                   -- 截图路径
    report TEXT DEFAULT '',            -- AI 分析报告
    permanent INTEGER DEFAULT 0,       -- 1=永久保存
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 6.2 数据清理

- 每 6 小时自动删除 7 天前的非永久事件
- `permanent=1` 的事件不会被清理
- 截图文件随事件一并删除

---

## 7. 前端页面

### 7.1 页面一览

| 页面 | 模板 | 功能 |
|------|------|------|
| `/` | index.html | 仪表盘：统计、快捷入口、最近报告 |
| `/hall` | hall.html | 监控大厅：多路视频网格、告警浮层 |
| `/cameras` | cameras.html | 摄像头增删改查、ONVIF 扫描 |
| `/register` | register.html | 三步人脸注册向导 |
| `/manage` | manage.html | 人员管理、面部管理 |
| `/history` | history.html | 事件列表、AI 报告查看 |
| `/test` | test.html | 上传视频模拟测试 |

### 7.2 前端轮询频率

| 页面 | 接口 | 间隔 |
|------|------|------|
| hall.html | `/api/cameras` | 3s |
| index.html | `/api/health` | 3s |
| index.html | `/api/latest_report` | 10s |
| index.html | `/api/cameras` + `/api/faces` + `/api/events` | 15s |
| test.html | `/test/state` | 1s |

### 7.3 告警机制

- **SSE** (`/events`): 所有页面使用
- **声音**: Web Audio API (beep + 语音)  
- **通知**: Browser Notification API
- **视觉**: 全屏红色遮罩 + 黄色警告闪烁

---

## 8. 配置与调优

### 8.1 核心参数 (`app.py`)

```python
# 跌倒检测
FALL_PROB_THRESHOLD = 0.75       # 跌倒判定阈值
WARN_PROB_THRESHOLD = 0.55       # 警告阈值
FALL_CONSECUTIVE_FRAMES = 2      # 连续帧确认
FALL_COOLDOWN_SECONDS = 5        # 告警冷却

# 检测频率
DETECTION_INTERVAL = 3           # YOLO 姿态每 N 帧
FACE_RECOGNITION_INTERVAL = 30   # 人脸识别间隔

# 追踪
TRACK_MAX_LOST = 30              # 丢失容忍帧数
IOU_MATCH_MIN = 0.3              # 匹配 IoU 阈值

# 人脸
FACE_SIMILARITY_THRESHOLD = 0.50 # 最低匹配相似度
FACE_NAME_HOLD_FRAMES = 90       # 名字持有时间

# 性能
YOLO_IMGSZ = 416                 # YOLO 输入尺寸
JPEG_QUALITY = 60                # MJPEG 质量
```

### 8.2 AI 分析配置 (`config.py`)

环境变量 (`.env` 文件):
```
AI_PROVIDER=deepseek             # deepseek or gemini
AI_API_KEY=sk-xxxxxxxx
AI_MODEL=deepseek-chat
AI_TIMEOUT=10
```

### 8.3 性能调优建议

| 场景 | 调整 |
|------|------|
| 单路高清 | `JPEG_QUALITY=75`, 不 resize |
| 25 路监控 | `JPEG_QUALITY=50`, resize 720p, `DETECTION_INTERVAL=5` |
| CPU 无 GPU | `DEVICE='cpu'`, `YOLO_IMGSZ=256` |
| 低延迟 | `maxsize=2`（已默认）, `BUFFERSIZE=1` |

---

## 9. 海康摄像头接入指南

### 9.1 RTSP URL 格式

```
主码流: rtsp://用户名:密码@IP:554/Streaming/Channels/101
子码流: rtsp://用户名:密码@IP:554/Streaming/Channels/102

通道 N 主码流 = N * 100 + 1
通道 N 子码流 = N * 100 + 2
```

### 9.2 配置建议

| 参数 | 建议值 | 原因 |
|------|--------|------|
| 视频编码 | H.264 | 软解比 H.265 快 2-3 倍 |
| 分辨率 | 1920×1080 | 检测够用、编码不卡 |
| 帧率 | 20-25 fps | 跌倒检测 ≥15 fps 足够 |
| 码率类型 | 定码率 | 稳定网络传输 |
| 传输协议 | TCP | 比 UDP 稳定 |

### 9.3 NVR 管理页面

```
http://<NVR_IP> → 配置 → 视音频 → 视频 → 通道 1 → 主码流
```

常见默认凭据: `admin` / `admin12345`

### 9.4 自动发现

`/api/cameras/scan` 通过 ONVIF WS-Discovery 扫描局域网设备，自动枚举 NVR 通道并生成 RTSP URL。

需要安装: `pip install onvif-zeep WSDiscovery`

---

## 10. 故障排查

### 画面卡顿

| 症状 | 可能原因 | 解决 |
|------|----------|------|
| FPS < 源流帧率 | H.265 软解慢 | NVR 改 H.264 |
| FPS = 源流帧率但显卡 | 4K→浏览器缩放 | 缩放到 1080p |
| 多路全卡 | CPU/GPU 过载 | 增大 `DETECTION_INTERVAL` |

### 画面糊

| 症状 | 可能原因 | 解决 |
|------|----------|------|
| 模糊 | JPEG 质量太低 | 调高 `JPEG_QUALITY` |
| 模糊 | 子码流分辨率低 | 切到主码流 |
| 模糊 | 4K 被浏览器缩放 | 后端缩放到 1080p |

### 启动问题

| 症状 | 可能原因 | 解决 |
|------|----------|------|
| 首次进入需要刷新 | 服务器未就绪 | 已修：前端自动重试 10 次 |
| 退出后电脑卡 | ffmpeg 子进程滞留 | 已修：退出时释放所有资源 |
| `CUDA out of memory` | 显存不足 | 减小 `YOLO_IMGSZ` 或用 CPU |

### 摄像头离线

| 症状 | 可能原因 | 解决 |
|------|----------|------|
| 画面卡死不恢复 | 网络断开 | 已修：30 次失败后自动重连 |
| RTSP 连接不上 | 密码错误/IP 不通 | 用 `/api/cameras/test` 测试 |
| NVR 连接数超限 | 并发连接太多 | 减少同时开启的摄像头数 |

---

## 11. 已知限制

1. **Python GIL**: 多路时 JPEG 编码竞争 CPU，25 路无法全部 60 FPS
2. **SQLite 并发**: 写操作串行化，高并发场景考虑迁移 PostgreSQL
3. **不存在生产 WSGI**: 开发用 Werkzeug，生产环境建议 `waitress` (Windows) 或 `gunicorn` (Linux)
4. **CORS 全开**: `cors_allowed_origins='*'`，内网部署可接受
5. **RTSP 凭证明文**: `cameras.json` 中密码明文存储，注意文件权限
6. **不支持 PTZ 控制**: 仅拉流显示，不支持云台控制
7. **无 GPU 硬解码**: 使用 CPU 软解 H.264，单路 1080p 约 5ms/帧，多路时压力大
