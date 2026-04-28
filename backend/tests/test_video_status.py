"""/api/video/status/{task_id} 鉴权与退款集成测试

五十四续:本端点之前匿名可调,任意人猜 task_id 拿归档视频 URL = 隐私泄漏。
现在 require login + task_ownership.verify 校归属。

覆盖:
- 401:未登录
- 403:登录但非 owner
- 200:owner + status=processing(透传 fal 状态)
- 200:owner + status=failed → 触发 refund_tracker.try_refund 退款
"""
from unittest.mock import patch, AsyncMock
import pytest


@pytest.fixture()
def app_with_video(app):
    """在共用 app 上注册 video 路由"""
    from app.api import video as video_module
    if not any(str(r.path).startswith("/api/video") for r in app.routes):
        app.include_router(video_module.router, prefix="/api/video")
    return app


@pytest.fixture()
def client_v(app_with_video):
    from fastapi.testclient import TestClient
    return TestClient(app_with_video)


@pytest.fixture(autouse=True)
def _reset_state():
    """每个测试清 task_ownership + refund_tracker"""
    from app.services import task_ownership, refund_tracker
    task_ownership._clear_for_test()
    refund_tracker._clear_for_test()
    yield
    task_ownership._clear_for_test()
    refund_tracker._clear_for_test()


def test_status_unauthenticated_returns_401(client_v):
    r = client_v.get("/api/video/status/some_task_id")
    assert r.status_code == 401


def test_status_not_owner_returns_403(client_v, register, auth_header):
    """A 注册了 task,B 登录调 → 403"""
    a_token, a_user = register(client_v, "vs-owner@example.com")
    b_token, b_user = register(client_v, "vs-other@example.com")

    from app.services import task_ownership
    task_ownership.register("fal_task_a", a_user["id"])

    r = client_v.get("/api/video/status/fal_task_a", headers=auth_header(b_token))
    assert r.status_code == 403


def test_status_unregistered_task_returns_403(client_v, register, auth_header):
    """登录了但 task 没注册过(攻击者瞎猜 task_id)→ 403,不是 200 假装查 fal"""
    token, _ = register(client_v, "vs-guess@example.com")
    r = client_v.get("/api/video/status/random_guess_task", headers=auth_header(token))
    assert r.status_code == 403


def test_status_owner_pass_through_processing(client_v, register, auth_header):
    """owner 调 + status=processing → 透传 fal 状态"""
    token, user = register(client_v, "vs-pass@example.com")
    from app.services import task_ownership
    task_ownership.register("fal_task_proc", user["id"])

    async def fake_status(task_id, endpoint_hint=None):
        return {"status": "processing"}

    with patch("app.api.video.get_video_service") as mock_factory:
        mock_factory.return_value.get_task_status = AsyncMock(side_effect=fake_status)
        r = client_v.get("/api/video/status/fal_task_proc", headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["status"] == "processing"


def test_status_owner_failed_triggers_refund(client_v, register, auth_header, set_credits):
    """owner 调 + status=failed + refund_tracker 有 entry → 退款 + 余额涨"""
    token, user = register(client_v, "vs-refund@example.com")
    set_credits(user["id"], 50)
    from app.services import task_ownership, refund_tracker
    task_ownership.register("fal_task_fail", user["id"])
    refund_tracker.register("fal_task_fail", user["id"], 15)  # 模拟装饰器 register

    async def fake_status(task_id, endpoint_hint=None):
        return {"status": "failed", "error": "fal error"}

    with patch("app.api.video.get_video_service") as mock_factory:
        mock_factory.return_value.get_task_status = AsyncMock(side_effect=fake_status)
        r = client_v.get("/api/video/status/fal_task_fail", headers=auth_header(token))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body.get("refunded") == 15

    me = client_v.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 50 + 15
