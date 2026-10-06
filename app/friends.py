"""好友列表读取：滚动到底部收集全部好友，并缓存头像到本机。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from .jsbridge import build_script
from .paths import AVATAR_DIR, FRIENDS_CACHE_PATH

AVATAR_TIMEOUT = 10
AVATAR_MAX_BYTES = 3 * 1024 * 1024

COLLECT_BODY = r"""
const nameOf = (node) => {
  for (const selector of A.nameSelectors) {
    const targetNode = node.querySelector(selector);
    const text = targetNode ? (targetNode.innerText || targetNode.textContent || '').trim() : '';
    if (text) return text.split('\n')[0];
  }
  const own = (node.innerText || '').trim();
  return own ? own.split('\n')[0] : '';
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
for (const node of nodes) {
  const name = normalizeName(nameOf(node));
  if (!name) continue;
  friends.push({ name: name, avatar: findAvatar(node) });
}
return { friends: friends, count: nodes.length };
"""

SCROLL_BODY = r"""
const applyScroll = (node, step) => {
  if (!node) return null;
  const before = node.scrollTop;
  const max = Math.max(0, node.scrollHeight - node.clientHeight);
  node.scrollTop = Math.min(max, before + step);
  return {
    before: before,
    after: node.scrollTop,
    max: max,
    atBottom: node.scrollTop >= max - 4,
  };
};
for (const selector of A.scrollSelectors) {
  const result = applyScroll(queryOne(selector), A.step);
  if (result) return result;
}
const items = queryAll(A.itemSelector);
let node = items.length ? items[items.length - 1] : null;
while (node && node !== document.body && node !== document.documentElement) {
  const style = getComputedStyle(node);
  const scrollable = /(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 40;
  if (scrollable) return applyScroll(node, A.step);
  node = node.parentElement;
}
return { before: 0, after: 0, max: 0, atBottom: true, notFound: true };
"""


async def collect_friends(
    bridge,
    selectors: Dict[str, List[str]],
    logger=None,
    max_scrolls: int = 120,
    step: int = 700,
) -> List[Dict[str, str]]:
    """滚动整个好友列表并收集名字与头像地址。"""
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
    idle_rounds = 0
    for round_index in range(max_scrolls):
        data = await bridge.evaluate(build_script(COLLECT_BODY, collect_args))
        added = 0
        for item in (data or {}).get("friends") or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            avatar = str(item.get("avatar") or "").strip()
            if name not in friends:
                friends[name] = {"name": name, "avatar": avatar}
                added += 1
            elif avatar and not friends[name]["avatar"]:
                friends[name]["avatar"] = avatar

        scroll = await bridge.evaluate(build_script(SCROLL_BODY, scroll_args))
        await asyncio.sleep(1.2)
        scroll = scroll if isinstance(scroll, dict) else {}
        moved = int(scroll.get("after") or 0) > int(scroll.get("before") or 0)
        at_bottom = bool(scroll.get("atBottom"))

        if logger is not None and (round_index == 0 or added):
            logger.info(f"好友列表扫描中：已收集 {len(friends)} 位好友")

        if at_bottom and added == 0:
            break
        idle_rounds = 0 if (added or moved) else idle_rounds + 1
        if idle_rounds >= 3:
            break

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
