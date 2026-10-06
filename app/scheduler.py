"""每日定时：按北京时间到点触发发送，同一天只成功发送一次。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine, Dict, Optional

from .logger import BEIJING


def parse_hhmm(value: str) -> tuple:
    hour_text, minute_text = str(value).split(":", 1)
    return int(hour_text), int(minute_text)


def scheduled_at(now: datetime, send_time: str) -> datetime:
    """返回当天的计划发送时刻。"""
    hour, minute = parse_hhmm(send_time)
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


class DailyScheduler:
    """轮询式定时器：到点触发，可选错过补发，发送成功后当天不再重复。"""

    def __init__(
        self,
        config_provider: Callable[[], Dict[str, Any]],
        run_once: Callable[[str], Coroutine],
        store,
        logger,
        tick_seconds: int = 15,
    ):
        self._config_provider = config_provider
        self._run_once = run_once
        self._store = store
        self._logger = logger
        self._tick_seconds = max(5, int(tick_seconds))
        self._task: Optional[asyncio.Task] = None
        self._sending = False
        self._skipped_date = ""

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
                self._logger.warning(f"定时检查出现异常：{exc}")
            await asyncio.sleep(self._tick_seconds)

    async def _tick(self) -> None:
        config = self._config_provider()
        if not config.get("daily_enabled"):
            return

        now = datetime.now(BEIJING)
        today = now.strftime("%Y-%m-%d")
        if self._store.has_success(today):
            return

        target = scheduled_at(now, config.get("send_time") or "09:00")
        if now < target:
            return

        grace_minutes = int(config.get("missed_grace_minutes") or 0)
        if not config.get("missed_run") and now - target > timedelta(minutes=grace_minutes):
            if self._skipped_date != today:
                self._skipped_date = today
                self._logger.warning(f"已错过 {config.get('send_time')} 且未开启补发，今天跳过发送")
            return

        if self._sending:
            return

        self._sending = True
        try:
            await self._run_once("定时任务")
        finally:
            self._sending = False

    def next_run_at(self, now: Optional[datetime] = None) -> Optional[datetime]:
        """返回下一次计划发送时间；已关闭定时返回 None。"""
        config = self._config_provider()
        if not config.get("daily_enabled"):
            return None
        current = now or datetime.now(BEIJING)
        target = scheduled_at(current, config.get("send_time") or "09:00")
        if self._store.has_success(current.strftime("%Y-%m-%d")):
            return target + timedelta(days=1)
        if current >= target:
            grace_minutes = int(config.get("missed_grace_minutes") or 0)
            can_catch_up = bool(config.get("missed_run")) or (current - target) <= timedelta(minutes=grace_minutes)
            return current if can_catch_up else target + timedelta(days=1)
        return target
