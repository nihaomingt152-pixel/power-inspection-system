# AGENTS.md - 项目记忆与开发者指南 (v4.0)

## 1. 项目概览
- **核心目标**: 基于 YOLO + Qwen3-VL-Flash 的电力输电线路智能巡检平台。支持图片/视频/摄像头多源输入、YOLO 检测、AI 语义分析、兜底异物检测、分级预警、工单闭环管理、GIS 地图、硬件监控、Word 报告导出。
- **技术栈**: Python 3.9+ / FastAPI / Ultralytics YOLO / PyTorch / SQLAlchemy / SQLite / DashScope Qwen3-VL-Flash / OpenCV / Bootstrap 5.3 / ECharts 5 / Leaflet.js / bcrypt / pynvml
- **架构模式**: 单体 Web 应用，FastAPI 后端 + Jinja2 模板 + RESTful API，前后端不分离。
- **版本历史**:
  | 阶段 | 内容 |
  |------|------|
  | Phase 0 | 数据集合并（百度VOC + 绝缘子YOLO）→ 8分类训练 |
  | Phase 1 | 完整Web应用（FastAPI + YOLO + Qwen3-VL + SQLite） |
  | Phase 2 | 后端重构（异步修复、视频优化、新API） |
  | Phase 3 | 生产扩展（认证、工单、GIS地图、仪表盘、GPU监控、语音播报） |
  | Phase 3.5 | 认证模块修复（passlib→bcrypt原生库） |
  | Phase 4 | 前端重构（common.js公共模块、全页面初始化加固） |
  | Phase 5 | CSS遮罩层修复（Modal display被覆盖导致点击全拦截） |
  | Phase 6 | Admin/Worker 双角色隔离 + 检测→派发→处理→复检→闭环 |
  | Phase 6.1 | 提交复检模态框补全 + Admin 重新派发已驳回工单 |
  | Phase 7 | 视频检测结果统一存储（一条视频一条记录）+ 帧网格展示 |
  | Phase 7.1 | 所有帧保存真实图片（800x600 JPG）+ 视频删除功能 |
  | Phase 7.2 | UI 优化（视频选择反馈提示、ModalManager 防遮罩残留） |
  | Phase 8 | 移动端访问性能优化（图片压缩、缩略图精简返回、分页/轮询适配） |
  | Phase 26 | 视频分析全链路优化（移动端压缩、进度+剩余时间、智能跳帧、YOLO批处理、AI异步、NVENC硬件编码、MD5缓存） |
  | Phase 27 | 视频AI调用优化（逐帧AI→整段视频一次性总结、取消时间轴、video_summary字段） |
  | Phase 28 | 视频界面优化（删兜底间隔输入框、AI分析开关控制总结）+ 派发工单修复（弹窗层级遮挡、图片显示、GPS小地图点选） |
  | Phase 29 | 单帧预警等级手动修改（任意帧改等级→生成工单、等级持久化、修改记录追溯）+ 派发工单 AI 摘要 + 工单图片/AI摘要链路修复 |
  | Phase 30 | 地图总览显示未闭环工单位置（📌标记+状态徽标、排除已闭环、Worker 数据隔离） |

## 2. 系统运行逻辑

### 2.1 整体数据流
```
用户浏览器 → FastAPI (Jinja2模板渲染) → RESTful API → Service层 → YOLO模型 / Qwen3-VL / 数据库
                    ↑                        ↑
               SessionMiddleware      Depends(get_db) 依赖注入
```

### 2.2 页面路由与认证流程
```
用户访问 / → 检测是否需要登录
  ├─ 未登录 → /login（登录/注册页面）
  ├─ Admin → / 主检测页（Session Cookie 认证）
  └─ Worker → 重定向 /dashboard（无权访问检测中心）

页面路由:
  /                    → index.html    主检测页（图片/视频/摄像头三标签）
  /login               → login.html    登录/注册页（无需认证）
  /dashboard           → dashboard.html 数据仪表盘（ECharts图表）
  /map                 → map.html      GIS地图总览（Leaflet + OpenStreetMap）
  /orders              → orders.html   工单管理（CRUD + 状态流转）
  /video/play/{task_id}→ video_player.html 视频播放页（支持 ?time= 跳转）
```

