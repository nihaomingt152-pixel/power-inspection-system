#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多模态 AI 分析服务。
通过 DashScope SDK 调用 Qwen3-VL-Flash 模型，对电力巡检图片进行智能分析。
"""

import os
import re
import json
import time
import base64
import logging
from pathlib import Path
from typing import Tuple, Optional
from io import BytesIO

from PIL import Image
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# 检查 dashscope 是否已安装
try:
    import dashscope
    from dashscope import MultiModalConversation
    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False
    logger.warning("dashscope 未安装，AI 分析功能将不可用。请运行: pip install dashscope")


class MultimodalService:
    """
    Qwen3-VL-Flash 多模态分析服务。
    使用 DashScope API 对电力巡检图片进行缺陷语义描述、严重程度分级、
    故障成因推断、维修建议生成等分析。
    """

    MODEL_NAME = "qwen3-vl-flash"

    # 缺陷类别中文名映射
    CLASS_NAMES_ZH = {
        0: "鸟巢",
        1: "风筝",
        2: "气球",
        3: "垃圾",
        4: "绝缘体外壳",
        5: "破损的绝缘壳",
        6: "闪燃损坏的绝缘器外壳",
        7: "良好的绝缘外壳",
    }

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化多模态服务。

        Args:
            api_key: DashScope API Key，默认从环境变量 DASHSCOPE_API_KEY 读取
        """
        if not DASHSCOPE_AVAILABLE:
            raise RuntimeError(
                "dashscope 未安装，无法使用 AI 分析功能。"
                "请运行: pip install dashscope"
            )

        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "未设置 DASHSCOPE_API_KEY！\n"
                "请在 .env 文件中配置 DASHSCOPE_API_KEY=your_key\n"
                "获取地址: https://dashscope.console.aliyun.com/apiKey"
            )

    def _encode_image_to_base64(self, image_path: str) -> str:
        """
        将本地图片编码为 base64 字符串（用于 API 调用）。

        Args:
            image_path: 图片文件路径

        Returns:
            base64 编码的图片字符串（不含 data:image 前缀）
        """
        with Image.open(image_path) as img:
            # 如果图片过大，先缩放
            max_size = 1920
            if max(img.size) > max_size:
                img.thumbnail((max_size, max_size), Image.LANCZOS)

            # 转为 RGB（避免 RGBA 导致的 API 兼容问题）
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def _format_detections(self, detections: list, classification: dict) -> str:
        """
        将 YOLO 检测结果格式化为人类可读的文本。

        Args:
            detections: 检测结果列表
            classification: 分类统计结果

        Returns:
            格式化后的文本描述
        """
        if not detections:
            return "未检测到任何目标。该区域本应有电力设备，请关注是否存在检测遗漏。"

        lines = [f"共检测到 {len(detections)} 个目标：\n"]
        for i, d in enumerate(detections, 1):
            zh_name = self.CLASS_NAMES_ZH.get(
                d["class_id"], d["class_name"]
            )
            bbox = d["bbox"]
            lines.append(
                f"  {i}. [{zh_name}] 置信度: {d['confidence']:.2%}, "
                f"位置: ({bbox['x1']:.0f}, {bbox['y1']:.0f}) 到 ({bbox['x2']:.0f}, {bbox['y2']:.0f})"
            )

        # 添加统计信息
        if classification.get("by_class"):
            lines.append("\n各类别统计：")
            for cls_name, count in classification["by_class"].items():
                lines.append(f"  - {cls_name}: {count} 个")

        return "\n".join(lines)

    def _build_prompt(self, detections: list, classification: dict) -> str:
        """
        构建发送给 Qwen3-VL-Flash 的分析提示词。

        Args:
            detections: YOLO 检测结果
            classification: 分类统计

        Returns:
            提示词文本
        """
        detection_text = self._format_detections(detections, classification)

        prompt = f"""你是一名资深的电力输电线路巡检专家。请根据提供的巡检图片和 YOLO 目标检测结果，进行专业的缺陷分析。

【YOLO 检测结果】
{detection_text}

【分析要求】
请严格按以下 JSON 格式输出分析报告（不要输出其他内容，确保 JSON 可被直接解析）：

```json
{{
    "description": "用中文详细描述检测到的缺陷情况。应包含：缺陷物体是什么、在图片中的具体位置（如左上角、右侧第二根杆塔等）、缺陷的形态特征（大小、颜色、形状等）。如果未检测到目标，请说明画面中可见的电力设备状态。",
    "severity": "一般/严重/紧急",
    "severity_reason": "判断为当前严重等级的具体原因",
    "cause": "推测导致该缺陷的可能原因（如雷击闪络、鸟粪腐蚀、机械磨损、材料老化、施工缺陷、恶劣天气等），需给出判断依据",
    "suggestion": "具体的维修或处理建议，包含建议处理时限和注意事项",
    "alert": true/false,
    "alert_reason": "触发或不触发预警的原因"
}}
```

【严重等级判定标准】
- **一般**：检测到正常设备或异物（鸟巢、风筝、气球、垃圾），暂不影响线路安全运行
- **严重**：检测到破损绝缘壳或闪燃损坏，但范围较小，短期内不会导致跳闸
- **紧急**：检测到大面积破损、严重闪燃痕迹或多处同时损坏，可能导致立即跳闸或断线

【注意】
1. 如果检测到破损绝缘壳(broken)或闪燃损坏(flashover)，通常应判定为"严重"或"紧急"
2. 如果只检测到异物（鸟巢、风筝、气球、垃圾），一般为"一般"等级
3. 如果未检测到任何目标，但图片显示有电力设备存在，应判定为"一般"并建议人工确认
4. alert 字段：severity 为"严重"或"紧急"时设为 true，否则为 false"""

        return prompt

    def _parse_response(self, response_text: str) -> dict:
        """
        解析 Qwen3-VL-Flash 的返回结果，提取 JSON 分析报告。

        Args:
            response_text: API 返回的原始文本

        Returns:
            解析后的字典
        """
        # 尝试直接从 JSON 代码块中提取
        json_match = re.search(r'```json\s*\n(.*?)\n```', response_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接匹配 JSON 对象
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                # 无法解析 JSON，返回原始文本
                logger.warning(f"无法从响应中提取 JSON: {response_text[:500]}")
                return {
                    "description": response_text,
                    "severity": "一般",
                    "severity_reason": "无法自动判定",
                    "cause": "未知",
                    "suggestion": "请人工复核分析结果",
                    "alert": False,
                    "alert_reason": "AI 返回格式异常，默认不触发预警",
                    "raw_response": response_text,
                }

        try:
            result = json.loads(json_str)
            # 确保必要字段存在
            defaults = {
                "description": "",
                "severity": "一般",
                "severity_reason": "",
                "cause": "未知",
                "suggestion": "请人工复核",
                "alert": False,
                "alert_reason": "",
            }
            for key, default_value in defaults.items():
                if key not in result:
                    result[key] = default_value
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}, 原始文本: {json_str[:500]}")
            return {
                "description": response_text[:1000],
                "severity": "一般",
                "severity_reason": f"JSON 解析失败: {str(e)}",
                "cause": "未知",
                "suggestion": "请人工复核",
                "alert": False,
                "alert_reason": "AI 返回 JSON 格式异常",
                "raw_response": response_text,
            }

    def analyze(
        self,
        image_path: str,
        detections: list,
        classification: dict,
    ) -> Tuple[dict, float]:
        """
        调用 Qwen3-VL-Flash 进行多模态分析。

        Args:
            image_path: 图片文件路径
            detections: YOLO 检测结果列表
            classification: 分类统计结果

        Returns:
            (分析结果字典, API 调用耗时(毫秒))
        """
        start_time = time.time()

        # 编码图片
        image_base64 = self._encode_image_to_base64(image_path)

        # 构建 Prompt
        prompt = self._build_prompt(detections, classification)

        # 构建消息（使用 base64 内嵌图片）
        messages = [
            {
                "role": "user",
                "content": [
                    {"image": f"data:image/jpeg;base64,{image_base64}"},
                    {"text": prompt},
                ],
            }
        ]

        # 调用 API
        logger.info(f"调用 Qwen3-VL-Flash API，图片: {Path(image_path).name}")
        try:
            response = MultiModalConversation.call(
                api_key=self.api_key,
                model=self.MODEL_NAME,
                messages=messages,
            )

            elapsed_ms = (time.time() - start_time) * 1000

            # 提取返回文本
            if response.output and response.output.choices:
                content = response.output.choices[0].message.content
                # content 可能是列表或字符串
                if isinstance(content, list):
                    response_text = "".join(
                        item.get("text", "") if isinstance(item, dict) else str(item)
                        for item in content
                    )
                else:
                    response_text = str(content)
            else:
                response_text = ""
                logger.warning(f"API 返回空内容: {response}")

            logger.info(f"API 调用完成，耗时: {elapsed_ms:.0f}ms")

            # 解析结果
            result = self._parse_response(response_text)
            result["_api_time_ms"] = round(elapsed_ms, 2)
            result["_model"] = self.MODEL_NAME

            return result, elapsed_ms

        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            logger.error(f"Qwen3-VL-Flash API 调用失败: {e}")
            raise RuntimeError(f"AI 分析服务调用失败: {str(e)}")
