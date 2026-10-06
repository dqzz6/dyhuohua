"""页面选择器：默认值来自旧版项目已验证的抖音创作者私信页，可在 data/selectors.json 中覆盖。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .paths import SELECTORS_PATH

DEFAULT_SELECTORS: Dict[str, List[str]] = {
    "chat_urls": [
        "https://creator.douyin.com/creator-micro/data/following/chat",
    ],
    "friends_tab": [
        'xpath=//*[@id="sub-app"]/div/div/div[1]/div[2]',
        'xpath=//*[@id="sub-app"]//div[contains(@class,"semi-tabs-tab")][1]',
    ],
    "friends_tab_texts": ["朋友私信", "好友私信"],
    "friend_item": [
        'xpath=//*[@id="sub-app"]//div[contains(@class,"semi-list-item-body")]',
        'xpath=//*[@id="sub-app"]//li[contains(@class,"semi-list-item")]',
        'xpath=//*[@id="sub-app"]//div[contains(@class,"item-header-name")]',
    ],
    "friend_name": [
        'span[class*="item-header-name"]',
        '[class*="item-header-name"]',
    ],
    "friend_list_scroll": [
        'xpath=//*[@id="sub-app"]//ul',
    ],
    "chat_input": [
        "xpath=//div[contains(@class, 'chat-input-')]//div[@contenteditable='true']",
        "xpath=//div[@contenteditable='true' and @role='textbox']",
        "xpath=(//div[@contenteditable='true'])[last()]",
        "xpath=//textarea",
    ],
    "own_message": [
        "[class*='box-item-']",
    ],
    "login_mask": [
        ".login-mask",
        ".login-guide-container",
        ".login-img-code-wrapper",
    ],
}


def load_selectors(path: Path = SELECTORS_PATH) -> Dict[str, List[str]]:
    """读取默认选择器，并用 data/selectors.json 中的同名字段覆盖。"""
    merged = {key: list(value) for key, value in DEFAULT_SELECTORS.items()}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        for key, value in dict(raw or {}).items():
            if key in merged and isinstance(value, list) and value:
                merged[key] = [str(item) for item in value]
    return merged