### 2.3 检测流程（核心业务逻辑）
```
1. 用户上传图片/视频/摄像头帧
2. YOLO 模型推理（线程锁串行化，RTX 4060 8G 显存限制）
3. 分类判定:
   ├─ 类别 5/6（破损绝缘壳/闪燃损坏）+ 置信度>0.7
   │    → 调用 Qwen3-VL 进行AI语义分析（严重程度、成因、建议）
   │    → 触发预警（三重判定: YOLO缺陷 + 置信度 + AI严重等级）
   │    → 语音播报（Web Speech API）
   └─ 其他类别 → 仅YOLO检测结果
4. 兜底异物检测（可选）:
   └─ Qwen-VL 独立扫描 YOLO 未覆盖的异常（塑料袋、工程机械、山火等）
      └─ 结果以黄色标签显示，不强制生成工单
5. 检测结果入库 → DetectionRecord（图片）/ VideoDetectionRecord（视频汇总）
6. 工单生成（可选）:
   └─ 运维人员创建 → 指派检修人 → processing → pending_review → closed/rejected
```

### 2.4 工单状态机
```
运维创建工单(可指定检测记录)
  └─ pending（待派发）
      └─ 检修确认接单 → processing（处理中）
          ├─ Worker 驳回（原因+补充说明）→ rejected → Admin 重新派发 → processing
          └─ 检修提交复检（照片+备注）→ pending_review（待复检）
              ├─ 运维确认闭环 → closed（已闭环）
              └─ 运维驳回 → rejected（已驳回）
                  └─ 检修重新提交 → pending_review
```

### 2.5 前端脚本加载顺序（关键！）
所有页面必须按此顺序加载 JS：
```
Bootstrap JS（CDN，先加载）
  → /static/js/common.js   （公共函数：log/apiGet/apiPost/$/notify/ModalManager + 点击诊断）
  → /static/js/auth.js     （认证：login/register/logout/getCurrentUser）
  → 页面专属 JS            （main.js / dashboard.js / map.js / orders.js）
```
**注意**: 顺序错误会导致函数未定义，页面功能失效。

## 3. 环境与指令
| 操作 | 命令 |
|------|------|
| 激活环境 | `conda activate E:\anaconda_environment\pytorch_for_GPU` |
| 安装依赖 | `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` |
| 启动服务 | `python app.py` |
| 访问主页 | http://localhost:5000 |
| API 文档 | http://localhost:5000/docs |
| 配置文件 | `.env`（模板见 `.env.example`） |
| 运行训练 | `python train_yolo26.py`（**训练数据不发布 GitHub**） |
| 数据集合并 | `python convert_and_merge.py`（**训练数据不发布 GitHub**） |
| JS语法检查 | `for f in static/js/*.js; do node -c "$f"; done` |
| 验证ffmpeg | `ffmpeg -version && ffmpeg -hide_banner -encoders 2>&1 | grep nvenc` |
| 代码存档 | `git add -A && git commit -m "..." && git push` |

