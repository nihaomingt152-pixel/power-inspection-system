#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能预警服务模块。
根据 YOLO 检测结果和多模态 AI 分析结论，判断是否触发预警。
"""

import os
import logging
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# 置信度阈值（从环境变量读取，默认 0.7）
ALERT_CONFIDENCE_THRESHOLD = float(os.getenv("ALERT_CONFIDENCE_THRESHOLD", "0.7"))

# 严重等级到预警等级的映射
SEVERITY_TO_ALERT_LEVEL = {
    "一般": "info",
    "严重": "warning",
    "紧急": "critical",
}


class AlertService:
    """
    智能预警评估服务。
    同时满足以下条件才触发预警：
    1. YOLO 检测到目标
    2. 置信度 > 阈值（默认 0.7）
    3. AI 分析结论为"严重"或"紧急"
    """

    @staticmethod
    def evaluate(
        detections: list,
        ai_result: Optional[dict],
        classification: dict,
    ) -> dict:
        """
        评估是否触发预警。

        Args:
            detections: YOLO 检测结果列表
            ai_result: AI 分析结果字典
            classification: 检测分类统计

        Returns:
            {
                "triggered": bool,
                "level": str,       # info / warning / critical / none
                "message": str,
                "reasons": [...],
                "high_confidence_defects": [...],
            }
        """
        reasons = []
        high_confidence_defects = []

        # 条件 1 & 2：YOLO 检测到目标 且 置信度 > 阈值
        for d in detections:
            if d["confidence"] > ALERT_CONFIDENCE_THRESHOLD:
                # 缺陷类别检查
                if d["class_id"] in {5, 6}:  # broken 或 flashover
                    high_confidence_defects.append(d)
                    reasons.append(
                        f"高置信度缺陷: {d['class_name']} (置信度: {d['confidence']:.2%})"
                    )

        # 条件 3：AI 分析结论为"严重"或"紧急"
        ai_severity = None
        ai_alert = False
        if ai_result:
            ai_severity = ai_result.get("severity", "一般")
            ai_alert = ai_result.get("alert", False)

        # 综合判定
        has_high_conf_defect = len(high_confidence_defects) > 0
        is_severe_ai = ai_severity in ("严重", "紧急")

        triggered = has_high_conf_defect and is_severe_ai

        if triggered:
            level = SEVERITY_TO_ALERT_LEVEL.get(ai_severity, "warning")
            message = (
                f"[{ai_severity}] 检测到 {len(high_confidence_defects)} 处 "
                f"高置信度缺陷: "
                + "; ".join(
                    f"{d['class_name']}({d['confidence']:.0%})"
                    for d in high_confidence_defects
                )
            )
            if ai_result and ai_result.get("description"):
                message += f"\nAI 分析: {ai_result['description'][:200]}..."
        elif has_high_conf_defect and not is_severe_ai:
            # 有高置信缺陷但 AI 不认为严重 → 不触发
            level = "none"
            message = (
                f"检测到高置信度缺陷但 AI 判定为[{ai_severity}]，"
                f"暂不触发预警。建议人工复核。"
            )
            reasons.append(f"AI 严重等级为'{ai_severity}'，未达到预警标准")
        elif not has_high_conf_defect and ai_result:
            # 没有高置信缺陷
            level = "none"
            message = "未检测到高置信度缺陷，系统正常运行。"
            if ai_result.get("description"):
                message += f" AI 分析: {ai_result['description'][:200]}"
        else:
            level = "none"
            message = "未检测到目标或目标置信度较低，系统正常运行。"

        result = {
            "triggered": triggered,
            "level": level,
            "severity": ai_severity or "未知",
            "message": message,
            "reasons": reasons,
            "high_confidence_defects": high_confidence_defects,
            "threshold": ALERT_CONFIDENCE_THRESHOLD,
        }

        if triggered:
            logger.warning(f"触发预警: {message}")
        else:
            logger.info(f"未触发预警: {message[:100]}")

        return result
