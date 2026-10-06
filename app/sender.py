"""发送流程：选中好友 -> 输入内容 -> 发送 -> 校验结果（全部通过调试协议完成）。"""

from __future__ import annotations

import asyncio
import unicodedata
from typing import Any, Dict, List, Tuple

from .cdp import CdpError
from .jsbridge import RESET_SCROLL_BODY, SCROLL_DOWN_BODY, build_script


class SendError(RuntimeError):
    """发送流程失败。"""


class LoginRequiredError(SendError):
    """账号未登录或登录状态已失效。"""


class TargetNotFoundError(SendError):
    """未在好友列表里找到目标。"""


CHAT_READY_SELECTOR = "#sub-app"
LOGIN_TEXT_MARKERS = ("扫码登录", "验证码登录", "密码登录", "登录/注册", "请登录")
SHIFT_MODIFIER = 8


def normalize_text(value: Any) -> str:
    """归一化文本：全角转半角、去掉零宽字符、压缩空白。"""
    raw = unicodedata.normalize("NFKC", str(value or ""))
    for token in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        raw = raw.replace(token, "")
    raw = raw.replace("\xa0", " ")
    return " ".join(raw.split()).strip()


def name_matches(candidate: str, target: str, mode: str = "equals") -> bool:
    """好友名匹配：equals 为全等，contains 为互相包含。"""
    left = normalize_text(candidate)
    right = normalize_text(target)
    if not left or not right:
        return False
    if mode == "contains":
        return right in left or left in right
    return left == right


def _script(body: str, args: Dict[str, Any]) -> str:
    return build_script(body, args)


PROBE_BODY = r"""
const text = (document.body && document.body.innerText) || '';
const loginVisible = A.loginMarkers.some((marker) => text.indexOf(marker) >= 0);
return {
  url: location.href,
  title: document.title,
  readyState: document.readyState,
  hasChatApp: Boolean(queryOne(A.chatReadySelector)),
  loginVisible: loginVisible,
};
"""


async def probe_page(bridge, selectors: Dict[str, List[str]]) -> Dict[str, Any]:
    result = await bridge.evaluate(
        _script(
            PROBE_BODY,
            {
                "chatReadySelector": CHAT_READY_SELECTOR,
                "loginMarkers": list(LOGIN_TEXT_MARKERS),
            },
        )
    )
    return result if isinstance(result, dict) else {}


async def ensure_logged_in(bridge, selectors: Dict[str, List[str]]) -> None:
    probe = await probe_page(bridge, selectors)
    if probe.get("loginVisible") and not probe.get("hasChatApp"):
        raise LoginRequiredError("账号未登录，请先在内置浏览器里扫码登录抖音创作者中心")


