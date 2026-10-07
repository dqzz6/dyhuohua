"""粉丝灯牌自动续费：监控直播间、开播后续灯牌并挂满指定时长。"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from .logger import BEIJING
from .paths import BADGE_HISTORY_PATH, LOG_DIR, ensure_dirs

DEFAULT_CHECK_INTERVAL_MINUTES = 10
DEFAULT_WATCH_MINUTES = 20
DEFAULT_LIVE_READY_WAIT_SECONDS = 120
MAX_CHECK_INTERVAL_MINUTES = 1440
MAX_WATCH_MINUTES = 180

LIVE_READY_TEXTS = ("在线观众", "本场点赞", "小时榜", "人气榜", "礼物", "送礼")
LIVE_OFFLINE_TEXTS = ("直播已结束", "暂未开播", "主播暂时离开")
LIVE_URL_PATTERN = re.compile(r"https?://live\.douyin\.com/[^\s,，;；]+", re.IGNORECASE)
BADGE_ENTRY_TEXTS = ("粉丝团", "粉丝牌", "灯牌")
BADGE_SEND_TEXTS = (
    "点亮(1钻)",
    "点亮（1钻）",
    "点亮灯牌",
    "赠送灯牌",
    "续费灯牌",
    "点亮",
)
BADGE_TOOLTIP_SEND_TEXTS = ("赠送",)
PAYMENT_ERROR_TEXTS = ("余额不足", "钻石不足", "充值")
LOGIN_ERROR_TEXTS = ("扫码登录", "登录后", "请先登录")


def normalize_live_url(value: Any) -> str:
    """把直播间地址统一成 https://live.douyin.com/房间号。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    matched_url = LIVE_URL_PATTERN.search(raw)
    if matched_url:
        raw = matched_url.group(0).rstrip("。！？!?）)]}>\"'")
    if re.fullmatch(r"\d+", raw):
        raw = f"https://live.douyin.com/{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("直播间地址必须以 http:// 或 https:// 开头")
    if (parsed.hostname or "").lower() != "live.douyin.com":
        raise ValueError("直播间地址必须是 live.douyin.com")
    path = re.sub(r"/+", "/", parsed.path or "").strip("/")
    if not path:
        raise ValueError("直播间地址缺少房间号")
    return f"https://live.douyin.com/{path}"


def normalize_live_urls(value: Any) -> List[str]:
    """支持文本、列表两种输入，按顺序去重。"""
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        text = str(value or "")
        raw_items = LIVE_URL_PATTERN.findall(text)
        for line in text.splitlines():
            stripped = line.strip()
            if re.fullmatch(r"\d+", stripped):
                raw_items.append(stripped)
        if not raw_items:
            raw_items = re.split(r"[\n,，;；]+", text)

    result: List[str] = []
    for item in raw_items:
        live_url = normalize_live_url(item)
        if live_url and live_url not in result:
            result.append(live_url)
    return result


def _parse_time(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=BEIJING)
    return parsed.astimezone(BEIJING)


