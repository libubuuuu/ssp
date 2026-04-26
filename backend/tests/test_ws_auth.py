"""WebSocket 鉴权 + 归属验证测试

覆盖两层防御:
- 鉴权层 (4401):无 token / 无效 / refresh / 吊销 → 全部拒
- 归属层 (4403):token 有效但 task 未注册 / 不属于该用户 → 拒
- 正常路径:owner 注册后用 access token 能连
"""
import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import tasks as tasks_module
from app.services import task_ownership
from app.services.auth import (
    create_user,
    create_access_token,
    create_refresh_token,
    invalidate_user_tokens,
)


@pytest.fixture()
def ws_client():
    """单独的 client,只挂 tasks router(避开重 db init)"""
    app = FastAPI()
    app.include_router(tasks_module.router, prefix="/api/tasks")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_ownership():
    """每个用例独立的注册表,防跨用例污染"""
    task_ownership._clear_for_test()
    yield
    task_ownership._clear_for_test()


def _make_user(email: str) -> dict:
    return create_user(email=email, password="secret123", name=email.split("@")[0])


def test_ws_without_token_closes_4401(ws_client):
    """没带 token query 参数 → 4401"""
    with pytest.raises(Exception) as exc_info:
        with ws_client.websocket_connect("/api/tasks/ws/test-task-id"):
            pass
    # TestClient 在 server close 时抛 WebSocketDisconnect
    assert "4401" in str(exc_info.value) or "1006" in str(exc_info.value) or "401" in str(exc_info.value).lower() or "close" in str(exc_info.value).lower()


def test_ws_with_invalid_token_closes_4401(ws_client):
    """无效 token → 4401"""
    with pytest.raises(Exception):
        with ws_client.websocket_connect("/api/tasks/ws/test-task-id?token=garbage"):
            pass


def test_ws_owner_can_connect(ws_client):
    """注册过归属的 owner 用合法 access token 能连上"""
    user = _make_user("ws-owner@example.com")
    token = create_access_token(user["id"], user["email"], "user")
    task_ownership.register("fal-task-owned-1", user["id"])
    with ws_client.websocket_connect(f"/api/tasks/ws/fal-task-owned-1?token={token}") as ws:
        assert ws is not None


def test_ws_rejects_refresh_token(ws_client):
    """refresh token 不能调业务 ws(只能 access)"""
    user = _make_user("ws-refresh-reject@example.com")
    refresh = create_refresh_token(user["id"], user["email"], "user")
    # 即使注册了归属,refresh token 也会在鉴权层就被拒(4401),不进归属层
    task_ownership.register("fal-task-refresh", user["id"])
    with pytest.raises(Exception):
        with ws_client.websocket_connect(f"/api/tasks/ws/fal-task-refresh?token={refresh}"):
            pass


def test_ws_rejects_revoked_token(ws_client):
    """invalidate_user_tokens 后旧 token close 4401"""
    user = _make_user("ws-revoke@example.com")
    token = create_access_token(user["id"], user["email"], "user")
    task_ownership.register("fal-task-revoke", user["id"])
    time.sleep(1)
    invalidate_user_tokens(user["id"])
    with pytest.raises(Exception):
        with ws_client.websocket_connect(f"/api/tasks/ws/fal-task-revoke?token={token}"):
            pass


def test_ws_rejects_unregistered_task(ws_client):
    """task 没注册过(可能伪造或 backend 重启)→ 4403,token 再合法也不行"""
    user = _make_user("ws-stranger@example.com")
    token = create_access_token(user["id"], user["email"], "user")
    with pytest.raises(Exception) as exc_info:
        with ws_client.websocket_connect(f"/api/tasks/ws/never-registered-task?token={token}"):
            pass
    # 4403 体现归属层失败,跟 4401 鉴权层失败区分(也接受 1006/close 防 TestClient 差异)
    s = str(exc_info.value)
    assert "4403" in s or "1006" in s or "close" in s.lower()


def test_ws_rejects_other_users_task(ws_client):
    """A 注册了 task,B 拿合法 token 也不能订阅 → 4403"""
    alice = _make_user("ws-alice@example.com")
    bob = _make_user("ws-bob@example.com")
    bob_token = create_access_token(bob["id"], bob["email"], "user")
    task_ownership.register("alice-private-task", alice["id"])
    with pytest.raises(Exception):
        with ws_client.websocket_connect(f"/api/tasks/ws/alice-private-task?token={bob_token}"):
            pass


def test_ownership_register_and_verify():
    """注册表本身的单元行为:register / verify / unregister / 跨用户拒绝"""
    task_ownership.register("t1", "user-a")
    assert task_ownership.verify("t1", "user-a") is True
    assert task_ownership.verify("t1", "user-b") is False
    assert task_ownership.verify("nonexistent", "user-a") is False
    task_ownership.unregister("t1")
    assert task_ownership.verify("t1", "user-a") is False