async def wait_for_chat_ready(bridge, timeout_seconds: int = 25) -> None:
    """等待私信页加载完成；如果是登录页则直接给出明确提示。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    probe: Dict[str, Any] = {}
    while loop.time() < deadline:
        try:
            probe = await probe_page(bridge, {})
        except CdpError:
            probe = {}
        if probe.get("hasChatApp") and probe.get("readyState") == "complete":
            return
        if probe.get("loginVisible"):
            raise LoginRequiredError("账号未登录，请先在内置浏览器里扫码登录抖音创作者中心")
        await asyncio.sleep(1.0)
    raise SendError("没有进入私信页面，请确认内置浏览器里打开的是抖音创作者中心的私信页")


ELEMENT_POINT_BODY = r"""
let node = null;
for (const selector of A.selectors) {
  const nodes = queryAll(selector);
  if (nodes.length > A.index) { node = nodes[A.index]; break; }
}
if (!node) return { found: false };
node.scrollIntoView({ block: 'center', inline: 'center' });
const rect = node.getBoundingClientRect();
if (rect.width <= 0 || rect.height <= 0) return { found: false, invisible: true };
return {
  found: true,
  x: Math.round(rect.x + rect.width / 2),
  y: Math.round(rect.y + rect.height / 2),
};
"""

JS_CLICK_BODY = r"""
let node = null;
for (const selector of A.selectors) {
  const nodes = queryAll(selector);
  if (nodes.length > A.index) { node = nodes[A.index]; break; }
}
if (!node) return { clicked: false };
node.click();
return { clicked: true };
"""

TEXT_POINT_BODY = r"""
const wanted = String(A.text || '').trim();
const nodes = queryAll('div,span,a,li,button,p');
let node = null;
for (const item of nodes) {
  if ((item.innerText || '').trim() === wanted) node = item;
}
if (!node) return { found: false };
node.scrollIntoView({ block: 'center', inline: 'center' });
const rect = node.getBoundingClientRect();
if (rect.width <= 0 || rect.height <= 0) return { found: false };
return { found: true, x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) };
"""


async def _click_point(bridge, info: Dict[str, Any]) -> None:
    try:
        await bridge.click_point(info["x"], info["y"])
    except CdpError:
        # 鼠标事件不可用时直接忽略，由脚本点击兜底。
        pass


async def click_selector(
    bridge,
    selectors: List[str],
    index: int = 0,
    fallback_js: bool = True,
) -> bool:
    info = await bridge.evaluate(_script(ELEMENT_POINT_BODY, {"selectors": selectors, "index": index}))
    if not isinstance(info, dict) or not info.get("found"):
        return False
    await _click_point(bridge, info)
    if fallback_js:
        # 坐标点击与脚本点击都执行一次：两者幂等，可明显提高成功率。
        try:
            await bridge.evaluate(_script(JS_CLICK_BODY, {"selectors": selectors, "index": index}))
        except CdpError:
            pass
    return True


async def click_text(bridge, text: str) -> bool:
    info = await bridge.evaluate(_script(TEXT_POINT_BODY, {"text": text}))
    if not isinstance(info, dict) or not info.get("found"):
        return False
    await _click_point(bridge, info)
    return True


QUERY_BODY = r"""
const nodes = queryAll(A.selector).slice(0, A.limit);
return nodes.map((node) => {
  const rect = node.getBoundingClientRect();
  return {
    tag: node.tagName.toLowerCase(),
    className: (node.className || '').toString().slice(0, 200),
    text: (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 200),
    visible: rect.width > 0 && rect.height > 0,
    rect: {
      x: Math.round(rect.x), y: Math.round(rect.y),
      width: Math.round(rect.width), height: Math.round(rect.height),
    },
    childCount: node.children.length,
  };
});
"""


async def query_elements(bridge, selector: str, limit: int = 20) -> Any:
    """按选择器枚举页面元素，返回可读的结构化结果。"""
    return await bridge.evaluate(
        _script(QUERY_BODY, {"selector": str(selector), "limit": max(1, int(limit))})
    )


async def click_friends_tab(bridge, selectors: Dict[str, List[str]], logger) -> bool:
    """切换到好友私信标签，失败时保持当前页面继续尝试。"""
    for selector in selectors.get("friends_tab", []):
        if await click_selector(bridge, [selector]):
            await asyncio.sleep(1.5)
            logger.info(f"已切换到朋友私信标签：{selector}")
            return True
    for text in selectors.get("friends_tab_texts", []):
        if await click_text(bridge, text):
            await asyncio.sleep(1.5)
            logger.info(f"已通过文字切换到朋友私信标签：{text}")
            return True
    logger.warning("未找到朋友私信标签，按当前页面继续尝试")
    return False


FRIEND_SCAN_BODY = r"""
const nameOf = (node) => {
  for (const selector of A.nameSelectors) {
    const targetNode = node.querySelector(selector);
    const text = targetNode ? (targetNode.innerText || targetNode.textContent || '').trim() : '';
    if (text) return text.split('\n')[0];
  }
  const own = (node.innerText || '').trim();
  return own ? own.split('\n')[0] : '';
};
const nodes = pickNodes(A.itemSelectors);
const wanted = normalizeName(A.target);
const names = [];
let hit = null;
for (const node of nodes) {
  const name = normalizeName(nameOf(node));
  if (!name) continue;
  if (names.indexOf(name) < 0) names.push(name);
  if (hit) continue;
  const matched = A.mode === 'contains'
    ? (name.indexOf(wanted) >= 0 || wanted.indexOf(name) >= 0)
    : name === wanted;
  if (matched) hit = node;
}
let point = null;
if (hit) {
  hit.setAttribute('data-dymsg-friend', '1');
  hit.scrollIntoView({ block: 'center' });
  const rect = hit.getBoundingClientRect();
  if (rect.width > 0 && rect.height > 0) {
    point = { x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) };
  }
}
return { matched: Boolean(hit), names: names.slice(0, 40), total: nodes.length, point };
"""

JS_FRIEND_CLICK_BODY = r"""
const node = queryOne('[data-dymsg-friend="1"]');
if (!node) return { clicked: false };
node.click();
return { clicked: true };
"""

async def find_friend(
    bridge,
    target_name: str,
    selectors: Dict[str, List[str]],
    logger=None,
    match_mode: str = "equals",
    max_scrolls: int = 120,
    step: int = 500,
) -> Dict[str, Any]:
    """在好友列表里查找目标好友：先回到顶部，再逐段下滑，直到找到或到底。"""
    seen_names: List[str] = []
    args = {
        "itemSelectors": selectors.get("friend_item", []),
        "nameSelectors": selectors.get("friend_name", []),
        "target": target_name,
        "mode": match_mode,
    }
    scroll_args = {
        "scrollSelectors": selectors.get("friend_list_scroll", []),
        "itemSelector": (selectors.get("friend_item") or [""])[0],
        "step": int(step),
    }

    # 好友可能在列表任意位置，先把列表滚回顶部再向下找，避免每次都从头开始找不到。
    reset = await bridge.evaluate(_script(RESET_SCROLL_BODY, scroll_args))
    await asyncio.sleep(0.8)
    if logger is not None and isinstance(reset, dict) and not reset.get("reset"):
        logger.warning("没有找到好友列表的滚动容器，只能在当前可见范围内查找")

    for round_index in range(max_scrolls):
        result = await bridge.evaluate(_script(FRIEND_SCAN_BODY, args))
        if not isinstance(result, dict):
            result = {}
        for name in result.get("names") or []:
            if name not in seen_names:
                seen_names.append(name)

        if result.get("matched"):
            if logger is not None:
                logger.info(f"已找到好友：{target_name}（匹配方式：{match_mode}）")
            return {"matched": True, "point": result.get("point") or {}}

        if round_index == 0 and logger is not None:
            logger.info(f"好友列表当前可见 {int(result.get('total') or 0)} 项，开始滚动查找")

        scroll = await bridge.evaluate(_script(SCROLL_DOWN_BODY, scroll_args))
        await asyncio.sleep(1.0)
        scroll = scroll if isinstance(scroll, dict) else {}
        if scroll.get("atBottom") or not scroll.get("scrolled"):
            break

    preview = "、".join(seen_names[:30]) if seen_names else "无"
    raise TargetNotFoundError(f"未在好友列表中找到「{target_name}」。已看到：{preview}")


FOCUS_INPUT_BODY = r"""
for (const selector of A.selectors) {
  const nodes = queryAll(selector);
  if (!nodes.length) continue;
  const node = nodes[nodes.length - 1];
  node.scrollIntoView({ block: 'center' });
  node.focus();
  if (node.setAttribute) node.setAttribute('data-dymsg-input', '1');
  return { found: true, selector: selector };
}
return { found: false };
"""


async def locate_chat_input(bridge, selectors: Dict[str, List[str]]) -> str:
    info = await bridge.evaluate(_script(FOCUS_INPUT_BODY, {"selectors": selectors.get("chat_input", [])}))
    if not isinstance(info, dict) or not info.get("found"):
        raise SendError("未找到聊天输入框，请确认已选中好友且页面已加载完成")
    return str(info.get("selector") or "")


READ_INPUT_BODY = r"""
const node = queryOne('[data-dymsg-input="1"]');
if (!node) return '';
if (node.isContentEditable) return node.innerText || '';
return node.value || '';
"""


async def read_input_text(bridge) -> str:
    value = await bridge.evaluate(_script(READ_INPUT_BODY, {}))
    return str(value or "")


CLEAR_INPUT_BODY = r"""
const node = queryOne('[data-dymsg-input="1"]');
if (!node) return { cleared: false };
node.focus();
if (node.isContentEditable) {
  const range = document.createRange();
  range.selectNodeContents(node);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
} else {
  node.select();
}
return { cleared: true };
"""


async def clear_input(bridge) -> None:
    info = await bridge.evaluate(_script(CLEAR_INPUT_BODY, {}))
    if isinstance(info, dict) and info.get("cleared"):
        await bridge.press_key("Delete")


COUNT_MESSAGE_BODY = r"""
const normalize = (value) => (value || '')
  .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
  .replace(/\s+/g, ' ')
  .trim();
