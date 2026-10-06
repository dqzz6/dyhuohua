"""轻量 CDP 客户端：通过 WebSocket 直连内置浏览器的调试端口。

内置浏览器（QtWebEngine）会暴露一个标准 Chrome DevTools 调试端口，
这里只用其中稳定的几个域：Runtime、Input、Page、Target。
"""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.request
from typing import Any, Dict, Optional

import websockets

KEY_SPECS: Dict[str, Dict[str, Any]] = {
    "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "text": "\r"},
    "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9},
    "Escape": {"key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27},
    "Backspace": {"key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8},
    "Delete": {"key": "Delete", "code": "Delete", "windowsVirtualKeyCode": 46, "nativeVirtualKeyCode": 46},
    "ArrowDown": {"key": "ArrowDown", "code": "ArrowDown", "windowsVirtualKeyCode": 40, "nativeVirtualKeyCode": 40},
    "ArrowUp": {"key": "ArrowUp", "code": "ArrowUp", "windowsVirtualKeyCode": 38, "nativeVirtualKeyCode": 38},
}


class CdpError(RuntimeError):
    """调试协议调用失败。"""


class CdpClient:
    """串行化使用一条 WebSocket 连接，所有调用都在同一事件循环里。"""

    def __init__(self, port: int, logger=None, host: str = "127.0.0.1"):
        self._port = int(port)
        self._host = host
        self._logger = logger
        self._ws = None
        self._session_id: Optional[str] = None
        self._counter = 0
        self._lock = asyncio.Lock()

    @property
    def endpoint(self) -> str:
        return f"http://{self._host}:{self._port}"

    @property
    def connected(self) -> bool:
        return self._ws is not None and bool(self._session_id)

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger.info(message)

    # ---------- 连接管理 ----------
    async def connect(self, timeout: float = 40.0) -> None:
        """连接调试端口并接管页面目标，失败会在超时时间内持续重试。"""
        async with self._lock:
            if self.connected:
                return
            await self._connect_locked(timeout)

    async def _connect_locked(self, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last_error: Optional[Exception] = None
        while loop.time() < deadline:
            try:
                version = await asyncio.to_thread(self._fetch_version)
                ws_url = str(version.get("webSocketDebuggerUrl") or "")
                if not ws_url:
                    raise CdpError("调试端口没有返回 WebSocket 地址")
                self._ws = await websockets.connect(ws_url, max_size=None, open_timeout=15)
                self._session_id = await self._attach_page()
                self._log("已接管内置浏览器页面")
                return
            except Exception as exc:
                last_error = exc
                await self._drop_connection()
                await asyncio.sleep(0.8)
        raise CdpError(f"连接内置浏览器调试端口失败：{last_error}")

    def _fetch_version(self) -> Dict[str, Any]:
        with urllib.request.urlopen(f"{self.endpoint}/json/version", timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    async def _attach_page(self) -> str:
        targets = await self._send_raw("Target.getTargets")
        page_targets = [item for item in targets.get("targetInfos", []) if item.get("type") == "page"]
        if not page_targets:
            raise CdpError("内置浏览器还没有加载出可用页面")
        attached = await self._send_raw(
            "Target.attachToTarget",
            {"targetId": page_targets[0]["targetId"], "flatten": True},
        )
        session_id = str(attached.get("sessionId") or "")
        if not session_id:
            raise CdpError("接管页面目标失败")
        return session_id

    async def _drop_connection(self) -> None:
        ws = self._ws
        self._ws = None
        self._session_id = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def close(self) -> None:
        async with self._lock:
            await self._drop_connection()

    # ---------- 协议收发 ----------
    async def _send_raw(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        session: Optional[str] = None,
        timeout: float = 25.0,
    ) -> Dict[str, Any]:
        if self._ws is None:
            raise CdpError("尚未连接内置浏览器")
        self._counter += 1
        message_id = self._counter
        payload: Dict[str, Any] = {"id": message_id, "method": method, "params": params or {}}
        if session:
            payload["sessionId"] = session
        await self._ws.send(json.dumps(payload, ensure_ascii=False))
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            data = json.loads(raw)
            if data.get("id") != message_id:
                continue
            if "error" in data:
                error = data.get("error") or {}
                raise CdpError(f"{method} 调用失败：{error.get('message')}")
            result = data.get("result")
            return result if isinstance(result, dict) else {}

    async def send(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 25.0) -> Dict[str, Any]:
        """对外统一入口：自动带上页面会话，失败后标记断线以便重连。"""
        async with self._lock:
            if not self.connected:
                await self._connect_locked(20.0)
            try:
                return await self._send_raw(method, params, self._session_id, timeout)
            except CdpError:
                raise
            except Exception as exc:
                await self._drop_connection()
                raise CdpError(f"{method} 调用异常：{exc}") from exc

    # ---------- 常用能力 ----------
    async def evaluate(self, expression: str, timeout: float = 25.0) -> Any:
        result = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            raise CdpError(f"页面脚本执行异常：{detail.get('text') or detail}")
        return (result.get("result") or {}).get("value")

    async def insert_text(self, text: str) -> None:
        await self.send("Input.insertText", {"text": str(text)})

    async def press_key(self, key: str, modifiers: int = 0) -> None:
        spec = KEY_SPECS.get(key)
        if spec is None:
            spec = {"key": key, "code": key, "windowsVirtualKeyCode": 0, "nativeVirtualKeyCode": 0}
        for phase in ("keyDown", "keyUp"):
            params: Dict[str, Any] = {"type": phase, "modifiers": int(modifiers)}
            params.update(spec)
            if phase == "keyUp":
                params.pop("text", None)
            await self.send("Input.dispatchKeyEvent", params)

    async def click_point(self, x: float, y: float) -> None:
        for phase, buttons in (("mousePressed", 1), ("mouseReleased", 0)):
            await self.send(
                "Input.dispatchMouseEvent",
                {
                    "type": phase,
                    "x": float(x),
                    "y": float(y),
                    "button": "left",
                    "buttons": buttons,
                    "clickCount": 1,
                },
            )

    async def screenshot(self, timeout: float = 30.0, beyond_viewport: bool = False) -> bytes:
        result = await self.send(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": bool(beyond_viewport)},
            timeout=timeout,
        )
        return base64.b64decode(result.get("data") or "")

    async def navigate(self, url: str) -> None:
        await self.send("Page.navigate", {"url": str(url)}, timeout=45.0)
