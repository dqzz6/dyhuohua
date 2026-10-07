"""页面脚本拼装：统一注入选择器工具函数，支持 CSS 与 xpath= 两种写法。"""

from __future__ import annotations

import json
from typing import Any, Dict

JS_PRELUDE = r"""
const queryAll = (selector) => {
  try {
    if (typeof selector === 'string' && selector.indexOf('xpath=') === 0) {
      const result = document.evaluate(
        selector.slice(6), document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null
      );
      const nodes = [];
      for (let index = 0; index < result.snapshotLength; index += 1) nodes.push(result.snapshotItem(index));
      return nodes;
    }
    return Array.from(document.querySelectorAll(selector));
  } catch (error) {
    return [];
  }
};
const queryOne = (selector, index) => {
  const nodes = queryAll(selector);
  if (!nodes.length) return null;
  return (index === undefined) ? nodes[0] : (nodes[index] || null);
};
const hasBox = (node) => {
  if (!node) return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
};
// 页面有时会把同一份列表渲染两遍（隐藏一份、可见一份）。
// 优先取可见的那份，实在取不到再退回原逻辑，避免点中 0 尺寸的隐藏节点。
const pickVisibleNodes = (selectorList) => {
  let fallback = [];
  for (const selector of (selectorList || [])) {
    const nodes = queryAll(selector);
    if (!nodes.length) continue;
    if (!fallback.length) fallback = nodes;
    const visible = nodes.filter(hasBox);
    if (visible.length) return visible;
  }
  return fallback;
};
const normalizeName = (value) => (value || '')
  .normalize('NFKC')
  .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
  .replace(/\s+/g, ' ')
  .trim();
const findScrollContainer = (scrollSelectors, itemSelector) => {
  const isScrollable = (node) => {
    if (!node) return false;
    const style = getComputedStyle(node);
    if (!/(auto|scroll)/.test(style.overflowY)) return false;
    return node.scrollHeight > node.clientHeight + 40;
  };
  const candidates = [];
  for (const selector of (scrollSelectors || [])) candidates.push(queryOne(selector));
  const items = queryAll(itemSelector);
  let node = items.length ? items[items.length - 1] : null;
  while (node && node !== document.body && node !== document.documentElement) {
    candidates.push(node);
    node = node.parentElement;
  }
  for (const candidate of candidates) {
    if (isScrollable(candidate)) return candidate;
  }
  for (const candidate of candidates) {
    if (candidate && candidate.scrollHeight > candidate.clientHeight + 40) return candidate;
  }
  return null;
};
const scrollTarget = (scrollSelectors, itemSelector) => {
  const node = findScrollContainer(scrollSelectors, itemSelector);
  if (!node) return { found: false, node: null, max: 0 };
  return { found: true, node: node, max: Math.max(0, node.scrollHeight - node.clientHeight) };
};
"""

# 把列表滚回顶部（虚拟列表必须从顶部开始逐段扫描，否则会漏）
RESET_SCROLL_BODY = r"""
const target = scrollTarget(A.scrollSelectors, A.itemSelector);
if (!target.found) return { reset: false, max: 0 };
target.node.scrollTop = 0;
return { reset: true, max: target.max };
"""

# 向下滚动一屏，并返回是否到底
SCROLL_DOWN_BODY = r"""
const target = scrollTarget(A.scrollSelectors, A.itemSelector);
if (!target.found) return { scrolled: false, atBottom: true, notFound: true };
const before = target.node.scrollTop;
target.node.scrollTop = Math.min(target.max, before + A.step);
return {
  before: before,
  after: target.node.scrollTop,
  max: target.max,
  scrolled: target.node.scrollTop > before,
  atBottom: target.node.scrollTop >= target.max - 4,
};
"""


def build_script(body: str, args: Dict[str, Any]) -> str:
    """把 JS 片段与参数拼成一段可直接执行的 IIFE。"""
    payload = json.dumps(args, ensure_ascii=False)
    return "(() => {\nconst A = " + payload + ";\n" + JS_PRELUDE + "\n" + body + "\n})()"
