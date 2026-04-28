"""七十六续:长视频工作台模型可切换架构测试

覆盖:
- 默认行为不变(env 全空 → DEFAULT_ENDPOINTS)
- 单 mode env 覆盖只动该 mode
- OVERRIDE 同时覆盖 edit + edit-o3
- 未知 model_key 报错
- fallback:override submit 抛异常后,fal_client.submit_async 会被再调用一次(默认 endpoint),且 record_failure 在 override key 上累计
- admin /studio-model-status 端点返回结构
"""
from unittest.mock import patch, AsyncMock, MagicMock
import pytest


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """每个测试清 lru_cache,避免 env 改了取到旧值"""
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """每个测试给一个干净的 circuit breaker,避免上轮失败串污染"""
    from app.services import circuit_breaker as cb_mod
    cb_mod._circuit_breaker = None
    yield
    cb_mod._circuit_breaker = None


def _make_service():
    from app.services.fal_service import FalVideoService
    return FalVideoService(fal_key="test")


# ==================== _resolve_endpoint ====================


def test_resolve_default_when_env_empty(monkeypatch):
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_EDIT", "")
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_EDIT_O3", "")
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_OVERRIDE", "")
    svc = _make_service()
    ep, src = svc._resolve_endpoint("kling/edit")
    assert ep == "fal-ai/kling-video/o1/video-to-video/edit"
    assert src == "default"
    ep_o3, src_o3 = svc._resolve_endpoint("kling/edit-o3")
    assert ep_o3 == "fal-ai/kling-video/o3/pro/video-to-video/edit"
    assert src_o3 == "default"


def test_resolve_env_edit_only_affects_edit_mode(monkeypatch):
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_EDIT", "fal-ai/custom/edit-test")
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_EDIT_O3", "")
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_OVERRIDE", "")
    svc = _make_service()
    assert svc._resolve_endpoint("kling/edit") == ("fal-ai/custom/edit-test", "env_edit")
    # o3 mode 应该仍是默认,不被波及
    assert svc._resolve_endpoint("kling/edit-o3")[1] == "default"


def test_resolve_env_edit_o3_only_affects_o3_mode(monkeypatch):
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_EDIT", "")
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_EDIT_O3", "fal-ai/custom/o3-test")
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_OVERRIDE", "")
    svc = _make_service()
    assert svc._resolve_endpoint("kling/edit-o3") == ("fal-ai/custom/o3-test", "env_edit_o3")
    assert svc._resolve_endpoint("kling/edit")[1] == "default"


def test_resolve_override_wins_over_env_for_both_modes(monkeypatch):
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_EDIT", "fal-ai/custom/edit-test")
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_EDIT_O3", "fal-ai/custom/o3-test")
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_OVERRIDE", "fal-ai/custom/override-test")
    svc = _make_service()
    assert svc._resolve_endpoint("kling/edit") == ("fal-ai/custom/override-test", "override")
    assert svc._resolve_endpoint("kling/edit-o3") == ("fal-ai/custom/override-test", "override")


def test_resolve_unknown_key_returns_none(monkeypatch):
    svc = _make_service()
    ep, src = svc._resolve_endpoint("kling/nonexistent")
    assert ep is None
    assert src == "default"


# ==================== _generate_video fallback ====================


@pytest.mark.asyncio
async def test_generate_default_path_when_no_override(monkeypatch):
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_EDIT", "")
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_OVERRIDE", "")
    svc = _make_service()

    fake_handler = MagicMock()
    fake_handler.request_id = "task-default-123"
    submit_mock = AsyncMock(return_value=fake_handler)
    with patch("app.services.fal_service.fal_client.submit_async", submit_mock):
        r = await svc._generate_video("kling/edit", {"video_url": "x"})
    assert r["task_id"] == "task-default-123"
    assert r["model"] == "fal-ai/kling-video/o1/video-to-video/edit"
    assert r["model_source"] == "default"
    submit_mock.assert_called_once()


@pytest.mark.asyncio
async def test_generate_override_path_succeeds(monkeypatch):
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_OVERRIDE", "fal-ai/new-model/v1")
    svc = _make_service()

    fake_handler = MagicMock()
    fake_handler.request_id = "task-override-456"
    submit_mock = AsyncMock(return_value=fake_handler)
    with patch("app.services.fal_service.fal_client.submit_async", submit_mock):
        r = await svc._generate_video("kling/edit", {"video_url": "x"})
    assert r["model"] == "fal-ai/new-model/v1"
    assert r["model_source"] == "override"
    # 单次成功,只调一次 fal,不应该走 fallback
    submit_mock.assert_called_once()


