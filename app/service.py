"""应用装配：异步工作线程 + 内置浏览器桥接 + 每日定时 + 本地控制接口。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import secrets
import threading
from datetime import datetime
from typing import Any, Coroutine, Dict, Optional

from .browser import BrowserBridge
from .config import load_config, save_config
from .control_api import ControlServer
from .logger import BEIJING, setup_logger
from .paths import LOG_DIR, RUNTIME_PATH, ensure_dirs
from .scheduler import DailyScheduler
from .selectors import load_selectors
from .sender import (
    LoginRequiredError,
    SendError,
    click_selector,
    probe_page,
    query_elements,
    send_message,
)
from .state import SendStore


class _AsyncWorker(threading.Thread):
    """独占一个事件循环的后台线程，所有浏览器调用都在这里执行。"""

    def __init__(self):
        super().__init__(name="async-worker", daemon=True)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._ready = threading.Event()

    def run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def wait_ready(self, timeout: float = 10.0) -> None:
        self._ready.wait(timeout)

    def submit(self, coroutine: Coroutine) -> concurrent.futures.Future:
        if self.loop is None:
            raise RuntimeError("后台工作线程尚未启动")
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)


class Application:
    """把内置浏览器、定时器与控制接口组装成一个可长期运行的服务。"""

    def __init__(self, debug_port: int) -> None:
        ensure_dirs()
        self.logger = setup_logger(LOG_DIR)
        self.store = SendStore()
        self.config: Dict[str, Any] = load_config()
        self.selectors = load_selectors()
        self.browser = BrowserBridge(debug_port, logger=self.logger)
        self.scheduler = DailyScheduler(self.config_snapshot, self._scheduled_run, self.store, self.logger)
        self.worker = _AsyncWorker()
        self.control: Optional[ControlServer] = None
        self.token = secrets.token_urlsafe(24)
        self._send_lock: Optional[asyncio.Lock] = None
        self._boot: Optional[concurrent.futures.Future] = None
        self._last_result: Dict[str, Any] = {}
        self._stopped = False

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self.worker.start()
        self.worker.wait_ready()
        self._boot = self.worker.submit(self._bootstrap())
        self.control = ControlServer(self, self.token, self.logger, port=int(self.config["control_api_port"]))
        self.control.start()
        self._write_runtime()

    async def _bootstrap(self) -> None:
        self._send_lock = asyncio.Lock()
        try:
            await self.browser.start()
        except Exception as exc:
            self.logger.error(f"接管内置浏览器失败：{exc}")
        self.scheduler.start()
        self.logger.info(f"服务已就绪，设定的每日发送时间为 {self.config['send_time']}")

    def wait_boot(self, timeout: float = 90.0) -> None:
        if self._boot is not None:
            self._boot.result(timeout=timeout)

    def submit(self, coroutine: Coroutine) -> concurrent.futures.Future:
        if self.worker.loop is None or not self.worker.loop.is_running():
            raise RuntimeError("后台工作线程已停止")
        return self.worker.submit(coroutine)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        loop = self.worker.loop
        if loop is None or not loop.is_running():
            return
        try:
            self.worker.submit(self._shutdown()).result(timeout=30)
        except Exception:
            pass
        if self.control is not None:
            self.control.stop()
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        self.logger.info("程序已退出")

    async def _shutdown(self) -> None:
        await self.scheduler.stop()
        await self.browser.stop()

    # ---------- 配置 ----------
    def config_snapshot(self) -> Dict[str, Any]:
        return dict(self.config)

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        self.config = save_config(dict(patch or {}))
        self.selectors = load_selectors()
        self.logger.info("配置已保存")
        return self.config_snapshot()

    # ---------- 页面与状态 ----------
    def start_url(self) -> str:
        return str(self.config.get("start_url") or "").strip() or self.selectors["chat_urls"][0]

    async def open_start_page(self) -> None:
        try:
            await self.browser.goto(self.start_url())
        except Exception as exc:
            self.logger.warning(f"打开起始页面失败：{exc}")

    async def open_browser(self) -> Dict[str, Any]:
        await self.browser.start()
        await self.open_start_page()
        return await self.browser.status()

    async def _guess_logged_in(self) -> Optional[bool]:
        try:
            probe = await probe_page(self.browser, self.selectors)
        except Exception:
            return None
        if probe.get("hasChatApp"):
            return True
        if probe.get("loginVisible"):
            return False
        return None

    async def status(self) -> Dict[str, Any]:
        browser_status = await self.browser.status()
        today = self.store.today_key()
        next_run = self.scheduler.next_run_at()
        return {
            "browser": browser_status,
            "logged_in": await self._guess_logged_in(),
            "config": self.config_snapshot(),
            "today": {
                "date": today,
                "sent": self.store.has_success(today),
                "record": self.store.get(today) or {},
            },
            "next_run_at": next_run.isoformat(timespec="seconds") if next_run else "",
            "last_result": dict(self._last_result),
        }

    # ---------- 发送 ----------
    async def _ensure_chat_page(self) -> None:
        # 页面可能还在加载，先给几次机会，避免把已经打开的私信页重新导航掉。
        for _ in range(3):
            try:
                probe = await probe_page(self.browser, self.selectors)
                if probe.get("hasChatApp"):
                    return
            except Exception:
                pass
            await asyncio.sleep(1)
        await self.browser.goto(self.start_url())
        await asyncio.sleep(2)

    async def run_send_now(self, target: str = "", message: str = "", reason: str = "手动") -> Dict[str, Any]:
        if self._send_lock is None:
            self._send_lock = asyncio.Lock()
        async with self._send_lock:
            target_name = (target or self.config.get("target_name") or "").strip()
            content = (message or self.config.get("message") or "").strip()
            timeout_seconds = int(self.config.get("send_timeout_seconds") or 120)
            self.logger.info(f"开始发送（{reason}）：目标「{target_name or '未设置'}」")
            try:
                await self._ensure_chat_page()
                result = await asyncio.wait_for(
                    send_message(
                        self.browser,
                        target_name,
                        content,
                        self.selectors,
                        self.logger,
                        match_mode=str(self.config.get("match_mode") or "equals"),
                        timeout_seconds=timeout_seconds,
                    ),
                    timeout=timeout_seconds,
                )
            except LoginRequiredError as exc:
                payload = self._failure("login_required", str(exc), target_name, content, reason)
            except SendError as exc:
                payload = self._failure("send_failed", str(exc), target_name, content, reason)
            except asyncio.TimeoutError:
                payload = self._failure("timeout", f"发送超时（{timeout_seconds} 秒）", target_name, content, reason)
            except Exception as exc:
                payload = self._failure(type(exc).__name__, str(exc), target_name, content, reason)
            else:
                detail = str(result.get("detail") or "")
                self.store.record("success", target_name, content, detail, reason)
                payload = {"ok": True, "target": target_name, "message": content, "detail": detail, "reason": reason}
                self.logger.info(f"发送成功：{detail}")

            self._last_result = payload
            return payload

    def _failure(self, code: str, detail: str, target: str, message: str, reason: str) -> Dict[str, Any]:
        self.store.record("failed", target, message, f"[{code}] {detail}", reason)
        self.logger.error(f"发送失败（{code}）：{detail}")
        return {"ok": False, "code": code, "detail": detail, "target": target, "message": message, "reason": reason}

    async def _scheduled_run(self, reason: str) -> Dict[str, Any]:
        return await self.run_send_now(reason=reason)

    # ---------- 控制接口用到的页面操作 ----------
    async def evaluate_script(self, script: str) -> Any:
        return await self.browser.evaluate(script)

    async def query_selector(self, selector: str, limit: int = 20) -> Any:
        return await query_elements(self.browser, selector, limit)

    async def click(self, selector: str) -> Dict[str, Any]:
        clicked = await click_selector(self.browser, [selector])
        if not clicked:
            raise ValueError(f"没有找到可点击的元素：{selector}")
        return {"selector": selector, "clicked": True}

    async def type_text(self, selector: str, text: str, submit: bool = False) -> Dict[str, Any]:
        await click_selector(self.browser, [selector], fallback_js=False)
        await self.browser.insert_text(str(text))
        if submit:
            await self.browser.press_key("Enter")
        return {"selector": selector, "length": len(str(text)), "submitted": bool(submit)}

    async def press(self, key: str) -> Dict[str, Any]:
        await self.browser.press_key(key)
        return {"key": key}

    async def take_screenshot(self, full_page: bool = False) -> Dict[str, Any]:
        stamp = datetime.now(BEIJING).strftime("%Y%m%d-%H%M%S")
        path = LOG_DIR / f"截图-{stamp}.png"
        await self.browser.screenshot(path, full_page=full_page)
        return {"path": str(path)}

    async def page_html(self, limit: int = 0) -> str:
        html = await self.browser.evaluate("document.documentElement.outerHTML")
        text = str(html or "")
        if limit and int(limit) > 0:
            return text[: int(limit)]
        return text

    async def pages_info(self) -> Any:
        status = await self.browser.status()
        return [{"index": 0, "url": status.get("url", ""), "title": status.get("title", "")}]

    # ---------- 运行信息 ----------
    def _write_runtime(self) -> None:
        payload = {
            "pid": os.getpid(),
            "control_api": self.control.url if self.control else "",
            "token": self.token,
            "debug_port": self.browser.port,
            "started_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        }
        RUNTIME_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
