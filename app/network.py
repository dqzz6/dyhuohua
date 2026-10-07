"""网络数据包捕获：直接读取抖音接口返回的 JSON，用于更准确地识别好友。"""

from __future__ import annotations

import base64
import json
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

MAX_RECORDS = 120
MAX_BODY_CHARS = 256 * 1024
TRUNCATED_SUFFIX = "\n...[响应内容过长，已截断]"


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


class NetworkCapture:
    """监听 CDP 网络事件，保存接口返回的 JSON 原文。"""

    def __init__(self, cdp, logger=None, max_records: int = MAX_RECORDS):
        self._cdp = cdp
        self._logger = logger
        self._records: Deque[Dict[str, Any]] = deque(maxlen=max_records)
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        if self._enabled:
            return
        await self._cdp.send("Network.enable", {})
        self._cdp.add_listener("Network.responseReceived", self._on_response)
        self._cdp.add_listener("Network.loadingFinished", self._on_finished)
        self._enabled = True
        if self._logger is not None:
            self._logger.info("已开启接口数据抓取")

    def _on_response(self, params: Dict[str, Any]) -> None:
        response = params.get("response") or {}
        mime = str(response.get("mimeType") or "")
        if "json" not in mime.lower():
            return
        request_id = str(params.get("requestId") or "")
        if not request_id:
            return
        if len(self._pending) >= self._records.maxlen:
            self._pending.pop(next(iter(self._pending)), None)
        self._pending[request_id] = {
            "url": str(response.get("url") or ""),
            "status": response.get("status"),
            "mime": mime,
            "at": time.time(),
        }

    async def _on_finished(self, params: Dict[str, Any]) -> None:
        request_id = str(params.get("requestId") or "")
        info = self._pending.pop(request_id, None)
        if not info:
            return
        try:
            result = await self._cdp.send("Network.getResponseBody", {"requestId": request_id}, timeout=20)
        except Exception:
            return
        body = result.get("body") or ""
        if result.get("base64Encoded"):
            try:
                body = base64.b64decode(body).decode("utf-8", "replace")
            except Exception:
                return
        if not body:
            return
        info["size"] = len(body)
        info["json"] = _try_json(body)
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + TRUNCATED_SUFFIX
        info["body"] = body
        self._records.append(info)

    def clear(self) -> None:
        self._records.clear()
        self._pending.clear()

    def records(self) -> List[Dict[str, Any]]:
        return list(self._records)

    def summary(self, keyword: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        """给联调用的概览：接口地址、状态、大小与顶层字段。"""
        result: List[Dict[str, Any]] = []
        word = str(keyword or "").lower()
        for index, record in enumerate(self._records):
            url = str(record.get("url") or "")
            if word and word not in url.lower():
                continue
            payload = record.get("json")
            keys: List[str] = []
            if isinstance(payload, dict):
                keys = list(payload.keys())[:20]
            result.append(
                {
                    "index": index,
                    "url": url,
                    "status": record.get("status"),
                    "size": int(record.get("size") or len(str(record.get("body") or ""))),
                    "keys": keys,
                }
            )
        return result[-max(1, int(limit)) :]

    def body(self, index: int, limit: int = 4000) -> Optional[str]:
        records = list(self._records)
        if index < 0 or index >= len(records):
            return None
        text = str(records[index].get("body") or "")
        if limit and int(limit) > 0:
            return text[: int(limit)]
        return text

    def json_payloads(self) -> List[Any]:
        return [record.get("json") for record in self._records if record.get("json") is not None]

    def payloads_for(self, url_mark: str) -> List[Any]:
        """取出地址里包含指定片段的接口 JSON 数据。"""
        mark = str(url_mark or "")
        return [
            record.get("json")
            for record in self._records
            if mark in str(record.get("url") or "") and record.get("json") is not None
        ]
