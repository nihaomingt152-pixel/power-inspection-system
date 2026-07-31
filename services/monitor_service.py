#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
硬件资源监控服务。
通过 pynvml 读取 GPU 利用率、显存、温度等信息。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import pynvml
    pynvml.nvmlInit()
    NVML_OK = True
except Exception as e:
    logger.warning(f"pynvml 初始化失败（可能无 NVIDIA GPU）: {e}")
    NVML_OK = False


def get_gpu_info() -> dict:
    """
    获取 GPU 实时信息。

    Returns:
        {
            "available": bool,
            "gpu_name": str,
            "utilization_pct": float,
            "memory_used_gb": float,
            "memory_total_gb": float,
            "temperature_c": float,
        }
    """
    if not NVML_OK:
        return {"available": False, "message": "pynvml 不可用或无 NVIDIA GPU"}

    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        name = pynvml.nvmlDeviceGetName(handle)

        return {
            "available": True,
            "gpu_name": str(name),
            "utilization_pct": float(util.gpu),
            "memory_used_gb": round(mem.used / (1024 ** 3), 2),
            "memory_total_gb": round(mem.total / (1024 ** 3), 2),
            "memory_pct": round(mem.used / mem.total * 100, 1),
            "temperature_c": float(temp),
        }
    except Exception as e:
        return {"available": False, "message": str(e)}
