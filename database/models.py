#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLAlchemy ORM 模型定义（Phase 2 扩展版）。
新增: User（用户）、WorkOrder（工单）
扩展: DetectionRecord（gps_lat, gps_lng, has_abnormal）
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime, JSON, Boolean, ForeignKey, Enum,
)
from sqlalchemy.orm import relationship
from database.db_connection import Base


# ============================================================
# 用户表
# ============================================================

class User(Base):
    """用户表。"""
    __tablename__ = "t_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True, comment="用户名")
    password_hash = Column(String(256), nullable=False, comment="密码哈希（bcrypt）")
    role = Column(
        Enum("inspector", "repairman", "admin", name="user_role"),
        nullable=False, default="inspector",
        comment="角色: inspector(运维), repairman(检修), admin(管理员)"
    )
    full_name = Column(String(64), nullable=True, comment="姓名")
    created_at = Column(DateTime, default=datetime.now, comment="注册时间")

    # 关联
    created_orders = relationship("WorkOrder", foreign_keys="WorkOrder.created_by", back_populates="creator")
    assigned_orders = relationship("WorkOrder", foreign_keys="WorkOrder.assigned_to", back_populates="assignee")

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "full_name": self.full_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# 检测记录表（扩展）
# ============================================================

class DetectionRecord(Base):
    """检测记录表。"""
    __tablename__ = "detection_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(32), nullable=False, default="image")
    source_name = Column(String(512), nullable=True)
    original_image_path = Column(String(1024), nullable=True)
    annotated_image_path = Column(String(1024), nullable=True)

    # GPS（Phase 2: 新增 gps_lat / gps_lng，替代旧字段 gps_latitude / gps_longitude）
    gps_latitude = Column(Float, nullable=True, comment="GPS 纬度（旧字段，保留兼容）")
    gps_longitude = Column(Float, nullable=True, comment="GPS 经度（旧字段，保留兼容）")
    gps_lat = Column(Float, nullable=True, comment="GPS 纬度（十进制）")
    gps_lng = Column(Float, nullable=True, comment="GPS 经度（十进制）")
    gps_source = Column(String(16), nullable=False, default="none", comment="GPS来源: exif/manual/none")

    capture_timestamp = Column(DateTime, nullable=True)
    yolo_detections = Column(JSON, nullable=True)
    total_detections = Column(Integer, default=0)
    defect_count = Column(Integer, default=0)
    normal_count = Column(Integer, default=0)
    ai_analysis = Column(JSON, nullable=True)
    alert_triggered = Column(Boolean, default=False)
    alert_level = Column(String(32), nullable=True)
    alert_message = Column(Text, nullable=True)
    has_abnormal = Column(Boolean, default=False, comment="兜底检测是否发现异常异物")
    abnormal_desc = Column(Text, nullable=True, comment="兜底检测异常描述")
    fallback_result = Column(JSON, nullable=True, comment="兜底异物检测完整结果（description/is_abnormal/confidence）")

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    yolo_time_ms = Column(Float, nullable=True)
    ai_time_ms = Column(Float, nullable=True)
    total_time_ms = Column(Float, nullable=True)
    video_filename = Column(String(512), nullable=True)
    frame_timestamp_seconds = Column(Float, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "original_image_path": self.original_image_path,
            "annotated_image_path": self.annotated_image_path,
            "gps_latitude": self.gps_latitude,
            "gps_longitude": self.gps_longitude,
            "gps_lat": self.gps_lat,
            "gps_lng": self.gps_lng,
            "gps_source": self.gps_source,
            "capture_timestamp": (
                self.capture_timestamp.isoformat() if self.capture_timestamp else None
            ),
            "yolo_detections": self.yolo_detections,
            "total_detections": self.total_detections,
            "defect_count": self.defect_count,
            "normal_count": self.normal_count,
            "ai_analysis": self.ai_analysis,
            "alert_triggered": self.alert_triggered,
            "alert_level": self.alert_level,
            "alert_message": self.alert_message,
            "has_abnormal": self.has_abnormal,
            "abnormal_desc": self.abnormal_desc,
            "fallback_result": self.fallback_result,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "yolo_time_ms": self.yolo_time_ms,
            "ai_time_ms": self.ai_time_ms,
            "total_time_ms": self.total_time_ms,
            "video_filename": self.video_filename,
            "frame_timestamp_seconds": self.frame_timestamp_seconds,
        }


# ============================================================
# 视频任务表（不变）
# ============================================================

class VideoTask(Base):
    """视频分析任务表。"""
    __tablename__ = "t_video_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(36), nullable=False, unique=True, index=True)
    original_filename = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    total_frames = Column(Integer, default=0)
    processed_frames = Column(Integer, default=0)
    output_video_path = Column(String(512), nullable=True)
    ai_reports = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "original_filename": self.original_filename,
            "status": self.status,
            "total_frames": self.total_frames,
            "processed_frames": self.processed_frames,
            "progress_pct": (
                round(self.processed_frames / self.total_frames * 100, 1)
                if self.total_frames > 0 else 0
            ),
            "output_video_path": self.output_video_path,
            "ai_reports": self.ai_reports or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# 工单表（Phase 2 新增）
# ============================================================