## 4. 项目文件结构
```
E:\WorkSpace\TrainModel\
├── app.py                          # FastAPI 主程序（全部路由 + SessionMiddleware）
├── train_yolo26.py                 # YOLO 训练脚本（⚠️ 不发布 GitHub）
├── convert_and_merge.py            # 数据集合并脚本（⚠️ 不发布 GitHub）
├── requirements.txt / .env.example / README.md / AGENTS.md
├── .gitignore                      # 排除训练数据、模型权重、数据库、上传文件
│
├── models/
│   └── yolo_model.py               # YOLO 检测器（加载/推理/绘制/分类/并发锁）
│
├── services/
│   ├── detection_service.py        # 检测服务（图片/视频/摄像头帧 + EXIF + 入库）
│   ├── multimodal_service.py       # Qwen3-VL-Flash API 封装
│   ├── fallback_service.py         # 兜底异物检测（Qwen-VL 扫描非 YOLO 覆盖的异常）
│   ├── alert_service.py            # 智能预警（置信度 + AI 严重等级三重判定）
│   ├── auth_service.py             # 认证（bcrypt 直接调用 + Session/Cookie + 角色权限）
│   ├── work_order_service.py       # 工单闭环（6状态流转 + 操作日志 + 重新派发）
│   ├── monitor_service.py          # GPU 硬件监控（pynvml）
│   └── report_service.py           # Word 报告导出（python-docx）
│
├── database/
│   ├── db_connection.py            # SQLite/MySQL 双模式 + 增量迁移(_safe_add_column)
│   └── models.py                   # ORM: User, DetectionRecord, VideoTask, WorkOrder, OrderLog, VideoDetectionRecord
│
├── templates/
│   ├── index.html                  # 主检测页（三标签页 + 派发/GPS地图/视频详情/帧详情/删除确认弹窗）
│   ├── login.html                  # 登录/注册页（预置 admin/123456, worker/123456）
│   ├── dashboard.html              # 数据仪表盘（ECharts 趋势图 + 饼图 + 工单卡片）
│   ├── map.html                    # GIS 地图总览（Leaflet + OpenStreetMap）
│   ├── orders.html                 # 工单管理（并排对比/时间线/驳回/删除/重新派发弹窗）
│   └── video_player.html           # 视频播放页（HTML5 video + 时间点跳转）
│
├── static/
│   ├── css/
│   │   └── style.css               # 全局样式（Modal 遮罩防御 + 帧网格 + 时间线）
│   ├── js/
│   │   ├── common.js               # ★ 公共函数（log/apiGet/apiPost/$/notify/ModalManager/renderNavMenu + 点击诊断）
│   │   ├── auth.js                 # 认证（登录/注册/登出/密码验证/getCurrentUser + 角色路由）
│   │   ├── main.js                 # 主交互（图片/视频/摄像头 + 派发 + GPS地图 + 视频详情网格）
│   │   ├── dashboard.js            # 仪表盘（ECharts 图表 + 周期筛选）
│   │   ├── map.js                  # GIS 地图（Leaflet 标记 + 颜色分级 + Popup）
│   │   └── orders.js               # 工单管理（CRUD + 流转 + 驳回 + 删除 + 日志时间线）
│   └── uploads/ / outputs/ / repair/
│
└── work/                           # 任务说明文档（Phase 6+ 开发任务）
```

## 5. API 路由全览

### 5.1 认证（无需登录）
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/auth/login` | POST | 登录（Form: username, password）→ 写入 Cookie |
| `/api/auth/register` | POST | 注册（Form: username, password, role, full_name） |
| `/api/auth/logout` | GET | 登出，删除 Cookie |
| `/api/auth/me` | GET | 当前用户信息（未登录返回 `{data: null}`） |

### 5.2 检测（仅 Admin）
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/upload/image` | POST | 单图检测（Form: file, call_ai, call_fallback） |
| `/api/upload/batch` | POST | 批量图片（Form: files[], call_ai） |
| `/api/upload/video` | POST | 视频分析（Form: file, call_ai, fallback_interval） |
| `/api/video/progress/{task_id}` | GET | 视频进度轮询 |
| `/api/predict_frame` | POST | 摄像头帧预测（JSON: {image: "base64..."}） |

