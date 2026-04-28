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
import os
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


# ==================== /batch-status async 退款 ====================


def _seed_after_submit(client_st, register_fn, auth_header_fn, set_credits_fn, email: str, n: int = 3):
    """登录 + 充值 + submit 一次,留下 batch_results,后续测试拿来跑 batch-status"""
    token, user = register_fn(client_st, email)
    set_credits_fn(user["id"], 1000)
    sid = _seed_session(user["id"], n)
    fake_ok = {"task_id": "fal-x", "endpoint_tag": "edit-o3", "status": "pending"}
    with patch("app.api.video_studio.get_video_service") as mock_svc_factory:
        mock_svc = mock_svc_factory.return_value
        mock_svc._generate_video = AsyncMock(return_value=fake_ok)
        r = client_st.post(
            "/api/studio/batch-generate",
            json=_payload(sid),
            headers=auth_header_fn(token),
        )
    assert r.status_code == 200
    return token, user, sid


def test_batch_status_async_failure_refunds(client_st, register, auth_header, set_credits):
    """fal 接了 3 段,async 阶段 1 段挂掉 → /batch-status 应自动退 15"""
    token, user, sid = _seed_after_submit(client_st, register, auth_header, set_credits, "studio-async-a@example.com", 3)

    me_after_submit = client_st.get("/api/auth/me", headers=auth_header(token)).json()
    assert me_after_submit["credits"] == 1000 - 45  # 3 × 15 已扣

    # 模拟 poll:第一段 completed,第二段 failed (async 挂),第三段 still processing
    call_count = {"n": 0}
    async def fake_status(task_id, endpoint_hint=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"status": "completed", "video_url": "https://fal.media/done.mp4"}
        if call_count["n"] == 2:
            return {"status": "failed", "error": "fal internal"}
        return {"status": "processing"}

    with patch("app.api.video_studio.get_video_service") as mock_svc_factory:
        mock_svc = mock_svc_factory.return_value
        mock_svc.get_task_status = AsyncMock(side_effect=fake_status)
        r = client_st.get(f"/api/studio/batch-status/{sid}", headers=auth_header(token))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["completed"] == 1
    assert body["failed"] == 1
    assert body["processing"] == 1
    assert body["refunded_this_call"] == 15

    me = client_st.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 1000 - 45 + 15  # 退一段


def test_batch_status_no_double_refund_on_repoll(client_st, register, auth_header, set_credits):
    """同一段连续 poll 两次都 failed,只退一次"""
    token, user, sid = _seed_after_submit(client_st, register, auth_header, set_credits, "studio-async-b@example.com", 2)

    async def always_failed(task_id, endpoint_hint=None):
        return {"status": "failed", "error": "x"}

    # 第一次 poll
    with patch("app.api.video_studio.get_video_service") as mock_svc_factory:
        mock_svc = mock_svc_factory.return_value
        mock_svc.get_task_status = AsyncMock(side_effect=always_failed)
        r1 = client_st.get(f"/api/studio/batch-status/{sid}", headers=auth_header(token))
    assert r1.status_code == 200
    assert r1.json()["refunded_this_call"] == 30  # 2 段 × 15

    me1 = client_st.get("/api/auth/me", headers=auth_header(token)).json()
    assert me1["credits"] == 1000 - 30 + 30  # 全退 = 没扣

    # 第二次 poll 同 session — 不应再退
    with patch("app.api.video_studio.get_video_service") as mock_svc_factory:
        mock_svc = mock_svc_factory.return_value
        mock_svc.get_task_status = AsyncMock(side_effect=always_failed)
        r2 = client_st.get(f"/api/studio/batch-status/{sid}", headers=auth_header(token))
    assert r2.status_code == 200
    assert r2.json()["refunded_this_call"] == 0  # 已退过

    me2 = client_st.get("/api/auth/me", headers=auth_header(token)).json()
    assert me2["credits"] == me1["credits"]  # 不变


def test_batch_status_submit_failed_segments_not_double_refunded(client_st, register, auth_header, set_credits):
    """/batch-generate 时 submit 失败的段,/batch-status 不应再退一次(refunded 标记保护)"""
    token, user = register(client_st, "studio-async-c@example.com")
    set_credits(user["id"], 1000)
    sid = _seed_session(user["id"], 3)

    # submit 时 1 段失败 — 这段在 batch-generate 里已经退过
    call_count = {"n": 0}
    async def fake_generate(model_key, args):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"error": "fal down at submit"}
        return {"task_id": f"t{call_count['n']}", "endpoint_tag": "edit-o3", "status": "pending"}

    with patch("app.api.video_studio.get_video_service") as mock_svc_factory:
        mock_svc = mock_svc_factory.return_value
        mock_svc._generate_video = AsyncMock(side_effect=fake_generate)
        r_gen = client_st.post(
            "/api/studio/batch-generate",
            json=_payload(sid),
            headers=auth_header(token),
        )
    assert r_gen.json()["cost"] == 30  # 2 × 15 (1 段已退)
    me_after_submit = client_st.get("/api/auth/me", headers=auth_header(token)).json()
    assert me_after_submit["credits"] == 1000 - 30  # 已退过 1 段

    # poll:剩下 2 段都 completed
    async def fake_status(task_id, endpoint_hint=None):
        return {"status": "completed", "video_url": "https://fal.media/x.mp4"}

    with patch("app.api.video_studio.get_video_service") as mock_svc_factory:
        mock_svc = mock_svc_factory.return_value
        mock_svc.get_task_status = AsyncMock(side_effect=fake_status)
        r_poll = client_st.get(f"/api/studio/batch-status/{sid}", headers=auth_header(token))

    body = r_poll.json()
    assert body["completed"] == 2
    assert body["failed"] == 1  # submit 失败那段还是 failed
    assert body["refunded_this_call"] == 0  # 关键:不应重复退

    me = client_st.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 1000 - 30  # 不变,没多退