@pytest.mark.asyncio
async def test_generate_override_fails_falls_back_to_default(monkeypatch):
    """override endpoint 抛异常,自动回退默认 endpoint 再试一次"""
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_OVERRIDE", "fal-ai/broken-model/v1")
    svc = _make_service()

    call_endpoints = []

    async def fake_submit(endpoint, arguments=None):
        call_endpoints.append(endpoint)
        if endpoint == "fal-ai/broken-model/v1":
            raise RuntimeError("override boom")
        # 默认 endpoint 成功
        h = MagicMock()
        h.request_id = "task-fallback-789"
        return h

    with patch("app.services.fal_service.fal_client.submit_async", side_effect=fake_submit):
        r = await svc._generate_video("kling/edit", {"video_url": "x"})

    # 应当先调 override,失败后回退默认 endpoint
    assert call_endpoints == [
        "fal-ai/broken-model/v1",
        "fal-ai/kling-video/o1/video-to-video/edit",
    ]
    assert r["task_id"] == "task-fallback-789"
    assert r["model_source"] == "default_after_fallback"


@pytest.mark.asyncio
async def test_generate_override_circuit_open_skips_to_default(monkeypatch):
    """override 已熔断时不再尝试,直接默认 endpoint"""
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_OVERRIDE", "fal-ai/cooked-model/v1")
    svc = _make_service()

    # 预先把 override key 弄成 open 状态
    from app.services.circuit_breaker import get_circuit_breaker
    cb = get_circuit_breaker()
    state = cb._get_state("override:fal-ai/cooked-model/v1")
    state["state"] = "open"
    from datetime import datetime
    state["last_failure"] = datetime.now()  # 防止 reset_timeout 立即半开

    fake_handler = MagicMock()
    fake_handler.request_id = "task-skipped-001"
    submit_mock = AsyncMock(return_value=fake_handler)
    with patch("app.services.fal_service.fal_client.submit_async", submit_mock):
        r = await svc._generate_video("kling/edit", {"video_url": "x"})

    # 应当只调用一次,且是默认 endpoint
    submit_mock.assert_called_once()
    args, kwargs = submit_mock.call_args
    assert args[0] == "fal-ai/kling-video/o1/video-to-video/edit"
    assert r["model_source"] == "default_after_fallback"


# ==================== admin /studio-model-status ====================


