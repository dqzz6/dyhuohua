"""统一日志：界面、控制台、文件共用同一份记录，时间统一北京时间。"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, List
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")
_LOGGER_NAME = "抖音自动消息"
_buffer: Deque[str] = deque(maxlen=1000)
_lock = threading.Lock()
_initialized = False


class _MemoryHandler(logging.Handler):
    """把日志同时留在内存里，供图形界面实时展示。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _buffer.append(self.format(record))
        except Exception:
            pass


def setup_logger(log_dir: Path, level: int = logging.INFO) -> logging.Logger:
    """初始化全局日志器，重复调用不会重复挂载处理器。"""
    global _initialized
    logger = logging.getLogger(_LOGGER_NAME)
    with _lock:
        if _initialized:
            return logger
        logger.setLevel(level)
        logger.propagate = False

        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        formatter.converter = lambda *_: datetime.now(BEIJING).timetuple()

        log_dir.mkdir(parents=True, exist_ok=True)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        file_handler = logging.FileHandler(log_dir / "运行日志.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        memory_handler = _MemoryHandler()
        memory_handler.setFormatter(formatter)
        logger.addHandler(memory_handler)

        _initialized = True
        return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def recent_lines(limit: int = 200) -> List[str]:
    """返回最近若干行日志，供界面与控制接口读取。"""
    try:
        count = max(1, int(limit))
    except (TypeError, ValueError):
        count = 200
    return list(_buffer)[-count:]
