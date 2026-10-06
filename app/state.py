"""发送记录：保证同一天只成功发送一次，重复启动或重启不会重复发送。"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logger import BEIJING
from .paths import HISTORY_PATH, ensure_dirs


class SendStore:
    """按日期记录发送结果，文件损坏时自动回落为空记录。"""

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
        return data if isinstance(data, dict) else {}

    def _write(self, data: Dict[str, Any]) -> None:
        ensure_dirs()
        temp_path = self._path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._path)

    def get(self, date_key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._read().get(str(date_key))
        return entry if isinstance(entry, dict) else None

    def has_success(self, date_key: Optional[str] = None) -> bool:
        entry = self.get(date_key or self.today_key()) or {}
        return str(entry.get("status") or "") == "success"

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
        entry = {
            "status": status,
            "target": str(target or ""),
            "message": str(message or ""),
            "detail": str(detail or ""),
            "reason": str(reason or ""),
            "sentAt": datetime.now(BEIJING).isoformat(timespec="seconds"),
        }
        with self._lock:
            data = self._read()
            data[key] = entry
            self._write(data)
        return entry

    def history(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._read()
        keys = sorted(data.keys(), reverse=True)[: max(1, int(limit))]
        result = []
        for key in keys:
            entry = data.get(key)
            if isinstance(entry, dict):
                result.append({"date": key, **entry})
        return result
