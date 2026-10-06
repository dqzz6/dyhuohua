"""每日定时：按北京时间到点触发发送，同一天同一位好友只成功发送一次。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine, Dict, List, Optional

from .logger import BEIJING


def parse_hhmm(value: str) -> tuple:
    hour_text, minute_text = str(value).split(":", 1)
    return int(hour_text), int(minute_text)


def scheduled_at(now: datetime, send_time: str) -> datetime:
    """返回当天的计划发送时刻。"""
    hour, minute = parse_hhmm(send_time)
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


class DailyScheduler:
    """轮询式定时器：到点触发全部待发好友，支持错过补发与失败重试。"""

    def __init__(
        self,
        config_provider: Callable[[], Dict[str, Any]],
        run_once: Callable[[bool, str], Coroutine],
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

    def _targets(self, config: Dict[str, Any]) -> List[str]:
        return [str(name).strip() for name in (config.get("target_names") or []) if str(name).strip()]

    def _ready_targets(self, config: Dict[str, Any], now: datetime, today: str) -> List[str]:
        """挑出这次可以尝试发送的好友：已成功的不再发，失败的控制重试频率。"""
        max_attempts = int(config.get("max_attempts_per_day") or 3)
        retry_interval = int(config.get("retry_interval_minutes") or 10)
        ready: List[str] = []
        for name in self._store.pending_targets(today, self._targets(config)):
            if self._store.attempt_count(today, name) >= max_attempts:
                continue
            last_attempt = self._store.last_attempt_at(today, name)
            if last_attempt is not None and now - last_attempt < timedelta(minutes=retry_interval):
                continue
            ready.append(name)
        return ready

    async def _tick(self) -> None:
        config = self._config_provider()
        if not config.get("daily_enabled"):
            return
        targets = self._targets(config)
        if not targets:
            return

        now = datetime.now(BEIJING)
        today = now.strftime("%Y-%m-%d")
        target_time = scheduled_at(now, config.get("send_time") or "09:00")
        if now < target_time:
            return

        attempted_today = any(self._store.attempt_count(today, name) > 0 for name in targets)
        grace_minutes = int(config.get("missed_grace_minutes") or 0)
        if (
            not config.get("missed_run")
            and not attempted_today
            and now - target_time > timedelta(minutes=grace_minutes)
        ):
            if self._skipped_date != today:
                self._skipped_date = today
                self._logger.warning(f"已错过 {config.get('send_time')} 且未开启补发，今天跳过发送")
            return

        if self._sending:
            return
        if not self._ready_targets(config, now, today):
            return

        self._sending = True
        try:
            await self._run_once(False, "定时任务")
        finally:
            self._sending = False

    def next_run_at(self, now: Optional[datetime] = None) -> Optional[datetime]:
        """返回下一次计划发送时间；已关闭定时或没有好友时返回 None。"""
        config = self._config_provider()
        if not config.get("daily_enabled"):
            return None
        targets = self._targets(config)
        if not targets:
            return None
        current = now or datetime.now(BEIJING)
        today = current.strftime("%Y-%m-%d")
        target_time = scheduled_at(current, config.get("send_time") or "09:00")
        if not self._store.pending_targets(today, targets):
            return target_time + timedelta(days=1)
        if current >= target_time:
            grace_minutes = int(config.get("missed_grace_minutes") or 0)
            attempted_today = any(self._store.attempt_count(today, name) > 0 for name in targets)
            can_catch_up = (
                bool(config.get("missed_run"))
                or attempted_today
                or (current - target_time) <= timedelta(minutes=grace_minutes)
            )
            return current if can_catch_up else target_time + timedelta(days=1)
        return target_time