class BadgeRenewalStore:
    """文件结构：{日期: {直播间地址: 状态记录}}。"""

    def __init__(self, path: Path = BADGE_HISTORY_PATH):
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
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self._path)

    def get(self, live_url: str, date_key: Optional[str] = None) -> Dict[str, Any]:
        key = str(live_url or "").strip()
        with self._lock:
            day = self._read().get(date_key or self.today_key())
        if not isinstance(day, dict):
            return {}
        entry = day.get(key)
        return dict(entry) if isinstance(entry, dict) else {}

    def latest(self, live_url: str) -> Dict[str, Any]:
        key = str(live_url or "").strip()
        with self._lock:
            data = self._read()
        for date_key in sorted(data.keys(), reverse=True):
            day = data.get(date_key)
            if not isinstance(day, dict):
                continue
            entry = day.get(key)
            if isinstance(entry, dict):
                return {"date": date_key, **entry}
        return {}

    def should_check(
        self,
        live_url: str,
        interval_minutes: int,
        *,
        now: Optional[datetime] = None,
        force: bool = False,
    ) -> tuple:
        current = now or datetime.now(BEIJING)
        entry = self.get(live_url, current.strftime("%Y-%m-%d"))
        if str(entry.get("status") or "") == "sent":
            return False, "今天已经续过灯牌"
        last_checked = _parse_time(entry.get("lastCheckedAt"))
        interval = max(1, int(interval_minutes or DEFAULT_CHECK_INTERVAL_MINUTES))
        if (
            not force
            and last_checked
            and current - last_checked < timedelta(minutes=interval)
        ):
            return False, "未到下次检测时间"
        return True, ""

    def next_check_at(
        self,
        live_urls: List[str],
        interval_minutes: int,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[datetime]:
        current = now or datetime.now(BEIJING)
        interval = max(1, int(interval_minutes or DEFAULT_CHECK_INTERVAL_MINUTES))
        candidates: List[datetime] = []
        for live_url in live_urls:
            entry = self.get(live_url, current.strftime("%Y-%m-%d"))
            if str(entry.get("status") or "") == "sent":
                continue
            last_checked = _parse_time(entry.get("lastCheckedAt"))
            if last_checked is None:
                return current
            candidates.append(last_checked + timedelta(minutes=interval))
        if not candidates:
            return None
        return min(candidates)

    def record(
        self,
        live_url: str,
        status: str,
        detail: str = "",
        *,
        watched_seconds: int = 0,
        live_title: str = "",
        date_key: Optional[str] = None,
        checked_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        key = str(live_url or "").strip()
        day_key = date_key or self.today_key()
        now_value = (checked_at or datetime.now(BEIJING)).astimezone(BEIJING).isoformat(
            timespec="seconds"
        )
        with self._lock:
            data = self._read()
            day = data.get(day_key)
            if not isinstance(day, dict):
                day = {}
            previous = day.get(key)
            attempts = 1
            if isinstance(previous, dict):
                try:
                    attempts = int(previous.get("attempts") or 0) + 1
                except (TypeError, ValueError):
                    attempts = 1
            entry = {
                "status": str(status or ""),
                "detail": str(detail or ""),
                "lastCheckedAt": now_value,
                "attempts": attempts,
            }
            if watched_seconds:
                entry["watchedSeconds"] = int(watched_seconds)
            if live_title:
                entry["liveTitle"] = str(live_title)
            if str(status or "") == "sent":
                entry["sentAt"] = now_value
            day[key] = entry
            data[day_key] = day
            self._write(data)
        return dict(entry)

    def history(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._read()
        result: List[Dict[str, Any]] = []
        for date_key in sorted(data.keys(), reverse=True)[: max(1, int(limit))]:
            day = data.get(date_key)
            if not isinstance(day, dict):
                continue
            for live_url, entry in day.items():
                if isinstance(entry, dict):
                    result.append({"date": date_key, "liveUrl": live_url, **entry})
        return result


def detect_live_status(probe: Dict[str, Any]) -> tuple:
    """根据页面标题和正文判断直播间是否开播。"""
    url = str((probe or {}).get("url") or "")
    title = str((probe or {}).get("title") or "").strip()
    body = str((probe or {}).get("body") or "")
    text = f"{title}\n{body}"
    if any(item in text for item in LIVE_READY_TEXTS):
        return True, title or "直播间已开播"
    if any(item in body for item in LIVE_OFFLINE_TEXTS):
        return False, f"未开播：{title or '直播间'}"
    if "live.douyin.com" in url and title and "直播" in title and bool(body.strip()):
        return True, title
    return False, f"未识别到直播状态：{title or '页面尚未加载完成'}"


async def _read_live_page(browser) -> Dict[str, Any]:
    script = r"""(() => {
        const body = document.body ? (document.body.innerText || "") : "";
        return {
            url: String(location.href || ""),
            title: String(document.title || ""),
            ready: String(document.readyState || ""),
            body: body.slice(0, 16000)
        };
    })()"""
    result = await browser.evaluate(script)
    return result if isinstance(result, dict) else {}


async def wait_for_live_room(browser, timeout_seconds: float = 60.0) -> tuple:
    """等待直播间页面稳定，返回 (是否开播, 详情, 页面信息)。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(5.0, float(timeout_seconds))
    last_probe: Dict[str, Any] = {}
    last_detail = "直播间页面尚未加载完成"
    while loop.time() < deadline:
        try:
            last_probe = await _read_live_page(browser)
        except Exception as exc:
            last_probe = {}
            last_detail = f"读取直播间页面失败：{exc}"
        else:
            is_live, detail = detect_live_status(last_probe)
            last_detail = detail
            if is_live:
                return True, detail, last_probe
            if "未开播" in detail:
                return False, detail, last_probe
        await asyncio.sleep(1.5)
    return False, last_detail, last_probe


async def _click_text_candidate(
    browser,
    texts: List[str],
    *,
    scope_selector: str = "",
    native_click: bool = False,
) -> Dict[str, Any]:
    """查找可见文本并点击最近的按钮/可交互节点。"""
    wanted = json.dumps([str(item) for item in texts], ensure_ascii=False)
    scope = json.dumps(str(scope_selector or ""), ensure_ascii=False)
    native_click_text = "true" if native_click else "false"
    script = f"""(() => {{
        const wanted = {wanted};
        const scopeSelector = {scope};
        const nativeClick = {native_click_text};
        const roots = scopeSelector
            ? Array.from(document.querySelectorAll(scopeSelector))
            : [document];
        const nodes = [];
        for (const root of roots) {{
            nodes.push(...Array.from(root.querySelectorAll(
                "button,[role='button'],a,[tabindex],div,span"
            )));
        }}
        const candidates = [];
        for (const node of nodes) {{
            const text = String(node.innerText || node.textContent || "")
                .replace(/\\s+/g, " ").trim();
            if (!text || text.length > 40) continue;
            if (["点亮了", "已点亮", "加入了", "赠送了", "续费了", "开通了"]
                .some((item) => text.includes(item))) continue;
            const matched = wanted.find((item) => text.includes(item));
            if (!matched) continue;
            const target = node.closest("button,[role='button'],a,[tabindex]") || node;
            const box = target.getBoundingClientRect();
            if (box.width <= 0 || box.height <= 0) continue;
            if (target.disabled
                || target.getAttribute("aria-disabled") === "true") continue;
            const className = String(target.className || "");
            const clickable = Boolean(
                target.matches("button,a,[role='button'],[tabindex]")
            )
                || /button|btn|click|operation/i.test(className);
            candidates.push({{
                text,
                matched,
                x: box.left + box.width / 2,
                y: box.top + box.height / 2,
                width: box.width,
                height: box.height,
                clickable,
                target
            }});
        }}
        if (!candidates.length) return null;
        candidates.sort((a, b) => {{
            const exactA = a.text === a.matched ? 0 : 1;
            const exactB = b.text === b.matched ? 0 : 1;
            const clickableA = a.clickable ? 0 : 1;
            const clickableB = b.clickable ? 0 : 1;
            return exactA - exactB
                || clickableA - clickableB
                || a.text.length - b.text.length
                || b.y - a.y;
        }});
        const candidate = candidates[0];
        const clickTarget = candidate.target;
        if (clickTarget && !nativeClick) {{
            if (typeof clickTarget.click === "function") {{
                clickTarget.click();
            }} else {{
                clickTarget.dispatchEvent(new MouseEvent("click", {{
                    bubbles: true,
                    cancelable: true,
                    view: window,
                    clientX: candidate.x,
                    clientY: candidate.y
                }}));
            }}
        }}
        return {{
            text: candidate.text,
            matched: candidate.matched,
            x: candidate.x,
            y: candidate.y,
            width: candidate.width,
            height: candidate.height
        }};
    }})()"""
    result = await browser.evaluate(script)
    if (
        native_click
        and isinstance(result, dict)
        and result.get("x") is not None
        and result.get("y") is not None
    ):
        await browser.click_point(float(result["x"]), float(result["y"]))
    return result if isinstance(result, dict) else {}


async def _click_text_when_ready(
    browser,
    texts: List[str],
    *,
    scope_selector: str = "",
    timeout_seconds: float = 15.0,
    native_click: bool = False,
) -> Dict[str, Any]:
    """轮询等待页面元素出现后再点击，适配直播页面加载较慢的情况。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout_seconds))
    last_result: Dict[str, Any] = {}
    while loop.time() < deadline:
        last_result = await _click_text_candidate(
            browser,
            texts,
            scope_selector=scope_selector,
            native_click=native_click,
        )
        if last_result:
            return last_result
        await asyncio.sleep(1.0)
    return last_result


async def _read_badge_progress(browser) -> Dict[str, Any]:
    script = r"""(() => {
        const info = document.querySelector("#room_info_bar");
        const infoText = info ? String(info.innerText || "") : "";
        const match = infoText.match(/今日任务\s*(\d+)\s*\/\s*(\d+)/);
        const tooltip = Array.from(document.querySelectorAll(".dylive-tooltip"))
            .find((node) => {
                const box = node.getBoundingClientRect();
                return box.width > 0 && box.height > 0;
            });
        const tooltipText = tooltip ? String(tooltip.innerText || "") : "";
        return {
            progress: match ? Number(match[1]) : null,
            total: match ? Number(match[2]) : null,
            completed: tooltipText.includes("已完成"),
            tooltipText,
        };
    })()"""
    result = await browser.evaluate(script)
    return result if isinstance(result, dict) else {}


async def _wait_for_badge_send_confirmation(
    browser,
    before_state: Dict[str, Any],
    timeout_seconds: float = 15.0,
) -> tuple:
    """等待任务进度或已完成状态变化，确认灯牌实际送出。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout_seconds))
    before_progress = before_state.get("progress")
    before_completed = bool(before_state.get("completed"))
    last_state: Dict[str, Any] = {}
    while loop.time() < deadline:
        try:
            last_state = await _read_badge_progress(browser)
        except Exception:
            last_state = {}
        progress = last_state.get("progress")
        completed = bool(last_state.get("completed"))
        if completed and not before_completed:
            return True, "页面已显示粉丝团任务完成"
        if (
            isinstance(before_progress, int)
            and isinstance(progress, int)
            and progress > before_progress
        ):
            return True, f"粉丝团任务进度已从 {before_progress} 变为 {progress}"
        if before_completed and completed:
            return True, "粉丝团任务此前已完成"
        await asyncio.sleep(1.0)
    return False, str(last_state.get("tooltipText") or "")


async def _save_badge_debug(browser, live_url: str, label: str, logger) -> str:
    stamp = datetime.now(BEIJING).strftime("%Y%m%d-%H%M%S")
    safe_label = re.sub(r'[\\/:*?"<>|]+', "_", str(label or "调试"))[:40]
    path = LOG_DIR / "灯牌续费调试" / f"{stamp}-{safe_label}.png"
    try:
        await browser.screenshot(path)
        logger.warning(f"已保存续灯牌调试截图：{path}")
        return str(path)
    except Exception as exc:
        logger.warning(f"保存续灯牌调试截图失败：{exc}")
        return ""


async def _watch_live_room(browser, seconds: int, logger) -> None:
    """保持直播间页面打开，定期确认页面连接仍然可用。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1, int(seconds))
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(30.0, remaining))
        try:
            await browser.evaluate(
                "({url: String(location.href || ''), hidden: Boolean(document.hidden)})"
            )
        except Exception as exc:
            logger.warning(f"挂直播间期间页面连接异常，程序会继续等待：{exc}")


