"""应用装配：异步工作线程 + 内置浏览器桥接 + 每日发送 + 自动续灯牌 + 本地控制接口。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import secrets
import threading
from datetime import datetime
from typing import Any, Callable, Coroutine, Dict, List, Optional
from urllib.parse import urlparse

from .badge_renewal import (
    BadgeRenewalMonitor,
    BadgeRenewalStore,
    normalize_live_urls,
    renew_badge_in_live_room,
    test_badge_gift_in_live_room,
)
from .browser import BrowserBridge
from .config import load_config, save_config
from .control_api import ControlServer
from .friends import (
    USER_DETAIL_URL_MARK,
    cache_avatar,
    collect_friends,
    extract_user_details,
    merge_friends,
    read_cache,
    read_visible_friends,
    write_cache,
)
from .logger import BEIJING, setup_logger
from .live_status import LiveStatusClient
from .message import normalize_mode, pick_message
from .network import NetworkCapture
from .paths import LOG_DIR, RUNTIME_PATH, ensure_dirs
from .scheduler import DailyScheduler
from .selectors import load_selectors
from .sender import (
    LoginRequiredError,
    SendError,
    click_friends_tab,
    click_selector,
    probe_page,
    query_elements,
    send_message,
    wait_for_chat_ready,
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

    def __init__(self, debug_port: int, *, write_runtime: bool = True) -> None:
        ensure_dirs()
        self._write_runtime_enabled = bool(write_runtime)
        self.logger = setup_logger(LOG_DIR)
        self.store = SendStore()
        self.badge_store = BadgeRenewalStore()
        self.config: Dict[str, Any] = load_config()
        self.selectors = load_selectors()
        start_url = str(self.config.get("start_url") or "").strip()
        if not start_url:
            start_url = str((self.selectors.get("chat_urls") or [""])[0] or "")
        chat_host = urlparse(start_url).netloc.lower() or "creator.douyin.com"
        self.browser = BrowserBridge(
            debug_port,
            logger=self.logger,
            target_index=0,
            target_url_contains=chat_host,
            target_match_required=True,
        )
        self._debug_port = int(debug_port)
        self.live_browser: Optional[BrowserBridge] = None
        self.network = NetworkCapture(self.browser.cdp, logger=self.logger)
        self.scheduler = DailyScheduler(self.config_snapshot, self._scheduled_run, self.store, self.logger)
        self.badge_monitor = BadgeRenewalMonitor(
            self.config_snapshot,
            self._scheduled_badge_run,
            self.logger,
        )
        self.worker = _AsyncWorker()
        self.control: Optional[ControlServer] = None
        self.token = secrets.token_urlsafe(24)
        self._send_lock: Optional[asyncio.Lock] = None
        self._badge_lock: Optional[asyncio.Lock] = None
        self._browser_lock: Optional[asyncio.Lock] = None
        self._chat_browser_lock: Optional[asyncio.Lock] = None
        self._live_lock: Optional[asyncio.Lock] = None
        self._boot: Optional[concurrent.futures.Future] = None
        self._last_result: Dict[str, Any] = {}
        self._last_badge_result: Dict[str, Any] = {}
        self._stopped = False
        self._chat_browser_available = False
        self._chat_browser_loader: Optional[Callable[[], None]] = None
        self._live_browser_available = False
        self._live_browser_loader: Optional[Callable[[], None]] = None
        self.live_status = LiveStatusClient(self.logger)

    @property
    def chat_browser_lazy(self) -> bool:
        return bool(self.config.get("chat_browser_lazy"))

    @property
    def chat_browser_available(self) -> bool:
        return self._chat_browser_available

    @property
    def live_browser_enabled(self) -> bool:
        return bool(self.config.get("live_browser_enabled"))

    def set_chat_browser_loader(self, loader: Callable[[], None]) -> None:
        self._chat_browser_loader = loader

    def mark_chat_browser_loaded(self) -> None:
        self._chat_browser_available = True

    def set_live_browser_loader(self, loader: Callable[[], None]) -> None:
        self._live_browser_loader = loader

    def mark_live_browser_loaded(self) -> None:
        self._live_browser_available = True

    def _create_live_browser_bridge(self) -> BrowserBridge:
        if self.live_browser is None:
            self.live_browser = BrowserBridge(
                self._debug_port,
                logger=self.logger,
                target_index=1,
                target_url_contains="live.douyin.com",
                target_match_required=True,
            )
        return self.live_browser

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self.worker.start()
        self.worker.wait_ready()
        self._boot = self.worker.submit(self._bootstrap())
        self.control = ControlServer(self, self.token, self.logger, port=int(self.config["control_api_port"]))
        self.control.start()
        if self._write_runtime_enabled:
            self._write_runtime()

    async def _bootstrap(self) -> None:
        self._send_lock = asyncio.Lock()
        self._badge_lock = asyncio.Lock()
        self._browser_lock = asyncio.Lock()
        self._chat_browser_lock = asyncio.Lock()
        self._live_lock = asyncio.Lock()
        try:
            if self._chat_browser_available:
                await self._start_chat_browser()
        except Exception as exc:
            self.logger.error(f"接管内置浏览器失败：{exc}")
        self.scheduler.start()
        self.logger.info(f"服务已就绪，设定的每日发送时间为 {self.config['send_time']}")
        self.badge_monitor.start()
        if self.config.get("badge_renewal_enabled"):
            self.logger.info(
                f"自动续灯牌已开启：每 {self.config['badge_check_interval_minutes']} 分钟检测，"
                f"开播后挂机 {self.config['badge_watch_minutes']} 分钟"
            )

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
        await self.badge_monitor.stop()
        await self.scheduler.stop()
        await self.browser.stop()
        await self.disable_live_browser()

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

    async def enable_chat_browser(self) -> Dict[str, Any]:
        """连接已经创建好的私信浏览器页面。"""
        self._chat_browser_available = True
        try:
            await self._start_chat_browser()
            status = await self.browser.status()
            status["loaded"] = True
            return status
        except Exception as exc:
            self.logger.warning(f"私信浏览器暂未就绪：{exc}")
            return {
                "loaded": True,
                "started": False,
                "url": "",
                "title": "",
                "error": str(exc),
            }

    async def _require_chat_browser(self) -> BrowserBridge:
        if not self._chat_browser_available:
            loader = self._chat_browser_loader
            if loader is None:
                raise RuntimeError("私信浏览器尚未加载，请先点「打开私信页」")
            await asyncio.to_thread(loader)
        if not self._chat_browser_available:
            raise RuntimeError("私信浏览器尚未加载，请先点「打开私信页」")
        await self._start_chat_browser()
        return self.browser

    async def _start_chat_browser(self) -> None:
        if self._chat_browser_lock is None:
            self._chat_browser_lock = asyncio.Lock()
        async with self._chat_browser_lock:
            await self.browser.start()
            await self.network.start()

    async def open_start_page(self) -> None:
        try:
            await self.browser.goto(self.start_url())
        except Exception as exc:
            self.logger.warning(f"打开起始页面失败：{exc}")

    async def open_browser(self) -> Dict[str, Any]:
        await self._require_chat_browser()
        await self.open_start_page()
        return await self.browser.status()

    async def goto_chat_url(self, url: str) -> Dict[str, Any]:
        await self._require_chat_browser()
        await self.browser.goto(url)
        return {"url": str(url)}

    async def enable_live_browser(self) -> Dict[str, Any]:
        """连接已经创建好的直播浏览器页面。页面必须由界面线程提前加载。"""
        if not self.live_browser_enabled:
            return {
                "enabled": False,
                "started": False,
                "url": "",
                "title": "",
            }
        self._live_browser_available = True
        try:
            bridge = self._create_live_browser_bridge()
            await bridge.start()
            status = await bridge.status()
            status["enabled"] = True
            return status
        except Exception as exc:
            self.logger.warning(f"直播浏览器暂未就绪：{exc}")
            return {
                "enabled": True,
                "started": False,
                "url": "",
                "title": "",
                "error": str(exc),
            }

    async def disable_live_browser(self) -> Dict[str, Any]:
        """关闭直播浏览器的 CDP 连接，页面控件由界面线程负责释放。"""
        bridge = self.live_browser
        self.live_browser = None
        self._live_browser_available = False
        if bridge is not None:
            await bridge.stop()
        return {"enabled": False, "started": False}

    async def _require_live_browser(self) -> BrowserBridge:
        if not self.live_browser_enabled:
            raise RuntimeError("直播浏览器未启用，请先勾选“启用直播浏览器”并保存")
        if not self._live_browser_available:
            loader = self._live_browser_loader
            if loader is None:
                raise RuntimeError("直播浏览器加载器不可用")
            await asyncio.to_thread(loader)
        if not self._live_browser_available:
            raise RuntimeError("直播浏览器尚未加载")
        bridge = self._create_live_browser_bridge()
        await bridge.start()
        return bridge

    async def _guess_logged_in(self) -> Optional[bool]:
        if not self._chat_browser_available:
            return None
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
        if self._chat_browser_available:
            browser_status = await self.browser.status()
            browser_status["loaded"] = True
        else:
            browser_status = {
                "loaded": False,
                "started": False,
                "url": "",
                "title": "",
                "page_count": 0,
            }
        if (
            self.live_browser_enabled
            and self._live_browser_available
            and self.live_browser is not None
        ):
            live_browser_status = await self.live_browser.status()
            live_browser_status["enabled"] = True
            live_browser_status["loaded"] = True
        else:
            live_browser_status = {
                "enabled": bool(self.live_browser_enabled),
                "loaded": False,
                "started": False,
                "url": "",
                "title": "",
                "page_count": 0,
            }
        today = self.store.today_key()
        next_run = self.scheduler.next_run_at()
        targets = self._targets()
        success = [name for name in targets if self.store.is_success(today, name)]
        return {
            "browser": browser_status,
            "live_browser": live_browser_status,
            "logged_in": await self._guess_logged_in(),
            "config": self.config_snapshot(),
            "today": {
                "date": today,
                "total": len(targets),
                "sentCount": len(success),
                "sent": bool(targets) and len(success) == len(targets),
                "records": self.store.day_entries(today),
            },
            "next_run_at": next_run.isoformat(timespec="seconds") if next_run else "",
            "last_result": dict(self._last_result),
            "badge": self.badge_status_snapshot(),
        }

    def badge_status_snapshot(self) -> Dict[str, Any]:
        config = self.config_snapshot()
        live_urls = list(config.get("badge_live_urls") or [])
        interval = int(config.get("badge_check_interval_minutes") or 10)
        next_check = self.badge_store.next_check_at(live_urls, interval)
        return {
            "enabled": bool(config.get("badge_renewal_enabled")),
            "live_browser_enabled": bool(config.get("live_browser_enabled")),
            "urls": live_urls,
            "check_interval_minutes": interval,
            "watch_minutes": int(config.get("badge_watch_minutes") or 20),
            "running": self.badge_monitor.running,
            "next_check_at": next_check.isoformat(timespec="seconds") if next_check else "",
            "states": {url: self.badge_store.get(url) for url in live_urls},
            "latest": {url: self.badge_store.latest(url) for url in live_urls},
            "last_result": dict(self._last_badge_result),
        }

    # ---------- 发送 ----------
    def _targets(self) -> List[str]:
        result: List[str] = []
        for item in self.config.get("target_names") or []:
            name = str(item or "").strip()
            if name and name not in result:
                result.append(name)
        return result

    async def scan_friends(self, cache: bool = True, reload: bool = True) -> List[Dict[str, Any]]:
        """读取完整好友列表：页面滚动定位 + 接口数据校准昵称与头像。"""
        await self._require_chat_browser()
        # 先重新加载私信页，清掉前端缓存，保证每位好友的详情接口都会重新请求一次。
        self.network.clear()
        if reload:
            try:
                await self.browser.goto(self.start_url())
            except Exception as exc:
                self.logger.warning(f"重新加载私信页失败：{exc}")
            await asyncio.sleep(2)
        await wait_for_chat_ready(self.browser)
        await click_friends_tab(self.browser, self.selectors, self.logger)
        dom_friends = await collect_friends(self.browser, self.selectors, self.logger)
        details = extract_user_details(self.network.payloads_for(USER_DETAIL_URL_MARK))
        if details:
            friends = merge_friends(dom_friends, details)
            self.logger.info(
                f"已结合接口数据校准好友列表：页面 {len(dom_friends)} 条，接口 {len(details)} 位，最终 {len(friends)} 位"
            )
            if len(details) * 2 < len(dom_friends):
                self.logger.warning("接口数据少于页面条目，列表可能不完整，可再点一次「读取好友列表」")
        else:
            friends = dom_friends
            self.logger.warning("这次没有抓到好友详情接口，已按页面文本识别，可能有重复项")
        if cache:
            for item in friends:
                item["avatarPath"] = cache_avatar(str(item.get("avatar") or ""), str(item.get("name") or ""))
            write_cache(friends)
        return friends

    async def check_friends_fresh(self, sample_size: int = 3) -> Dict[str, Any]:
        """只校验缓存名单最前面几位是否还在当前列表里，不做整表滚动。"""
        await self._require_chat_browser()
        cached = read_cache()
        if not cached:
            return {"fresh": False, "reason": "没有缓存名单", "friends": []}
        sample = [str(item.get("name") or "") for item in cached[: max(1, int(sample_size))]]
        visible = [
            str(item.get("name") or "")
            for item in await read_visible_friends(self.browser, self.selectors)
        ]
        hit = sum(1 for name in sample if name in visible)
        need = 1 if len(sample) == 1 else 2
        return {
            "fresh": hit >= min(need, len(sample)),
            "sample": sample,
            "hit": hit,
            "visible": len(visible),
            "friends": cached,
        }

    async def ensure_friends_ready(self, force: bool = False) -> Dict[str, Any]:
        """启动时优先复用缓存名单，只有快速校验不通过才整表重读。"""
        await self._require_chat_browser()
        if force:
            return {"mode": "full", "friends": await self.scan_friends()}

        cached = read_cache()
        try:
            await wait_for_chat_ready(self.browser, timeout_seconds=12)
        except Exception as exc:
            if cached:
                self.logger.info(f"私信页尚未就绪（{exc}），先沿用上次的好友名单")
                return {"mode": "cache", "friends": cached, "note": "页面未就绪"}
            return {"mode": "empty", "friends": []}

        for attempt in range(3):
            check = await self.check_friends_fresh()
            if check.get("fresh"):
                self.logger.info(
                    f"好友名单快速校验通过（比对 {check.get('hit')}/{len(check.get('sample') or [])} 位），直接沿用上次名单"
                )
                return {"mode": "cache", "friends": check.get("friends") or []}
            if not check.get("friends"):
                break
            if attempt < 2:
                await asyncio.sleep(1.5)

        self.logger.info("好友名单快速校验未通过，重新读取完整好友列表")
        return {"mode": "full", "friends": await self.scan_friends()}

    # ---------- 接口抓包（联调排查用） ----------
    def capture_summary(self, keyword: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        return self.network.summary(keyword, limit)

    def capture_body(self, index: int, limit: int = 4000) -> Optional[str]:
        return self.network.body(index, limit)

    def capture_clear(self) -> Dict[str, Any]:
        self.network.clear()
        return {"cleared": True}

    async def capture_reload(self) -> Dict[str, Any]:
        await self._require_chat_browser()
        page_url = await self.browser.evaluate("location.href")
        await self.browser.goto(str(page_url))
        return {"url": page_url}

    async def _ensure_chat_page(self) -> None:
        await self._require_chat_browser()
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

    async def run_send_now(
        self,
        targets: Optional[List[str]] = None,
        message: str = "",
        reason: str = "手动",
        force: bool = True,
    ) -> Dict[str, Any]:
        if self._send_lock is None:
            self._send_lock = asyncio.Lock()
        if self._browser_lock is None:
            self._browser_lock = asyncio.Lock()
        async with self._send_lock:
            async with self._browser_lock:
                return await self._run_send_locked(targets, message, reason, force)

    async def _run_send_locked(
        self,
        targets: Optional[List[str]],
        message: str,
        reason: str,
        force: bool,
    ) -> Dict[str, Any]:
        names = self._resolve_targets(targets)
        content = (message or self.config.get("message") or "").strip()
        message_mode = normalize_mode(self.config.get("message_mode"))
        timeout_seconds = int(self.config.get("send_timeout_seconds") or 120)
        if not names:
            detail = "没有可发送的好友，请先读取好友列表并勾选"
            self.logger.error(f"发送失败（no_target）：{detail}")
            payload = {"ok": False, "code": "no_target", "detail": detail, "results": []}
            self._last_result = payload
            return payload

        mode_label = "随机抽一行" if message_mode == "random_line" else "整条发送"
        self.logger.info(f"开始发送（{reason}）：共 {len(names)} 位好友，发送方式：{mode_label}")
        await self._ensure_chat_page()

        results: List[Dict[str, Any]] = []
        stopped_early = False
        for index, name in enumerate(names, start=1):
            today = self.store.today_key()
            if not force and self.store.is_success(today, name):
                continue
            sent_message = pick_message(content, message_mode, self.store.last_sent_message(name))
            if not sent_message:
                results.append(self._failure("empty_message", "消息内容为空", name, content, reason))
                continue
            self.logger.info(
                f"[{index}/{len(names)}] 正在给「{name}」发送消息：{sent_message[:30]}"
            )
            try:
                result = await asyncio.wait_for(
                    send_message(
                        self.browser,
                        name,
                        sent_message,
                        self.selectors,
                        self.logger,
                        match_mode=str(self.config.get("match_mode") or "equals"),
                        timeout_seconds=timeout_seconds,
                    ),
                    timeout=timeout_seconds,
                )
            except LoginRequiredError as exc:
                results.append(self._failure("login_required", str(exc), name, sent_message, reason))
                stopped_early = True
                break
            except SendError as exc:
                results.append(self._failure("send_failed", str(exc), name, sent_message, reason))
            except asyncio.TimeoutError:
                results.append(
                    self._failure("timeout", f"发送超时（{timeout_seconds} 秒）", name, sent_message, reason)
                )
            except Exception as exc:
                results.append(self._failure(type(exc).__name__, str(exc), name, sent_message, reason))
            else:
                detail = str(result.get("detail") or "")
                self.store.record("success", name, sent_message, detail, reason)
                results.append({"ok": True, "target": name, "detail": detail, "message": sent_message})
                self.logger.info(f"「{name}」发送成功：{detail}")
            await asyncio.sleep(1.0)

        success_count = sum(1 for item in results if item.get("ok"))
        failed_names = [str(item.get("target")) for item in results if not item.get("ok")]
        detail = f"成功 {success_count} 位，失败 {len(failed_names)} 位"
        if stopped_early:
            detail += "；登录状态失效，已提前终止"
        if failed_names:
            detail += f"（失败：{'、'.join(failed_names[:5])}）"
        payload = {
            "ok": bool(results) and not failed_names,
            "detail": detail,
            "results": results,
            "successCount": success_count,
            "failed": failed_names,
            "reason": reason,
        }
        self._last_result = payload
        return payload

    def _resolve_targets(self, targets: Optional[List[str]]) -> List[str]:
        if targets is None:
            return self._targets()
        if isinstance(targets, str):
            targets = [targets]
        result: List[str] = []
        for item in targets:
            name = str(item or "").strip()
            if name and name not in result:
                result.append(name)
        return result

    def _failure(self, code: str, detail: str, target: str, message: str, reason: str) -> Dict[str, Any]:
        self.store.record("failed", target, message, f"[{code}] {detail}", reason)
        self.logger.error(f"发送失败（{code}）：{detail}")
        return {"ok": False, "code": code, "detail": detail, "target": target, "message": message, "reason": reason}

    async def _scheduled_run(self, force: bool = False, reason: str = "定时任务") -> Dict[str, Any]:
        return await self.run_send_now(reason=reason, force=bool(force))

    # ---------- 自动续灯牌 ----------
    async def _scheduled_badge_run(self) -> Dict[str, Any]:
        return await self.run_badge_renewal(force=False, reason="定时监控")

    async def run_badge_renewal(
        self,
        urls: Optional[List[str]] = None,
        *,
        force: bool = False,
        reason: str = "手动",
    ) -> Dict[str, Any]:
        if self._badge_lock is None:
            self._badge_lock = asyncio.Lock()
        if self._live_lock is None:
            self._live_lock = asyncio.Lock()

        async with self._badge_lock:
            config = self.config_snapshot()
            if urls is None:
                if not config.get("badge_renewal_enabled"):
                    return {
                        "ok": False,
                        "detail": "自动续灯牌未开启",
                        "results": [],
                    }
                raw_urls = list(config.get("badge_live_urls") or [])
            else:
                raw_urls = list(urls)
            try:
                live_urls = normalize_live_urls(raw_urls)
            except ValueError as exc:
                return {"ok": False, "detail": str(exc), "results": []}
            if not live_urls:
                return {"ok": False, "detail": "没有配置需要监控的直播间地址", "results": []}

            interval = int(config.get("badge_check_interval_minutes") or 10)
            watch_minutes = int(config.get("badge_watch_minutes") or 20)
            results: List[Dict[str, Any]] = []
            for live_url in live_urls:
                should_check, skip_reason = self.badge_store.should_check(
                    live_url,
                    interval,
                    force=force,
                )
                if not should_check:
                    results.append(
                        {
                            "ok": True,
                            "status": "skipped",
                            "liveUrl": live_url,
                            "detail": skip_reason,
                        }
                    )
                    continue

                room_id = urlparse(live_url).path.strip("/")
                self.logger.info(f"通过直播接口检测是否开播（{reason}）：{live_url}")
                status_result = await self.live_status.check(room_id)
                if status_result.get("status") != "live":
                    result = {
                        "status": (
                            "not_live"
                            if status_result.get("status") == "not_live"
                            else "failed"
                        ),
                        "detail": str(status_result.get("detail") or ""),
                        "liveTitle": str(status_result.get("title") or ""),
                        "watchedSeconds": 0,
                    }
                else:
                    try:
                        live_bridge = await self._require_live_browser()
                    except Exception as exc:
                        result = {
                            "status": "failed",
                            "detail": f"{type(exc).__name__}：{exc}",
                            "liveTitle": str(status_result.get("title") or ""),
                            "watchedSeconds": 0,
                        }
                    else:
                        self.logger.info(f"接口确认已开播，开始续灯牌：{live_url}")
                        async with self._live_lock:
                            try:
                                result = await renew_badge_in_live_room(
                                    live_bridge,
                                    live_url,
                                    watch_minutes,
                                    self.logger,
                                )
                            except Exception as exc:
                                result = {
                                    "status": "failed",
                                    "detail": f"{type(exc).__name__}：{exc}",
                                    "liveTitle": str(status_result.get("title") or ""),
                                    "watchedSeconds": 0,
                                }
                                self.logger.exception(f"续灯牌任务异常：{live_url}")

                status = str(result.get("status") or "failed")
                detail = str(result.get("detail") or "")
                self.badge_store.record(
                    live_url,
                    status,
                    detail,
                    watched_seconds=int(result.get("watchedSeconds") or 0),
                    live_title=str(result.get("liveTitle") or ""),
                )
                results.append(
                    {
                        "ok": status in {"sent", "not_live"},
                        "status": status,
                        "liveUrl": live_url,
                        "detail": detail,
                        "watchedSeconds": int(result.get("watchedSeconds") or 0),
                    }
                )
                await asyncio.sleep(1.0)

            sent_count = sum(1 for item in results if item.get("status") == "sent")
            not_live_count = sum(1 for item in results if item.get("status") == "not_live")
            failed_count = sum(1 for item in results if item.get("status") == "failed")
            skipped_count = sum(1 for item in results if item.get("status") == "skipped")
            detail_parts = [f"续灯牌成功 {sent_count} 个"]
            if not_live_count:
                detail_parts.append(f"未开播 {not_live_count} 个")
            if failed_count:
                detail_parts.append(f"失败 {failed_count} 个")
            if skipped_count:
                detail_parts.append(f"跳过 {skipped_count} 个")
            payload = {
                "ok": failed_count == 0,
                "detail": "，".join(detail_parts),
                "results": results,
                "reason": reason,
            }
            self._last_badge_result = payload
            return payload

    async def test_badge_gift(self, urls: Optional[List[str]] = None) -> Dict[str, Any]:
        """实际赠送一次灯牌用于测试，不进入挂机且不受当日成功状态限制。"""
        if self._badge_lock is None:
            self._badge_lock = asyncio.Lock()
        if self._live_lock is None:
            self._live_lock = asyncio.Lock()

        async with self._badge_lock:
            config = self.config_snapshot()
            raw_urls = list(urls if urls is not None else config.get("badge_live_urls") or [])
            try:
                live_urls = normalize_live_urls(raw_urls)
            except ValueError as exc:
                return {"ok": False, "detail": str(exc), "results": []}
            if not live_urls:
                return {"ok": False, "detail": "没有配置需要测试的直播间地址", "results": []}

            live_url = live_urls[0]
            room_id = urlparse(live_url).path.strip("/")
            self.logger.info(f"通过直播接口检测是否开播：{live_url}")
            status_result = await self.live_status.check(room_id)
            if status_result.get("status") != "live":
                status = (
                    "not_live"
                    if status_result.get("status") == "not_live"
                    else "failed"
                )
                detail = str(status_result.get("detail") or "")
                result = {
                    "status": status,
                    "detail": detail,
                    "liveTitle": str(status_result.get("title") or ""),
                    "watchedSeconds": 0,
                }
            else:
                try:
                    live_bridge = await self._require_live_browser()
                except Exception as exc:
                    result = {
                        "status": "failed",
                        "detail": f"{type(exc).__name__}：{exc}",
                        "liveTitle": str(status_result.get("title") or ""),
                        "watchedSeconds": 0,
                    }
                else:
                    async with self._live_lock:
                        try:
                            result = await test_badge_gift_in_live_room(
                                live_bridge,
                                live_url,
                                self.logger,
                            )
                        except Exception as exc:
                            result = {
                                "status": "failed",
                                "detail": f"{type(exc).__name__}：{exc}",
                                "liveTitle": str(status_result.get("title") or ""),
                                "watchedSeconds": 0,
                            }
                            self.logger.exception(f"测试赠送灯牌异常：{live_url}")

            status = str(result.get("status") or "failed")
            detail = str(result.get("detail") or "")
            existing_status = str(self.badge_store.get(live_url).get("status") or "")
            if status == "sent" or existing_status != "sent":
                self.badge_store.record(
                    live_url,
                    status,
                    detail,
                    watched_seconds=0,
                    live_title=str(result.get("liveTitle") or ""),
                )
            payload = {
                "ok": status == "sent",
                "detail": detail,
                "status": status,
                "liveUrl": live_url,
                "results": [
                    {
                        "ok": status == "sent",
                        "status": status,
                        "liveUrl": live_url,
                        "detail": detail,
                    }
                ],
            }
            self._last_badge_result = payload
            return payload

    # ---------- 控制接口用到的页面操作 ----------
    async def evaluate_script(self, script: str) -> Any:
        await self._require_chat_browser()
        return await self.browser.evaluate(script)

    async def query_selector(self, selector: str, limit: int = 20) -> Any:
        await self._require_chat_browser()
        return await query_elements(self.browser, selector, limit)

    async def click(self, selector: str) -> Dict[str, Any]:
        await self._require_chat_browser()
        clicked = await click_selector(self.browser, [selector])
        if not clicked:
            raise ValueError(f"没有找到可点击的元素：{selector}")
        return {"selector": selector, "clicked": True}

    async def type_text(self, selector: str, text: str, submit: bool = False) -> Dict[str, Any]:
        await self._require_chat_browser()
        await click_selector(self.browser, [selector], fallback_js=False)
        await self.browser.insert_text(str(text))
        if submit:
            await self.browser.press_key("Enter")
        return {"selector": selector, "length": len(str(text)), "submitted": bool(submit)}

    async def press(self, key: str) -> Dict[str, Any]:
        await self._require_chat_browser()
        await self.browser.press_key(key)
        return {"key": key}

    async def take_screenshot(self, full_page: bool = False) -> Dict[str, Any]:
        await self._require_chat_browser()
        stamp = datetime.now(BEIJING).strftime("%Y%m%d-%H%M%S")
        path = LOG_DIR / f"截图-{stamp}.png"
        await self.browser.screenshot(path, full_page=full_page)
        return {"path": str(path)}

    async def page_html(self, limit: int = 0) -> str:
        await self._require_chat_browser()
        html = await self.browser.evaluate("document.documentElement.outerHTML")
        text = str(html or "")
        if limit and int(limit) > 0:
            return text[: int(limit)]
        return text

    async def pages_info(self) -> Any:
        if self._chat_browser_available:
            chat = await self.browser.status()
        else:
            chat = {"url": "", "title": "", "loaded": False}
        if (
            self.live_browser_enabled
            and self._live_browser_available
            and self.live_browser is not None
        ):
            live = await self.live_browser.status()
        else:
            live = {
                "url": "",
                "title": "",
                "enabled": bool(self.live_browser_enabled),
                "loaded": False,
            }
        return [
            {
                "index": 0,
                "name": "私信浏览器",
                "url": chat.get("url", ""),
                "title": chat.get("title", ""),
            },
            {
                "index": 1,
                "name": "直播浏览器",
                "url": live.get("url", ""),
                "title": live.get("title", ""),
            },
        ]

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