const wanted = normalize(A.probe);
if (!wanted) return 0;
let matches = 0;
for (const selector of A.selectors) {
  for (const node of queryAll(selector)) {
    if (node.closest('[contenteditable="true"]')) continue;
    const text = normalize(node.innerText || node.textContent || '');
    if (text && text.indexOf(wanted) >= 0) matches += 1;
  }
}
return matches;
"""


async def count_own_messages(bridge, message: str, selectors: Dict[str, List[str]]) -> int:
    probe = normalize_text(message)[:50]
    if not probe:
        return 0
    try:
        value = await bridge.evaluate(
            _script(COUNT_MESSAGE_BODY, {"selectors": selectors.get("own_message", []), "probe": probe})
        )
        return int(value or 0)
    except Exception:
        return 0


async def confirm_sent(
    bridge,
    message: str,
    selectors: Dict[str, List[str]],
    timeout_seconds: int = 30,
) -> Tuple[bool, str]:
    """发送后校验：输入框已清空，且会话里出现了这条消息。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    detail = "等待发送结果超时"
    while loop.time() < deadline:
        remaining = normalize_text(await read_input_text(bridge))
        matched = await count_own_messages(bridge, message, selectors)
        if matched > 0 and not remaining:
            return True, f"输入框已清空，会话中已出现该消息（匹配 {matched} 处）"
        detail = f"输入框仍有内容：{remaining[:40]}" if remaining else "输入框已清空，但会话中暂未检测到该消息"
        await asyncio.sleep(1.0)
    return False, detail


