"""消息内容处理：支持「随机抽一行发送」和「整条发送」两种方式。"""

from __future__ import annotations

import random
from typing import List

RANDOM_LINE = "random_line"
WHOLE = "whole"


def normalize_mode(value) -> str:
    return RANDOM_LINE if str(value or "").strip() == RANDOM_LINE else WHOLE


def split_candidates(text: str) -> List[str]:
    """把多行文本拆成候选消息，去掉空行与首尾空白，并保持顺序去重。"""
    result: List[str] = []
    for line in str(text or "").splitlines():
        item = line.strip()
        if item and item not in result:
            result.append(item)
    return result


def pick_message(text: str, mode: str = WHOLE, avoid: str = "") -> str:
    """按发送方式生成这一次真正要发的内容。

    random_line：每行作为一条候选，随机抽一条；
    whole：原文整条发送（保留换行）。
    """
    if normalize_mode(mode) != RANDOM_LINE:
        return str(text or "").strip()

    candidates = split_candidates(text)
    if not candidates:
        return ""

    last = str(avoid or "").strip()
    if len(candidates) > 1 and last:
        filtered = [item for item in candidates if item != last]
        if filtered:
            candidates = filtered
    return random.choice(candidates)
