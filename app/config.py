"""配置读写：JSON 存储，缺省字段自动补全，写入原子且线程安全。"""

from __future__ import annotations

import copy
import json
import re
import threading
from typing import Any, Dict

from .badge_renewal import (
    DEFAULT_CHECK_INTERVAL_MINUTES,
    DEFAULT_WATCH_MINUTES,
    MAX_CHECK_INTERVAL_MINUTES,
    MAX_WATCH_MINUTES,
)
from .badge_renewal import normalize_live_urls
from .paths import CONFIG_PATH, DEFAULT_INSTANCE_CONTROL_PORT, ensure_dirs

TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{1,2})$")

DEFAULT_CONFIG: Dict[str, Any] = {
    "target_names": [],
    "message": "续火花",
    "message_mode": "whole",
    "send_time": "09:00",
    "daily_enabled": True,
    "missed_run": False,
    "missed_grace_minutes": 180,
    "retry_interval_minutes": 10,
    "max_attempts_per_day": 3,
    "start_url": "https://creator.douyin.com/creator-micro/data/following/chat",
    "match_mode": "equals",
    "headless": False,
    "control_api_port": DEFAULT_INSTANCE_CONTROL_PORT,
    "send_timeout_seconds": 120,
    "chat_browser_lazy": True,
    "live_browser_enabled": False,
    "badge_renewal_enabled": False,
    "badge_live_urls": [],
    "badge_check_interval_minutes": DEFAULT_CHECK_INTERVAL_MINUTES,
    "badge_watch_minutes": DEFAULT_WATCH_MINUTES,
}

_lock = threading.RLock()


def normalize_time(value: Any) -> str:
    """把时间统一成 HH:MM；允许输入 9:5 这类简写，非法时抛 ValueError。"""
    raw = str(value or "").strip()
    match = TIME_PATTERN.fullmatch(raw)
    if not match:
        raise ValueError("发送时间必须为 时:分 格式，例如 09:30")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("发送时间必须在 00:00 到 23:59 之间")
    return f"{hour:02d}:{minute:02d}"


def normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """合并缺省值并做类型与范围校正。"""
    source = dict(raw or {})
    merged = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in source.items():
        if key in merged:
            merged[key] = value

    merged["send_time"] = normalize_time(merged.get("send_time"))
    merged["target_names"] = _normalize_target_names(source.get("target_names"), source.get("target_name"))
    merged["message"] = str(merged.get("message") or "").strip()
    merged["message_mode"] = "random_line" if str(merged.get("message_mode")) == "random_line" else "whole"
    merged["match_mode"] = "contains" if str(merged.get("match_mode")) == "contains" else "equals"
    merged["daily_enabled"] = bool(merged.get("daily_enabled"))
    merged["missed_run"] = bool(merged.get("missed_run"))
    merged["headless"] = bool(merged.get("headless"))
    merged["missed_grace_minutes"] = max(0, _to_int(merged.get("missed_grace_minutes"), 180))
    merged["retry_interval_minutes"] = max(1, _to_int(merged.get("retry_interval_minutes"), 10))
    merged["max_attempts_per_day"] = min(10, max(1, _to_int(merged.get("max_attempts_per_day"), 3)))
    merged["control_api_port"] = min(
        65500,
        max(
            1024,
            _to_int(
                merged.get("control_api_port"),
                DEFAULT_INSTANCE_CONTROL_PORT,
            ),
        ),
    )
    merged["send_timeout_seconds"] = max(30, _to_int(merged.get("send_timeout_seconds"), 120))
    merged["chat_browser_lazy"] = bool(merged.get("chat_browser_lazy"))
    merged["badge_renewal_enabled"] = bool(merged.get("badge_renewal_enabled"))
    if "live_browser_enabled" in source:
        merged["live_browser_enabled"] = bool(merged.get("live_browser_enabled"))
    else:
        # 旧版本没有独立开关时，延续原有续灯牌行为。
        merged["live_browser_enabled"] = bool(merged.get("badge_renewal_enabled"))
    if merged["badge_renewal_enabled"]:
        # 续灯牌必须依赖直播浏览器，避免配置出现不可执行的组合。
        merged["live_browser_enabled"] = True
    try:
        merged["badge_live_urls"] = normalize_live_urls(merged.get("badge_live_urls"))
    except ValueError:
        merged["badge_live_urls"] = []
    merged["badge_check_interval_minutes"] = min(
        MAX_CHECK_INTERVAL_MINUTES,
        max(1, _to_int(merged.get("badge_check_interval_minutes"), DEFAULT_CHECK_INTERVAL_MINUTES)),
    )
    merged["badge_watch_minutes"] = min(
        MAX_WATCH_MINUTES,
        max(1, _to_int(merged.get("badge_watch_minutes"), DEFAULT_WATCH_MINUTES)),
    )
    merged["start_url"] = str(merged.get("start_url") or DEFAULT_CONFIG["start_url"]).strip()
    merged.pop("target_name", None)
    return merged


def _normalize_target_names(names: Any, legacy_target: Any = None) -> list:
    """好友名去重去空白；同时兼容旧版单好友配置。"""
    result: list = []
    for item in list(names or []) if isinstance(names, (list, tuple)) else []:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    legacy = str(legacy_target or "").strip()
    if legacy and legacy not in result:
        result.insert(0, legacy)
    return result


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _write(config: Dict[str, Any]) -> None:
    ensure_dirs()
    temp_path = CONFIG_PATH.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(CONFIG_PATH)


def load_config() -> Dict[str, Any]:
    """读取配置；文件不存在或损坏时回落到缺省配置。"""
    ensure_dirs()
    with _lock:
        if not CONFIG_PATH.exists():
            config = normalize_config({})
            _write(config)
            return config
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        return normalize_config(raw if isinstance(raw, dict) else {})


def save_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """在现有配置上打补丁并落盘，返回规范化后的完整配置。"""
    with _lock:
        config = load_config()
        config.update(dict(patch or {}))
        config = normalize_config(config)
        _write(config)
        return config
