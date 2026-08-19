#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YOLO 模型加载、推理与结果绘制封装。
支持单张图片推理、批量推理，以及检测结果可视化。
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional, Tuple, List

import cv2
import numpy as np
from PIL import Image
from ultralytics import YOLO
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# 类别名称映射（8 类）
CLASS_NAMES = {
    0: "nest",
    1: "kite",
    2: "balloon",
    3: "trash",
    4: "insulator_shell",
    5: "broken_insulator_shell",
    6: "flashover_damaged_insulator_shell",
    7: "good_insulator_shell",
}

# 缺陷类别（ID 5、6 为缺陷，其余为正常/异物）
DEFECT_CLASS_IDS = {1, 2, 3, 5, 6}  # 风筝、气球、垃圾、破损绝缘子、闪络绝缘子

# 类别对应的 BGR 颜色（用于绘制检测框）
CLASS_COLORS = {
    0: (0, 255, 0),       # nest - 绿色
    1: (0, 165, 255),     # kite - 橙色
    2: (0, 255, 255),     # balloon - 黄色
    3: (128, 128, 128),   # trash - 灰色
    4: (255, 0, 0),       # insulator_shell - 蓝色
    5: (0, 0, 255),       # broken_insulator_shell - 红色
    6: (255, 0, 255),     # flashover_damaged - 品红
    7: (0, 255, 0),       # good_insulator_shell - 绿色
}


class YOLODetector:
    """YOLO 目标检测器，封装模型加载、推理与可视化。"""

    def __init__(self, model_path: Optional[str] = None):
        """
        初始化检测器并加载模型。

        Args:
            model_path: 训练好的 best.pt 路径，默认从环境变量读取
        """
        if model_path is None:
            model_path = os.getenv(
                "YOLO_MODEL_PATH",
                "./runs/train/yolo_train_20260729_215517/weights/best.pt"
            )

        # 如果路径是相对于项目根目录的，解析为绝对路径
        model_path = Path(model_path)
        if not model_path.is_absolute():
            # 尝试从项目根目录解析
            project_root = Path(__file__).resolve().parent.parent
            model_path = project_root / model_path

        model_path = str(model_path)

        if not Path(model_path).exists():
            raise FileNotFoundError(
                f"YOLO 模型文件不存在: {model_path}\n"
                f"请确认已运行 train_yolo26.py 完成训练，或修改 .env 中的 YOLO_MODEL_PATH。"
            )

        logger.info(f"加载 YOLO 模型: {model_path}")
        self.model = YOLO(model_path)
        self.model_path = model_path
        self.class_names = CLASS_NAMES

        # 预热模型（首次推理较慢，预热后可加速后续推理）
        logger.info("预热模型...")
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        self.model(dummy, verbose=False)
        logger.info("模型加载完成，预热完毕")

    def detect(self, image: np.ndarray, conf_threshold: float = 0.25) -> Tuple[List[dict], float]:
        """
        对单张图片执行目标检测。

        Args:
            image: BGR 格式的 numpy 数组（OpenCV 读取格式）
            conf_threshold: 置信度阈值

        Returns:
            (检测结果列表, 推理耗时(毫秒))
            每个结果包含: class_id, class_name, confidence, bbox(x1,y1,x2,y2)
        """
        start = time.time()

        # YOLO 推理
        results = self.model(image, conf=conf_threshold, verbose=False)

        elapsed_ms = (time.time() - start) * 1000

        detections = []
        if results and len(results) > 0:
            result = results[0]
            boxes = result.boxes
            if boxes is not None and len(boxes) > 0:
                for i in range(len(boxes)):
                    cls_id = int(boxes.cls[i].item())
                    conf = float(boxes.conf[i].item())
                    xyxy = boxes.xyxy[i].cpu().numpy()

                    detections.append({
                        "class_id": cls_id,
                        "class_name": self.class_names.get(cls_id, f"unknown_{cls_id}"),
                        "confidence": round(conf, 4),
                        "bbox": {
                            "x1": round(float(xyxy[0]), 2),
                            "y1": round(float(xyxy[1]), 2),
                            "x2": round(float(xyxy[2]), 2),
                            "y2": round(float(xyxy[3]), 2),
                        },
                    })

        return detections, elapsed_ms

    def draw_detections(
        self,
        image: np.ndarray,
        detections: List[dict],
        line_thickness: int = 2,
        font_scale: float = 0.6,
    ) -> np.ndarray:
        """
        在图片上绘制检测框、类别标签和置信度。

        Args:
            image: 原始 BGR 图片
            detections: detect() 返回的检测结果列表
            line_thickness: 框线粗细
            font_scale: 字体大小

        Returns:
            绘制了检测框的 BGR 图片
        """
        drawn = image.copy()
        h, w = drawn.shape[:2]

        for det in detections:
            cls_id = det["class_id"]
            cls_name = det["class_name"]
            conf = det["confidence"]
            bbox = det["bbox"]

            color = CLASS_COLORS.get(cls_id, (255, 255, 255))

            # 绘制边界框
            x1, y1 = int(bbox["x1"]), int(bbox["y1"])
            x2, y2 = int(bbox["x2"]), int(bbox["y2"])
            cv2.rectangle(drawn, (x1, y1), (x2, y2), color, line_thickness)

            # 绘制类别标签背景
            label = f"{cls_name} {conf:.2f}"
            (text_w, text_h), baseline = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 2
            )
            # 标签放在检测框上方
            label_y = max(y1 - text_h - 5, text_h + 5)
            cv2.rectangle(
                drawn,
                (x1, label_y - text_h - 5),
                (x1 + text_w + 6, label_y + baseline),
                color,
                -1,  # 填充
            )
            cv2.putText(
                drawn,
                label,
                (x1 + 3, label_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                (255, 255, 255),  # 白色文字
                2,
                cv2.LINE_AA,
            )

        return drawn

    def classify_detections(self, detections: List[dict]) -> dict:
        """
        对检测结果进行分类统计。

        Returns:
            {
                "total": 总检测数,
                "defects": [...缺陷列表],
                "normals": [...正常列表],
                "by_class": {class_name: count},
            }
        """
        defects = [d for d in detections if d["class_id"] in DEFECT_CLASS_IDS]
        normals = [d for d in detections if d["class_id"] not in DEFECT_CLASS_IDS]

        by_class = {}
        for d in detections:
            name = d["class_name"]
            by_class[name] = by_class.get(name, 0) + 1

        return {
            "total": len(detections),
            "defects": defects,
            "normals": normals,
            "defect_count": len(defects),
            "normal_count": len(normals),
            "by_class": by_class,
        }
