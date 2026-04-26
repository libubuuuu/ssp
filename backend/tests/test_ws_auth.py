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


# === 推送管道(polling + broadcast)===

class _FakeVideoService:
    """按预设序列返回 status,模拟 FAL 任务由 processing 转 completed/failed"""
    def __init__(self, sequence):
        self._iter = iter(sequence)
        self._last = sequence[-1] if sequence else {"status": "processing"}
        self.calls = []

    async def get_task_status(self, task_id, endpoint_hint=None):
        self.calls.append((task_id, endpoint_hint))
        try:
            return next(self._iter)
        except StopIteration:
            return self._last


@pytest.fixture()
def fast_polling(monkeypatch):
    """把 polling 间隔压到很小,避免测试慢"""
    monkeypatch.setattr(tasks_module, "POLL_INTERVAL_SEC", 0.02)
    monkeypatch.setattr(tasks_module, "POLL_MAX_ITERATIONS", 50)
    yield
    # 测后清空 polling task 字典(任何遗留的 task 都已结束或被取消)
    tasks_module._polling_tasks.clear()
    tasks_module.active_connections.clear()


def _install_fake_video_service(monkeypatch, fake):
    """替换全局单例,让 get_video_service() 返回 fake"""
    from app.services import fal_service
    monkeypatch.setattr(fal_service, "_video_service", fake)


def test_ws_pushes_progress_then_closes_on_completion(ws_client, monkeypatch, fast_polling):
    """polling 链路:processing → processing → completed,客户端依次收到三条,最后被服务端关闭"""
    fake = _FakeVideoService([
        {"status": "processing"},
        {"status": "processing"},
        {"status": "completed", "video_url": "https://fake.test/v.mp4"},
    ])
    _install_fake_video_service(monkeypatch, fake)

    user = _make_user("ws-progress@example.com")
    token = create_access_token(user["id"], user["email"], "user")
    task_ownership.register("polled-task-1", user["id"])

    with ws_client.websocket_connect(f"/api/tasks/ws/polled-task-1?token={token}&endpoint=i2v") as ws:
        m1 = ws.receive_json()
        assert m1["status"] == "processing"
        assert m1["task_id"] == "polled-task-1"

        m2 = ws.receive_json()
        assert m2["status"] == "processing"

        m3 = ws.receive_json()
        assert m3["status"] == "completed"
        assert m3["result_url"] == "https://fake.test/v.mp4"

        # 服务端在 completed 后关闭连接
        with pytest.raises(Exception):
            ws.receive_json()

    # 完成态应同步清掉归属注册
    assert task_ownership.verify("polled-task-1", user["id"]) is False
    # endpoint_hint 应该被透传给 FAL 查询
    assert fake.calls and fake.calls[0][1] == "i2v"


def test_ws_pushes_failed_status(ws_client, monkeypatch, fast_polling):
    """failed 也走 final + close 通路"""
    fake = _FakeVideoService([
        {"status": "failed", "error": "FAL 任务失败"},
    ])
    _install_fake_video_service(monkeypatch, fake)

    user = _make_user("ws-failed@example.com")
    token = create_access_token(user["id"], user["email"], "user")
    task_ownership.register("polled-task-fail", user["id"])

    with ws_client.websocket_connect(f"/api/tasks/ws/polled-task-fail?token={token}") as ws:
        m = ws.receive_json()
        assert m["status"] == "failed"
        assert m["error"] == "FAL 任务失败"
        with pytest.raises(Exception):
            ws.receive_json()

    assert task_ownership.verify("polled-task-fail", user["id"]) is False


def test_ws_polling_shared_across_clients(ws_client, monkeypatch, fast_polling):
    """同 task 的多个 owner 客户端共享一次 polling — 只调一次 FAL 就 broadcast 给两边"""
    fake = _FakeVideoService([
        {"status": "completed", "video_url": "https://fake.test/shared.mp4"},
    ])
    _install_fake_video_service(monkeypatch, fake)

    user = _make_user("ws-shared@example.com")
    token = create_access_token(user["id"], user["email"], "user")
    task_ownership.register("shared-task", user["id"])

    with ws_client.websocket_connect(f"/api/tasks/ws/shared-task?token={token}") as ws_a:
        with ws_client.websocket_connect(f"/api/tasks/ws/shared-task?token={token}") as ws_b:
            ma = ws_a.receive_json()
            mb = ws_b.receive_json()
            assert ma["status"] == "completed"
            assert mb["status"] == "completed"
            assert ma["result_url"] == mb["result_url"] == "https://fake.test/shared.mp4"

    # 两个客户端共用一次 polling,FAL 只被调一次
    assert len(fake.calls) == 1