### 5.3 工单管理（需登录）
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/orders` | POST | 创建工单（运维，支持 detection_record_id 关联、annotated_image_path、ai_summary JSON 字符串） |
| `/api/orders` | GET | 工单列表（分页，检修只看自己的） |
| `/api/orders/{id}` | GET | 工单详情 |
| `/api/orders/{id}/accept` | POST | 确认接单（检修） |
| `/api/orders/{id}/submit-review` | POST | 提交复检（检修，照片 + review_remark 必填备注） |
| `/api/orders/{id}/approve` | POST | 确认闭环（运维） |
| `/api/orders/{id}/reject` | POST | 驳回（运维，Form: reason） |
| `/api/orders/{id}/reject-worker` | POST | Worker 驳回（仅 processing 状态，原因 + 补充说明） |
| `/api/orders/{id}/reassign` | POST | Admin 重新派发已驳回工单（JSON body） |
| `/api/orders/{id}/delete` | DELETE | Admin 硬删除工单（级联删除操作日志） |
| `/api/orders/{id}/logs` | GET | 工单操作日志列表 |
| `/api/repairmen` | GET | 检修人员列表（用于指派下拉框） |

### 5.4 视频检测记录（Phase 7）
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/video/records/{id}` | GET | 视频检测记录详情（含完整 frames_data） |
| `/api/video/records/{id}` | DELETE | Admin 删除视频记录 + 清理帧图片物理文件 |
| `/api/video/records/{id}/frame/{idx}` | GET | 单帧详情（含 AI 分析 + 图片 URL + 等级推断） |
| `/api/video/records/{id}/frame/{idx}/severity` | PUT | 手动修改单帧预警等级（运维/管理员，Body: severity） |
| `/api/video/records/{id}/keyframes` | GET | 关键帧列表（网格展示用） |

### 5.5 数据与状态
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/dashboard/stats?days=N` | GET | 仪表盘数据（Worker 数据隔离：仅自己的工单） |
| `/api/map/records` | GET | GIS 地图标记：`data.records`（检测记录）+ `data.orders`（未闭环工单，status≠closed，Worker 仅自己负责的） |
| `/api/system/status` | GET | GPU 硬件监控（利用率/显存/温度） |
| `/api/history` | GET | 混合查询（detection_records 图片 + t_video_detections 视频） |
| `/api/history/{id}` | GET | 记录详情 |
| `/api/history/{id}` | DELETE | Admin 删除图片记录（删标注图 + 解除工单关联） |
| `/api/history/{id}/gps` | POST | 手动更新 GPS 坐标（gps_source='manual'） |
| `/api/export/{id}` | GET | 导出 Word 报告 |
| `/api/export/video/{filename}` | GET | 下载标注视频 MP4 |
| `/api/preview/{path}` | GET | 预览图片（uploads/outputs/repair 目录） |
| `/api/status` | GET | 系统状态（模型/API Key/CUDA） |

## 6. 数据库模型

### 6.1 表清单
| 表名 | ORM 类 | 说明 |
|------|--------|------|
| `detection_records` | DetectionRecord | 图片检测记录（含 gps_lat/lng, gps_source, has_abnormal 等） |
| `t_users` | User | 用户（username 唯一, role 枚举, bcrypt 密码） |
| `t_work_orders` | WorkOrder | 工单（6状态流转 + review_remark + detection_record_id + ai_summary） |
| `t_order_logs` | OrderLog | 工单操作日志（created/accepted/submitted/approved/rejected/reassigned/deleted） |
| `t_video_tasks` | VideoTask | 视频分析任务（进度跟踪 + AI 报告 JSON） |
| `t_video_detections` | VideoDetectionRecord | 视频检测汇总（一条视频一条记录，frames_data JSON + frame_images + video_summary + file_md5） |

### 6.2 YOLO 8 类别
| ID | 类名 | 中文 | 分类 |
|----|------|------|------|
| 0 | nest | 鸟巢 | 异物 |
| 1 | kite | 风筝 | 异物 |
| 2 | balloon | 气球 | 异物 |
| 3 | trash | 垃圾 | 异物 |
| 4 | insulator_shell | 绝缘体外壳 | 正常 |
| 5 | broken_insulator_shell | 破损绝缘壳 | **缺陷** |
| 6 | flashover_damaged_insulator_shell | 闪燃损坏绝缘壳 | **缺陷** |
| 7 | good_insulator_shell | 良好绝缘壳 | 正常 |

### 6.3 工单状态流转
```
pending → processing → pending_review → closed（闭环）
                       pending_review → rejected（驳回，可重新提交）
