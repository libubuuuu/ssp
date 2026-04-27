"""Sentry before_send 过滤测试(隐藏雷 #3)"""
import pytest
from fastapi import HTTPException

from app.services.sentry_filter import before_send, _is_fal_transient


def _hint(exc):
    """构造 Sentry hint dict,exc 直接当作 exc_value 塞进 exc_info 元组"""
    return {"exc_info": (type(exc), exc, None)}


# === 4xx HTTPException → 丢 ===

def test_4xx_http_exception_dropped():
    for status in [400, 401, 402, 403, 404, 422, 429, 451, 499]:
        e = HTTPException(status_code=status, detail="x")
        assert before_send({"event": "x"}, _hint(e)) is None, f"{status} 应被过滤"


def test_5xx_http_exception_kept():
    e = HTTPException(status_code=500, detail="boom")
    event = {"event": "x"}
    assert before_send(event, _hint(e)) is event


def test_502_503_kept_when_not_fal():
    """5xx 没 fal 标识 → 保留(可能是我们自己服务的问题)"""
    for status in [500, 502, 503, 504]:
        e = HTTPException(status_code=status, detail="our backend died")
        event = {"event": "x"}
        assert before_send(event, _hint(e)) is event


# === fal 上游瞬时错误 → 丢 ===

def test_fal_429_dropped():
    """文本含 fal + 429 → 当 fal 限流处理"""
    e = Exception("fal-ai/kling-video/o3 returned 429 rate limit exceeded")
    assert before_send({"event": "x"}, _hint(e)) is None


def test_fal_503_dropped():
    e = Exception("fal.media gateway returned 503 service unavailable")
    assert before_send({"event": "x"}, _hint(e)) is None


def test_fal_504_dropped():
    e = Exception("fal-ai/nano-banana-2 504 gateway timeout")
    assert before_send({"event": "x"}, _hint(e)) is None


# === 不要误伤 ===

def test_non_fal_503_kept():
    """503 但不含 fal 关键词 → 保留(我们自己服务异常)"""
    e = Exception("internal database 503 service unavailable")
    event = {"event": "x"}
    assert before_send(event, _hint(e)) is event


def test_random_value_error_kept():
    """普通业务异常 → 保留"""
    e = ValueError("unexpected None")
    event = {"event": "x"}
    assert before_send(event, _hint(e)) is event


def test_no_exc_info_keeps_event():
    """没异常的 event(手动 capture_message)→ 保留"""
    event = {"event": "x"}
    assert before_send(event, {}) is event
    assert before_send(event, {"exc_info": None}) is event


# === _is_fal_transient 单元 ===

def test_is_fal_transient_unit():
    assert _is_fal_transient(Exception("fal.media 429 rate limit"))
    assert _is_fal_transient(Exception("fal-ai/kling-video service unavailable"))
    # 没 fal 关键词 — False
    assert not _is_fal_transient(Exception("our backend rate limit hit"))
    # 有 fal 但不是瞬时错(e.g. 输入错误)— False
    assert not _is_fal_transient(Exception("fal-ai invalid prompt parameter"))
    assert not _is_fal_transient(None)