# ==================== /upload-chunk size 守卫 ====================


def test_upload_chunk_oversized_chunk_413(client_st, register, auth_header):
    """单 chunk > 10MB → 413,且部分文件被清掉(不留磁盘垃圾)"""
    token, _ = register(client_st, "chunk-oversized@example.com")
    big = b"x" * (11 * 1024 * 1024)  # 11MB,超 10MB 上限
    r = client_st.post(
        "/api/studio/upload-chunk",
        headers=auth_header(token),
        files={"chunk": ("part.bin", big, "application/octet-stream")},
        data={
            "upload_id": "0123456789abcdef",
            "chunk_idx": "0",
            "total_chunks": "5",
            "filename": "x.mp4",
        },
    )
    assert r.status_code == 413
    assert "10MB" in r.json()["detail"]

    # 部分文件应已清:_uploading/{user}_{upload_id}/000000 不应存在
    from app.api.video_studio import UPLOAD_TMP_DIR
    found = list(UPLOAD_TMP_DIR.glob("*_0123456789abcdef/000000"))
    assert not found, f"残留垃圾文件:{found}"


def test_upload_chunk_at_size_limit_ok(client_st, register, auth_header):
    """正好 10MB 应该通过(只有 > 才挡)"""
    token, _ = register(client_st, "chunk-limit@example.com")
    exact = b"x" * (10 * 1024 * 1024)
    r = client_st.post(
        "/api/studio/upload-chunk",
        headers=auth_header(token),
        files={"chunk": ("part.bin", exact, "application/octet-stream")},
        data={
            "upload_id": "fedcba9876543210",
            "chunk_idx": "0",
            "total_chunks": "5",
            "filename": "x.mp4",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["received_bytes"] == 10 * 1024 * 1024


def test_upload_chunk_parallel_uploads_429(client_st, register, auth_header):
    """同 user 并行 upload_id > 5 → 429"""
    token, user = register(client_st, "chunk-parallel@example.com")
    user_id = str(user["id"])

    # 手动塞 5 个 upload_dir(模拟已发起 5 个 upload),不用真上传
    from app.api.video_studio import UPLOAD_TMP_DIR
    for i in range(5):
        (UPLOAD_TMP_DIR / f"{user_id}_{i:016x}").mkdir(parents=True, exist_ok=True)

    # 第 6 个 upload_id 应被拒
    r = client_st.post(
        "/api/studio/upload-chunk",
        headers=auth_header(token),
        files={"chunk": ("part.bin", b"x" * 1024, "application/octet-stream")},
        data={
            "upload_id": "ffffffffffffffff",
            "chunk_idx": "0",
            "total_chunks": "5",
            "filename": "x.mp4",
        },
    )
    assert r.status_code == 429
    assert "并行" in r.json()["detail"]

    # 清理:手动收尾防污染下一个测试
    import shutil
    for i in range(5):
        shutil.rmtree(UPLOAD_TMP_DIR / f"{user_id}_{i:016x}", ignore_errors=True)


def test_upload_chunk_continuation_doesnt_count_as_new(client_st, register, auth_header):
    """已存在的 upload_dir 续传不算新建,即使已有 5 个仍能续"""
    token, user = register(client_st, "chunk-continue@example.com")
    user_id = str(user["id"])

    from app.api.video_studio import UPLOAD_TMP_DIR
    same_id = "abcdef0123456789"
    target = UPLOAD_TMP_DIR / f"{user_id}_{same_id}"
    target.mkdir(parents=True, exist_ok=True)
    # 再塞 4 个 dummy upload(凑满 5)
    for i in range(4):
        (UPLOAD_TMP_DIR / f"{user_id}_{i:016x}").mkdir(parents=True, exist_ok=True)

    # 续传同 upload_id 应通过(不算新建)
    r = client_st.post(
        "/api/studio/upload-chunk",
        headers=auth_header(token),
        files={"chunk": ("part.bin", b"x" * 1024, "application/octet-stream")},
        data={
            "upload_id": same_id,
            "chunk_idx": "1",
            "total_chunks": "5",
            "filename": "x.mp4",
        },
    )
    assert r.status_code == 200, r.text

    # 清理
    import shutil
    shutil.rmtree(target, ignore_errors=True)
    for i in range(4):
        shutil.rmtree(UPLOAD_TMP_DIR / f"{user_id}_{i:016x}", ignore_errors=True)


def test_clean_stale_uploads_removes_old_dirs(tmp_path, monkeypatch):
    """GC: 超 24h 的老目录被删,新目录保留"""
    import time
    from app.api import video_studio as studio_mod

    # 用临时 UPLOAD_TMP_DIR 隔离测试
    fake_tmp = tmp_path / "_uploading"
    fake_tmp.mkdir()
    monkeypatch.setattr(studio_mod, "UPLOAD_TMP_DIR", fake_tmp)

    old = fake_tmp / "1_oldupload"
    old.mkdir()
    (old / "000000").write_bytes(b"x" * 1024)
    # 设 mtime 到 25h 前
    old_ts = time.time() - 25 * 3600
    os.utime(old, (old_ts, old_ts))

    fresh = fake_tmp / "2_freshupload"
    fresh.mkdir()
    (fresh / "000000").write_bytes(b"x" * 512)

    res = studio_mod.clean_stale_uploads(hours=24)
    assert res["scanned"] == 2
    assert res["deleted"] == 1
    assert res["freed_bytes"] >= 1024
    assert not old.exists()
    assert fresh.exists()
