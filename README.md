# 电力输电线路智能检测分析预警系统 v3.0

基于 **YOLO + Qwen3-VL-Flash** 的电力输电线路智能巡检平台。支持图片 / 视频 / 摄像头多源输入，YOLO 缺陷检测、Qwen3-VL AI 语义分析、兜底异物检测、分级预警、工单闭环管理、GIS 地图总览、数据仪表盘、GPU 硬件监控、Word 报告导出。

> 前端界面 v3.1 已上线：电光蓝品牌视觉升级，登录页分栏布局、仪表盘统计卡重构等（详见[前端界面 v3.1 视觉升级](#前端界面-v31-视觉升级)）。

---

## 一、功能特性

### 智能检测
- **图片检测**：单张 / 批量上传，YOLO 8 分类检测 + Qwen3-VL AI 语义分析（严重程度 / 成因 / 建议）
- **视频分析**：智能跳帧 + YOLO 批量推理 + NVENC 硬件编码，整段视频一次性 AI 总结（全程仅调用 1 次 API）
- **摄像头实时检测**：浏览器摄像头逐帧 YOLO 推理（不调用 AI），Canvas 流光检测框

### 预警与工单
- **三重预警判定**：YOLO 缺陷 + 置信度阈值 + AI 严重等级，语音播报
- **工单全流程闭环**：创建 → 派发 → 检修接单 → 提交复检（照片+备注）→ 闭环 / 驳回 → 重新派发
- **单帧等级手动修改**：任意帧可手动改预警等级（正常/一般/严重/紧急）→ 生成工单，修改记录可追溯
- 派发工单附带 AI 摘要与缺陷图，Worker 端工单详情完整展示

### 管理与可视化
- **Admin / Worker 双角色**：前后端双重权限控制 + 数据隔离
- **GIS 地图总览**（Leaflet）：检测记录 GPS 标记 + **未闭环工单位置**（📌 状态徽标）
- **数据仪表盘**（ECharts）、**GPU 硬件监控**、**Word 报告导出**

---

## 二、技术栈

| 类别 | 技术 |
|------|------|
| 后端 | Python 3.9+ / FastAPI / SQLAlchemy / SQLite（可选 MySQL） |
| AI | Ultralytics YOLO / PyTorch / DashScope Qwen3-VL-Flash |
| 视觉 | OpenCV / ffmpeg（NVENC 硬件编码） |
| 前端 | Bootstrap 5.3 / ECharts 5 / Leaflet.js / Jinja2 |
| 认证 | bcrypt + Session Cookie |

---

## 三、快速开始

### 1. 环境准备

```bash
conda activate E:\anaconda_environment\pytorch_for_GPU
cd E:\WorkSpace\TrainModel
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 2. 配置

编辑 `.env` 文件（模板见 `.env.example`）：

```env
DASHSCOPE_API_KEY=你的key
MAX_VIDEO_SECONDS=60    # 视频分析最大时长
YOLO_MODEL_PATH=./runs/train/yolo_train_*/weights/best.pt
```

可选：安装 ffmpeg 以启用 NVENC 硬件编码（无 ffmpeg 时自动降级 libx264/OpenCV）。

### 3. 启动

```bash
python app.py
```

浏览器访问：
- **主界面**: http://localhost:5000
- **API 文档**: http://localhost:5000/docs

预置账户：
- **Admin（运维）**：`admin / 123456`
- **Worker（检修）**：`worker / 123456`

---

## 四、页面路由

| 路由 | 页面 | 说明 |
|------|------|------|
| `/` | 主检测页 | 图片 / 视频 / 摄像头三标签 |
| `/login` | 登录注册 | 无需认证，预置账户 |
| `/dashboard` | 数据仪表盘 | ECharts 趋势图 + 饼图 + 工单卡片 |
| `/map` | GIS 地图总览 | 检测记录 + 未闭环工单 GPS 标记 |
| `/orders` | 工单管理 | 工单 CRUD + 状态流转 + 日志时间线 |
| `/video/play/{id}` | 视频播放 | HTML5 播放 + 时间点跳转 |

---

## 五、前端界面 v3.1 视觉升级

在既有 Apple 风格基础上升级为"现代 SaaS 控制台"视觉语言，**纯前端改动、零功能影响**（业务元素 `id`/`onclick`/`data-*` 均未改动）。

### 设计语言
- **品牌色**：电光蓝渐变 `#0a84ff → #0857cc`，贯穿主按钮、品牌标识、活跃导航、卡片标题装饰条
- **圆角 / 阴影**：卡片 16px 大圆角、多层柔和阴影，提升容器层次
- **文字层级**：页面标题 + 副标题 + 三级灰阶，降低认知负担
- **微交互**：按钮悬浮上浮、上传区图标缩放、弹窗弹性入场、页面淡入

### 主要页面
- **登录页**：重构为左右分栏 —— 左侧深蓝品牌区（系统定位 + 核心能力），右侧表单区；窄屏自动隐藏品牌区
- **检测中心**：页头（标题 + 副标题）、上传区图标徽章、AI 开关归入"选项面板"卡片
- **数据仪表盘**：统计卡改为"图标徽章 + 左对齐大数字 + 底部状态色条"
- **工单管理**：筛选工具栏升级为面板卡片，表格表头加底色圆角
- **GIS 地图**：图例统一由全局样式管理（玻璃拟态卡片）

---

## 六、视频分析优化（Phase 26-30 亮点）

| 优化项 | 说明 |
|--------|------|
| **移动端压缩** | ffmpeg.wasm 上传前压缩至 720P，公网上传缩短约 70% |
| **进度可视化** | 进度条 + 预估剩余时间 + 已用时间 |
| **智能跳帧** | 场景变化检测，静止画面复用推理结果（缩短 40-60%） |
| **YOLO 批处理** | 4 帧批量推理，GPU 利用率提升 2-3 倍 |
| **整段 AI 总结** | 全程只调用 1 次 API 生成 `video_summary`（取代逐帧 AI） |
| **NVENC 编码** | ffmpeg 硬件编码标注视频，合成提速 5-10 倍 |
| **MD5 缓存** | 相同视频重复上传秒级返回缓存结果 |
| **单帧等级修改** | 任意帧手动改预警等级 → 生成工单 |
| **未闭环工单地图** | 地图总览标注未闭环工单具体位置 |

---

## 七、YOLO 8 类别

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

---

## 八、数据库

- **默认 SQLite**：零配置，文件 `database/power_inspection.db`
- **可选 MySQL**：修改 `.env` 中 `DB_TYPE=mysql` 及相关连接信息
- **增量迁移**：`_safe_add_column` 自动为旧库补充新列，升级无需删库

核心表：

| 表名 | 说明 |
|------|------|
| `detection_records` | 图片检测记录（含 GPS） |
| `t_video_detections` | 视频检测汇总（一条视频一条记录，含 `video_summary` / `file_md5`） |
| `t_video_tasks` | 视频分析任务进度 |
| `t_work_orders` | 工单（含 `ai_summary` 字段） |
| `t_order_logs` | 工单操作日志 |
| `t_users` | 用户（bcrypt 密码哈希） |

---

## 九、API 概览

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/auth/login` | POST | 登录（写入 Session Cookie） |
| `/api/upload/image` | POST | 图片检测 |
| `/api/upload/batch` | POST | 批量图片检测 |
| `/api/upload/video` | POST | 视频分析（含 MD5 缓存） |
| `/api/video/progress/{id}` | GET | 视频进度轮询 |
| `/api/video/records/{id}/frame/{idx}/severity` | PUT | 修改单帧预警等级 |
| `/api/orders` | POST / GET | 工单创建 / 列表 |
| `/api/map/records` | GET | 地图标记（检测记录 + 未闭环工单） |
| `/api/dashboard/stats` | GET | 仪表盘数据 |
| `/api/system/status` | GET | GPU 硬件监控 |
| `/api/export/{id}` | GET | Word 报告导出 |

完整 API 文档见 `/docs`（Swagger UI）。

---

## 十、常见问题

**Q: 视频分析很慢？**
A: 默认处理前 60 秒。已启用智能跳帧 + YOLO 批量推理 + NVENC 硬件编码，速度较逐帧分析大幅提升。

**Q: 输出视频为空 / NVENC 编码失败？**
A: 系统优先使用 ffmpeg `h264_nvenc`（需 NVIDIA 驱动满足编码器要求）。不支持时自动降级 libx264 / OpenCV，不会中断分析。

**Q: AI 分析返回空？**
A: 检查 `.env` 中 `DASHSCOPE_API_KEY` 是否正确，且网络能访问阿里云 DashScope API。

**Q: 摄像头无法开启？**
A: 需要 HTTPS 或 localhost 环境，浏览器需授予摄像头权限。

---

## 十一、目录结构

```
app.py                          # FastAPI 主程序（全部路由）
models/yolo_model.py            # YOLO 检测器封装
services/                       # 检测/AI/工单/认证/监控/报告服务
database/                       # SQLAlchemy 模型 + 连接 + 增量迁移
templates/                      # Jinja2 页面（index/dashboard/map/orders/video_player）
static/css,js,uploads,outputs   # 前端资源与上传文件
work/ui_beautify/               # 前端 v3.1 美化开发记录（before/after 截图与对比图，不入库）
```

> **注意**：训练数据（BaiduData/、my_dataset/、Insulator*）、模型权重（runs/、*.pt）、训练脚本（train_yolo26.py、convert_and_merge.py）、.env、app.log、数据库、上传文件及 `work/ui_beautify` 截图均不随仓库发布（见 `.gitignore`）。