class WorkOrder(Base):
    """缺陷工单表。"""
    __tablename__ = "t_work_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False, comment="工单标题")
    description = Column(Text, nullable=True, comment="缺陷描述")
    severity = Column(String(32), nullable=False, default="一般",
                       comment="严重程度: 一般/严重/紧急")
    original_image_path = Column(String(1024), nullable=True, comment="缺陷原图")
    annotated_image_path = Column(String(1024), nullable=True, comment="标注图")
    repair_image_path = Column(String(1024), nullable=True, comment="复检图片")
    gps_lat = Column(Float, nullable=True, comment="GPS 纬度")
    gps_lng = Column(Float, nullable=True, comment="GPS 经度")
    detection_record_id = Column(Integer, ForeignKey("detection_records.id"), nullable=True, comment="关联的检测记录ID")
    created_by = Column(Integer, ForeignKey("t_users.id"), nullable=False, comment="创建者(运维)")
    assigned_to = Column(Integer, ForeignKey("t_users.id"), nullable=True, comment="指派检修人员")
    status = Column(
        Enum("pending", "processing", "pending_review", "rejected", "closed",
             name="order_status"),
        nullable=False, default="pending",
        comment="状态: pending/processing/pending_review/rejected/closed"
    )
    reject_reason = Column(Text, nullable=True, comment="驳回理由")
    review_remark = Column(Text, nullable=True, comment="Worker提交复检时的处理说明")
    close_remark = Column(Text, nullable=True, comment="闭环备注（运维确认闭环时填写）")
    ai_summary = Column(JSON, nullable=True, comment="AI 分析摘要（派发时记录，工单详情展示）")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 关联
    creator = relationship("User", foreign_keys=[created_by], back_populates="created_orders")
    assignee = relationship("User", foreign_keys=[assigned_to], back_populates="assigned_orders")

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "original_image_path": self.original_image_path,
            "annotated_image_path": self.annotated_image_path,
            "repair_image_path": self.repair_image_path,
            "gps_lat": self.gps_lat,
            "gps_lng": self.gps_lng,
            "detection_record_id": self.detection_record_id,
            "created_by": self.created_by,
            "assigned_to": self.assigned_to,
            "creator_name": self.creator.full_name if self.creator else None,
            "assignee_name": self.assignee.full_name if self.assignee else None,
            "status": self.status,
            "reject_reason": self.reject_reason,
            "review_remark": self.review_remark,
            "close_remark": self.close_remark,
            "ai_summary": self.ai_summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# 工单操作日志表（Phase 6 补充）
# ============================================================

class OrderLog(Base):
    """工单操作日志表。记录每次状态变更和操作。"""
    __tablename__ = "t_order_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("t_work_orders.id"), nullable=False, comment="关联工单ID")
    operator_id = Column(Integer, ForeignKey("t_users.id"), nullable=False, comment="操作人ID")
    operator_name = Column(String(255), nullable=False, comment="操作人姓名（冗余）")
    action = Column(String(50), nullable=False, comment="操作类型: created/accepted/submitted/approved/rejected/deleted")
    content = Column(Text, nullable=True, comment="操作详情（驳回原因、闭环备注等）")
    created_at = Column(DateTime, default=datetime.now, comment="操作时间")

    def to_dict(self):
        return {
            "id": self.id,
            "order_id": self.order_id,
            "operator_id": self.operator_id,
            "operator_name": self.operator_name,
            "action": self.action,
            "content": self.content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# 视频检测记录表（Phase 7：一条视频一条记录）
# ============================================================

class VideoDetectionRecord(Base):
    """视频检测汇总记录表。每条视频分析完成后生成一条记录。"""
    __tablename__ = "t_video_detections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(36), nullable=False, unique=True, index=True, comment="关联 t_video_tasks.task_id")
    original_filename = Column(String(255), nullable=True, comment="原始视频文件名")
    duration_seconds = Column(Float, nullable=True, comment="视频总时长（秒）")
    total_frames = Column(Integer, default=0, comment="总帧数")
    sampled_frames = Column(Integer, default=0, comment="采样帧数")
    defect_count = Column(Integer, default=0, comment="缺陷帧数")
    severity = Column(String(20), nullable=True, comment="最高严重等级")
    has_alert = Column(Boolean, default=False, comment="是否触发预警")
    gps_lat = Column(Float, nullable=True, comment="GPS纬度")
    gps_lng = Column(Float, nullable=True, comment="GPS经度")
    gps_source = Column(String(16), nullable=False, default="none", comment="GPS来源")
    frames_data = Column(JSON, nullable=True, comment="所有帧的检测结果JSON")
    frame_images = Column(JSON, nullable=True, default=list, comment="所有帧的图片路径列表（Phase 7.1）")
    keyframe_images = Column(JSON, nullable=True, comment="关键帧图片路径列表（旧版，兼容保留）")
    output_video_path = Column(String(512), nullable=True, comment="标注视频路径")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    created_by = Column(Integer, ForeignKey("t_users.id"), nullable=True, comment="创建人ID")
    status = Column(String(20), nullable=False, default="completed", comment="状态: completed/failed")
    file_md5 = Column(String(32), nullable=True, index=True, comment="视频文件 MD5（重复上传缓存，优化7）")
    video_summary = Column(JSON, nullable=True, comment="整段视频AI分析总结（每视频仅调用一次API生成）")

    def to_dict(self):
        return {
            "id": self.id,
            "record_type": "video",
            "task_id": self.task_id,
            "original_filename": self.original_filename,
            "duration_seconds": self.duration_seconds,
            "total_frames": self.total_frames,
            "sampled_frames": self.sampled_frames,
            "defect_count": self.defect_count,
            "severity": self.severity,
            "has_alert": self.has_alert,
            "gps_lat": self.gps_lat,
            "gps_lng": self.gps_lng,
            "gps_source": self.gps_source,
            "frames_data": self.frames_data,
            "frame_images": self.frame_images or [],
            "keyframe_images": self.keyframe_images,
            "output_video_path": self.output_video_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by,
            "status": self.status,
            "video_summary": self.video_summary,
        }
