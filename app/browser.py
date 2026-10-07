"""内置浏览器桥接层：把调试协议客户端包装成业务直接可用的接口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .cdp import CdpClient


class BrowserBridge:
    """业务层只跟这个对象打交道，不直接碰 Qt 控件，保证线程安全。"""

    def __init__(
        self,
        debug_port: int,
        logger=None,
        *,
        target_index: int = 0,
        target_url_contains: str = "",
    ):
        self._port = int(debug_port)
        self._logger = logger
        self._cdp = CdpClient(
            self._port,
            logger,
            target_index=target_index,
            target_url_contains=target_url_contains,
        )

    @property
    def port(self) -> int:
        return self._port

    @property
    def cdp(self) -> CdpClient:
        return self._cdp

    async def start(self) -> None:
        await self._cdp.connect()

    async def stop(self) -> None:
        await self._cdp.close()

    async def goto(self, url: str) -> None:
        await self._cdp.navigate(url)
        if self._logger is not None:
            self._logger.info(f"已在内置浏览器中打开：{url}")

    async def evaluate(self, expression: str) -> Any:
        return await self._cdp.evaluate(expression)

    async def insert_text(self, text: str) -> None:
        await self._cdp.insert_text(text)

    async def press_key(self, key: str, modifiers: int = 0) -> None:
        await self._cdp.press_key(key, modifiers)

    async def click_point(self, x: float, y: float) -> None:
        await self._cdp.click_point(x, y)

    async def screenshot(self, path: Path, full_page: bool = False) -> Path:
        data = await self._cdp.screenshot(beyond_viewport=bool(full_page))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    async def status(self) -> Dict[str, Any]:
        try:
            info = await self._cdp.evaluate(
                "({ url: location.href, title: document.title, ready: document.readyState })"
            )
        except Exception:
            return {"started": False, "url": "", "title": "", "page_count": 0}
        if not isinstance(info, dict):
            return {"started": False, "url": "", "title": "", "page_count": 0}
        return {
            "started": True,
            "url": str(info.get("url") or ""),
            "title": str(info.get("title") or ""),
            "page_count": 1,
        }