def test_admin_studio_model_status_default(monkeypatch, client, register, auth_header, set_role):
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_EDIT", "")
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_EDIT_O3", "")
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_OVERRIDE", "")
    from app.config import get_settings
    get_settings.cache_clear()

    # 防御:确保 STUDIO_TASKS 干净(其他 test 文件 / fixture 可能留残)
    from app.api import video_studio as studio_mod
    studio_mod.STUDIO_TASKS.clear()

    token, user = register(client, "studio_admin1@x.com")
    set_role(user["id"], "admin")

    r = client.get("/api/admin/studio-model-status", headers=auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config"]["STUDIO_VIDEO_MODEL_OVERRIDE"] == ""
    assert body["resolved"]["kling/edit"]["source"] == "default"
    assert body["resolved"]["kling/edit-o3"]["source"] == "default"
    assert body["batch_stats"]["total_segments"] == 0
    assert body["batch_stats"]["success_rate"] is None


def test_admin_studio_model_status_with_override_and_batch_data(
    monkeypatch, client, register, auth_header, set_role
):
    monkeypatch.setenv("STUDIO_VIDEO_MODEL_OVERRIDE", "fal-ai/new-model/v1")
    from app.config import get_settings
    get_settings.cache_clear()

    from app.api import video_studio as studio_mod
    studio_mod.STUDIO_TASKS.clear()
    studio_mod.STUDIO_TASKS["sess-x"] = {
        "user_id": "1",
        "batch_results": [
            {"status": "completed"},
            {"status": "completed"},
            {"status": "failed", "error": "fal timeout"},
            {"status": "failed", "error": "fal timeout"},
            {"status": "pending"},
        ],
    }

    token, user = register(client, "studio_admin2@x.com")
    set_role(user["id"], "admin")

    r = client.get("/api/admin/studio-model-status", headers=auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config"]["STUDIO_VIDEO_MODEL_OVERRIDE"] == "fal-ai/new-model/v1"
    assert body["resolved"]["kling/edit"]["endpoint"] == "fal-ai/new-model/v1"
    assert body["resolved"]["kling/edit"]["source"] == "override"
    bs = body["batch_stats"]
    assert bs["total_segments"] == 5
    assert bs["completed"] == 2
    assert bs["failed"] == 2
    assert bs["pending_or_running"] == 1
    assert bs["success_rate"] == 0.4
    assert body["top_errors"][0] == {"error": "fal timeout", "count": 2}

    studio_mod.STUDIO_TASKS.clear()


def test_admin_studio_model_status_requires_admin(client, register, auth_header):
    """非 admin 拿不到 — 普通用户应返 403"""
    token, _user = register(client, "studio_normal@x.com")
    r = client.get("/api/admin/studio-model-status", headers=auth_header(token))
    assert r.status_code == 403


# ==================== Step 3 灰度:admin role 选 kling/reference ====================


@pytest.fixture()
def studio_client(app):
    """挂 video_studio 路由,用法同 test_video_studio.py 但隔离用同名属性避免污染"""
    from app.api import video_studio as studio_mod
    if not any(str(r.path).startswith("/api/studio/") for r in app.routes):
        app.include_router(studio_mod.router, prefix="/api/studio")
    from fastapi.testclient import TestClient
    return TestClient(app)


def _seed_studio_session(user_id: int, n: int = 1) -> str:
    from app.api import video_studio as studio_mod
    sid = f"grayscale-test-{user_id}"
    studio_mod.STUDIO_TASKS[sid] = {
        "user_id": str(user_id),
        "segments": [
            {"index": i, "fal_url": f"https://fal.media/seg{i}.mp4", "duration": 5.0}
            for i in range(n)
        ],
        "status": "split_done",
    }
    return sid


def _grayscale_payload(sid: str, mode: str) -> dict:
    return {
        "session_id": sid,
        "segments": [],
        "elements": [{
            "name": "模特A",
            "main_image_url": "https://fal.media/m.jpg",
            "reference_image_urls": [],
        }],
        "mode": mode,
    }


def _capture_model_key(studio_client, token, payload):
    """触发 batch-generate,捕获传给 _generate_video 的 model_key"""
    from unittest.mock import patch, AsyncMock
    fake_ok = {"task_id": "x", "endpoint_tag": "edit", "status": "pending"}
    with patch("app.api.video_studio.get_video_service") as factory:
        mock_svc = factory.return_value
        mock_svc._generate_video = AsyncMock(return_value=fake_ok)
        r = studio_client.post(
            "/api/studio/batch-generate",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        # 第一个 positional arg 是 model_key
        return mock_svc._generate_video.call_args.args[0]


def test_grayscale_admin_edit_mode_picks_reference(
    studio_client, register, set_role, set_credits
):
    """admin + mode=edit → 灰度到 kling/reference"""
    from app.api import video_studio as studio_mod
    studio_mod.STUDIO_TASKS.clear()
    token, user = register(studio_client, "grayscale_admin1@x.com")
    set_role(user["id"], "admin")
    set_credits(user["id"], 1000)
    sid = _seed_studio_session(user["id"], n=1)
    mk = _capture_model_key(studio_client, token, _grayscale_payload(sid, "edit"))
    assert mk == "kling/reference"
    studio_mod.STUDIO_TASKS.clear()


def test_grayscale_admin_o3_mode_keeps_o3(
    studio_client, register, set_role, set_credits
):
    """admin + mode=o3 → 保持 kling/edit-o3(中文口播不灰度)"""
    from app.api import video_studio as studio_mod
    studio_mod.STUDIO_TASKS.clear()
    token, user = register(studio_client, "grayscale_admin2@x.com")
    set_role(user["id"], "admin")
    set_credits(user["id"], 1000)
    sid = _seed_studio_session(user["id"], n=1)
    mk = _capture_model_key(studio_client, token, _grayscale_payload(sid, "o3"))
    assert mk == "kling/edit-o3"
    studio_mod.STUDIO_TASKS.clear()


def test_grayscale_normal_user_edit_mode_unchanged(
    studio_client, register, set_credits
):
    """普通用户 + mode=edit → 仍是 kling/edit(灰度不波及)"""
    from app.api import video_studio as studio_mod
    studio_mod.STUDIO_TASKS.clear()
    token, user = register(studio_client, "grayscale_user1@x.com")
    set_credits(user["id"], 1000)
    sid = _seed_studio_session(user["id"], n=1)
    mk = _capture_model_key(studio_client, token, _grayscale_payload(sid, "edit"))
    assert mk == "kling/edit"
    studio_mod.STUDIO_TASKS.clear()


def test_grayscale_normal_user_o3_mode_unchanged(
    studio_client, register, set_credits
):
    """普通用户 + mode=o3 → 仍是 kling/edit-o3"""
    from app.api import video_studio as studio_mod
    studio_mod.STUDIO_TASKS.clear()
    token, user = register(studio_client, "grayscale_user2@x.com")
    set_credits(user["id"], 1000)
    sid = _seed_studio_session(user["id"], n=1)
    mk = _capture_model_key(studio_client, token, _grayscale_payload(sid, "o3"))
    assert mk == "kling/edit-o3"
    studio_mod.STUDIO_TASKS.clear()
