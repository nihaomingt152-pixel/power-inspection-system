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
import shutil
import logging
import threading
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable

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

# ffmpeg 是否支持 h264_nvenc（全局缓存，首次探测）
_FFMPEG_NVENC_OK: Optional[bool] = None


def _has_nvenc_encoder() -> bool:
    """探测本机 ffmpeg 的 h264_nvenc 是否**运行时可用**。

    只查 `-encoders`（编译支持）不够：最新 ffmpeg 构建可能要求比本机更新的
    NVIDIA 驱动（如 NVENC API 13.1 需驱动 ≥610），此时编码会失败并输出空视频。
    因此这里用一次真实短编码探测，结果缓存于进程生命周期。
    """
    global _FFMPEG_NVENC_OK
    if _FFMPEG_NVENC_OK is not None:
        return _FFMPEG_NVENC_OK
    if shutil.which("ffmpeg") is None:
        _FFMPEG_NVENC_OK = False
        return False
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        r = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=c=black:s=160x120:d=0.2:r=5",
             "-c:v", "h264_nvenc", "-preset", "p1", "-b:v", "256k",
             "-pix_fmt", "yuv420p", tmp_path],
            capture_output=True, timeout=30,
        )
        _FFMPEG_NVENC_OK = r.returncode == 0 and os.path.getsize(tmp_path) > 0
    except Exception as e:
        logger.warning(f"NVENC 运行时探测失败: {e}")
        _FFMPEG_NVENC_OK = False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    logger.info(f"ffmpeg NVENC 硬件编码可用: {_FFMPEG_NVENC_OK}")
    return _FFMPEG_NVENC_OK


class _FFmpegVideoWriter:
    """通过 ffmpeg stdin 管道逐帧编码输出视频。

    优先使用 NVIDIA NVENC 硬件编码（编码速度是 CPU 的 5-10 倍），
    系统无 ffmpeg 或硬件编码不可用时 ``available=False``，调用方降级为 OpenCV。
    写入的是 BGR ndarray，ffmpeg 负责转码为 H.264 yuv420p（浏览器可播放）。
    """

    def __init__(self, output_path: str, width: int, height: int, fps: float):
        self.available = False
        self.proc = None
        if not width or not height:
            return
        if shutil.which("ffmpeg") is None:
            return
        codec_args = (
            ["-c:v", "h264_nvenc", "-preset", "p1", "-b:v", "4M"]
            if _has_nvenc_encoder()
            else ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]
        )
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
            *codec_args, "-pix_fmt", "yuv420p", output_path,
        ]
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.available = True
        except Exception as e:
            logger.warning(f"ffmpeg 编码器启动失败，降级 OpenCV: {e}")

    def write(self, frame: np.ndarray):
        if self.proc is None:
            return
        try:
            self.proc.stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError) as e:
            logger.warning(f"ffmpeg 写入帧失败: {e}")

    def close(self):
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=120)
        except Exception as e:
            logger.warning(f"ffmpeg 编码结束失败: {e}")


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