async def send_message(
    bridge,
    target_name: str,
    message: str,
    selectors: Dict[str, List[str]],
    logger,
    match_mode: str = "equals",
    timeout_seconds: int = 120,
) -> Dict[str, Any]:
    """完整发送流程，成功返回结果字典，失败抛出 SendError 子类。"""
    if not normalize_text(target_name):
        raise SendError("请先设置目标好友名称")
    if not str(message).strip():
        raise SendError("请先设置要发送的消息内容")

    await ensure_logged_in(bridge, selectors)
    await wait_for_chat_ready(bridge)
    await click_friends_tab(bridge, selectors, logger)

    found = await find_friend(bridge, target_name, selectors, logger, match_mode=match_mode)
    point = found.get("point") or {}
    if point.get("x") is not None:
        await _click_point(bridge, point)
    else:
        # 窗口被最小化或不可见时拿不到坐标，改用脚本点击兜底。
        clicked = await bridge.evaluate(_script(JS_FRIEND_CLICK_BODY, {}))
        if not isinstance(clicked, dict) or not clicked.get("clicked"):
            raise TargetNotFoundError(f"找到了「{target_name}」但无法点击，请重试")
        logger.warning("好友元素坐标不可用，已改用脚本点击")
    await asyncio.sleep(1.5)

    await locate_chat_input(bridge, selectors)
    await clear_input(bridge)

    lines = str(message).split("\n")
    for index, line in enumerate(lines):
        if line:
            await bridge.insert_text(line)
        if index < len(lines) - 1:
            await bridge.press_key("Enter", modifiers=SHIFT_MODIFIER)
    await asyncio.sleep(0.4)

    logger.info(f"准备发送消息给「{target_name}」，长度 {len(message)} 字")
    await bridge.press_key("Enter")

    send_timeout = max(10, min(60, int(timeout_seconds)))
    ok, detail = await confirm_sent(bridge, message, selectors, send_timeout)
    if not ok:
        raise SendError(f"发送结果未确认：{detail}")

    return {"ok": True, "target": target_name, "message": message, "detail": detail}
