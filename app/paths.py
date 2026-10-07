"""项目路径定义，统一出口，避免在各模块里散落硬编码路径。"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"

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
