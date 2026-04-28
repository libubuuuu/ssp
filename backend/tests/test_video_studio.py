"""
长视频工作台 /batch-generate 计费测试

关注点:
- 鉴权 + 归属(403)
- 余额不足 402,不扣费
- happy path 全段提交成功 → 扣 N × 15
- 部分 fal submit 失败 → 失败段返还 + 实扣 = (N - failed) × 15
- 全段 submit 失败 → 全额返还 actual_cost = 0
- 返 cost 字段供前端 sidebar 同步
- 写消费记录(generation_history)

不测:
- async 任务后续失败的返还(那是 batch-status / merge 阶段的事,留下次)
- /upload /split /merge 路径(本次只关心计费收口)
"""
from unittest.mock import patch, AsyncMock
import pytest


@pytest.fixture()
def app_with_studio(app):
    """在共用 app 上注册 video_studio 路由"""
    from app.api import video_studio as studio_module
    if not any("studio" in str(r.path) for r in app.routes):
        app.include_router(studio_module.router, prefix="/api/studio")
    return app


@pytest.fixture()
def client_st(app_with_studio):
    from fastapi.testclient import TestClient
    return TestClient(app_with_studio)


@pytest.fixture(autouse=True)
def reset_studio_tasks():
    """每个测试清空 studio 内存 state"""
    from app.api import video_studio as studio_module
    studio_module.STUDIO_TASKS.clear()
    yield
    studio_module.STUDIO_TASKS.clear()


def _seed_session(user_id: int, n_segments: int = 3) -> str:
    """在 STUDIO_TASKS 里塞一个 session,返回 session_id"""
    from app.api import video_studio as studio_module
    sid = f"test-session-{user_id}-{n_segments}"
    studio_module.STUDIO_TASKS[sid] = {
        "user_id": str(user_id),
        "segments": [
            {"index": i, "fal_url": f"https://fal.media/seg{i}.mp4", "duration": 5.0}
            for i in range(n_segments)
        ],
        "status": "split_done",
    }
    return sid


def _payload(session_id: str, mode: str = "o3") -> dict:
    return {
        "session_id": session_id,
        "segments": [],  # 后端用 task["segments"],req.segments 在当前实现里没用
        "elements": [
            {
                "name": "模特A",
                "main_image_url": "https://fal.media/model.jpg",
                "reference_image_urls": [],
            }
        ],
        "mode": mode,
    }


# ==================== 鉴权 / 归属 ====================


def test_batch_generate_unauthenticated_rejected(client_st):
    r = client_st.post("/api/studio/batch-generate", json=_payload("any"))
    assert r.status_code == 401


def test_batch_generate_other_user_session_403(client_st, register, auth_header, set_credits):
    token_a, user_a = register(client_st, "studio-a@example.com")
    token_b, user_b = register(client_st, "studio-b@example.com")
    set_credits(user_b["id"], 1000)

    sid = _seed_session(user_a["id"], 3)
    r = client_st.post(
        "/api/studio/batch-generate",
        json=_payload(sid),
        headers=auth_header(token_b),
    )
    assert r.status_code == 403


def test_batch_generate_session_not_found(client_st, register, auth_header, set_credits):
    token, user = register(client_st, "studio-c@example.com")
    set_credits(user["id"], 1000)

    r = client_st.post(
        "/api/studio/batch-generate",
        json=_payload("does-not-exist"),
        headers=auth_header(token),
    )
    assert r.status_code == 404


# ==================== 计费 ====================


def test_batch_generate_insufficient_credits_402(client_st, register, auth_header, set_credits):
    """余额 < N × 15 → 402,不扣费"""
    token, user = register(client_st, "studio-d@example.com")
    set_credits(user["id"], 30)  # 不够 3 × 15 = 45

    sid = _seed_session(user["id"], 3)
    r = client_st.post(
        "/api/studio/batch-generate",
        json=_payload(sid),
        headers=auth_header(token),
    )
    assert r.status_code == 402

    me = client_st.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 30  # 完全没动


def test_batch_generate_happy_path_deducts_full(client_st, register, auth_header, set_credits):
    """3 段全 submit 成功 → 扣 45,返 cost=45"""
    token, user = register(client_st, "studio-e@example.com")
    set_credits(user["id"], 100)

    sid = _seed_session(user["id"], 3)

    fake_ok = {"task_id": "fal-task-x", "endpoint_tag": "edit-o3", "status": "pending"}
    with patch(
        "app.api.video_studio.get_video_service"
    ) as mock_svc_factory:
        mock_svc = mock_svc_factory.return_value
        mock_svc._generate_video = AsyncMock(return_value=fake_ok)
        r = client_st.post(
            "/api/studio/batch-generate",
            json=_payload(sid),
            headers=auth_header(token),
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cost"] == 45  # 3 × 15
    assert body["submit_failed"] == 0
    assert body["total"] == 3

    me = client_st.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 55  # 100 - 45


def test_batch_generate_partial_failure_refunds_failed(client_st, register, auth_header, set_credits):
    """3 段中 1 段 submit 失败 → 退 15,实扣 30"""
    token, user = register(client_st, "studio-f@example.com")
    set_credits(user["id"], 100)

    sid = _seed_session(user["id"], 3)

    # 3 次调用:第二次返 error,其余 ok
    call_count = {"n": 0}
    async def fake_generate(model_key, args):
        call_count["n"] += 1
        if call_count["n"] == 2:
            return {"error": "circuit breaker open"}
        return {"task_id": f"t{call_count['n']}", "endpoint_tag": "edit-o3", "status": "pending"}

    with patch("app.api.video_studio.get_video_service") as mock_svc_factory:
        mock_svc = mock_svc_factory.return_value
        mock_svc._generate_video = AsyncMock(side_effect=fake_generate)
        r = client_st.post(
            "/api/studio/batch-generate",
            json=_payload(sid),
            headers=auth_header(token),
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cost"] == 30  # 2 × 15(1 段返了)
    assert body["submit_failed"] == 1

    me = client_st.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 70  # 100 - 30


def test_batch_generate_all_failed_full_refund(client_st, register, auth_header, set_credits):
    """全 submit 失败 → 全退,实扣 0"""
    token, user = register(client_st, "studio-g@example.com")
    set_credits(user["id"], 100)

    sid = _seed_session(user["id"], 3)

    with patch("app.api.video_studio.get_video_service") as mock_svc_factory:
        mock_svc = mock_svc_factory.return_value
        mock_svc._generate_video = AsyncMock(return_value={"error": "fal down"})
        r = client_st.post(
            "/api/studio/batch-generate",
            json=_payload(sid),
            headers=auth_header(token),
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cost"] == 0
    assert body["submit_failed"] == 3

    me = client_st.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 100  # 完全退回


def test_batch_generate_writes_generation_history(client_st, register, auth_header, set_credits):
    """实扣 > 0 时应写 generation_history"""
    token, user = register(client_st, "studio-h@example.com")
    set_credits(user["id"], 100)

    sid = _seed_session(user["id"], 2)

    fake_ok = {"task_id": "x", "endpoint_tag": "edit-o3", "status": "pending"}
    with patch("app.api.video_studio.get_video_service") as mock_svc_factory:
        mock_svc = mock_svc_factory.return_value
        mock_svc._generate_video = AsyncMock(return_value=fake_ok)
        r = client_st.post(
            "/api/studio/batch-generate",
            json=_payload(sid),
            headers=auth_header(token),
        )
    assert r.status_code == 200

    from app.database import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT module, cost FROM generation_history WHERE user_id = ?",
            (user["id"],),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "video/replace/element"
    assert rows[0][1] == 30  # 2 × 15
