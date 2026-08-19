#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缺陷工单闭环管理服务。
完整流转: 待派发(pending) → 处理中(processing) → 待复检(pending_review) → 闭环(closed) / 驳回(rejected)
"""

import logging
from typing import Optional, List
from datetime import datetime

from sqlalchemy.orm import Session
from database.models import WorkOrder, User, OrderLog, DetectionRecord

logger = logging.getLogger(__name__)


def _add_order_log(db: Session, order_id: int, operator_id: int, operator_name: str,
                   action: str, content: str = None):
    """写入操作日志（内部辅助函数）。"""
    log_entry = OrderLog(
        order_id=order_id,
        operator_id=operator_id,
        operator_name=operator_name,
        action=action,
        content=content,
    )
    db.add(log_entry)
    logger.info(f"工单 #{order_id} 操作日志: {action} by {operator_name}")


def get_order_logs(db: Session, order_id: int) -> list:
    """获取工单的操作日志列表（按时间倒序）。"""
    logs = db.query(OrderLog).filter(
        OrderLog.order_id == order_id
    ).order_by(OrderLog.created_at.desc()).all()
    return [log.to_dict() for log in logs]


def delete_order(db: Session, order_id: int, user_id: int) -> dict:
    """
    Admin 硬删除工单及关联的操作日志。
    返回被删除工单的基本信息（用于通知）。
    """
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise ValueError(f"工单不存在: {order_id}")

    reviewer = db.query(User).filter(User.id == user_id).first()
    if not reviewer or reviewer.role not in ("inspector", "admin"):
        raise ValueError("仅运维人员或管理员可删除工单")

    # 保存信息用于通知
    info = {
        "id": order.id,
        "title": order.title,
        "assigned_to": order.assigned_to,
    }

    # 级联删除操作日志
    db.query(OrderLog).filter(OrderLog.order_id == order_id).delete()
    # 删除工单
    db.delete(order)
    db.commit()
    logger.info(f"工单 #{order_id} 已删除 by user {user_id}")
    return info


def create_work_order(
    db: Session,
    title: str,
    description: str,
    severity: str,
    created_by: int,
    assigned_to: int,
    original_image_path: str = None,
    annotated_image_path: str = None,
    gps_lat: float = None,
    gps_lng: float = None,
    detection_record_id: int = None,
    ai_summary: dict = None,
) -> WorkOrder:
    """运维人员创建工单。"""
    if severity not in ("一般", "严重", "紧急"):
        raise ValueError(f"无效严重等级: {severity}")

    # 验证指派目标存在且角色为检修人员
    assignee = db.query(User).filter(User.id == assigned_to).first()
    if not assignee:
        raise ValueError(f"指派用户不存在: {assigned_to}")
    if assignee.role != "repairman":
        raise ValueError("只能指派给检修人员(repairman)")

    # 从关联检测记录自动补图片路径与 AI 摘要（图片记录派发时前端只传 detection_record_id）
    if (not annotated_image_path or not ai_summary) and detection_record_id:
        det = db.query(DetectionRecord).filter(DetectionRecord.id == detection_record_id).first()
        if det:
            if not annotated_image_path:
                annotated_image_path = det.annotated_image_path or None
                original_image_path = original_image_path or det.original_image_path or None
            if not ai_summary and det.ai_analysis:
                ai_summary = det.ai_analysis

    order = WorkOrder(
        title=title,
        description=description,
        severity=severity,
        original_image_path=original_image_path,
        annotated_image_path=annotated_image_path,
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        detection_record_id=detection_record_id,
        ai_summary=ai_summary,
        created_by=created_by,
        assigned_to=assigned_to,
        status="processing",  # 创建即指派 → 直接进入处理中
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    # 写入操作日志
    creator = db.query(User).filter(User.id == created_by).first()
    creator_name = creator.full_name if creator else f"用户#{created_by}"
    _add_order_log(db, order.id, created_by, creator_name, "created",
                   f"创建工单: [{severity}] {title}")
    db.commit()
    logger.info(f"工单创建: #{order.id} [{severity}] {title}")
    return order


def accept_order(db: Session, order_id: int, user_id: int) -> WorkOrder:
    """检修人员确认接单（processing 状态的操作，标记已开始处理）。"""
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise ValueError(f"工单不存在: {order_id}")
    if order.assigned_to != user_id:
        raise ValueError("此工单未指派给你")
    if order.status != "processing":
        raise ValueError(f"工单状态不允许接单: {order.status}")
    # 状态保持 processing，记录日志
    op = db.query(User).filter(User.id == user_id).first()
    op_name = op.full_name if op else f"用户#{user_id}"
    _add_order_log(db, order_id, user_id, op_name, "accepted", "确认接单，开始处理")
    db.commit()
    logger.info(f"工单 #{order_id} 已确认接单 by user {user_id}")
    return order


def submit_review(
    db: Session,
    order_id: int,
    user_id: int,
    repair_image_path: str = None,
    review_remark: str = None,
) -> WorkOrder:
    """检修人员提交复检（含处理说明）。"""
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise ValueError(f"工单不存在: {order_id}")
    if order.assigned_to != user_id:
        raise ValueError("此工单未指派给你")
    if order.status not in ("processing", "rejected"):
        raise ValueError(f"工单状态不允许提交复检: {order.status}")

    order.status = "pending_review"
    if repair_image_path:
        order.repair_image_path = repair_image_path
    if review_remark:
        order.review_remark = review_remark
    order.updated_at = datetime.now()

    op = db.query(User).filter(User.id == user_id).first()
    op_name = op.full_name if op else f"用户#{user_id}"
    _add_order_log(db, order_id, user_id, op_name, "submitted",
                   f"提交复检{': ' + review_remark if review_remark else ''}")

    db.commit()
    db.refresh(order)
    logger.info(f"工单 #{order_id} 已提交复检 by user {user_id}")
    return order


def approve_order(db: Session, order_id: int, user_id: int, close_remark: str = "") -> WorkOrder:
    """运维人员确认闭环（可填写闭环备注，缺省回退到 Worker 复检说明）。"""
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise ValueError(f"工单不存在: {order_id}")
    # 运维人员审批
    reviewer = db.query(User).filter(User.id == user_id).first()
    if not reviewer or reviewer.role not in ("inspector", "admin"):
        raise ValueError("仅运维人员或管理员可确认闭环")

    if order.status != "pending_review":
        raise ValueError(f"工单状态不允许闭环: {order.status}")

    order.status = "closed"
    order.close_remark = close_remark.strip() if close_remark else (order.review_remark or "")
    order.updated_at = datetime.now()
    op_name = reviewer.full_name if reviewer else f"用户#{user_id}"
    _add_order_log(db, order_id, user_id, op_name, "approved",
                   f"确认闭环，审核通过{': ' + order.close_remark if order.close_remark else ''}")
    db.commit()
    db.refresh(order)
    logger.info(f"工单 #{order_id} 已闭环 by user {user_id}")
    return order


def reject_order(db: Session, order_id: int, user_id: int, reason: str = "") -> WorkOrder:
    """运维人员驳回工单。"""
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise ValueError(f"工单不存在: {order_id}")

    reviewer = db.query(User).filter(User.id == user_id).first()
    if not reviewer or reviewer.role not in ("inspector", "admin"):
        raise ValueError("仅运维人员或管理员可驳回")

    if order.status != "pending_review":
        raise ValueError(f"工单状态不允许驳回: {order.status}")

    order.status = "rejected"
    order.reject_reason = reason or "运维人员驳回，请重新检修"
    order.updated_at = datetime.now()
    op_name = reviewer.full_name if reviewer else f"用户#{user_id}"
    _add_order_log(db, order_id, user_id, op_name, "rejected",
                   f"Admin 驳回: {order.reject_reason}")
    db.commit()
    db.refresh(order)
    logger.info(f"工单 #{order_id} 已驳回 by user {user_id}: {reason}")
    return order


def reject_order_worker(db: Session, order_id: int, user_id: int,
                        reason: str = "", remark: str = "") -> WorkOrder:
    """Worker 驳回工单（仅 processing 状态可驳回）。"""
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise ValueError(f"工单不存在: {order_id}")
    if order.assigned_to != user_id:
        raise ValueError("此工单未指派给你")
    if order.status != "processing":
        raise ValueError(f"工单状态不允许驳回: {order.status}，仅 processing 状态可驳回")

    operator = db.query(User).filter(User.id == user_id).first()
    if not operator:
        raise ValueError("用户不存在")

    full_reason = reason
    if remark:
        full_reason = f"{reason}（补充: {remark}）"

    order.status = "rejected"
    order.reject_reason = full_reason
    order.updated_at = datetime.now()
    _add_order_log(db, order_id, user_id, operator.full_name or operator.username,
                   "rejected", f"Worker 驳回: {full_reason}")
    db.commit()
    db.refresh(order)
    logger.info(f"工单 #{order_id} 已被 Worker {user_id} 驳回: {full_reason}")
    return order


def reassign_order(
    db: Session,
    order_id: int,
    user_id: int,
    assigned_to: int,
    title: str = None,
    description: str = None,
    severity: str = None,
) -> WorkOrder:
    """Admin 重新派发已驳回的工单。"""
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise ValueError(f"工单不存在: {order_id}")

    reviewer = db.query(User).filter(User.id == user_id).first()
    if not reviewer or reviewer.role not in ("inspector", "admin"):
        raise ValueError("仅运维人员或管理员可重新派发")

    if order.status != "rejected":
        raise ValueError(f"仅已驳回状态的工单可重新派发，当前: {order.status}")

    # 验证新指派目标
    assignee = db.query(User).filter(User.id == assigned_to).first()
    if not assignee:
        raise ValueError(f"指派用户不存在: {assigned_to}")
    if assignee.role != "repairman":
        raise ValueError("只能指派给检修人员(repairman)")

    old_assignee = order.assigned_to

    # 更新工单
    if title is not None:
        order.title = title
    if description is not None:
        order.description = description
    if severity is not None:
        if severity not in ("一般", "严重", "紧急"):
            raise ValueError(f"无效严重等级: {severity}")
        order.severity = severity

    order.assigned_to = assigned_to
    order.status = "processing"
    order.reject_reason = None  # 清空驳回原因
    order.updated_at = datetime.now()

    op_name = reviewer.full_name if reviewer else f"用户#{user_id}"
    new_name = assignee.full_name or assignee.username
    _add_order_log(db, order_id, user_id, op_name, "reassigned",
                   f"重新派发工单给 {new_name}（原指派: 用户#{old_assignee}）")

    db.commit()
    db.refresh(order)
    logger.info(f"工单 #{order_id} 已重新派发给 {new_name} by user {user_id}")
    return order


def list_work_orders(
    db: Session,
    user: User,
    status_filter: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    获取工单列表。
    - 运维/admin：查看所有工单
    - 检修：仅查看指派给自己的工单
    """
    query = db.query(WorkOrder)

    if user.role == "repairman":
        query = query.filter(WorkOrder.assigned_to == user.id)

    if status_filter:
        query = query.filter(WorkOrder.status == status_filter)

    query = query.order_by(WorkOrder.created_at.desc())
    total = query.count()
    records = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "records": [r.to_dict() for r in records],
    }


def get_order_detail(db: Session, order_id: int) -> WorkOrder:
    """获取工单详情。"""
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise ValueError(f"工单不存在: {order_id}")
    return order


def get_repairmen(db: Session) -> list:
    """获取所有检修人员列表（用于指派下拉框）。"""
    users = db.query(User).filter(User.role == "repairman").all()
    return [u.to_dict() for u in users]


def get_order_stats(db: Session, user_id: int = None) -> dict:
    """获取工单各状态计数。若提供 user_id，仅统计该用户的工单（Worker 隔离）。"""
    from sqlalchemy import func
    query = db.query(
        WorkOrder.status, func.count(WorkOrder.id)
    )
    if user_id is not None:
        query = query.filter(WorkOrder.assigned_to == user_id)
    stats = query.group_by(WorkOrder.status).all()
    result = {"pending": 0, "processing": 0, "pending_review": 0, "rejected": 0, "closed": 0}
    for status, count in stats:
        if status in result:
            result[status] = count
    return result
