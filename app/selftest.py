"""自检脚本：校验配置、时间、匹配、记录与定时逻辑，并可完整跑一遍内置浏览器发送流程。"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import normalize_config, normalize_time
from .logger import BEIJING
from .message import pick_message, split_candidates
from .scheduler import DailyScheduler, scheduled_at
from .sender import name_matches, normalize_text
from .state import SendStore


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _check_config() -> None:
    _check(normalize_time("09:05") == "09:05", "时间规范化失败")
    _check(normalize_time("9:5") == "09:05", "时间简写规范化失败")
    _check(normalize_time("23:59") == "23:59", "时间规范化失败")
    for bad in ("24:00", "12:60", "", "abc"):
        try:
            normalize_time(bad)
        except ValueError:
            continue
        raise AssertionError(f"非法时间 {bad!r} 未被拦截")

    config = normalize_config({"send_time": "7:30"})
    _check(config["send_time"] == "07:30", "配置补全失败")
    _check(config["target_names"] == [], "缺省好友列表应为空")
    _check(config["daily_enabled"] is True, "缺省应开启每日定时")
    migrated = normalize_config({"target_name": "旧版好友"})
    _check(migrated["target_names"] == ["旧版好友"], "旧版单好友配置未迁移")
    deduped = normalize_config({"target_names": ["小明", "小明", " 小红 ", ""]})
    _check(deduped["target_names"] == ["小明", "小红"], "好友名去重失败")


def _check_matching() -> None:
    _check(name_matches("小明", "小明"), "全等匹配失败")
    _check(not name_matches("小明", "小明同学"), "全等模式不应匹配到更长的名字")
    _check(name_matches("小明同学", "小明", "contains"), "包含匹配失败")
    _check(name_matches("ＡＢＣ", "ABC"), "全角转半角失败")
    _check(normalize_text("  a\u200bb  ") == "ab", "零宽字符清理失败")
    _check(not name_matches("", "小明"), "空名字不应匹配")


def _check_schedule() -> None:
    now = datetime(2026, 10, 6, 8, 0, tzinfo=BEIJING)
    _check(scheduled_at(now, "09:30") == now.replace(hour=9, minute=30), "计划时间计算失败")


def _check_message() -> None:
    text = "第一条\n第二条"
    _check(split_candidates("第一条\n\n 第二条 \n第一条") == ["第一条", "第二条"], "候选拆分失败")
    _check(pick_message(text, "whole") == text, "整条发送失败")
    _check(pick_message(text, "random_line", avoid="第一条") == "第二条", "随机抽行未避开上次内容")
    _check(pick_message(text, "random_line") in {"第一条", "第二条"}, "随机抽行取值异常")


def _check_store() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = SendStore(Path(folder) / "history.json")
        _check(store.is_success("2026-10-06", "小明") is False, "初始状态不应为已发送")
        store.record("failed", "小明", "hi", "网络错误", "自检", date_key="2026-10-06")
        _check(store.is_success("2026-10-06", "小明") is False, "失败记录不应视为已发送")
        _check(store.attempt_count("2026-10-06", "小明") == 1, "失败次数统计失败")
        store.record("success", "小明", "hi", "已确认", "自检", date_key="2026-10-06")
        _check(store.is_success("2026-10-06", "小明") is True, "成功记录应视为已发送")
        _check(store.attempt_count("2026-10-06", "小明") == 2, "重复记录未累加尝试次数")
        store.record("success", "小红", "hi", "已确认", "自检", date_key="2026-10-06")
        _check(
            store.pending_targets("2026-10-06", ["小明", "小红", "小刚"]) == ["小刚"],
            "待发送好友筛选失败",
        )
        _check(store.history(5)[0]["date"] == "2026-10-06", "历史记录读取失败")


def _check_scheduler() -> None:
    logger = logging.getLogger("自检定时器")

    async def scenario(config, expected_calls: int, label: str) -> None:
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
            _check(len(calls) == expected_calls, f"{label}：期望触发 {expected_calls} 次，实际 {len(calls)} 次")

    base = normalize_config(
        {
            "send_time": "00:01",
            "daily_enabled": True,
            "missed_run": True,
            "target_names": ["小明", "小红"],
        }
    )
    asyncio.run(scenario(base, 1, "正常触发"))

    missed_time = (datetime.now(BEIJING) - timedelta(hours=6)).strftime("%H:%M")
    skipped = normalize_config(
        {
            "send_time": missed_time,
            "daily_enabled": True,
            "missed_run": False,
            "missed_grace_minutes": 60,
            "target_names": ["小明"],
        }
    )
    asyncio.run(scenario(skipped, 0, "关闭补发时错过不触发"))

    disabled = normalize_config({"send_time": "00:01", "daily_enabled": False, "target_names": ["小明"]})
    asyncio.run(scenario(disabled, 0, "关闭定时不触发"))

    no_target = normalize_config({"send_time": "00:01", "daily_enabled": True})
    asyncio.run(scenario(no_target, 0, "没有好友时不触发"))


TINY_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# 要发送的好友特意放在列表最底部，用来验证发送时会自动滑动查找
TEST_FRIEND_NAMES = [f"好友{index:02d}" for index in range(1, 24)] + ["测试好友", "另一个好友"]

_TEST_FRIEND_ITEMS = "".join(
    f'<div class="item"><img src="{TINY_PNG}"><span class="name">{name}</span></div>'
    for name in TEST_FRIEND_NAMES
)

TEST_PAGE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>自检页面</title></head>
<body>
<div id="sub-app">
  <div id="friends-tab">朋友私信</div>
  <div id="friend-list" style="height:200px;overflow-y:auto;border:1px solid #ddd">
    __FRIEND_ITEMS__
  </div>
  <div id="messages"></div>
  <div id="editor" contenteditable="true" role="textbox"
       style="height:60px;border:1px solid #ccc;margin-top:8px"></div>
</div>
<script>
const editor = document.getElementById('editor');
const messages = document.getElementById('messages');
editor.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    const text = (editor.innerText || '').replace(/\\n+$/, '');
    if (text.trim()) {
      const box = document.createElement('div');
      box.className = 'msg-box';
      box.textContent = text;
      messages.appendChild(box);
      editor.innerHTML = '';
    }
  }
});
</script>
</body></html>
""".replace("__FRIEND_ITEMS__", _TEST_FRIEND_ITEMS)