async def renew_badge_on_current_page(
    browser,
    live_url: str,
    watch_seconds: int,
    logger,
    *,
    ready_timeout_seconds: float = DEFAULT_LIVE_READY_WAIT_SECONDS,
) -> Dict[str, Any]:
    """处理当前已打开的直播间页面，适合浏览器自检复用。"""
    watch_seconds = max(1, int(watch_seconds))
    is_live, detail, probe = await wait_for_live_room(browser, ready_timeout_seconds)
    if not is_live:
        return {
            "status": "not_live",
            "detail": detail,
            "liveTitle": str((probe or {}).get("title") or ""),
            "watchedSeconds": 0,
        }

    entry = await _click_text_when_ready(
        browser,
        list(BADGE_ENTRY_TEXTS),
        scope_selector="#room_info_bar",
        timeout_seconds=15.0,
        native_click=True,
    )
    if not entry:
        entry = await _click_text_when_ready(
            browser,
            list(BADGE_ENTRY_TEXTS),
            timeout_seconds=10.0,
            native_click=True,
        )
    if not entry:
        await _save_badge_debug(browser, live_url, "未找到灯牌入口", logger)
        return {
            "status": "failed",
            "detail": "直播间已开播，但没有找到粉丝团/粉丝牌/灯牌入口",
            "liveTitle": detail,
            "watchedSeconds": 0,
        }

    before_state = await _read_badge_progress(browser)
    send_button = await _click_text_when_ready(
        browser,
        list(BADGE_TOOLTIP_SEND_TEXTS),
        scope_selector="[class*='dylive-tooltip']",
        timeout_seconds=15.0,
        native_click=True,
    )
    if not send_button:
        send_button = await _click_text_when_ready(
            browser,
            list(BADGE_SEND_TEXTS),
            timeout_seconds=10.0,
            native_click=True,
        )
    if not send_button:
        await _save_badge_debug(browser, live_url, "未找到点亮按钮", logger)
        return {
            "status": "failed",
            "detail": f"已点击「{entry.get('matched')}」，但没有找到点亮/续费按钮",
            "liveTitle": detail,
            "watchedSeconds": 0,
        }

    confirmed, confirm_detail = await _wait_for_badge_send_confirmation(
        browser,
        before_state,
        timeout_seconds=15.0,
    )
    if not confirmed:
        await _save_badge_debug(browser, live_url, "未确认灯牌送出", logger)
        return {
            "status": "failed",
            "detail": f"已点击「{send_button.get('matched')}」，但没有确认到灯牌送出",
            "liveTitle": detail,
            "watchedSeconds": 0,
        }
    logger.info(f"灯牌赠送已确认：{confirm_detail}")

    await asyncio.sleep(1.0)
    try:
        body_text = await browser.evaluate(
            "document.body ? String(document.body.innerText || '') : ''"
        )
    except Exception:
        body_text = ""
    if any(text in str(body_text or "") for text in PAYMENT_ERROR_TEXTS):
        await _save_badge_debug(browser, live_url, "余额不足", logger)
        return {
            "status": "failed",
            "detail": "灯牌续费失败：账号余额不足或页面要求充值",
            "liveTitle": detail,
            "watchedSeconds": 0,
        }
    if any(text in str(body_text or "") for text in LOGIN_ERROR_TEXTS):
        await _save_badge_debug(browser, live_url, "登录态失效", logger)
        return {
            "status": "failed",
            "detail": "灯牌续费失败：直播间登录态失效",
            "liveTitle": detail,
            "watchedSeconds": 0,
        }

    watched_text = (
        f"{watch_seconds // 60} 分钟" if watch_seconds >= 60 else f"{watch_seconds} 秒"
    )
    logger.info(
        f"已点击「{entry.get('matched')}」和「{send_button.get('matched')}」，"
        f"开始挂直播间 {watched_text}"
    )
    await _watch_live_room(browser, watch_seconds, logger)
    return {
        "status": "sent",
        "detail": (
            f"已续灯牌并挂满 {watched_text}"
            f"（入口：{entry.get('matched')}，操作：{send_button.get('matched')}）"
        ),
        "liveTitle": detail,
        "watchedSeconds": watch_seconds,
    }


