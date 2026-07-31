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
    对 YOLO 检测之外的异常异物（塑料袋、工程机械、山火等）进行扫描。
    """

    MODEL = "qwen3-vl-flash"
    PROMPT = (
        "这张电力设施图片中，除了已知的绝缘子、鸟巢、风筝、垃圾外，"
        "是否存在其他肉眼可见的异常异物或安全隐患？（如塑料袋、工程机械、山火、烟雾、"
        "断线、倒塔、树木倾倒等）。"
        "请按 JSON 格式回复: "
        '{"abnormal": true/false, "description": "简要描述（若abnormal为false则为空）"}'
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

    def check(self, image_path: str) -> Tuple[dict, float]:
        """
        执行兜底检测。

        Returns:
            ({"abnormal": bool, "description": str}, elapsed_ms)
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

            # 解析 JSON
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                result = json.loads(match.group(0))
            else:
                result = {"abnormal": False, "description": ""}

            logger.info(f"兜底检测完成: abnormal={result.get('abnormal')}, {elapsed:.0f}ms")
            return result, elapsed

        except Exception as e:
            elapsed = (time.time() - start) * 1000
            logger.warning(f"兜底检测失败: {e}")
            return {"abnormal": False, "description": f"检测异常: {e}"}, elapsed
