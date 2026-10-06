"""发送记录：按天、按好友记录结果，保证同一天同一位好友只成功发送一次。"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logger import BEIJING
from .paths import HISTORY_PATH, ensure_dirs


def _migrate_day(value: Any) -> Dict[str, Any]:
    """兼容旧版按天单条记录的格式：{日期: {status,...}} -> {日期: {好友: 记录}}。"""
    if not isinstance(value, dict):
        return {}
    if "status" not in value:
        return value
    target = str(value.get("target") or "").strip()
    if not target:
        return {}
    entry = {key: item for key, item in value.items() if key != "target"}
    return {target: entry}


class SendStore:
    """文件结构：{日期: {好友名: 记录}}，文件损坏时自动回落为空记录。"""

    def __init__(self, path: Path = HISTORY_PATH):
        self._path = Path(path)
        self._lock = threading.RLock()

    def today_key(self) -> str:
        return datetime.now(BEIJING).strftime("%Y-%m-%d")

    def _read(self) -> Dict[str, Any]:
        ensure_dirs()
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {key: _migrate_day(value) for key, value in data.items()}

    def _write(self, data: Dict[str, Any]) -> None:
        ensure_dirs()
        temp_path = self._path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._path)

    # ---------- 读取 ----------
    def day_entries(self, date_key: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            day = self._read().get(date_key or self.today_key())
        return day if isinstance(day, dict) else {}

    def get(self, date_key: str, target: str) -> Optional[Dict[str, Any]]:
        entry = self.day_entries(date_key).get(str(target))
        return entry if isinstance(entry, dict) else None

    def is_success(self, date_key: str, target: str) -> bool:
        entry = self.get(date_key, target) or {}
        return str(entry.get("status") or "") == "success"

    def success_targets(self, date_key: Optional[str] = None) -> List[str]:
        return [
            name
            for name, entry in self.day_entries(date_key).items()
            if isinstance(entry, dict) and str(entry.get("status") or "") == "success"
        ]

    def attempt_count(self, date_key: str, target: str) -> int:
        entry = self.get(date_key, target) or {}
        try:
            return int(entry.get("attempts") or 0)
        except (TypeError, ValueError):
            return 0

    def last_attempt_at(self, date_key: str, target: str) -> Optional[datetime]:
        entry = self.get(date_key, target) or {}
        raw = str(entry.get("sentAt") or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=BEIJING)
        return parsed.astimezone(BEIJING)

    def pending_targets(self, date_key: str, targets: List[str]) -> List[str]:
        return [name for name in targets if not self.is_success(date_key, name)]

    def last_sent_message(self, target: str) -> str:
        """往上找这位好友最近一次成功发送的内容，随机抽一行时用来避开重复。"""
        with self._lock:
            data = self._read()
        for date_key in sorted(data.keys(), reverse=True):
            day = data.get(date_key)
            if not isinstance(day, dict):
                continue
            entry = day.get(str(target))
            if isinstance(entry, dict) and str(entry.get("status") or "") == "success":
                return str(entry.get("message") or "")
        return ""

    def history(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._read()
        result: List[Dict[str, Any]] = []
        for date_key in sorted(data.keys(), reverse=True)[: max(1, int(limit))]:
            day = data.get(date_key)
            if not isinstance(day, dict):
                continue
            for target, entry in day.items():
                if isinstance(entry, dict):
                    result.append({"date": date_key, "target": target, **entry})
        return result

    # ---------- 写入 ----------
    def record(
        self,
        status: str,
        target: str,
        message: str,
        detail: str = "",
        reason: str = "",
        date_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        key = date_key or self.today_key()
        name = str(target or "")
        with self._lock:
            data = self._read()
            day = data.get(key)
            if not isinstance(day, dict):
                day = {}
            previous = day.get(name)
            attempts = 1
            if isinstance(previous, dict):
                try:
                    attempts = int(previous.get("attempts") or 0) + 1
                except (TypeError, ValueError):
                    attempts = 1
            entry = {
                "status": str(status),
                "message": str(message or ""),
                "detail": str(detail or ""),
                "reason": str(reason or ""),
                "attempts": attempts,
                "sentAt": datetime.now(BEIJING).isoformat(timespec="seconds"),
            }
            day[name] = entry
            data[key] = day
            self._write(data)
        return entry
