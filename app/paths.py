"""项目路径定义，统一出口，避免在各模块里散落硬编码路径。"""

from __future__ import annotations

import os
import re
import zlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTANCE_ENV = "DOUYIN_INSTANCE"
DEFAULT_INSTANCE_NAME = "默认"
DEFAULT_CONTROL_API_PORT = 8791


def normalize_instance_name(value) -> str:
    """把实例名限制为可安全用于目录名称的短字符串。"""
    raw = str(value or "").strip()
    if not raw:
        return DEFAULT_INSTANCE_NAME
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", raw)
    safe = safe.strip("._")
    return safe[:20] or DEFAULT_INSTANCE_NAME


def instance_port_offset(value) -> int:
    """为命名实例计算稳定的控制接口端口偏移。"""
    name = normalize_instance_name(value)
    if name == DEFAULT_INSTANCE_NAME:
        return 0
    return 1 + zlib.crc32(name.encode("utf-8")) % 1000


def instance_root(base_dir: Path, value) -> Path:
    """默认实例沿用原目录，命名实例使用独立目录。"""
    name = normalize_instance_name(value)
    if name == DEFAULT_INSTANCE_NAME:
        return Path(base_dir)
    return Path(base_dir) / "实例" / name


INSTANCE_NAME = normalize_instance_name(os.getenv(INSTANCE_ENV))
if INSTANCE_NAME == DEFAULT_INSTANCE_NAME:
    DATA_DIR = PROJECT_ROOT / "data"
    LOG_DIR = PROJECT_ROOT / "logs"
else:
    INSTANCE_ROOT = PROJECT_ROOT / "data" / "实例" / INSTANCE_NAME
    DATA_DIR = INSTANCE_ROOT / "数据"
    LOG_DIR = INSTANCE_ROOT / "日志"

DEFAULT_INSTANCE_CONTROL_PORT = DEFAULT_CONTROL_API_PORT + instance_port_offset(
    INSTANCE_NAME
)

CONFIG_PATH = DATA_DIR / "config.json"
HISTORY_PATH = DATA_DIR / "send_history.json"
BADGE_HISTORY_PATH = DATA_DIR / "badge_renewal_history.json"
SELECTORS_PATH = DATA_DIR / "selectors.json"
RUNTIME_PATH = DATA_DIR / "runtime.json"
AVATAR_DIR = DATA_DIR / "avatars"
FRIENDS_CACHE_PATH = DATA_DIR / "friends_cache.json"
# 内置浏览器（QtWebEngine）的登录数据目录
PROFILE_DIR = DATA_DIR / "web-profile"


def ensure_dirs() -> None:
    """确保数据目录与日志目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
