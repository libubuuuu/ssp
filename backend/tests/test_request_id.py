"""trace_id 中间件测试

覆盖:
- 每个响应都有 X-Request-ID 头
- 不同请求 trace_id 不同
- 上游传入的 X-Request-ID 被复用(链路追踪连贯性)
- 过长的上游 X-Request-ID 被丢弃,改成自己生成的
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.request_id import RequestIdMiddleware


@pytest.fixture()
def mini_app():
    """专门为 middleware 测试搭一个最小 app(不依赖业务路由)"""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ping")
    async def ping(request: Request):
        return {"trace_id": request.state.trace_id}

    return TestClient(app)


def test_response_has_request_id_header(mini_app):
    r = mini_app.get("/ping")
    assert r.status_code == 200
    rid = r.headers.get("X-Request-ID")
    assert rid is not None
    assert 1 <= len(rid) <= 64


def test_different_requests_get_different_trace_ids(mini_app):
    r1 = mini_app.get("/ping")
    r2 = mini_app.get("/ping")
    assert r1.headers["X-Request-ID"] != r2.headers["X-Request-ID"]


def test_upstream_request_id_is_reused(mini_app):
    """上游网关传入 X-Request-ID 的应该被原样保留,串成链路"""
    r = mini_app.get("/ping", headers={"X-Request-ID": "upstream-abc-123"})
    assert r.headers["X-Request-ID"] == "upstream-abc-123"


def test_upstream_too_long_is_replaced(mini_app):
    """上游传 64+ 字符的视为污染,丢弃改用自己生成的"""
    long = "x" * 200
    r = mini_app.get("/ping", headers={"X-Request-ID": long})
    rid = r.headers["X-Request-ID"]
    assert rid != long
    assert len(rid) <= 64


def test_trace_id_accessible_in_request_state(mini_app):
    """端点内能从 request.state.trace_id 取到"""
    r = mini_app.get("/ping")
    body_trace = r.json()["trace_id"]
    header_trace = r.headers["X-Request-ID"]
    assert body_trace == header_trace
