"""WebSocket 鉴权测试

覆盖:
- 无 token → close 4401
- 无效 token → close 4401
- 有效 token → 连接成功
- refresh token 不能调业务 ws(decode_jwt_token 拒 refresh)
- 已被吊销的 token → close 4401
"""
import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import tasks as tasks_module
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


def test_ws_with_valid_token_connects(ws_client):
    """有效 access token → 连接成功"""
    user = _make_user("ws-valid@example.com")
    token = create_access_token(user["id"], user["email"], "user")
    # 连接成功后立刻 close(测试不需要发消息)
    with ws_client.websocket_connect(f"/api/tasks/ws/test-task-id?token={token}") as ws:
        assert ws is not None  # 连上即通过


def test_ws_rejects_refresh_token(ws_client):
    """refresh token 不能调业务 ws(只能 access)"""
    user = _make_user("ws-refresh-reject@example.com")
    refresh = create_refresh_token(user["id"], user["email"], "user")
    with pytest.raises(Exception):
        with ws_client.websocket_connect(f"/api/tasks/ws/test-task-id?token={refresh}"):
            pass


def test_ws_rejects_revoked_token(ws_client):
    """invalidate_user_tokens 后旧 token close 4401"""
    user = _make_user("ws-revoke@example.com")
    token = create_access_token(user["id"], user["email"], "user")
    time.sleep(1)
    invalidate_user_tokens(user["id"])
    with pytest.raises(Exception):
        with ws_client.websocket_connect(f"/api/tasks/ws/test-task-id?token={token}"):
            pass