async def renew_badge_in_live_room(
    browser,
    live_url: str,
    watch_minutes: int,
    logger,
    *,
    ready_timeout_seconds: float = DEFAULT_LIVE_READY_WAIT_SECONDS,
) -> Dict[str, Any]:
    """打开直播间，开播时续灯牌并挂满指定分钟。"""
    normalized_url = normalize_live_url(live_url)
    watch_seconds = max(1, int(watch_minutes or DEFAULT_WATCH_MINUTES)) * 60
    logger.info(f"开始检测直播间：{normalized_url}")
    await browser.goto(normalized_url)
    return await renew_badge_on_current_page(
        browser,
        normalized_url,
        watch_seconds,
        logger,
        ready_timeout_seconds=ready_timeout_seconds,
    )


class BadgeRenewalMonitor:
    """后台轮询器：按检查间隔调用续灯牌任务。"""

    def __init__(
        self,
        config_provider: Callable[[], Dict[str, Any]],
        run_once: Callable[[], Any],
        logger,
        tick_seconds: int = 15,
    ):
        self._config_provider = config_provider
        self._run_once = run_once
        self._logger = logger
        self._tick_seconds = max(5, int(tick_seconds))
        self._task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def running(self) -> bool:
        return bool(self._running)

    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
        return self._task

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning(f"续灯牌监控出现异常：{exc}")
            await asyncio.sleep(self._tick_seconds)

    async def _tick(self) -> None:
        config = self._config_provider()
        if not config.get("badge_renewal_enabled"):
            return
        if not list(config.get("badge_live_urls") or []):
            return
        if self._running:
            return
        self._running = True
        try:
            await self._run_once()
        finally:
            self._running = False
