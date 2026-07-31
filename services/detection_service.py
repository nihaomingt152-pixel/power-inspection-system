#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检测服务模块（重构版）。
修复要点：
  - 引入 threading.Lock 限制 GPU 并发推理
  - 视频处理改为逐帧 YOLO + 选择性 AI（仅缺陷帧调用大模型）
  - 使用 cap.set(CAP_PROP_POS_FRAMES) 精确帧跳转
  - 使用 VideoWriter 合成带标注的输出视频
  - db 会话通过参数显式传入
"""

import os
import sys
import time
import uuid
import base64
import logging
import threading
import tempfile
from io import BytesIO
from pathlib import Path
from datetime import datetime
from typing import Optional

import cv2
import numpy as np
from PIL import Image
from dotenv import load_dotenv

from models.yolo_model import YOLODetector
from services.multimodal_service import MultimodalService
from services.alert_service import AlertService
from database.models import DetectionRecord, VideoTask, VideoDetectionRecord

load_dotenv()

logger = logging.getLogger(__name__)

# ---- 全局单例与锁 ----
_detector: Optional[YOLODetector] = None
_detector_lock = threading.Lock()
# GPU 推理串行锁（RTX 4060 8G 显存限制，同时仅允许 1 个推理任务）
_inference_lock = threading.Lock()

# ---- 配置 ----
MAX_VIDEO_SECONDS = int(os.getenv("MAX_VIDEO_SECONDS", "60"))
VIDEO_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "static" / "outputs"


def get_detector() -> YOLODetector:
    """获取 YOLO 检测器单例（双重检查锁定，线程安全）。"""
    global _detector
    if _detector is None:
        with _detector_lock:
            if _detector is None:
                _detector = YOLODetector()
    return _detector


def extract_exif_info(image_path: str) -> dict:
    """从图片 EXIF 信息中提取 GPS 坐标和拍摄时间戳。"""
    result = {"latitude": None, "longitude": None, "timestamp": None}
    try:
        from PIL.ExifTags import TAGS, GPSTAGS
        img = Image.open(image_path)
        exif_data = img._getexif()
        if exif_data is None:
            return result

        for tag_id, value in exif_data.items():
            tag_name = TAGS.get(tag_id, tag_id)
            if tag_name == "DateTimeOriginal":
                try:
                    result["timestamp"] = datetime.strptime(
                        str(value), "%Y:%m:%d %H:%M:%S"
                    )
                except (ValueError, TypeError):
                    pass
                break

        gps_info = {}
        for tag_id, value in exif_data.items():
            tag_name = TAGS.get(tag_id, tag_id)
            if tag_name == "GPSInfo":
                for gps_tag_id, gps_value in value.items():
                    gps_tag_name = GPSTAGS.get(gps_tag_id, gps_tag_id)
                    gps_info[gps_tag_name] = gps_value
                break

        if gps_info:
            try:
                def _to_degrees(v):
                    d, m, s = v
                    return float(d) + float(m) / 60.0 + float(s) / 3600.0
                if "GPSLatitude" in gps_info and "GPSLatitudeRef" in gps_info:
                    lat = _to_degrees(gps_info["GPSLatitude"])
                    if gps_info["GPSLatitudeRef"] == "S":
                        lat = -lat
                    result["latitude"] = round(lat, 6)
                if "GPSLongitude" in gps_info and "GPSLongitudeRef" in gps_info:
                    lon = _to_degrees(gps_info["GPSLongitude"])
                    if gps_info["GPSLongitudeRef"] == "W":
                        lon = -lon
                    result["longitude"] = round(lon, 6)
            except (KeyError, TypeError, ZeroDivisionError):
                logger.warning(f"GPS 解析失败: {image_path}")
        img.close()
    except Exception as e:
        logger.warning(f"EXIF 提取失败: {image_path}, 错误: {e}")
    return result


def save_annotated_image(image: np.ndarray, original_name: str, output_dir: str) -> str:
    """保存带标注的图片。"""
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"annotated_{ts}_{Path(original_name).name}"
    output_path = os.path.join(output_dir, filename)
    cv2.imwrite(output_path, image)
    return output_path


def resize_frame(frame: np.ndarray, target_width: int = 800, target_height: int = 600) -> np.ndarray:
    """将帧缩放到目标尺寸（保持宽高比，不足部分用黑边填充）。"""
    h, w = frame.shape[:2]
    if h == 0 or w == 0:
        return frame
    scale = min(target_width / w, target_height / h)
    new_w, new_h = int(w * scale), int(h * scale)
    resized = cv2.resize(frame, (new_w, new_h))
    # 缩放后小于目标尺寸的部分用黑边填充
    padded = cv2.copyMakeBorder(
        resized,
        0, target_height - new_h,
        0, target_width - new_w,
        cv2.BORDER_CONSTANT, value=[0, 0, 0],
    )
    return padded


def _should_call_ai(detections: list) -> bool:
    """
    判断是否应调用 AI 大模型分析。
    仅在检测到 broken_insulator_shell(5) 或 flashover_damaged_insulator_shell(6)
    且置信度 > 0.7 时才返回 True，节省 API 调用成本和耗时。
    """
    for d in detections:
        if d["class_id"] in (5, 6) and d["confidence"] > 0.7:
            return True
    return False


# ============================================================
# 单张图片处理（重构：db 参数化 + 推理锁）
# ============================================================

def process_single_image(
    image_path: str,
    db=None,
    save_annotated: bool = True,
    call_ai: bool = True,
) -> dict:
    """
    处理单张图片：YOLO 检测 + AI 分析 + 预警 + 入库。

    Args:
        image_path: 图片路径
        db: SQLAlchemy 会话（可选，传入则使用此会话）
        save_annotated: 是否保存标注图
        call_ai: 是否调用 AI
    """
    total_start = time.time()
    detector = get_detector()
    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(f"无法读取图片: {image_path}")

    # --- YOLO 检测（加锁，串行推理）---
    with _inference_lock:
        detections, yolo_time_ms = detector.detect(image)

    classification = detector.classify_detections(detections)
    annotated = detector.draw_detections(image, detections)
    exif = extract_exif_info(image_path)

    annotated_path = None
    if save_annotated:
        out_dir = Path(__file__).resolve().parent.parent / "static" / "uploads" / "annotated"
        annotated_path = save_annotated_image(annotated, image_path, str(out_dir))

    # --- AI 分析 ---
    ai_result = None
    ai_time_ms = None
    if call_ai and detections:
        try:
            svc = MultimodalService()
            ai_result, ai_time_ms = svc.analyze(
                image_path=image_path,
                detections=detections,
                classification=classification,
            )
        except Exception as e:
            logger.warning(f"AI 分析失败: {e}")
            ai_result = {
                "description": "AI 分析暂不可用",
                "severity": "一般",
                "cause": "无法确定",
                "suggestion": "请人工复核",
                "alert": False,
            }

    alert_result = AlertService.evaluate(detections, ai_result, classification)
    total_time_ms = (time.time() - total_start) * 1000

    # --- 入库 ---
    record_id = None
    own_db = db is None
    if own_db:
        from database.db_connection import get_db_session
        db = get_db_session()
    try:
        record = DetectionRecord(
            source_type="image",
            source_name=Path(image_path).name,
            original_image_path=str(image_path),
            annotated_image_path=annotated_path,
            gps_latitude=exif["latitude"],
            gps_longitude=exif["longitude"],
            gps_lat=exif["latitude"],
            gps_lng=exif["longitude"],
            gps_source="exif" if exif["latitude"] is not None else "none",
            capture_timestamp=exif["timestamp"],
            yolo_detections=detections,
            total_detections=classification["total"],
            defect_count=classification["defect_count"],
            normal_count=classification["normal_count"],
            ai_analysis=ai_result,
            alert_triggered=alert_result["triggered"],
            alert_level=alert_result["level"],
            alert_message=alert_result["message"],
            yolo_time_ms=round(yolo_time_ms, 2),
            ai_time_ms=round(ai_time_ms, 2) if ai_time_ms else None,
            total_time_ms=round(total_time_ms, 2),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        record_id = record.id
    except Exception as e:
        logger.error(f"数据库写入失败: {e}")
        if own_db:
            db.rollback()
    finally:
        if own_db:
            db.close()

    return {
        "record_id": record_id,
        "detections": detections,
        "classification": classification,
        "exif": exif,
        "ai_analysis": ai_result,
        "alert": alert_result,
        "timing": {
            "yolo_ms": round(yolo_time_ms, 2),
            "ai_ms": round(ai_time_ms, 2) if ai_time_ms else None,
            "total_ms": round(total_time_ms, 2),
        },
        "annotated_image_path": annotated_path,
    }


# ============================================================
# 视频逐帧处理（重构核心：全帧 YOLO + 关键帧 AI + VideoWriter）
# ============================================================

def process_video_file(
    video_path: str,
    db,
    task_id: str,
    call_ai: bool = True,
    max_seconds: int = None,
    user_id: int = None,
) -> dict:
    """
    处理视频文件：逐帧 YOLO 推理 + 仅缺陷帧调用 AI + 合成输出视频。

    策略：
      - 使用 cap.set(CAP_PROP_POS_FRAMES) 精确跳帧（避免 while True 全量遍历）
      - 默认处理前 N 秒（MAX_VIDEO_SECONDS 环境变量，默认 60）
      - 每帧都做 YOLO 检测和画框
      - 仅当检测到类别 5/6 且置信度 > 0.7 时才调用 AI
      - 使用 VideoWriter 合成带检测框的 MP4 输出视频

    Args:
        video_path: 视频文件路径
        db: SQLAlchemy 会话（用于进度更新）
        task_id: VideoTask 的 UUID
        call_ai: 是否启用 AI 分析
        max_seconds: 最大处理时长（秒），默认取环境变量

    Returns:
        {"task_id": ..., "output_video": ..., "ai_reports": [...], "stats": {...}}
    """
    if max_seconds is None:
        max_seconds = MAX_VIDEO_SECONDS

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"无法打开视频: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 25.0  # 兜底
    orig_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames_in_video = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # 计算实际处理的帧范围
    max_frames = min(int(max_seconds * fps), total_frames_in_video)
    # 输出视频统一缩放到 720P
    out_w, out_h = 1280, 720
    if orig_width > 0 and orig_height > 0:
        scale = min(out_w / orig_width, out_h / orig_height)
        out_w = int(orig_width * scale)
        out_h = int(orig_height * scale)

    # 创建输出视频目录
    VIDEO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_filename = f"output_{task_id[:8]}_{Path(video_path).stem}.mp4"
    output_path = str(VIDEO_OUTPUT_DIR / output_filename)

    fourcc = cv2.VideoWriter_fourcc(*"avc1")  # H.264
    if fourcc == -1:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # 兜底
    writer = cv2.VideoWriter(output_path, fourcc, fps, (out_w, out_h))
    if not writer.isOpened():
        # 尝试其他编码器
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_path, fourcc, fps, (out_w, out_h))

    detector = get_detector()
    ai_reports = []
    frames_data = []        # 所有帧的检测数据
    keyframe_images = []    # 关键帧图片路径（兼容旧字段，保留缺陷帧）
    frame_images = []       # 所有帧的图片路径（Phase 7.1）
    max_severity = "一般"    # 最高严重等级
    severity_rank = {"一般": 0, "严重": 1, "紧急": 2}
    stats = {"total_frames": max_frames, "yolo_detected": 0, "ai_analyzed": 0,
             "alerts": 0, "defect_frames": []}

    # 关键帧图片保存目录
    keyframe_dir = Path(__file__).resolve().parent.parent / "static" / "uploads" / "annotated"
    keyframe_dir.mkdir(parents=True, exist_ok=True)

    # 更新 VideoTask：开始处理
    task = db.query(VideoTask).filter(VideoTask.task_id == task_id).first()
    if task:
        task.status = "processing"
        task.total_frames = max_frames
        task.processed_frames = 0
        db.commit()

    logger.info(f"视频处理开始: {video_path}, 前 {max_seconds}s, 共 {max_frames} 帧")

    # 逐帧处理（使用 cap.set 精确跳转，不依赖 while True）
    for frame_idx in range(max_frames):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            logger.warning(f"读取帧 {frame_idx} 失败，跳过")
            continue

        frame_sec = frame_idx / fps

        # 缩放到输出尺寸
        if (orig_width, orig_height) != (out_w, out_h):
            frame = cv2.resize(frame, (out_w, out_h))

        # YOLO 推理（加锁）
        with _inference_lock:
            detections, yolo_ms = detector.detect(frame)

        if detections:
            stats["yolo_detected"] += 1

        classification = detector.classify_detections(detections)
        annotated = detector.draw_detections(frame, detections)

        # 写入输出视频
        writer.write(annotated)

        # ---- AI 选择性调用：仅高置信缺陷帧 ----
        ai_result = None
        ai_ms = None
        if call_ai and _should_call_ai(detections):
            try:
                # 保存当前帧的临时图片用于 AI 分析
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    cv2.imwrite(tmp.name, annotated)
                    tmp_path = tmp.name
                svc = MultimodalService()
                ai_result, ai_ms = svc.analyze(
                    image_path=tmp_path,
                    detections=detections,
                    classification=classification,
                )
                stats["ai_analyzed"] += 1
                # 记录到 AI 报告列表
                ai_reports.append({
                    "timestamp": round(frame_sec, 2),
                    "frame_index": frame_idx,
                    "description": ai_result.get("description", "")[:300],
                    "severity": ai_result.get("severity", "未知"),
                    "cause": ai_result.get("cause", ""),
                    "suggestion": ai_result.get("suggestion", ""),
                })
                # 清理临时文件
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            except Exception as e:
                logger.warning(f"帧 {frame_idx} AI 分析失败: {e}")

        alert_result = AlertService.evaluate(detections, ai_result, classification)
        if alert_result["triggered"]:
            stats["alerts"] += 1
            stats["defect_frames"].append({
                "frame_index": frame_idx,
                "timestamp": round(frame_sec, 2),
                "severity": alert_result.get("severity", "未知"),
            })

        # 追踪最高严重等级
        sev = ai_result.get("severity", "一般") if ai_result else "一般"
        if severity_rank.get(sev, 0) > severity_rank.get(max_severity, 0):
            max_severity = sev

        # 判断是否有缺陷
        has_defect = detections and any(d["class_id"] in (5, 6) for d in detections)

        # 所有帧都保存带 YOLO 框的图片（Phase 7.1：彻底解决"无目标"黑块问题）
        frame_filename = f"video_{task_id[:8]}_frame_{frame_idx:06d}.jpg"
        frame_image_path = str(keyframe_dir / frame_filename)
        # 缩放至 800x600（保持宽高比，黑边填充）+ JPG 85% 质量
        resized = resize_frame(annotated)
        success = cv2.imwrite(frame_image_path, resized, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            logger.error(f"帧图片保存失败: {frame_image_path}")
        elif not os.path.exists(frame_image_path) or os.path.getsize(frame_image_path) < 100:
            logger.error(f"帧图片文件异常（空或过小）: {frame_image_path} ({os.path.getsize(frame_image_path) if os.path.exists(frame_image_path) else 'N/A'} bytes)")
        else:
            frame_images.append({
                "frame_index": frame_idx,
                "timestamp": round(frame_sec, 2),
                "image_path": f"static/uploads/annotated/{frame_filename}",
                "has_defect": has_defect,
                "severity": sev if has_defect else "一般",
            })
            # 兼容旧字段：缺陷帧也记录到 keyframe_images
            if has_defect:
                keyframe_images.append({
                    "frame_index": frame_idx,
                    "timestamp": round(frame_sec, 2),
                    "image_path": f"static/uploads/annotated/{frame_filename}",
                    "has_defect": True,
                    "severity": sev,
                })

        # 收集帧数据（所有帧）
        frames_data.append({
            "frame_index": frame_idx,
            "timestamp": round(frame_sec, 2),
            "detections": detections,
            "has_defect": has_defect,
            "ai_analysis": ai_result,
            "has_image": True,  # Phase 7.1: 所有帧都保存了图片
        })

        # 更新进度（每 30 帧写一次 DB，减少 IO）
        if frame_idx % 30 == 0 or frame_idx == max_frames - 1:
            task = db.query(VideoTask).filter(VideoTask.task_id == task_id).first()
            if task:
                task.processed_frames = frame_idx + 1
                task.ai_reports = ai_reports
                db.commit()

    # 收尾
    writer.release()
    cap.release()

    duration = round(max_frames / fps, 2) if fps > 0 else 0

    # 创建视频检测汇总记录（一条视频一条记录）
    video_record = VideoDetectionRecord(
        task_id=task_id,
        original_filename=Path(video_path).name,
        duration_seconds=duration,
        total_frames=max_frames,
        sampled_frames=max_frames,  # 实际处理了所有帧
        defect_count=len(stats["defect_frames"]),
        severity=max_severity,
        has_alert=stats["alerts"] > 0,
        frames_data=frames_data,
        frame_images=frame_images,
        keyframe_images=keyframe_images,
        output_video_path=f"/static/outputs/{output_filename}",
        created_by=user_id,
        status="completed",
    )
    db.add(video_record)

    # 更新任务为完成
    task = db.query(VideoTask).filter(VideoTask.task_id == task_id).first()
    if task:
        task.status = "completed"
        task.processed_frames = max_frames
        task.output_video_path = f"static/outputs/{output_filename}"
        task.ai_reports = ai_reports
        db.commit()

    logger.info(
        f"视频处理完成: {output_path}, "
        f"YOLO 检测 {stats['yolo_detected']}/{max_frames} 帧, "
        f"AI 分析 {stats['ai_analyzed']} 帧, "
        f"预警 {stats['alerts']} 次, "
        f"帧图片 {len(frame_images)} 张, 缺陷帧图片 {len(keyframe_images)} 张"
    )

    return {
        "task_id": task_id,
        "output_video": f"/static/outputs/{output_filename}",
        "ai_reports": ai_reports,
        "stats": stats,
        "total_frames": max_frames,
        "fps": round(fps, 2),
        "duration_processed": duration,
        "video_record_id": video_record.id,
    }


# ============================================================
# 摄像头实时帧预测（仅 YOLO，不调 AI）
# ============================================================

def predict_frame_base64(image_base64: str) -> dict:
    """
    对 Base64 编码的摄像头帧执行 YOLO 检测（不调用 AI 大模型）。

    Args:
        image_base64: Base64 编码的图片（可包含 data:image/jpeg;base64, 前缀）

    Returns:
        {
            "annotated_base64": "base64...",  # 带检测框的图片
            "detections": [...],
            "yolo_time_ms": float,
        }
    """
    # 解码 Base64
    b64_data = image_base64
    if "," in b64_data:
        b64_data = b64_data.split(",", 1)[1]
    img_bytes = base64.b64decode(b64_data)

    # 解码为 OpenCV 图像
    nparr = np.frombuffer(img_bytes, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法解码 Base64 图片")

    detector = get_detector()

    # YOLO 推理（加锁）
    with _inference_lock:
        detections, yolo_ms = detector.detect(image)

    classification = detector.classify_detections(detections)
    annotated = detector.draw_detections(image, detections)

    # 编码回 Base64
    _, buffer = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
    annotated_b64 = base64.b64encode(buffer).decode("utf-8")

    return {
        "annotated_base64": f"data:image/jpeg;base64,{annotated_b64}",
        "detections": detections,
        "classification": classification,
        "yolo_time_ms": round(yolo_ms, 2),
    }
