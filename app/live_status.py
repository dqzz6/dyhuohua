"""直播状态查询：通过抖音直播接口判断主播是否开播，不依赖浏览器页面文字。"""

from __future__ import annotations

import asyncio
import http.cookiejar
import json
import urllib.parse
import urllib.request
from typing import Any, Dict

LIVE_ROOM_URL = "https://live.douyin.com/{room_id}"
LIVE_ENTER_API = "https://live.douyin.com/webcast/room/web/enter/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _to_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_live_status_payload(payload: Any) -> Dict[str, Any]:
    """解析直播接口返回，状态统一为 live、not_live 或 unknown。"""
    if not isinstance(payload, dict):
        return {
            "status": "unknown",
            "live": None,
            "detail": "直播接口没有返回有效数据",
            "title": "",
            "roomStatus": -1,
        }

    status_code = _to_int(payload.get("status_code"), -1)
    if status_code != 0:
        return {
            "status": "unknown",
            "live": None,
            "detail": f"直播接口返回异常状态：{status_code}",
            "title": "",
            "roomStatus": -1,
        }

    data = payload.get("data")
    data = data if isinstance(data, dict) else {}
    rooms = data.get("data")
    rooms = rooms if isinstance(rooms, list) else []
    if not rooms:
        return {
            "status": "not_live",
            "live": False,
            "detail": "主播当前未开播",
            "title": "",
            "roomStatus": -1,
        }

    room = rooms[0] if isinstance(rooms[0], dict) else {}
    room_status = _to_int(room.get("status"), -1)
    title = str(room.get("title") or "").strip()

    if room_status == 2:
        return {
            "status": "live",
            "live": True,
            "detail": title or "主播正在直播",
            "title": title,
            "roomStatus": room_status,
        }
    if room_status in {1, 3, 4}:
        return {
            "status": "not_live",
            "live": False,
            "detail": f"主播当前未开播（接口状态：{room_status}）",
            "title": title,
            "roomStatus": room_status,
        }
    return {
        "status": "unknown",
        "live": None,
        "detail": f"直播接口返回未知房间状态：{room_status}",
        "title": title,
        "roomStatus": room_status,
    }


class LiveStatusClient:
    """带 Cookie 会话的直播状态客户端，可重复查询多个直播间。"""

    def __init__(self, logger=None, timeout: float = 20.0):
        self._logger = logger
        self._timeout = max(5.0, float(timeout))
        self._cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookies)
        )
        self._warmed = False
        self._lock = asyncio.Lock()

    async def check(self, room_id: str) -> Dict[str, Any]:
        room_id = str(room_id or "").strip()
        if not room_id:
            return {
                "status": "unknown",
                "live": None,
                "detail": "直播间房间号为空",
                "title": "",
                "roomStatus": -1,
            }
        async with self._lock:
            try:
                payload = await asyncio.to_thread(self._request, room_id)
            except Exception as exc:
                return {
                    "status": "unknown",
                    "live": None,
                    "detail": f"直播接口查询失败：{exc}",
                    "title": "",
                    "roomStatus": -1,
                }
        return parse_live_status_payload(payload)

    def _request(self, room_id: str) -> Dict[str, Any]:
        if not self._warmed:
            self._warm_up(room_id)
        payload = self._request_json(room_id)
        if payload:
            return payload

        # Cookie 失效或站点会话变化时，重新访问直播间刷新会话并重试一次。
        self._warmed = False
        self._warm_up(room_id)
        payload = self._request_json(room_id)
        if not payload:
            raise RuntimeError("直播接口返回空内容")
        return payload

    def _warm_up(self, room_id: str) -> None:
        request = urllib.request.Request(
            LIVE_ROOM_URL.format(room_id=urllib.parse.quote(room_id)),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        with self._opener.open(request, timeout=self._timeout) as response:
            response.read(512)
        self._warmed = True

    def _request_json(self, room_id: str) -> Dict[str, Any]:
        params = {
            "aid": "6383",
            "app_name": "douyin_web",
            "live_id": "1",
            "device_platform": "web",
            "language": "zh-CN",
            "enter_from": "web_live",
            "cookie_enabled": "true",
            "screen_width": "1920",
            "screen_height": "1080",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_version": "130.0.0.0",
            "web_rid": room_id,
        }
        url = f"{LIVE_ENTER_API}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": LIVE_ROOM_URL.format(
                    room_id=urllib.parse.quote(room_id)
                ),
            },
        )
        with self._opener.open(request, timeout=self._timeout) as response:
            body = response.read().decode("utf-8", "replace")
        if not body:
            return {}
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise RuntimeError(f"直播接口返回格式错误：{exc}") from exc
        return payload if isinstance(payload, dict) else {}