TEST_SELECTORS = {
    "chat_urls": ["about:blank"],
    "friends_tab": ["#friends-tab"],
    "friends_tab_texts": ["朋友私信"],
    "friend_item": ["#friend-list .item"],
    "friend_name": [".name"],
    "friend_list_scroll": ["#friend-list"],
    "chat_input": ["#editor"],
    "own_message": [".msg-box"],
    "login_mask": [],
}


def _check_embedded_flow() -> None:
    """在真实的内置浏览器里跑一遍完整发送流程（用本地自检页面代替抖音页面）。"""
    from PySide6.QtCore import QMetaObject, Qt
    from PySide6.QtWidgets import QApplication

    from .embedded import EmbeddedBrowser, prepare_debug_port
    from .service import Application

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    port = prepare_debug_port()
    qt_app = QApplication.instance() or QApplication(sys.argv[:1])

    temp_dir = Path(tempfile.mkdtemp(prefix="douyin-selftest-"))
    browser = EmbeddedBrowser(temp_dir / "profile")
    browser.resize(900, 640)
    browser.show()
    application = Application(port)
    application.selectors = TEST_SELECTORS
    application.config["daily_enabled"] = False  # 自检期间关闭定时，避免干扰
    application.store = SendStore(temp_dir / "history.json")
    application.start()

    outcome = {}

    def wait_page_ready(timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ready = application.submit(
                    application.browser.evaluate(
                        "document.readyState === 'complete' && Boolean(document.querySelector('#sub-app'))"
                    )
                ).result(timeout=15)
            except Exception:
                ready = False
            if ready:
                return True
            time.sleep(0.5)
        return False

    def worker() -> None:
        try:
            application.wait_boot(60)
            _check(wait_page_ready(30.0), "自检页面没有加载出来")
            result = application.submit(
                application.run_send_now(targets=["测试好友", "另一个好友"], message="自检消息")
            ).result(timeout=150)
            outcome["result"] = result

            async def probes():
                bridge = application.browser
                return {
                    "eval": await bridge.evaluate("1 + 1"),
                    "title": await bridge.evaluate("document.title"),
                    "boxes": await bridge.evaluate("document.querySelectorAll('.msg-box').length"),
                    "input": await bridge.evaluate(
                        "document.getElementById('editor') ? document.getElementById('editor').innerText : ''"
                    ),
                    "click": await application.click("#friends-tab"),
                    "query": await application.query_selector(".item", 5),
                    "screenshot": await application.take_screenshot(False),
                    "html": await application.page_html(0),
                    "friends": await application.scan_friends(cache=False, reload=False),
                    "scrollTop": await bridge.evaluate(
                        "(document.querySelector('#friend-list') || {}).scrollTop || 0"
                    ),
                    "fresh": await application.check_friends_fresh(),
                }

            outcome["probes"] = application.submit(probes()).result(timeout=90)
        except Exception as exc:
            outcome["error"] = repr(exc)
        finally:
            QMetaObject.invokeMethod(qt_app, "quit", Qt.ConnectionType.QueuedConnection)

    browser.setHtml(TEST_PAGE)
    threading.Thread(target=worker, name="selftest-browser", daemon=True).start()
    qt_app.exec()
    application.stop()

    if outcome.get("error"):
        raise AssertionError(outcome["error"])
    result = outcome.get("result") or {}
    _check(result.get("ok") is True, f"发送流程失败：{result.get('detail')} | 明细：{result.get('results')}")
    probes = outcome.get("probes") or {}
    _check(probes.get("eval") == 2, "页面脚本执行失败")
    _check("自检页面" in str(probes.get("title")), "页面标题读取失败")
    _check(int(probes.get("boxes") or 0) >= 2, "多好友发送没有全部落到会话里")
    _check(not str(probes.get("input") or "").strip(), "发送后输入框没有清空")
    _check((probes.get("click") or {}).get("clicked") is True, "元素点击失败")
    _check(len(probes.get("query") or []) >= 2, "元素查询失败")
    _check(Path(str((probes.get("screenshot") or {}).get("path"))).exists(), "截图失败")
    _check("sub-app" in str(probes.get("html") or ""), "页面 HTML 读取失败")
    friends = probes.get("friends") or []
    names = [str(item.get("name") or "") for item in friends]
    _check(
        len(names) >= len(TEST_FRIEND_NAMES),
        f"好友列表没有滚动收集完整：{len(names)}/{len(TEST_FRIEND_NAMES)} 位",
    )
    _check("好友23" in names and "测试好友" in names, "好友列表缺少首尾好友")
    _check(any(item.get("avatar") for item in friends), "没有解析到头像")
    _check(
        float(probes.get("scrollTop") or 0) > 0,
        "发送时没有自动滑动好友列表",
    )
    _check(
        (probes.get("fresh") or {}).get("fresh") is False,
        "快速校验在没有缓存对应好友时不应判定为通过",
    )


def run_selftest(include_browser: bool = False) -> int:
    print("== 逻辑自检 ==")
    _check_config()
    print("配置与时间：通过")
    _check_matching()
    print("好友名匹配：通过")
    _check_schedule()
    print("定时时间计算：通过")
    _check_message()
    print("消息发送方式：通过")
    _check_store()
    print("发送记录幂等：通过")
    _check_scheduler()
    print("定时触发逻辑：通过")

    if include_browser:
        print("== 内置浏览器自检 ==")
        _check_embedded_flow()
        print("内置浏览器接管、点击、输入、发送、截图：通过")

    print("自检全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_selftest(include_browser=True))
