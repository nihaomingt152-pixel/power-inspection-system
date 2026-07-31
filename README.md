# 电力输电线路智能检测分析预警系统 v2.0

基于 **YOLO + Qwen3-VL-Flash** 的电力巡检智能分析平台。  
支持图片检测、视频逐帧分析（带输出视频合成）、实时摄像头检测。

---

## 一、v2.0 更新要点

| 改进项 | 说明 |
|--------|------|
| **后端架构修复** | 阻塞路由改用 `def` 线程池、DB 会话依赖注入、临时文件 finally 清理、YOLO 推理串行锁 |
| **视频处理重构** | 逐帧 YOLO（全帧） + 仅缺陷帧调用 AI（`cap.set` 精确跳帧，VideoWriter 合成输出 MP4） |
| **新 API** | 视频进度轮询 `/api/video/progress/{id}`、输出视频下载、摄像头帧预测 `/api/predict_frame` |
| **三标签页 UI** | 图片检测（批量网格卡片）、视频分析（双播放器 + AI 时间轴）、实时摄像头（YOLO 实时推理） |
| **数据库** | 新增 `t_video_tasks` 表跟踪视频分析进度与 AI 报告 |

---

## 二、快速开始

### 1. 环境准备

```bash
conda activate E:\anaconda_environment\pytorch_for_GPU
cd E:\WorkSpace\TrainModel
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 2. 配置 API Key

编辑 `.env` 文件（已生成）：

```env
DASHSCOPE_API_KEY=你的key
MAX_VIDEO_SECONDS=60    # 视频分析最大时长，可调整
```

### 3. 启动

```bash
python app.py
```

浏览器访问：
- **主界面**: http://localhost:5000
- **API 文档**: http://localhost:5000/docs

---

## 三、功能模块

### Tab 1 - 图片检测

- 支持拖拽/点击上传单张或多张图片
- 单张：完整 YOLO + AI 分析 + 详情面板
- 批量：网格卡片展示，每张独立标注，点击放大查看 AI 报告
- 红/黄/绿标签区分严重程度

### Tab 2 - 视频分析

- 上传视频 → 后台逐帧处理
- **策略**: 每帧 YOLO 推理 + 仅缺陷帧（类别 5/6 且置信度 > 0.7）调用 AI
- 实时进度条 + 处理帧数显示
- **双视频对比**: 原始 vs YOLO 标注输出（支持下载）
- **AI 时间轴**: 点击时间点跳转视频对应位置

### Tab 3 - 实时摄像头

- 调用浏览器摄像头（`getUserMedia`）
- 每 300ms 截帧 → 后端 YOLO 推理（**不调用 AI 大模型**）
- 实时显示原始画面 vs 检测结果
- 滚动日志显示最近 10 条检测结果

---

## 四、API 接口

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/upload/image` | POST | 单张图片检测 |
| `/api/upload/batch` | POST | 批量图片检测 |
| `/api/upload/video` | POST | 上传视频，返回 task_id |
| `/api/video/progress/{id}` | GET | 轮询视频进度 |
| `/api/export/video/{filename}` | GET | 下载标注视频 |
| `/api/predict_frame` | POST | 摄像头帧预测（仅 YOLO） |
| `/api/history` | GET | 历史记录（分页） |
| `/api/history/{id}` | GET | 记录详情 |
| `/api/export/{id}` | GET | 导出 Word 报告 |
| `/api/status` | GET | 系统状态 |

---

## 五、数据库

- **默认 SQLite**：零配置，数据库文件 `database/power_inspection.db`
- **可选 MySQL**：修改 `.env` 中 `DB_TYPE=mysql` 及相关连接信息

新增 `t_video_tasks` 表结构：

| 字段 | 说明 |
|------|------|
| `task_id` | UUID 任务标识 |
| `status` | pending / processing / completed / failed |
| `total_frames` | 总帧数 |
| `processed_frames` | 已处理帧数 |
| `output_video_path` | 输出视频路径 |
| `ai_reports` | JSON 数组，关键帧 AI 报告 |

---

## 六、YOLO 类别

| ID | 名称 | 分类 |
|----|------|------|
| 0 | nest | 异物 |
| 1 | kite | 异物 |
| 2 | balloon | 异物 |
| 3 | trash | 异物 |
| 4 | insulator_shell | 正常 |
| 5 | broken_insulator_shell | **缺陷** |
| 6 | flashover_damaged_insulator_shell | **缺陷** |
| 7 | good_insulator_shell | 正常 |

---

## 七、摄像头使用说明

1. 切换到「实时摄像头」标签页
2. 点击「开启摄像头」→ 浏览器弹出权限请求 → 允许
3. 系统自动每 300ms 截帧送后端 YOLO 推理
4. 右侧实时显示检测框标注结果
5. 底部日志滚动显示检测到的目标
6. 点击「暂停」释放摄像头和 GPU 资源

> **注意**：摄像头模式不调用 AI 大模型，仅做 YOLO 推理，延迟约 50-150ms/帧。
> 关闭页面或切换标签时摄像头自动释放。

---

## 八、常见问题

**Q: 视频处理很慢？**  
A: 逐帧 YOLO 推理受 GPU 性能限制。默认处理前 60 秒（约 1500 帧），可在 `.env` 调整 `MAX_VIDEO_SECONDS`。

**Q: AI 分析返回空？**  
A: 检查 `.env` 中 `DASHSCOPE_API_KEY` 是否正确，且网络能访问阿里云 DashScope API。

**Q: 摄像头无法开启？**  
A: 需要 HTTPS 或 localhost 环境，浏览器需授予摄像头权限。Chrome/Firefox/Edge 均支持。