def save_image_thumb(src_path: str, output_dir: str) -> str:
    """生成标注图缩略图（长边 480px），移动端预览用，避免公网加载大图。

    原图本身已很小（<=480px）时返回 None，调用方保持原路径即可。
    """
    img = cv2.imread(src_path)
    if img is None:
        return None
    h, w = img.shape[:2]
    if max(h, w) <= 480:
        return None
    scale = 480 / max(h, w)
    thumb = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    stem, _ = os.path.splitext(os.path.basename(src_path))
    thumb_path = os.path.join(output_dir, f"{stem}_thumb.jpg")
    cv2.imwrite(thumb_path, thumb, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return thumb_path


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


def _extract_detections_from_result(result) -> list:
    """从 ultralytics 批量推理的单帧结果中提取检测列表（与 YOLODetector.detect 格式一致）。"""
    detections = []
    boxes = result.boxes
    if boxes is not None and len(boxes) > 0:
        detector = get_detector()
        for i in range(len(boxes)):
            cls_id = int(boxes.cls[i].item())
            conf = float(boxes.conf[i].item())
            xyxy = boxes.xyxy[i].cpu().numpy()
            detections.append({
                "class_id": cls_id,
                "class_name": detector.class_names.get(cls_id, f"unknown_{cls_id}"),
                "confidence": round(conf, 4),
                "bbox": {
                    "x1": round(float(xyxy[0]), 2),
                    "y1": round(float(xyxy[1]), 2),
                    "x2": round(float(xyxy[2]), 2),
                    "y2": round(float(xyxy[3]), 2),
                },
            })
    return detections


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
    annotated_thumb_path = None
    if save_annotated:
        out_dir = Path(__file__).resolve().parent.parent / "static" / "uploads" / "annotated"
        annotated_path = save_annotated_image(annotated, image_path, str(out_dir))
        # 移动端精简返回用的缩略图（长边 480px），预览时避免加载大图
        annotated_thumb_path = save_image_thumb(annotated_path, str(out_dir))

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
        "annotated_thumb_path": annotated_thumb_path,
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
    progress_callback: Callable[[int, int], None] = None,
    file_md5: str = None,
    batch_size: int = 4,
) -> dict:
    """
    处理视频文件：跳帧 + 批量 YOLO + 异步 AI + 硬件编码（Phase 26 优化版）。

    性能优化：
      - 智能跳帧：场景变化极小的帧复用上一次推理结果，不重复推理（缩短 40-60%）
      - YOLO 批处理：缓冲 batch_size 帧一次性推理，GPU 利用率提升 2-3 倍
      - 整段视频一次性 AI 总结（Phase 27）：全程不逐帧调用 AI，处理完成后仅调用一次
        generate_video_summary()，API 调用次数从 N 次降为 1 次
      - 视频合成：优先 ffmpeg NVENC 硬件编码（5-10 倍提速），降级 OpenCV

    Args:
        video_path: 视频文件路径
        db: SQLAlchemy 会话（用于进度更新）
        task_id: VideoTask 的 UUID
        call_ai: 是否启用 AI 总结（false 时仅做 YOLO，不调用任何 API）
        max_seconds: 最大处理时长（秒），默认取环境变量
        user_id: 创建人 ID
        progress_callback: 进度回调 (processed_frames, total_frames)
        file_md5: 视频文件 MD5（用于重复上传缓存，优化 7）
        batch_size: YOLO 批量推理帧数（RTX 4060 8G 建议 4，过大可能 OOM）

    Returns:
        {"task_id": ..., "output_video": ..., "ai_reports": [...], "stats": {...}, "video_summary": {...}}
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
    # 输出视频统一缩放到 720P；取偶数尺寸以满足 yuv420p 硬编码要求
    out_w, out_h = 1280, 720
    if orig_width > 0 and orig_height > 0:
        scale = min(out_w / orig_width, out_h / orig_height)
        out_w = int(orig_width * scale)
        out_h = int(orig_height * scale)
    out_w -= out_w % 2
    out_h -= out_h % 2

    # 创建输出视频目录
    VIDEO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_filename = f"output_{task_id[:8]}_{Path(video_path).stem}.mp4"
    output_path = str(VIDEO_OUTPUT_DIR / output_filename)

    # 视频编码器：优先 ffmpeg（NVENC 硬件加速），不可用时降级 OpenCV
    ffmpeg_writer = _FFmpegVideoWriter(output_path, out_w, out_h, fps)
    use_ffmpeg = ffmpeg_writer.available
    writer = None
    if not use_ffmpeg:
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
    max_severity = "一般"    # 最高严重等级（由整段视频 AI 总结决定）
    stats = {"total_frames": max_frames, "yolo_detected": 0,
             "alerts": 0, "defect_frames": [], "skipped_frames": 0}

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

    logger.info(
        f"视频处理开始: {video_path}, 前 {max_seconds}s, 共 {max_frames} 帧, "
        f"编码器={'ffmpeg' if use_ffmpeg else 'opencv'}, batch={batch_size}"
    )

    # ---- 智能跳帧参数（优化 3）----
    SKIP_THRESHOLD = 30          # 灰度帧差均值阈值（0-255），低于此视为画面静止
    MIN_FRAME_INTERVAL = 5       # 画面静止时每 5 帧仍强制推理一次，防漏检

    # 批处理缓冲（优化 4：攒够 batch_size 一次性推理）
    batch_frames: list = []
    batch_indices: list = []
    # 跳帧状态
    prev_gray = None
    last_detections = None       # 最近一次已完成推理的结果（跳帧帧复用）
    in_defect_mode = False       # 缺陷追踪模式：检测到缺陷后转入密集分析
    consecutive_clean = 0

    def finalize_frame(frame_idx, frame, detections, skipped):
        """单帧统一后处理：画框/写视频/存帧图/记录数据/判断缺陷。

        Phase 27 起不再逐帧调用 AI，仅收集帧数据；整段视频的 AI 总结统一在末尾调用一次。
        """
        nonlocal in_defect_mode, consecutive_clean
        if detections:
            stats["yolo_detected"] += 1
        if skipped:
            stats["skipped_frames"] += 1
        classification = detector.classify_detections(detections)
        annotated = detector.draw_detections(frame, detections)

        # 写入输出视频
        if use_ffmpeg:
            ffmpeg_writer.write(annotated)
        else:
            writer.write(annotated)

        frame_sec = frame_idx / fps
        has_defect = detections and any(d["class_id"] in (5, 6) for d in detections)

        # 记录 YOLO 缺陷帧（severity 由整段视频 AI 总结统一决定）
        if has_defect:
            stats["defect_frames"].append({
                "frame_index": frame_idx,
                "timestamp": round(frame_sec, 2),
                "severity": "一般",
            })

        # 缺陷追踪模式：检测到缺陷进入密集分析，连续 30 帧无缺陷退出
        if has_defect:
            consecutive_clean = 0
            in_defect_mode = True
        elif in_defect_mode:
            consecutive_clean += 1
            if consecutive_clean >= 30:
                in_defect_mode = False
                consecutive_clean = 0

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
                "severity": "一般",  # AI 结果稍后回填时修正缺陷帧
            })
            # 兼容旧字段：缺陷帧也记录到 keyframe_images
            if has_defect:
                keyframe_images.append({
                    "frame_index": frame_idx,
                    "timestamp": round(frame_sec, 2),
                    "image_path": f"static/uploads/annotated/{frame_filename}",
                    "has_defect": True,
                    "severity": "一般",
                })

        # 帧数据（ai_analysis 由异步结果稍后回填）
        frames_data.append({
            "frame_index": frame_idx,
            "timestamp": round(frame_sec, 2),
            "detections": detections,
            "has_defect": has_defect,
            "ai_analysis": None,
            "has_image": True,  # Phase 7.1: 所有帧都保存了图片
            "skipped": bool(skipped),
        })

    def flush_batch():
        """对缓冲的帧执行一次批量 YOLO 推理并逐帧后处理。"""
        nonlocal last_detections, batch_frames, batch_indices
        if not batch_frames:
            return
        # 批量推理（单次锁内完成，GPU 利用率显著高于逐帧）
        with _inference_lock:
            results = detector.model(batch_frames, conf=0.25, verbose=False)
        for i, (fidx, frame) in enumerate(zip(batch_indices, batch_frames)):
            detections = _extract_detections_from_result(results[i])
            last_detections = detections
            finalize_frame(fidx, frame, detections, skipped=False)
        batch_frames = []
        batch_indices = []

    # ---- 主循环：跳帧 + 批处理（使用 cap.set 精确跳转）----
    frame_idx = 0
    while frame_idx < max_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            logger.warning(f"读取帧 {frame_idx} 失败，提前结束")
            break

        # 缩放到输出尺寸
        if (orig_width, orig_height) != (out_w, out_h):
            frame = cv2.resize(frame, (out_w, out_h))

        # 场景变化检测：与上一帧的灰度均值差
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        diff = None if prev_gray is None else float(cv2.absdiff(prev_gray, gray).mean())
        prev_gray = gray

        # 决定是否跳帧：画面静止 + 非缺陷追踪模式 + 非强制分析帧
        should_skip = (
            last_detections is not None
            and not in_defect_mode
            and diff is not None
            and diff < SKIP_THRESHOLD
            and frame_idx % MIN_FRAME_INTERVAL != 0
        )

        if should_skip:
            # 复用最近一次推理结果画框，跳过 YOLO 推理
            finalize_frame(frame_idx, frame, last_detections, skipped=True)
        else:
            batch_frames.append(frame)
            batch_indices.append(frame_idx)
            if len(batch_frames) >= batch_size:
                flush_batch()

        frame_idx += 1
        # 进度回调（每 5 帧 + 最后一帧，避免频繁锁竞争）
        if progress_callback and (frame_idx % 5 == 0 or frame_idx == max_frames):
            progress_callback(frame_idx, max_frames)

    # 收尾：flush 剩余批次，关闭编码器
    flush_batch()
    cap.release()
    if use_ffmpeg:
        ffmpeg_writer.close()
    else:
        writer.release()

    duration = round(max_frames / fps, 2) if fps > 0 else 0

    # ---- 整段视频 AI 总结（Phase 27：每个视频仅调用一次 API）----
    video_summary = None
    if call_ai:
        try:
            svc = MultimodalService()
            video_summary, _sum_ms = svc.generate_video_summary(
                frames_data=frames_data,
                frame_images=frame_images,
                total_frames=max_frames,
                defect_count=len(stats["defect_frames"]),
                duration=duration,
            )
            logger.info(f"整段视频 AI 总结完成: {_sum_ms:.0f}ms, "
                        f"风险等级 {video_summary.get('risk_level', '未知')}")
        except Exception as e:
            logger.warning(f"整段视频 AI 总结失败: {e}")
            video_summary = {
                "overall_description": "AI 总结暂不可用",
                "main_issues": [],
                "risk_level": "中",
                "suggestions": "请人工复核视频检测结果",
                "focus_points": "",
                "extra_notes": f"AI 总结调用失败: {str(e)}",
            }
    # Phase 28: call_ai=False 时不生成默认总结（video_summary 保持 None），
    # 前端据此隐藏 AI 总结区块；整体等级保持"一般"、不触发预警

    # 整体严重等级由 AI 总结的风险等级决定；未启用 AI 时保持默认"一般"
    risk_to_severity = {"低": "一般", "中": "一般", "高": "严重", "紧急": "紧急"}
    if video_summary:
        max_severity = risk_to_severity.get(video_summary.get("risk_level", "中"), "一般")
    has_alert = max_severity in ("严重", "紧急")
    stats["alerts"] = 1 if has_alert else 0

    # 缺陷帧与帧图片的严重等级统一回填为整体等级
    for fd in stats["defect_frames"]:
        fd["severity"] = max_severity
    for fi in frame_images:
        if fi["has_defect"]:
            fi["severity"] = max_severity
    for kf in keyframe_images:
        kf["severity"] = max_severity

    # 创建视频检测汇总记录（一条视频一条记录）
    video_record = VideoDetectionRecord(
        task_id=task_id,
        original_filename=Path(video_path).name,
        duration_seconds=duration,
        total_frames=max_frames,
        sampled_frames=max_frames,  # 实际处理了所有帧
        defect_count=len(stats["defect_frames"]),
        severity=max_severity,
        has_alert=has_alert,
        frames_data=frames_data,
        frame_images=frame_images,
        keyframe_images=keyframe_images,
        output_video_path=f"/static/outputs/{output_filename}",
        created_by=user_id,
        status="completed",
        file_md5=file_md5,
        video_summary=video_summary,
    )
    # 立即提交汇总记录，确保即使后续 task 更新失败也不丢失结果
    db.add(video_record)
    db.commit()
    db.refresh(video_record)

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
        f"跳帧 {stats['skipped_frames']} 帧, "
        f"AI 总结 {'已生成' if video_summary else '未生成'} "
        f"(风险等级 {video_summary.get('risk_level', '无') if video_summary else '无'}), "
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
        "video_summary": video_summary,
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
