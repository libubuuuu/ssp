"""Sentry before_send 过滤(隐藏雷 #3)

Sentry 免费额度 5K events/月。如果 4xx 客户端错误 + 上游限流也都上报,
高峰几小时就烧光,真 bug 就被淹没了。

策略:
- 4xx HTTPException(401/402/403/404/422 等)— 用户输入问题,不是 bug,丢
- 5xx HTTPException — 服务端真问题,留
- fal.ai 上游 429/503(已重试 + 用户面前已 graceful 降级)— 丢
- 未捕获 Exception(任何非 HTTPException)— 留

测试:
- before_send(event, hint) -> event 或 None
- 4xx HTTPException → 返回 None
- 5xx HTTPException → 返回 event
- 普通 Exception → 返回 event
- fal 503 / 429 字符串匹配 → 返回 None
"""
from __future__ import annotations

import re
from typing import Any, Optional


# 必须同时满足:fal 标识 + 瞬时关键词(防误伤业务自身 503)
_FAL_IDENT_PATTERNS = [
    re.compile(r"fal\.media", re.IGNORECASE),
    re.compile(r"fal-ai/", re.IGNORECASE),
]
_TRANSIENT_KEYWORDS = ["rate limit", "service unavailable", "gateway timeout", "throttle"]


def _is_fal_transient(exc_value: Any) -> bool:
    """识别 fal.ai 上游已重试的瞬时错误

    严格双重匹配:
    - 必须含 fal 标识(fal.media / fal-ai/)
    - 必须含瞬时关键词(rate limit / service unavailable / gateway timeout / throttle)

    单纯一个 "503" 数字不算 fal 错(可能是业务自己的 503),不丢。
    """
    if exc_value is None:
        return False
    text = str(exc_value).lower()
    if not any(p.search(text) for p in _FAL_IDENT_PATTERNS):
        return False
    return any(kw in text for kw in _TRANSIENT_KEYWORDS)


def before_send(event: dict, hint: dict) -> Optional[dict]:
    """Sentry init 的 before_send 钩子。返回 None 丢弃,返 event 保留。

    Sentry 调用约定:
    - hint['exc_info'] 是 sys.exc_info() 三元组 (exc_type, exc_value, tb)
    - 没有异常时 hint['exc_info'] 是 None
    """
    exc_info = hint.get("exc_info") if hint else None
    if not exc_info:
        # 不是异常事件(例如手动 capture_message)— 保留
        return event

    exc_type, exc_value, _tb = exc_info

    # 1. HTTPException 4xx → 客户端错误,丢
    try:
        from fastapi import HTTPException
    except ImportError:
        HTTPException = None  # type: ignore

    if HTTPException is not None and isinstance(exc_value, HTTPException):
        status = getattr(exc_value, "status_code", 500)
        if 400 <= status < 500:
            return None  # 用户输入错,不算 bug
        # 5xx 走 fal 检测兜底,然后保留

    # 2. fal 上游瞬时错误(已重试 + 已 graceful 降级)→ 丢
    if _is_fal_transient(exc_value):
        return None

    # 3. 其他都保留
    return event
