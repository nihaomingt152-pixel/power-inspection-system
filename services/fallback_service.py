#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开放词汇兜底检测服务。
利用 Qwen3-VL-Flash 作为兜底检测器，发现 YOLO 未覆盖的异常异物。
"""

import os
import re
import json
import time
import base64
import logging
from io import BytesIO
from pathlib import Path
from typing import Tuple, Optional

from PIL import Image
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

try:
    from dashscope import MultiModalConversation
    DASHSCOPE_OK = True
except ImportError:
    DASHSCOPE_OK = False


class FallbackDetector:
    """
    兜底异常检测器。
    对整张图片进行独立的语义扫描（不受 YOLO 8 类训练集限制），
    发现未知异物、设备缺陷或安全隐患，作为 AI 分析之外的第二道防线。
    """

    MODEL = "qwen3-vl-flash"
    # 独立 Prompt：目标是"判断图片中有什么"（开放场景），与 AI 分析的"缺陷深度分析"区分开
    PROMPT = (
        "请分析这张图片，判断它属于以下哪类电力设施场景：\n"
        "1. 正常输电线路（绝缘子完好、无外物）\n"
        "2. 存在异物（鸟巢、风筝、垃圾、塑料袋等）\n"
        "3. 存在设备缺陷（破损绝缘子、闪络痕迹等）\n"
        "4. 存在安全隐患（山火、施工机械靠近等）\n"
        "5. 其他异常情况\n\n"
        "请用简洁的语言描述这张图片中你看到的内容，并判断是否存在异常。\n"
        "输出格式（每行一个字段，不要输出其他内容）：\n"
        "- 描述：[你的描述]\n"
        "- 是否异常：[是/否]\n"
        "- 置信度：[高/中/低]"
    )

    def __init__(self, api_key: str = None):
        if not DASHSCOPE_OK:
            raise RuntimeError("dashscope 未安装")
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError("未设置 DASHSCOPE_API_KEY")

    def _encode_image(self, image_path: str) -> str:
        """将图片编码为 base64。"""
        with Image.open(image_path) as img:
            if max(img.size) > 1024:
                img.thumbnail((1024, 1024), Image.LANCZOS)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=75)
            return base64.b64encode(buf.getvalue()).decode("utf-8")

    @staticmethod
    def _normalize_confidence(value) -> str:
        """将置信度归一化为 high/medium/low。"""
        text = str(value or "").strip().lower()
        if text in ("高", "high", "高置信", "确定"):
            return "high"
        if text in ("中", "medium", "中置信"):
            return "medium"
        return "low"

    @staticmethod
    def _parse_bool(value) -> bool:
        """兼容"是/否"、"true/false"等布尔文本。"""
        text = str(value or "").strip().lower()
        return text in ("是", "true", "yes", "1", "异常", "有", "存在")

    def _parse_result(self, text: str) -> dict:
        """
        解析兜底检测返回，统一为结构化字段。

        Returns:
            {"description": str, "is_abnormal": bool, "confidence": "high/medium/low"}
        """
        # 优先尝试 JSON（兼容旧格式 abnormal / description 与约定格式 is_abnormal）
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(0))
                return {
                    "description": result.get("description", result.get("描述", "")) or "",
                    "is_abnormal": self._parse_bool(
                        result.get("is_abnormal", result.get("abnormal", False))
                    ),
                    "confidence": self._normalize_confidence(
                        result.get("confidence", result.get("置信度", "low"))
                    ),
                }
            except json.JSONDecodeError:
                pass

        # 非 JSON：按 "- 描述：xxx" 行格式解析
        description, confidence = "", "low"
        is_abnormal = False
        for line in text.splitlines():
            # 清理 "- "、"* "、"1. " 等项目符号前缀，再按字段开头匹配
            line = re.sub(r'^[\s\-*•·\d]+[.、)）]?\s*', '', line.strip())
            if line.startswith("描述") and (":" in line or "：" in line):
                description = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            elif line.startswith("是否异常"):
                is_abnormal = self._parse_bool(line.split("：", 1)[-1].split(":", 1)[-1])
            elif line.startswith("置信度"):
                confidence = self._normalize_confidence(
                    line.split("：", 1)[-1].split(":", 1)[-1]
                )
        return {
            "description": description or text.strip()[:200],
            "is_abnormal": is_abnormal,
            "confidence": confidence,
        }

    def check(self, image_path: str) -> Tuple[dict, float]:
        """
        执行兜底检测（独立于 AI 分析，判断整图内容）。

        Returns:
            ({"description": str, "is_abnormal": bool, "confidence": str}, elapsed_ms)
        """
        start = time.time()
        try:
            image_b64 = self._encode_image(image_path)
            messages = [{
                "role": "user",
                "content": [
                    {"image": f"data:image/jpeg;base64,{image_b64}"},
                    {"text": self.PROMPT},
                ],
            }]
            resp = MultiModalConversation.call(
                api_key=self.api_key, model=self.MODEL, messages=messages,
            )
            elapsed = (time.time() - start) * 1000
            text = ""
            if resp.output and resp.output.choices:
                content = resp.output.choices[0].message.content
                if isinstance(content, list):
                    text = "".join(
                        item.get("text", "") if isinstance(item, dict) else str(item)
                        for item in content
                    )
                else:
                    text = str(content)

            result = self._parse_result(text)
            logger.info(
                f"兜底检测完成: is_abnormal={result['is_abnormal']}, "
                f"confidence={result['confidence']}, {elapsed:.0f}ms"
            )
            return result, elapsed

        except Exception as e:
            elapsed = (time.time() - start) * 1000
            logger.warning(f"兜底检测失败: {e}")
            return {
                "description": f"检测异常: {e}",
                "is_abnormal": False,
                "confidence": "low",
            }, elapsed
