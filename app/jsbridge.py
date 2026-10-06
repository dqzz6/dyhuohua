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
const pickNodes = (selectorList) => {
  for (const selector of (selectorList || [])) {
    const nodes = queryAll(selector);
    if (nodes.length) return nodes;
  }
  return [];
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
"""


def build_script(body: str, args: Dict[str, Any]) -> str:
    """把 JS 片段与参数拼成一段可直接执行的 IIFE。"""
    payload = json.dumps(args, ensure_ascii=False)
    return "(() => {\nconst A = " + payload + ";\n" + JS_PRELUDE + "\n" + body + "\n})()"
