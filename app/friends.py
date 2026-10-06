"""好友列表读取：滚动到底部收集全部好友，并缓存头像到本机。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from .jsbridge import build_script
from .paths import AVATAR_DIR, FRIENDS_CACHE_PATH

AVATAR_TIMEOUT = 10
AVATAR_MAX_BYTES = 3 * 1024 * 1024
JUNK_NAME_PATTERN = re.compile(r"^(\d+|\d{1,2}:\d{2}.*)$")

COLLECT_BODY = r"""
const strictNameOf = (node) => {
  for (const selector of A.nameSelectors) {
    const targetNode = node.querySelector(selector);
    const text = targetNode ? (targetNode.innerText || targetNode.textContent || '').trim() : '';
    if (text) return text.split('\n')[0];
  }
  return '';
};
const isImageUrl = (value) => {
  const url = String(value || '').trim();
  if (!url) return false;
  if (url.indexOf('data:image') === 0) return true;
  if (!/^https?:\/\//.test(url)) return false;
  return /avatar|head|douyinpic|byteimg|\.jpe?g|\.png|\.webp|\.image/i.test(url);
};
const findAvatar = (node) => {
  for (const img of node.querySelectorAll('img')) {
    const url = img.currentSrc || img.src || img.getAttribute('src') || img.getAttribute('data-src');
    if (isImageUrl(url)) return url;
  }
  for (const item of node.querySelectorAll('*')) {
    const style = getComputedStyle(item).backgroundImage || '';
    const match = style.match(/url\(["']?([^"')]+)["']?\)/);
    if (match && isImageUrl(match[1])) return match[1];
  }
  return '';
};
const nodes = pickNodes(A.itemSelectors);
const friends = [];
const loose = [];
for (const node of nodes) {
  const strict = normalizeName(strictNameOf(node));
  const own = normalizeName((node.innerText || '').split('\n')[0]);
  if (strict) {
    friends.push({ name: strict, avatar: findAvatar(node) });
  } else if (own) {
    loose.push({ name: own, avatar: findAvatar(node) });
  }
}
return { friends: friends, loose: loose, count: nodes.length };
"""

SCROLL_BODY = r"""
const target = findScrollContainer(A.scrollSelectors, A.itemSelector);
if (!target) return { before: 0, after: 0, max: 0, atBottom: true, notFound: true };
const before = target.scrollTop;
const max = Math.max(0, target.scrollHeight - target.clientHeight);
target.scrollTop = Math.min(max, before + A.step);
return {
  before: before,
  after: target.scrollTop,
  max: max,
  atBottom: target.scrollTop >= max - 4,
};
"""

RESET_BODY = r"""
const target = findScrollContainer(A.scrollSelectors, A.itemSelector);
if (!target) return { reset: false };
target.scrollTop = 0;
return { reset: true, max: Math.max(0, target.scrollHeight - target.clientHeight) };
"""


async def collect_friends(
    bridge,
    selectors: Dict[str, List[str]],
    logger=None,
    max_scrolls: int = 120,
    step: int = 500,
    passes: int = 2,
) -> List[Dict[str, str]]:
    """滚动整个好友列表并收集名字与头像地址；多跑几遍取并集，避免虚拟列表渲染延迟漏人。"""
    collect_args = {
        "itemSelectors": selectors.get("friend_item", []),
        "nameSelectors": selectors.get("friend_name", []),
    }
    scroll_args = {
        "scrollSelectors": selectors.get("friend_list_scroll", []),
        "itemSelector": (selectors.get("friend_item") or [""])[0],
        "step": int(step),
    }

    friends: Dict[str, Dict[str, str]] = {}
    loose_friends: Dict[str, Dict[str, str]] = {}

    async def scan_once() -> int:
        """扫描当前已渲染的好友，返回新增数量。"""
        data = await bridge.evaluate(build_script(COLLECT_BODY, collect_args))
        added = 0
        payload = data if isinstance(data, dict) else {}
        for item in payload.get("friends") or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            avatar = str(item.get("avatar") or "").strip()
            if name not in friends:
                friends[name] = {"name": name, "avatar": avatar}
                added += 1
            elif avatar and not friends[name]["avatar"]:
                friends[name]["avatar"] = avatar
        for item in payload.get("loose") or []:
            name = str(item.get("name") or "").strip()
            if not name or JUNK_NAME_PATTERN.match(name):
                continue
            avatar = str(item.get("avatar") or "").strip()
            if name not in loose_friends:
                loose_friends[name] = {"name": name, "avatar": avatar}
            elif avatar and not loose_friends[name]["avatar"]:
                loose_friends[name]["avatar"] = avatar
        return added

    async def one_pass(pass_index: int) -> None:
        # 虚拟列表会复用节点，必须先回到顶部再逐段下滑，否则只能读到当前可视区域。
        reset = await bridge.evaluate(build_script(RESET_BODY, scroll_args))
        await asyncio.sleep(1.5)
        if logger is not None and pass_index == 1 and not (isinstance(reset, dict) and reset.get("reset")):
            logger.warning("没有找到好友列表的滚动容器，只能读取当前可见的好友")

        idle_rounds = 0
        for round_index in range(max_scrolls):
            added = await scan_once()
            scroll = await bridge.evaluate(build_script(SCROLL_BODY, scroll_args))
            await asyncio.sleep(1.3)
            scroll = scroll if isinstance(scroll, dict) else {}
            moved = int(scroll.get("after") or 0) > int(scroll.get("before") or 0)
            at_bottom = bool(scroll.get("atBottom"))
            if logger is not None and (round_index == 0 or added):
                logger.info(f"好友列表扫描中（第 {pass_index} 遍）：已收集 {len(friends)} 位好友")
            if at_bottom and added == 0:
                break
            idle_rounds = 0 if (added or moved) else idle_rounds + 1
            if idle_rounds >= 3:
                break
        await scan_once()

    for pass_index in range(1, max(1, int(passes)) + 1):
        await one_pass(pass_index)

    if not friends and loose_friends:
        # 页面结构变化导致找不到名字节点时，退回按整行首行提取。
        friends = loose_friends
        if logger is not None:
            logger.warning("没有识别到标准好友名字节点，已按整行文本兜底提取")

    if logger is not None:
        logger.info(f"好友列表扫描完成，共 {len(friends)} 位好友")
    return list(friends.values())


def cache_avatar(url: str, name: str, avatar_dir: Path = AVATAR_DIR) -> str:
    """把头像下载到本地并返回路径，失败时返回空字符串。"""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        if raw.startswith("data:image"):
            header, _, payload = raw.partition(",")
            if not payload:
                return ""
            data = base64.b64decode(payload)
            suffix = ".png" if "png" in header else (".webp" if "webp" in header else ".jpg")
        else:
            request = urllib.request.Request(raw, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=AVATAR_TIMEOUT) as response:
                data = response.read(AVATAR_MAX_BYTES + 1)
                if len(data) > AVATAR_MAX_BYTES:
                    return ""
                content_type = str(response.headers.get("Content-Type") or "").split(";")[0].strip()
            suffix = mimetypes.guess_extension(content_type or "") or ".jpg"
            if suffix in (".jpe",):
                suffix = ".jpg"
        if not data:
            return ""
        avatar_dir = Path(avatar_dir)
        avatar_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:16]
        path = avatar_dir / f"{digest}{suffix}"
        path.write_bytes(data)
        return str(path)
    except Exception:
        return ""


def write_cache(friends: List[Dict[str, Any]], path: Path = FRIENDS_CACHE_PATH) -> None:
    """把好友与头像缓存写到本地，下次启动先展示旧数据。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(friends, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def read_cache(path: Path = FRIENDS_CACHE_PATH) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