rejected →（Admin 重新派发）→ processing
```

## 7. 关键架构决策

### 7.1 异步与线程
- **阻塞路由**：`/api/upload/*` 和 `/api/predict_frame` 使用 `def`（非 `async def`），FastAPI 自动放入线程池，不阻塞事件循环。
- **YOLO 推理锁**：`detection_service.py` 中 `threading.Lock` 限制 GPU 串行推理（RTX 4060 8G 显存有限）。
- **视频后台处理**：上传后立即返回 `task_id`，`threading.Thread` 后台处理，前端轮询进度。

### 7.2 视频处理策略（Phase 7 重构 + Phase 26 优化）
- **逐帧 YOLO**：`cap.set(CAP_PROP_POS_FRAMES, idx)` 精确跳帧，不做 `while True` 全量遍历。
- **AI 选择性调用**：仅缺陷帧（类别 5/6 且置信度 >0.7）调用 Qwen3-VL，其余帧只做 YOLO。
- **帧图片保存（Phase 7.1）**：**所有帧**保存带 YOLO 框的 JPG（缩放 800x600 保持宽高比黑边填充 + 质量 85%），存入 `static/uploads/annotated/video_{task_id}_frame_{index:06d}.jpg`。
- **汇总记录**：一条视频只产生一条 `VideoDetectionRecord`，`frames_data` JSON 存所有帧数据，`frame_images` 存所有帧图片路径。
- **VideoWriter 输出**：H.264 编码 MP4，保存至 `static/outputs/`。
- **智能跳帧（Phase 26）**：灰度帧差均值 < 30 视为画面静止，复用上一次推理结果画框跳过推理（每 5 帧强制推理一次防漏检）；检测到缺陷进入"缺陷追踪模式"密集分析，连续 30 帧干净后退出。
- **YOLO 批处理（Phase 26）**：缓冲 4 帧一次性推理（`detector.model(batch)`），RTX 4060 8G 上限，GPU 利用率提升。
- **整段视频一次性 AI 总结（Phase 27）**：**全程不再逐帧调用 AI**（API 从 N 次降为 1 次），视频处理完成后调用一次 `generate_video_summary()`：选取覆盖不同缺陷类别的代表性帧（每类 1-2 张，最多 5 张）+ 缺陷统计，生成结构化 `video_summary` 存入 `t_video_detections.video_summary`。整体严重等级/预警由总结的 `risk_level` 映射（低/中→一般，高→严重，紧急→紧急），不再依赖逐帧 AI。`call_ai=False` 且无缺陷时生成默认"良好"结论。
- **硬件编码（Phase 26）**：优先 ffmpeg stdin 管道 + `h264_nvenc`（输出尺寸强制偶数兼容 yuv420p），无 ffmpeg 或 NVENC **运行时**不可用时自动降级 libx264/OpenCV。`_has_nvenc_encoder()` 用真实短编码探测（只查 `-encoders` 会误判——最新构建可能要求更新的驱动，如 NVENC API 13.1 需驱动 ≥610）。本机已装 ffmpeg（`C:\ffmpeg\bin`，2026-02 构建，兼容驱动 581），NVENC 可用。
- **MD5 缓存（Phase 26）**：上传时流式计算文件 MD5，命中 `t_video_detections.file_md5` 已完成记录时直接返回缓存结果（含 `video_summary`，不重复调用 AI），不启动分析线程。
- **AI 总结展示（Phase 27）**：取消"AI 分析时间轴"（`renderTimeline`/`video-timeline-card` 已移除），改为 `renderVideoSummary()` 同步渲染两处——主检测页"🤖 AI 分析总结"卡片 + 视频详情弹窗总结面板；帧详情弹窗提示查看整段总结。

### 7.3 认证与安全
- **密码哈希**：直接使用 `bcrypt` 原生库（hashpw/checkpw/gensalt），**不用 passlib**（与 bcrypt 5.0 不兼容）。
- **72 字节截断**：`hash_password` 和 `verify_password` 同步截断密码至 72 字节。
- **Session 机制**：`starlette.middleware.sessions.SessionMiddleware` + `itsdangerous` 签名 Cookie。
- **预置账户**：启动时自动创建 `admin/123456`（运维）和 `worker/123456`（检修），旧密码哈希自动修复。

### 7.4 数据库
- **默认 SQLite**：文件 `database/power_inspection.db`，自动创建，零配置。
- **增量迁移**：`init_db()` 中 `_safe_add_column()` 自动为旧库添加新列（PRAGMA 检查 + ALTER TABLE），无需删库。
- **MySQL 可选**：设置 `.env` 中 `DB_TYPE=mysql` + 连接信息。
- **会话管理**：路由使用 `Depends(get_db)` 依赖注入，Service 层显式传入 `db` 参数。

### 7.5 预警与兜底检测
- **预警条件**（三重判定）：YOLO 检测到缺陷 (5/6) + 置信度 >0.7 + AI 判定"严重/紧急"。
- **兜底异物检测**：Qwen3-VL-Flash 独立调用，Prompt 要求扫描非 YOLO 覆盖的异常（塑料袋、工程机械、山火等），结果以黄色标签展示，不强制生成工单。
- **语音播报**：AI 判定"严重"或"紧急"时，前端 `window.speechSynthesis` 播报，右上角可切换开关。
- **Canvas 流光框**：摄像头帧不渲染后端静态图，前端 Canvas 2D 绘制带 shadowBlur + 渐变描边的检测框。

### 7.6 前端初始化模式（Phase 4 重构）
- **UI 事件优先**：`initTabs()` 和 `initDropzone()` 在认证检查之前执行，确保即使 auth API 失败，页面基本交互仍可用。
- **独立 try/catch**：每个初始化步骤独立包裹 try/catch，一个步骤失败不影响其他步骤。
- **空值保护**：所有 DOM 操作使用可选链 `?.` 或提前 return。
- **公共模块**：`common.js` 提供 `log()`/`apiGet()`/`apiPost()`/`$()`/`show()`/`hide()`/`notify()`/`safeInit()`。

### 7.7 CSS Modal 遮罩问题（Phase 5 修复）⚠️ 重要
- **问题**：`style.css` 中 `.modal { display: flex }` 覆盖了 Bootstrap 的 `.modal { display: none }`，导致未显示的 Modal 以 `opacity: 0` 的透明状态覆盖在页面上方拦截所有鼠标点击。
- **症状**：键盘 Tab/Enter 正常，鼠标点击全部无效。
- **修复**：
  ```css
  .modal:not(.show) {
      display: none !important;
      pointer-events: none !important;
  }
  .modal-backdrop:not(.show) {
      display: none !important;
      pointer-events: none !important;
  }
  .alert-toast { pointer-events: none; }
  .toast-item   { pointer-events: auto; }
  ```
- **教训**：自定义 CSS 绝不能覆盖 Bootstrap Modal 的 `display` 属性。

### 7.8 ModalManager（Phase 7.2）⚠️ 重要
- **问题**：关闭 Modal 后 `modal-backdrop` 残留导致页面变黑、无法点击。
- **修复**：`common.js` 中统一 `ModalManager`：
  - `open(modalId)`：销毁旧实例 → 清理残留遮罩 → 新建实例 → show
  - `close(modalId)`：hide + 延迟 300ms 清理
  - 全局 `hidden.bs.modal` 监听自动清理 `.modal-backdrop`、`modal-open` 类
- **约定**：**所有 Modal 操作必须使用 `ModalManager.open()` / `ModalManager.close()`**，禁止直接 `new bootstrap.Modal()`。
- **嵌套弹窗层级（Phase 28）**：视频详情 → 帧详情 → 生成工单（派发）的嵌套打开中，`dispatch-modal` 会因 z-index 低于上层被遮挡。修复双重保障：① `#dispatch-modal { z-index: 9999 !important }`；② `createOrderFromFrame()` 先 `ModalManager.close('frameDetailModal')`，**延迟 350ms** 等 backdrop 清理完成后再打开派发弹窗（`ModalManager.close` 的 300ms 清理窗口内立即 open 会误删新弹窗 backdrop，形成竞态）。
- **派发工单 GPS 小地图（Phase 28）**：`dispatch-modal` 底部嵌入 Leaflet 小地图 `initDispatchMap()`（复用 `initGpsMiniMap` 模式：点击地图填充经纬度 + 可拖动 Marker），每次打开前销毁旧实例。

### 7.10 移动端优化（Phase 8）📱
- **设备判断**：`common.js` 全局 `isMobile()` —— `window.innerWidth < 768`（与 Bootstrap md 断点一致），所有优化仅移动端启用，电脑端行为完全不变。
- **图片上传前压缩**（`main.js` `compressImage()`）：移动端 canvas 绘制缩放（长边 1024px）+ JPEG 质量 0.7，压缩失败回退原文件。公网链路传输时间缩短 60-80%。
- **检测结果精简返回**：`api_upload_image` 增加 `mobile` 表单参数；`detection_service.py` 生成标注图缩略图（长边 480px，`_thumb.jpg`，`save_image_thumb()`），移动端返回时 `annotated_image_path` 替换为缩略图路径（AI 分析文本保持完整），前端拼 `/api/preview/` URL 逻辑无需改动。
- **列表分页**：`main.js` `loadHistory()` / `orders.js` `loadOrders()` 移动端 page_size 15→10。
- **视频轮询**：`main.js` `startVideoPolling()` 移动端间隔 1.5s→5s；`document.hidden` 时跳过请求（切后台自动暂停）。
- **缩略图命名约定**：`annotated_{ts}_{原文件名}_thumb.jpg`，与标注图同目录 `static/uploads/annotated/`，可被 `/api/preview` 正常访问。

### 7.9 CDN 依赖与网络
- **Bootstrap 5.3.3**：CSS + JS 均从 `cdn.jsdelivr.net` 加载。
- **ECharts 5.5.0**：仅 `dashboard.html` 使用。
- **Leaflet 1.9.4**：`map.html` + `index.html`（GPS 地图点选）使用，CSS 在 `<head>`，JS 在 `</body>` 前。
- **Chart.js**：项目中**未使用**。

## 8. 开发约定
- **命名**：变量/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_CASE`。
- **注释**：中文注释，解释"为什么"而非"做了什么"。
- **错误处理**：API 层 `HTTPException`，Service 层 `raise` 由调用方捕获。
- **环境变量**：全部可配项集中在 `.env`，模板 `.env.example`。修改 `.env` 后重启生效。
- **新增 Service**：模块文件放在 `services/`，通过 `app.py` 路由导入调用。
- **新增页面**：HTML 放在 `templates/`，JS 放在 `static/js/`，在 `app.py` 添加 `@app.get` 路由渲染模板。
- **新增 JS**：必须在对应 HTML 中以正确顺序引用（common.js → auth.js → 专属JS）。
- **CSS**：修改 `style.css` 时注意不要覆盖 Bootstrap 核心属性（特别是 `display`、`position`、`z-index`）。
- **权限控制**：检测 API 使用 `Depends(_require_inspector_or_admin)`；Worker 页面访问 `/` 自动重定向 `/dashboard`。
- **Git 发布**：训练数据（BaiduData/my_dataset/Insulator*）、模型权重（runs/、yolo*.pt）、.env、app.log 不提交 GitHub。

## 9. 故障排查指南

### 9.1 页面点击无反应
1. 打开 F12 控制台，查看 `[CLICK-DIAG]` 日志，确认点击被哪个元素接收。
2. 如果不期望的元素（如 `.modal`）接收了点击 → 检查 `style.css` 的 Modal 遮罩防御规则。
3. 如果页面变黑无法点击 → 检查是否有 `.modal-backdrop` 残留 → 确认使用 `ModalManager.close()` 关闭弹窗。
4. 如果无 `[CLICK-DIAG]` 日志 → `common.js` 未加载，检查脚本加载顺序。
5. 尝试**硬刷新**（Ctrl+Shift+R）清除浏览器缓存。

### 9.2 页面功能不完整
1. 检查 F12 控制台是否有 JS 错误。
2. 验证 `[common.js] 已加载` 日志出现。
3. 检查对应页面的 `[xxx.js] 已加载` 日志。
4. 确认脚本加载顺序：common.js → auth.js → 页面 JS。

### 9.3 认证问题
1. 预置账户不生效 → 删除 `database/power_inspection.db` 重启服务。
2. 密码错误 → 确认密码在 4-72 字符之间。
3. `bcrypt` 相关错误 → 确认使用 `import bcrypt` 原生库而非 `passlib`。

### 9.4 YOLO 模型问题
1. 模型路径：`runs/train/yolo_train_*/weights/best.pt`
2. 确认 CUDA 可用：`python -c "import torch; print(torch.cuda.is_available())"`
3. GPU 显存不足 → 减小 `imgsz` 或 batch size。

### 9.5 视频详情弹窗问题
1. 缩略图黑块 → 确认视频是新流程处理（Phase 7.1+ 所有帧存图），旧数据仅缺陷帧有图属正常降级。
2. 遮罩残留 → 用 `ModalManager.open/close`，勿直接 new bootstrap.Modal。
3. 视频处理失败 → 查看 `app.log` 中 `视频异常` 日志。

### 9.6 CDN 加载慢
1. Bootstrap CSS/JS 从 jsdelivr CDN 加载，国内可能慢。
2. 如持续超时，可下载 Bootstrap 到 `static/` 本地引用。
3. ECharts 和 Leaflet 仅特定页面使用，不影响主检测页。

### 9.7 图片预览 URL 拼接错误（Phase 29 修复）⚠️
1. **症状**：视频帧派发工单 / 工单详情 / 检测结果中的图片显示空白或裂图。
2. **原因**：`split('/static/uploads/')` 对**无前导斜杠**的路径（如 `frame_images` 里的 `static/uploads/annotated/x.jpg`）匹配失败，返回整串路径，导致 URL 变成 `/api/preview/static/uploads/...` 而 404。对带前导斜杠的路径（`/static/uploads/...`）才能正常分割。
3. **修复**：统一使用 `common.js` 的 `buildPreviewUrl(path)`，正则 `(?:^|\/)static\/uploads\/(.+)$` 兼容相对/URL/Windows 绝对路径三种格式。
4. **教训**：拼接 `/api/preview/` URL 一律用 `buildPreviewUrl()`，禁止直接用 `split('/static/uploads/')` 手工处理路径。

### 9.8 工单详情无图片（Worker 端）（Phase 29 修复）⚠️
1. **症状**：派发工单后，检修人员（Worker）在工单详情中看不到缺陷图（图片记录派发与视频帧派发都可能出现）。
2. **原因**：`POST /api/orders` 需显式传 `annotated_image_path`。视频帧派发（`currentDispatchRecordId=null`）未传图片路径；图片记录派发只传 `detection_record_id`，而 `create_work_order` 此前不自动补图。
3. **修复**：
   - `create_work_order`：`detection_record_id` 关联时自动从 DetectionRecord 补 `annotated_image_path`/`original_image_path`
   - `api_order_detail`：旧工单（无图但有 `detection_record_id`）详情时自动补
   - 前端：视频帧派发记录 `currentDispatchImagePath`（当前帧 `image_path`），`submitDispatch` 时作为 `annotated_image_path` 传给后端
4. **注意**：`create_work_order` 不通过 `detection_record_id` 自动关联图片是历史设计，补图逻辑需维护两处（创建 + 详情兼容）。
