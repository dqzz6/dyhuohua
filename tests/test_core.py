"""核心逻辑测试：配置、匹配、定时、发送记录、发送流程参数校验。"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import normalize_config, normalize_time  # noqa: E402
from app.friends import extract_user_details, merge_friends, normalize_key  # noqa: E402
from app.logger import BEIJING  # noqa: E402
from app.scheduler import DailyScheduler, scheduled_at  # noqa: E402
from app.sender import SendError, name_matches, normalize_text, send_message  # noqa: E402
from app.state import SendStore  # noqa: E402


def test_normalize_time() -> None:
    assert normalize_time("09:05") == "09:05"
    assert normalize_time("9:5") == "09:05"
    assert normalize_time("23:59") == "23:59"
    for bad in ("24:00", "12:60", "", "abc"):
        try:
            normalize_time(bad)
        except ValueError:
            continue
        raise AssertionError(f"非法时间 {bad!r} 未被拦截")


def test_normalize_config() -> None:
    config = normalize_config({"send_time": "7:30", "control_api_port": 10})
    assert config["send_time"] == "07:30"
    assert config["control_api_port"] == 1024
    assert config["daily_enabled"] is True
    assert config["match_mode"] == "equals"
    assert config["target_names"] == []


def test_target_names_migration_and_dedup() -> None:
    assert normalize_config({"target_name": "旧版好友"})["target_names"] == ["旧版好友"]
    assert normalize_config({"target_names": ["小明", "小明", " 小红 ", ""]})["target_names"] == ["小明", "小红"]


def test_friend_details_from_api() -> None:
    payloads = [
        {
            "user_list": [
                {"user_id": "u1", "user": {"nickname": "小明", "avatar_thumb": {"url_list": ["https://a/1.jpg"]}}},
                {"user_id": "u2", "user": {"SecretUseId": "u2", "nickname": "小红", "avatar_thumb": {"url_list": ["https://a/2.jpg"]}}},
            ]
        },
        {"user_list": [{"user_id": "u1", "user": {"nickname": "小明", "avatar_thumb": {"url_list": ["https://a/1.jpg"]}}}]},
        {"user_list": [{"user_id": "u3", "user": {"nickname": ""}}]},
    ]
    details = extract_user_details(payloads)
    assert [item["name"] for item in details] == ["小明", "小红"]
    assert details[0]["avatar"].endswith("1.jpg")
    assert details[1]["userId"] == "u2"


def test_merge_friends_removes_noise() -> None:
    details = [
        {"userId": "u1", "name": "小明", "avatar": "https://a/1.jpg"},
        {"userId": "u2", "name": "小红", "avatar": "https://a/2.jpg"},
    ]
    dom = [
        {"name": "20"},
        {"name": "小明"},
        {"name": "小明"},
        {"name": "00:57小红,你好"},
        {"name": "小红"},
    ]
    merged = merge_friends(dom, details)
    assert [item["name"] for item in merged] == ["小明", "小红"]
    assert all(item["userId"] for item in merged)
    assert merge_friends(dom, []) == []
    assert normalize_key("Ａ Ｂ\u200b") == "ab"


def test_name_matching() -> None:
    assert name_matches("小明", "小明")
    assert not name_matches("小明", "小明同学")
    assert name_matches("小明同学", "小明", "contains")
    assert name_matches("ＡＢＣ", "ABC")


def test_normalize_text() -> None:
    assert normalize_text("  a\u200bb  ") == "ab"
    assert normalize_text(None) == ""


def test_scheduled_at() -> None:
    now = datetime(2026, 10, 6, 8, 0, tzinfo=BEIJING)
    assert scheduled_at(now, "09:30") == now.replace(hour=9, minute=30)


def test_send_store_single_success_per_day() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = SendStore(Path(folder) / "history.json")
        assert store.is_success("2026-10-06", "小明") is False
        store.record("failed", "小明", "hi", "网络错误", "测试", date_key="2026-10-06")
        assert store.is_success("2026-10-06", "小明") is False
        assert store.attempt_count("2026-10-06", "小明") == 1
        store.record("success", "小明", "hi", "已确认", "测试", date_key="2026-10-06")
        assert store.is_success("2026-10-06", "小明") is True
        store.record("success", "小红", "hi", "已确认", "测试", date_key="2026-10-06")
        assert store.pending_targets("2026-10-06", ["小明", "小红", "小刚"]) == ["小刚"]


def _run_scheduler(config, expected_calls: int) -> None:
    logger = logging.getLogger("测试定时器")

    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = SendStore(Path(folder) / "history.json")
            calls = []

            async def run_once(force: bool, reason: str) -> None:
                calls.append(reason)
                for name in config.get("target_names") or ["小明"]:
                    store.record("success", name, "hi", "已确认", reason)

            scheduler = DailyScheduler(lambda: config, run_once, store, logger, tick_seconds=5)
            await scheduler._tick()
            await scheduler._tick()
            assert len(calls) == expected_calls, f"期望触发 {expected_calls} 次，实际 {len(calls)} 次"

    asyncio.run(scenario())


def test_scheduler_triggers_once() -> None:
    _run_scheduler(
        normalize_config({"send_time": "00:01", "missed_run": True, "target_names": ["小明", "小红"]}),
        1,
    )


def test_scheduler_skips_when_disabled() -> None:
    _run_scheduler(
        normalize_config({"send_time": "00:01", "daily_enabled": False, "target_names": ["小明"]}),
        0,
    )


def test_scheduler_skips_without_targets() -> None:
    _run_scheduler(normalize_config({"send_time": "00:01", "daily_enabled": True}), 0)


def test_scheduler_skips_long_missed_run() -> None:
    missed = (datetime.now(BEIJING) - timedelta(hours=6)).strftime("%H:%M")
    config = normalize_config(
        {"send_time": missed, "missed_run": False, "missed_grace_minutes": 60, "target_names": ["小明"]}
    )
    _run_scheduler(config, 0)


def test_send_message_rejects_empty_input() -> None:
    class _Dummy:
        pass

    async def scenario() -> None:
        for target, message in (("", "内容"), ("小明", "")):
            try:
                await send_message(_Dummy(), target, message, {}, logging.getLogger("测试"))
            except SendError:
                continue
            raise AssertionError("空参数未被拦截")

    asyncio.run(scenario())


def _run_all() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items()) if name.startswith("test_") and callable(obj)]
    failed = 0
    for name, func in tests:
        try:
            func()
        except Exception as exc:
            failed += 1
            print(f"[失败] {name}: {exc}")
        else:
            print(f"[通过] {name}")
    print(f"共 {len(tests)} 项，失败 {failed} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
