"""口播带货工作台 — 七十七续 P1 骨架测试

覆盖:
- 6 端点鉴权(401)
- list 空数组
- status 不存在 404 / 跨用户 403
- start 校验:tier / 法律确认 / 模特数量 / 积分不足 / 已 start 不能再 start
- compute_charge 计费正确
- cancel 退款比例
- _step_progress 状态机映射

不涉及 fal 真实调用(P2/P3 实现后再加)。
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client_oral(app):
    from app.api import oral as oral_mod
    if not any(str(r.path).startswith("/api/oral/") for r in app.routes):
        app.include_router(oral_mod.router, prefix="/api/oral")
    return TestClient(app)


def _seed_session(user_id: str, duration: float = 30.0, status: str = "uploaded", tier: str = "economy", credits_charged: int = 0) -> str:
    """直接 INSERT 一条 oral_session,绕过 /upload(/upload 需要真实视频文件)"""
    import uuid
    from app.database import get_db
    sid = uuid.uuid4().hex[:12]
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO oral_sessions (id, user_id, tier, status, original_video_path, duration_seconds, credits_charged)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sid, str(user_id), tier, status, "/tmp/fake.mp4", duration, credits_charged),
        )
        conn.commit()
    return sid


# ==================== 鉴权 ====================


def test_oral_list_unauthenticated_401(client_oral):
    r = client_oral.get("/api/oral/list")
    assert r.status_code == 401


def test_oral_status_unauthenticated_401(client_oral):
    r = client_oral.get("/api/oral/status/anysid")
    assert r.status_code == 401


def test_oral_start_unauthenticated_401(client_oral):
    r = client_oral.post("/api/oral/start", json={
        "session_id": "x", "tier": "economy", "models": [], "products": [], "legal_consent": True,
    })
    assert r.status_code == 401


def test_oral_edit_unauthenticated_401(client_oral):
    r = client_oral.post("/api/oral/edit", json={"session_id": "x", "edited_transcript": "y"})
    assert r.status_code == 401


def test_oral_cancel_unauthenticated_401(client_oral):
    r = client_oral.post("/api/oral/cancel/x")
    assert r.status_code == 401


# ==================== list ====================


def test_oral_list_empty_for_new_user(client_oral, register, auth_header):
    token, _user = register(client_oral, "oral-list-empty@x.com")
    r = client_oral.get("/api/oral/list", headers=auth_header(token))
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_oral_list_returns_user_sessions_only(client_oral, register, auth_header):
    token_a, ua = register(client_oral, "oral-list-a@x.com")
    token_b, ub = register(client_oral, "oral-list-b@x.com")
    sid_a = _seed_session(ua["id"], duration=45.0)
    _seed_session(ub["id"], duration=10.0)

    r = client_oral.get("/api/oral/list", headers=auth_header(token_a))
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == sid_a
    assert sessions[0]["duration_seconds"] == 45.0
    assert sessions[0]["tier"] == "economy"


# ==================== status ====================


def test_oral_status_not_found_404(client_oral, register, auth_header):
    token, _user = register(client_oral, "oral-status-404@x.com")
    r = client_oral.get("/api/oral/status/nonexistent", headers=auth_header(token))
    assert r.status_code == 404


def test_oral_status_cross_user_403(client_oral, register, auth_header):
    token_a, ua = register(client_oral, "oral-cross-a@x.com")
    token_b, _ub = register(client_oral, "oral-cross-b@x.com")
    sid = _seed_session(ua["id"])
    r = client_oral.get(f"/api/oral/status/{sid}", headers=auth_header(token_b))
    assert r.status_code == 403


def test_oral_status_returns_step_progress(client_oral, register, auth_header):
    token, user = register(client_oral, "oral-status-ok@x.com")
    sid = _seed_session(user["id"], status="asr_running")
    r = client_oral.get(f"/api/oral/status/{sid}", headers=auth_header(token))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "asr_running"
    assert body["step_progress"]["step1"] == "running"
    assert body["step_progress"]["step5"] == "pending"


# ==================== start 校验 ====================


def _start_payload(sid: str, tier: str = "economy", consent: bool = True, n_models: int = 1):
    return {
        "session_id": sid,
        "tier": tier,
        "models": [{"name": f"M{i}", "image_url": "https://x/m.jpg"} for i in range(n_models)],
        "products": [],
        "legal_consent": consent,
    }


def test_oral_start_legal_consent_required(client_oral, register, auth_header, set_credits):
    token, user = register(client_oral, "oral-consent@x.com")
    set_credits(user["id"], 1000)
    sid = _seed_session(user["id"], duration=30.0)
    r = client_oral.post("/api/oral/start", json=_start_payload(sid, consent=False), headers=auth_header(token))
    assert r.status_code == 400
    assert "用户责任声明" in r.json()["detail"]


def test_oral_start_invalid_tier_400(client_oral, register, auth_header, set_credits):
    token, user = register(client_oral, "oral-tier@x.com")
    set_credits(user["id"], 1000)
    sid = _seed_session(user["id"])
    r = client_oral.post("/api/oral/start", json=_start_payload(sid, tier="diamond"), headers=auth_header(token))
    assert r.status_code == 400


def test_oral_start_models_required(client_oral, register, auth_header, set_credits):
    token, user = register(client_oral, "oral-no-model@x.com")
    set_credits(user["id"], 1000)
    sid = _seed_session(user["id"])
    r = client_oral.post("/api/oral/start", json=_start_payload(sid, n_models=0), headers=auth_header(token))
    assert r.status_code == 400


def test_oral_start_too_many_models(client_oral, register, auth_header, set_credits):
    token, user = register(client_oral, "oral-5-models@x.com")
    set_credits(user["id"], 1000)
    sid = _seed_session(user["id"])
    r = client_oral.post("/api/oral/start", json=_start_payload(sid, n_models=5), headers=auth_header(token))
    assert r.status_code == 400


def test_oral_start_session_not_found(client_oral, register, auth_header, set_credits):
    token, user = register(client_oral, "oral-no-sess@x.com")
    set_credits(user["id"], 1000)
    r = client_oral.post("/api/oral/start", json=_start_payload("nonexistent"), headers=auth_header(token))
    assert r.status_code == 404


def test_oral_start_cross_user_403(client_oral, register, auth_header, set_credits):
    token_a, ua = register(client_oral, "oral-start-a@x.com")
    token_b, ub = register(client_oral, "oral-start-b@x.com")
    set_credits(ub["id"], 1000)
    sid = _seed_session(ua["id"])
    r = client_oral.post("/api/oral/start", json=_start_payload(sid), headers=auth_header(token_b))
    assert r.status_code == 403


def test_oral_start_insufficient_credits_402(client_oral, register, auth_header, set_credits):
    token, user = register(client_oral, "oral-poor@x.com")
    set_credits(user["id"], 10)  # 不够 60s × 2.67 = 160 积分
    sid = _seed_session(user["id"], duration=60.0)
    r = client_oral.post("/api/oral/start", json=_start_payload(sid), headers=auth_header(token))
    assert r.status_code == 402


def test_oral_start_economy_charges_correctly(client_oral, register, auth_header, set_credits):
    """30 秒经济档:160/60 × 30 = 80 积分"""
    token, user = register(client_oral, "oral-charge@x.com")
    set_credits(user["id"], 1000)
    sid = _seed_session(user["id"], duration=30.0)
    r = client_oral.post("/api/oral/start", json=_start_payload(sid), headers=auth_header(token))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "asr_running"
    assert body["credits_charged"] == 80


def test_oral_start_premium_charges_correctly(client_oral, register, auth_header, set_credits):
    """60 秒顶级档:700 积分"""
    token, user = register(client_oral, "oral-charge-prem@x.com")
    set_credits(user["id"], 1000)
    sid = _seed_session(user["id"], duration=60.0)
    r = client_oral.post("/api/oral/start", json=_start_payload(sid, tier="premium"), headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["credits_charged"] == 700


def test_oral_start_already_started_400(client_oral, register, auth_header, set_credits):
    token, user = register(client_oral, "oral-double@x.com")
    set_credits(user["id"], 1000)
    sid = _seed_session(user["id"], status="asr_running")
    r = client_oral.post("/api/oral/start", json=_start_payload(sid), headers=auth_header(token))
    assert r.status_code == 400


# ==================== edit ====================


def test_oral_edit_wrong_status_400(client_oral, register, auth_header):
    """only asr_done can submit edit"""
    token, user = register(client_oral, "oral-edit-bad@x.com")
    sid = _seed_session(user["id"], status="uploaded")
    r = client_oral.post("/api/oral/edit", json={"session_id": sid, "edited_transcript": "hi"}, headers=auth_header(token))
    assert r.status_code == 400


def test_oral_edit_happy_path(client_oral, register, auth_header):
    token, user = register(client_oral, "oral-edit-ok@x.com")
    sid = _seed_session(user["id"], status="asr_done")
    r = client_oral.post("/api/oral/edit", json={"session_id": sid, "edited_transcript": "新文案"}, headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["status"] == "edit_submitted"


def test_oral_edit_too_long_400(client_oral, register, auth_header):
    token, user = register(client_oral, "oral-edit-long@x.com")
    sid = _seed_session(user["id"], status="asr_done")
    r = client_oral.post(
        "/api/oral/edit",
        json={"session_id": sid, "edited_transcript": "x" * 5001},
        headers=auth_header(token),
    )
    assert r.status_code == 400


# ==================== cancel ====================


def test_oral_cancel_running_refunds(client_oral, register, auth_header):
    """cancel 在 asr_running 阶段,按 99% 退款"""
    token, user = register(client_oral, "oral-cancel@x.com")
    sid = _seed_session(user["id"], status="asr_running", credits_charged=100)
    r = client_oral.post(f"/api/oral/cancel/{sid}", headers=auth_header(token))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "cancelled"
    assert body["credits_refunded"] == 99


def test_oral_cancel_completed_400(client_oral, register, auth_header):
    """终态不能取消"""
    token, user = register(client_oral, "oral-cancel-done@x.com")
    sid = _seed_session(user["id"], status="completed", credits_charged=100)
    r = client_oral.post(f"/api/oral/cancel/{sid}", headers=auth_header(token))
    assert r.status_code == 400


# ==================== compute_charge 单元 ====================


def test_compute_charge_economy_30s():
    from app.api.oral import compute_charge
    # 160/60 * 30 = 80
    assert compute_charge("economy", 30.0) == 80


def test_compute_charge_standard_60s():
    from app.api.oral import compute_charge
    assert compute_charge("standard", 60.0) == 360


def test_compute_charge_premium_60s():
    from app.api.oral import compute_charge
    assert compute_charge("premium", 60.0) == 700


def test_compute_charge_rounds_up():
    """1 秒视频按 1 秒算,不向下截断"""
    from app.api.oral import compute_charge
    # 160/60 * 1 = 2.67 → ceil → 3
    assert compute_charge("economy", 1.0) == 3


def test_compute_charge_unknown_tier_raises():
    from app.api.oral import compute_charge
    with pytest.raises(ValueError):
        compute_charge("diamond", 30.0)


# ==================== _step_progress 单元 ====================


def test_step_progress_uploaded_all_pending():
    from app.api.oral import _step_progress
    p = _step_progress("uploaded")
    assert all(v == "pending" for v in p.values())


def test_step_progress_completed_all_done():
    from app.api.oral import _step_progress
    p = _step_progress("completed")
    assert all(v == "done" for v in p.values())


def test_step_progress_lipsync_running():
    from app.api.oral import _step_progress
    p = _step_progress("lipsync_running")
    assert p["step1"] == p["step2"] == p["step3"] == p["step4"] == "done"
    assert p["step5"] == "running"


# ==================== P2:_run_asr_step + _run_tts_step ====================
#
# 真实 fal 调用 mock 掉(单测不打 fal 网络)。验证状态机推进 + 失败退款。

import asyncio


@pytest.fixture()
def patch_asr_ok(monkeypatch, tmp_path):
    """mock _extract_audio_track + fal upload + ASR service 全部成功"""
    from app.api import oral as oral_mod

    fake_audio = tmp_path / "audio.mp3"
    fake_voice_ref = tmp_path / "voice_ref.mp3"

    def fake_extract(vp, ap, vrp):
        # 写出真实文件让上传函数能开
        from pathlib import Path as _P
        _P(ap).touch()
        _P(vrp).touch()
        return True, ""

    monkeypatch.setattr(oral_mod, "_extract_audio_track", fake_extract)

    class FakeASR:
        async def transcribe(self, url):
            return {"text": "原文案 hello world", "chunks": [{"start": 0, "end": 1, "text": "原文案"}]}

    from app.services import fal_service
    monkeypatch.setattr(fal_service, "_asr_service", FakeASR())

    # mock fal_client.upload_file_async
    import fal_client
    async def fake_upload(p):
        return f"fal://{p}"
    monkeypatch.setattr(fal_client, "upload_file_async", fake_upload)


@pytest.fixture()
def patch_voice_ok(monkeypatch):
    class FakeVoice:
        async def clone_voice(self, ref_url, text):
            return {"audio_url": "https://fal.media/new_audio.mp3", "voice_id": "v_abc"}

    from app.services import fal_service
    monkeypatch.setattr(fal_service, "_voice_service", FakeVoice())

    import fal_client
    async def fake_upload(p):
        return f"fal://{p}"
    monkeypatch.setattr(fal_client, "upload_file_async", fake_upload)


def test_run_asr_step_happy_path_to_asr_done(patch_asr_ok, register, client_oral, auth_header, set_credits):
    token, user = register(client_oral, "oral-asr-ok@x.com")
    sid = _seed_session(user["id"], status="asr_running", credits_charged=160)

    from app.api.oral import _run_asr_step, _get_session
    asyncio.run(_run_asr_step(sid))

    sess = _get_session(sid)
    assert sess["status"] == "asr_done"
    assert "原文案" in sess["asr_transcript"]
    assert sess["extracted_audio_path"]
    assert sess["voice_ref_audio_path"]


def test_run_asr_step_ffmpeg_fail_refunds_100pct(monkeypatch, register, client_oral, set_credits):
    """ffmpeg 失败 → status=failed_step1,退 100%"""
    from app.api import oral as oral_mod

    monkeypatch.setattr(oral_mod, "_extract_audio_track", lambda v, a, vr: (False, "fake ffmpeg crash"))

    token, user = register(client_oral, "oral-asr-ffmpeg-fail@x.com")
    sid = _seed_session(user["id"], status="asr_running", credits_charged=160)

    asyncio.run(oral_mod._run_asr_step(sid))

    sess = oral_mod._get_session(sid)
    assert sess["status"] == "failed_step1"
    assert sess["error_step"] == "step1"
    assert "ffmpeg" in sess["error_message"]
    assert sess["credits_refunded"] == 160  # 100%


def test_run_asr_step_wizper_fail_refunds_100pct(monkeypatch, register, client_oral, set_credits):
    """ASR fal 调用返 error → 退 100%"""
    from app.api import oral as oral_mod
    from app.services import fal_service

    monkeypatch.setattr(oral_mod, "_extract_audio_track", lambda v, a, vr: (True, ""))

    class FakeASR:
        async def transcribe(self, url):
            return {"error": "fal timeout"}

    monkeypatch.setattr(fal_service, "_asr_service", FakeASR())

    import fal_client
    async def fake_upload(p):
        return "fal://x"
    monkeypatch.setattr(fal_client, "upload_file_async", fake_upload)

    token, user = register(client_oral, "oral-wizper-fail@x.com")
    sid = _seed_session(user["id"], status="asr_running", credits_charged=80)

    asyncio.run(oral_mod._run_asr_step(sid))

    sess = oral_mod._get_session(sid)
    assert sess["status"] == "failed_step1"
    assert "fal timeout" in sess["error_message"]
    assert sess["credits_refunded"] == 80


def test_run_asr_step_skips_if_status_changed(monkeypatch, register, client_oral):
    """如果 session 状态在跑过程中被改成 cancelled,失败处理不要覆盖"""
    from app.api import oral as oral_mod
    from app.services import fal_service

    monkeypatch.setattr(oral_mod, "_extract_audio_track", lambda v, a, vr: (False, "ffmpeg死了"))

    token, user = register(client_oral, "oral-asr-cancelled@x.com")
    sid = _seed_session(user["id"], status="cancelled", credits_charged=80)  # 提前置成 cancelled

    asyncio.run(oral_mod._run_asr_step(sid))

    sess = oral_mod._get_session(sid)
    # status 仍是 cancelled,没有被覆盖成 failed_step1
    assert sess["status"] == "cancelled"


def test_run_tts_step_happy_path_writes_audio(patch_voice_ok, register, client_oral, tmp_path):
    """P3 改造后:_run_tts_step 只写 new_audio_url,status 不直接推到 done(留给
    _try_advance_to_lipsync 原子判断)。单独跑 tts 时 swap 还未完成,status 保持 tts_running。"""
    from app.api import oral as oral_mod

    voice_ref = tmp_path / "voice_ref.mp3"
    voice_ref.touch()

    token, user = register(client_oral, "oral-tts-ok@x.com")
    sid = _seed_session(user["id"], status="edit_submitted", credits_charged=160)
    oral_mod._update_session(
        sid,
        voice_ref_audio_path=str(voice_ref),
        edited_transcript="新的口播文案,买这个超划算",
    )

    asyncio.run(oral_mod._run_tts_step(sid))

    sess = oral_mod._get_session(sid)
    assert sess["new_audio_url"] == "https://fal.media/new_audio.mp3"
    assert sess["voice_provider"] == "minimax"
    assert sess["voice_id"] == "v_abc"
    # status 保持 tts_running(swap 还没完成,不会推进 lipsync)
    assert sess["status"] == "tts_running"


def test_run_tts_step_with_swap_done_advances_to_lipsync(patch_voice_ok, register, client_oral, tmp_path, monkeypatch):
    """P3 关键:tts 完成时若 swap 已 done,_try_advance_to_lipsync 触发 status=lipsync_running"""
    from app.api import oral as oral_mod

    # 拦截 lipsync 任务避免真跑
    triggered = []
    async def fake_lipsync(sid):
        triggered.append(sid)
    monkeypatch.setattr(oral_mod, "_run_lipsync_step", fake_lipsync)

    voice_ref = tmp_path / "voice_ref.mp3"
    voice_ref.touch()

    token, user = register(client_oral, "oral-tts-advance@x.com")
    sid = _seed_session(user["id"], status="edit_submitted", credits_charged=160)
    oral_mod._update_session(
        sid,
        voice_ref_audio_path=str(voice_ref),
        edited_transcript="hi",
        swapped_video_url="https://x/swapped.mp4",  # swap 已经完成
    )

    asyncio.run(oral_mod._run_tts_step(sid))

    sess = oral_mod._get_session(sid)
    assert sess["status"] == "lipsync_running"


def test_run_tts_step_minimax_fail_refunds_95pct(monkeypatch, register, client_oral, tmp_path):
    """minimax 失败 → status=failed_step3,退 95%"""
    from app.api import oral as oral_mod
    from app.services import fal_service

    voice_ref = tmp_path / "voice_ref.mp3"
    voice_ref.touch()

    class FakeVoice:
        async def clone_voice(self, ref, text):
            return {"error": "minimax 503 unavailable"}

    monkeypatch.setattr(fal_service, "_voice_service", FakeVoice())

    import fal_client
    async def fake_upload(p):
        return "fal://x"
    monkeypatch.setattr(fal_client, "upload_file_async", fake_upload)

    token, user = register(client_oral, "oral-tts-fail@x.com")
    sid = _seed_session(user["id"], status="edit_submitted", credits_charged=160)
    oral_mod._update_session(
        sid,
        voice_ref_audio_path=str(voice_ref),
        edited_transcript="hi",
    )

    asyncio.run(oral_mod._run_tts_step(sid))

    sess = oral_mod._get_session(sid)
    assert sess["status"] == "failed_step3"
    assert sess["error_step"] == "step3"
    assert "minimax" in sess["error_message"].lower()
    # 95% 退款:160 * 0.95 = 152
    assert sess["credits_refunded"] == 152


def test_run_tts_step_unsupported_tier_fails(monkeypatch, register, client_oral, tmp_path):
    """tier=standard/premium 走 ElevenLabs(P6),P2 阶段直接 raise"""
    from app.api import oral as oral_mod

    voice_ref = tmp_path / "voice_ref.mp3"
    voice_ref.touch()

    token, user = register(client_oral, "oral-tts-tier@x.com")
    sid = _seed_session(user["id"], status="edit_submitted", credits_charged=360, tier="standard")
    oral_mod._update_session(
        sid,
        voice_ref_audio_path=str(voice_ref),
        edited_transcript="hi",
    )

    asyncio.run(oral_mod._run_tts_step(sid))

    sess = oral_mod._get_session(sid)
    assert sess["status"] == "failed_step3"
    assert "ElevenLabs" in sess["error_message"] or "tier" in sess["error_message"]


# ==================== P3:inpainting + lipsync + 并行汇合 ====================


@pytest.fixture()
def patch_inpainting_ok(monkeypatch):
    class FakeInp:
        async def inpaint(self, **kwargs):
            return {"video_url": "https://fal.media/swapped.mp4", "model": "fal-ai/wan-vace-14b/inpainting"}

    from app.services import fal_service
    monkeypatch.setattr(fal_service, "_inpainting_service", FakeInp())

    import fal_client
    async def fake_upload(p):
        return f"fal://{p}"
    monkeypatch.setattr(fal_client, "upload_file_async", fake_upload)

    # 跳过 archive_url(/uploads 写权限不在测试环境)
    async def fake_archive(url, uid, kind):
        return url
    monkeypatch.setattr("app.services.media_archiver.archive_url", fake_archive)


@pytest.fixture()
def patch_lipsync_ok(monkeypatch):
    class FakeLip:
        async def sync(self, video_url, audio_url, tier):
            return {"video_url": "https://fal.media/final.mp4", "model": "veed/lipsync"}

    from app.services import fal_service
    monkeypatch.setattr(fal_service, "_lipsync_service", FakeLip())

    # AIGC 水印 mock — 真跑 ffmpeg drawtext 在测试环境不稳定(字体/路径/权限),mock 出固定路径
    async def fake_watermark(url, uid, sid):
        return f"/uploads/oral/{uid}/{sid}/final.mp4"
    from app.api import oral as oral_mod
    monkeypatch.setattr(oral_mod, "_apply_aigc_watermark", fake_watermark)


def test_run_inpainting_step_happy_path(patch_inpainting_ok, register, client_oral, tmp_path):
    from app.api import oral as oral_mod

    mask = tmp_path / "mask.png"
    mask.write_bytes(b"\x89PNG\r\n\x1a\n")  # 假 PNG header

    token, user = register(client_oral, "oral-inp-ok@x.com")
    sid = _seed_session(user["id"], status="edit_submitted", credits_charged=160)
    oral_mod._update_session(
        sid,
        mask_image_path=str(mask),
        edited_transcript="hi",
        selected_models=json.dumps([{"name": "M1", "image_url": "https://x/m.jpg"}]),
        selected_products=json.dumps([]),
    )

    asyncio.run(oral_mod._run_inpainting_step(sid))

    sess = oral_mod._get_session(sid)
    assert sess["swapped_video_url"] == "https://fal.media/swapped.mp4"
    # status 此时不会推进到 lipsync_running(因为 new_audio_url 还为空,_try_advance 返 False)
    assert sess["status"] == "edit_submitted"


def test_run_inpainting_step_no_mask_fails(register, client_oral):
    from app.api import oral as oral_mod

    token, user = register(client_oral, "oral-inp-no-mask@x.com")
    sid = _seed_session(user["id"], status="edit_submitted", credits_charged=160)
    oral_mod._update_session(sid, edited_transcript="hi")
    # mask_image_path 没设

    asyncio.run(oral_mod._run_inpainting_step(sid))

    sess = oral_mod._get_session(sid)
    assert sess["status"] == "failed_step4"
    assert "mask" in sess["error_message"].lower()
    # Step 4 失败退 20%:160 * 0.20 = 32
    assert sess["credits_refunded"] == 32


def test_run_inpainting_step_fal_fail_refunds_20pct(monkeypatch, register, client_oral, tmp_path):
    from app.api import oral as oral_mod
    from app.services import fal_service

    class FailInp:
        async def inpaint(self, **kwargs):
            return {"error": "wan-vace 服务挂了"}

    monkeypatch.setattr(fal_service, "_inpainting_service", FailInp())

    import fal_client
    async def fake_upload(p):
        return "fal://x"
    monkeypatch.setattr(fal_client, "upload_file_async", fake_upload)

    mask = tmp_path / "mask.png"
    mask.write_bytes(b"x")

    token, user = register(client_oral, "oral-inp-fail@x.com")
    sid = _seed_session(user["id"], status="edit_submitted", credits_charged=200)
    oral_mod._update_session(
        sid,
        mask_image_path=str(mask),
        edited_transcript="hi",
        selected_models=json.dumps([{"name": "M1", "image_url": "https://x/m.jpg"}]),
    )

    asyncio.run(oral_mod._run_inpainting_step(sid))

    sess = oral_mod._get_session(sid)
    assert sess["status"] == "failed_step4"
    assert sess["credits_refunded"] == 40  # 200 * 0.20


def test_try_advance_to_lipsync_atomic(register, client_oral):
    """SQL 原子 UPDATE:只有一次返 True。第二次返 False 不重复触发。"""
    from app.api import oral as oral_mod

    token, user = register(client_oral, "oral-advance@x.com")
    sid = _seed_session(user["id"], status="edit_submitted")
    oral_mod._update_session(
        sid,
        new_audio_url="https://x/a.mp3",
        swapped_video_url="https://x/v.mp4",
    )

    assert oral_mod._try_advance_to_lipsync(sid) is True
    # 第二次返 False(rowcount=0 因为 status 已经是 lipsync_running)
    assert oral_mod._try_advance_to_lipsync(sid) is False

    sess = oral_mod._get_session(sid)
    assert sess["status"] == "lipsync_running"


def test_try_advance_blocks_when_one_side_missing(register, client_oral):
    from app.api import oral as oral_mod

    token, user = register(client_oral, "oral-advance-half@x.com")
    sid = _seed_session(user["id"], status="edit_submitted")
    oral_mod._update_session(sid, new_audio_url="https://x/a.mp3")
    # swapped_video_url 没设

    assert oral_mod._try_advance_to_lipsync(sid) is False
    sess = oral_mod._get_session(sid)
    assert sess["status"] == "edit_submitted"


def test_run_lipsync_step_happy_path_completes(patch_lipsync_ok, register, client_oral):
    from app.api import oral as oral_mod

    token, user = register(client_oral, "oral-lip-ok@x.com")
    sid = _seed_session(user["id"], status="lipsync_running", credits_charged=160)
    oral_mod._update_session(
        sid,
        new_audio_url="https://x/a.mp3",
        swapped_video_url="https://x/v.mp4",
    )

    asyncio.run(oral_mod._run_lipsync_step(sid))

    sess = oral_mod._get_session(sid)
    assert sess["status"] == "completed"
    # final URL 走 _apply_aigc_watermark fixture 返的本地公网路径
    assert sess["final_video_url"].startswith("/uploads/oral/")
    assert sess["final_video_url"].endswith("/final.mp4")
    assert sess["final_video_archived"] == sess["final_video_url"]


def test_run_lipsync_step_fal_fail_refunds_30pct(monkeypatch, register, client_oral):
    from app.api import oral as oral_mod
    from app.services import fal_service

    class FailLip:
        async def sync(self, **kwargs):
            return {"error": "veed service down"}

    monkeypatch.setattr(fal_service, "_lipsync_service", FailLip())

    token, user = register(client_oral, "oral-lip-fail@x.com")
    sid = _seed_session(user["id"], status="lipsync_running", credits_charged=200)
    oral_mod._update_session(
        sid,
        new_audio_url="https://x/a.mp3",
        swapped_video_url="https://x/v.mp4",
    )

    asyncio.run(oral_mod._run_lipsync_step(sid))

    sess = oral_mod._get_session(sid)
    assert sess["status"] == "failed_step5"
    assert sess["credits_refunded"] == 60  # 200 * 0.30


# ==================== L2 AIGC 水印 ====================


class _FakeHttpxStream:
    def __init__(self, status, body=b""):
        self.status_code = status
        self._body = body
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def aiter_bytes(self, chunk_size=65536):
        if self.status_code == 200:
            yield self._body


class _FakeHttpxClient:
    def __init__(self, status=200, body=b"fake bytes"):
        self._status = status
        self._body = body
    def __call__(self, *a, **kw): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    def stream(self, method, url):
        return _FakeHttpxStream(self._status, self._body)


def _patch_httpx(monkeypatch, status=200, body=b"fake video"):
    """monkeypatch httpx.AsyncClient 返指定 status/body。"""
    import httpx
    fake = _FakeHttpxClient(status=status, body=body)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: fake)


def test_apply_aigc_watermark_happy_path(monkeypatch, tmp_path):
    """下载 + ffmpeg drawtext 都成功 → 落 final.mp4 + 返 public URL + raw 删除。"""
    from app.api import oral as oral_mod
    from app.api import video_studio as vs_mod

    monkeypatch.setattr(oral_mod, "ORAL_UPLOAD_ROOT", tmp_path / "oral")
    _patch_httpx(monkeypatch, 200, b"fake fal final video")

    def fake_ff(cmd):
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake watermarked")
        return True, ""
    monkeypatch.setattr(vs_mod, "_run_ffmpeg", fake_ff)

    url = asyncio.run(oral_mod._apply_aigc_watermark(
        "https://fal.media/x.mp4", "user1", "sess1",
    ))

    assert url == "/uploads/oral/user1/sess1/final.mp4"
    out = tmp_path / "oral" / "user1" / "sess1" / "final.mp4"
    assert out.exists() and out.stat().st_size > 0
    # raw 应已被清理
    assert not (tmp_path / "oral" / "user1" / "sess1" / "_lipsync_raw.mp4").exists()


def test_apply_aigc_watermark_download_404_raises(monkeypatch, tmp_path):
    """fal final URL 返 404 → raise(由上层走 lipsync 失败退款)"""
    from app.api import oral as oral_mod
    monkeypatch.setattr(oral_mod, "ORAL_UPLOAD_ROOT", tmp_path / "oral")
    _patch_httpx(monkeypatch, 404)

    with pytest.raises(RuntimeError, match="404"):
        asyncio.run(oral_mod._apply_aigc_watermark(
            "https://fal.media/x.mp4", "user1", "sess2",
        ))


def test_apply_aigc_watermark_ffmpeg_fail_raises(monkeypatch, tmp_path):
    """ffmpeg drawtext 失败(字体丢/磁盘满)→ raise,不返无水印产物。"""
    from app.api import oral as oral_mod
    from app.api import video_studio as vs_mod
    monkeypatch.setattr(oral_mod, "ORAL_UPLOAD_ROOT", tmp_path / "oral")
    _patch_httpx(monkeypatch, 200)
    monkeypatch.setattr(vs_mod, "_run_ffmpeg", lambda cmd: (False, "Cannot open font"))

    with pytest.raises(RuntimeError, match="watermark ffmpeg failed"):
        asyncio.run(oral_mod._apply_aigc_watermark(
            "https://fal.media/x.mp4", "user1", "sess3",
        ))


def test_run_lipsync_step_watermark_fail_refunds_30pct(monkeypatch, register, client_oral):
    """合规硬性:水印失败 == lipsync 失败,退 30%(深度合成规定无水印不算合格)"""
    from app.api import oral as oral_mod
    from app.services import fal_service

    class FakeLip:
        async def sync(self, video_url, audio_url, tier):
            return {"video_url": "https://fal.media/final.mp4", "model": "veed/lipsync"}
    monkeypatch.setattr(fal_service, "_lipsync_service", FakeLip())

    async def fail_wm(url, uid, sid):
        raise RuntimeError("AIGC watermark ffmpeg failed: font missing")
    monkeypatch.setattr(oral_mod, "_apply_aigc_watermark", fail_wm)

    token, user = register(client_oral, "oral-wm-fail@x.com")
    sid = _seed_session(user["id"], status="lipsync_running", credits_charged=200)
    oral_mod._update_session(
        sid,
        new_audio_url="https://x/a.mp3",
        swapped_video_url="https://x/v.mp4",
    )

    asyncio.run(oral_mod._run_lipsync_step(sid))

    sess = oral_mod._get_session(sid)
    assert sess["status"] == "failed_step5"
    assert sess["credits_refunded"] == 60  # 200 * 0.30
    assert "watermark" in sess["error_message"].lower()


def test_resolution_for_tier():
    from app.api.oral import _resolution_for_tier
    assert _resolution_for_tier("economy") == "480p"
    assert _resolution_for_tier("standard") == "580p"
    assert _resolution_for_tier("premium") == "720p"


def test_lipsync_endpoint_for_tier():
    from app.services.fal_service import FalLipsyncService
    svc = FalLipsyncService("fake")
    assert svc.endpoint_for("economy") == "veed/lipsync"
    assert svc.endpoint_for("standard") == "fal-ai/latentsync"
    assert svc.endpoint_for("premium") == "fal-ai/sync-lipsync/v2"
    with pytest.raises(ValueError):
        svc.endpoint_for("diamond")


def test_step_progress_derives_from_fields():
    """改造后 _step_progress 读 session 字段派生 step3/4 状态"""
    from app.api.oral import _step_progress

    # Step3/4 都没开始
    sess = {"asr_transcript": "x", "edited_transcript": "y"}
    p = _step_progress("edit_submitted", sess)
    assert p["step1"] == "done"
    assert p["step2"] == "done"
    assert p["step3"] == "running"  # 有 edited_transcript 触发
    assert p["step4"] == "pending"  # mask 没传

    # Step3 完成,step4 还在跑
    sess["new_audio_url"] = "https://x/a.mp3"
    sess["mask_image_path"] = "/tmp/mask.png"
    p = _step_progress("edit_submitted", sess)
    assert p["step3"] == "done"
    assert p["step4"] == "running"

    # 都 done,等 lipsync 触发
    sess["swapped_video_url"] = "https://x/v.mp4"
    p = _step_progress("lipsync_running", sess)
    assert p["step3"] == "done"
    assert p["step4"] == "done"
    assert p["step5"] == "running"


# ==================== /upload-mask 端点 ====================


def test_upload_mask_unauthenticated_401(client_oral):
    r = client_oral.post("/api/oral/upload-mask", data={"session_id": "x"})
    assert r.status_code == 401


def test_upload_mask_happy_path(client_oral, register, auth_header):
    token, user = register(client_oral, "oral-mask@x.com")
    sid = _seed_session(user["id"], status="asr_done")

    # 假 PNG bytes
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    r = client_oral.post(
        "/api/oral/upload-mask",
        data={"session_id": sid},
        files={"file": ("mask.png", fake_png, "image/png")},
        headers=auth_header(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["size_bytes"] == len(fake_png)
    assert "mask.png" in body["mask_image_path"]


def test_upload_mask_non_image_rejected(client_oral, register, auth_header):
    token, user = register(client_oral, "oral-mask-bad@x.com")
    sid = _seed_session(user["id"])

    r = client_oral.post(
        "/api/oral/upload-mask",
        data={"session_id": sid},
        files={"file": ("evil.exe", b"binary", "application/octet-stream")},
        headers=auth_header(token),
    )
    assert r.status_code == 400


def test_upload_mask_session_completed_400(client_oral, register, auth_header):
    """终态不能再传 mask"""
    token, user = register(client_oral, "oral-mask-done@x.com")
    sid = _seed_session(user["id"], status="completed")

    r = client_oral.post(
        "/api/oral/upload-mask",
        data={"session_id": sid},
        files={"file": ("mask.png", b"\x89PNG", "image/png")},
        headers=auth_header(token),
    )
    assert r.status_code == 400


# ==================== P9b:双 mask + 双轮 inpaint ====================


def test_upload_mask_kind_person_writes_person_column(client_oral, register, auth_header):
    """kind=person → person_mask_image_path 写入,legacy mask_image_path 同步写"""
    from app.api import oral as oral_mod
    token, user = register(client_oral, "oral-pmask@x.com")
    sid = _seed_session(user["id"], status="asr_done")
    r = client_oral.post(
        "/api/oral/upload-mask",
        data={"session_id": sid, "kind": "person"},
        files={"file": ("mask.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, "image/png")},
        headers=auth_header(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "person"
    sess = oral_mod._get_session(sid)
    assert sess["person_mask_image_path"]
    assert sess["mask_image_path"] == sess["person_mask_image_path"]
    assert "mask.png" in sess["person_mask_image_path"]
    assert not sess.get("product_mask_image_path")


def test_upload_mask_kind_product_writes_product_column(client_oral, register, auth_header):
    """kind=product → product_mask_image_path 写入,legacy mask_image_path 不被污染"""
    from app.api import oral as oral_mod
    token, user = register(client_oral, "oral-prdmask@x.com")
    sid = _seed_session(user["id"], status="asr_done")
    r = client_oral.post(
        "/api/oral/upload-mask",
        data={"session_id": sid, "kind": "product"},
        files={"file": ("pm.png", b"\x89PNG" + b"\x00" * 50, "image/png")},
        headers=auth_header(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "product"
    sess = oral_mod._get_session(sid)
    assert sess["product_mask_image_path"]
    assert "product_mask.png" in sess["product_mask_image_path"]
    assert not sess.get("person_mask_image_path")
    assert not sess.get("mask_image_path")


def test_upload_mask_invalid_kind_400(client_oral, register, auth_header):
    token, user = register(client_oral, "oral-badkind@x.com")
    sid = _seed_session(user["id"], status="asr_done")
    r = client_oral.post(
        "/api/oral/upload-mask",
        data={"session_id": sid, "kind": "evil"},
        files={"file": ("m.png", b"\x89PNG", "image/png")},
        headers=auth_header(token),
    )
    assert r.status_code == 400


def test_run_inpainting_step_dual_mask_runs_two_rounds(monkeypatch, register, client_oral, tmp_path):
    """有 person + product mask + 产品 → 跑两轮 wan-vace,swapped_video_url=swap2 输出"""
    import asyncio
    from app.api import oral as oral_mod
    from app.services import fal_service

    calls = []

    class DualInp:
        async def inpaint(self, **kwargs):
            calls.append(kwargs)
            idx = len(calls)
            return {"video_url": f"https://fal.media/round{idx}.mp4", "model": f"wan-vace-r{idx}"}

    monkeypatch.setattr(fal_service, "_inpainting_service", DualInp())

    import fal_client
    async def fake_upload(p):
        return f"fal://{p}"
    monkeypatch.setattr(fal_client, "upload_file_async", fake_upload)
    async def fake_archive(url, uid, kind):
        return url
    monkeypatch.setattr("app.services.media_archiver.archive_url", fake_archive)

    person_mask = tmp_path / "person.png"
    person_mask.write_bytes(b"\x89PNG")
    product_mask = tmp_path / "product.png"
    product_mask.write_bytes(b"\x89PNG")

    token, user = register(client_oral, "oral-dual@x.com")
    sid = _seed_session(user["id"], status="edit_submitted", credits_charged=160)
    oral_mod._update_session(
        sid,
        person_mask_image_path=str(person_mask),
        product_mask_image_path=str(product_mask),
        edited_transcript="hi",
        selected_models=json.dumps([{"name": "M1", "image_url": "https://x/m.jpg"}]),
        selected_products=json.dumps([{"name": "P1", "image_url": "https://x/p.jpg"}]),
    )

    asyncio.run(oral_mod._run_inpainting_step(sid))

    sess = oral_mod._get_session(sid)
    assert len(calls) == 2, f"expected 2 wan-vace calls, got {len(calls)}"
    assert sess["swap1_video_url"] == "https://fal.media/round1.mp4"
    assert sess["swapped_video_url"] == "https://fal.media/round2.mp4"
    assert sess["swap1_fal_request_id"] == "wan-vace-r1"
    assert sess["swap_fal_request_id"] == "wan-vace-r2"
    # 第二轮 video_url 应该是第一轮输出
    assert calls[1]["video_url"] == "https://fal.media/round1.mp4"
    # 第一轮 prompt 提到 person,第二轮 prompt 提到 product
    assert "person" in calls[0]["prompt"].lower()
    assert "product" in calls[1]["prompt"].lower()


def test_run_inpainting_step_no_product_mask_single_round(monkeypatch, register, client_oral, tmp_path):
    """有 person mask 无 product mask → 只跑一轮,swap1 直接当 swapped_video_url"""
    import asyncio
    from app.api import oral as oral_mod
    from app.services import fal_service

    calls = []

    class SingleInp:
        async def inpaint(self, **kwargs):
            calls.append(kwargs)
            return {"video_url": "https://fal.media/single.mp4", "model": "wan-vace-r1"}

    monkeypatch.setattr(fal_service, "_inpainting_service", SingleInp())

    import fal_client
    async def fake_upload(p):
        return f"fal://{p}"
    monkeypatch.setattr(fal_client, "upload_file_async", fake_upload)
    async def fake_archive(url, uid, kind):
        return url
    monkeypatch.setattr("app.services.media_archiver.archive_url", fake_archive)

    person_mask = tmp_path / "person.png"
    person_mask.write_bytes(b"\x89PNG")

    token, user = register(client_oral, "oral-single@x.com")
    sid = _seed_session(user["id"], status="edit_submitted", credits_charged=160)
    oral_mod._update_session(
        sid,
        person_mask_image_path=str(person_mask),
        edited_transcript="hi",
        selected_models=json.dumps([{"name": "M1", "image_url": "https://x/m.jpg"}]),
        selected_products=json.dumps([]),  # 没产品
    )

    asyncio.run(oral_mod._run_inpainting_step(sid))

    sess = oral_mod._get_session(sid)
    assert len(calls) == 1, f"expected single call, got {len(calls)}"
    assert sess["swap1_video_url"] == "https://fal.media/single.mp4"
    assert sess["swapped_video_url"] == "https://fal.media/single.mp4"  # = swap1
    assert sess["swap_fal_request_id"] == "wan-vace-r1"


# ==================== WebSocket 实时进度推送 ====================


def test_oral_ws_no_token_closes_4401(client_oral, register, auth_header):
    token, user = register(client_oral, "oral-ws-no-token@x.com")
    sid = _seed_session(user["id"])
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client_oral.websocket_connect(f"/api/oral/ws/{sid}") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4401


def test_oral_ws_invalid_token_closes_4401(client_oral, register, auth_header):
    token, user = register(client_oral, "oral-ws-bad-token@x.com")
    sid = _seed_session(user["id"])
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client_oral.websocket_connect(f"/api/oral/ws/{sid}?token=garbage") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4401


def test_oral_ws_cross_user_closes_4403(client_oral, register, auth_header):
    token_a, ua = register(client_oral, "oral-ws-cross-a@x.com")
    token_b, _ub = register(client_oral, "oral-ws-cross-b@x.com")
    sid = _seed_session(ua["id"])
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client_oral.websocket_connect(f"/api/oral/ws/{sid}?token={token_b}") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4403


def test_oral_ws_happy_path_sends_initial_status(client_oral, register, auth_header):
    token, user = register(client_oral, "oral-ws-ok@x.com")
    sid = _seed_session(user["id"], duration=42.0, tier="standard", status="asr_running")
    with client_oral.websocket_connect(f"/api/oral/ws/{sid}?token={token}") as ws:
        msg = ws.receive_json()
        assert msg["session_id"] == sid
        assert msg["status"] == "asr_running"
        assert msg["tier"] == "standard"
        assert msg["duration_seconds"] == 42.0
        assert msg["step_progress"]["step1"] == "running"


def test_oral_ws_terminal_status_closes_after_initial(client_oral, register, auth_header):
    token, user = register(client_oral, "oral-ws-terminal@x.com")
    sid = _seed_session(user["id"], status="completed")
    from starlette.websockets import WebSocketDisconnect
    with client_oral.websocket_connect(f"/api/oral/ws/{sid}?token={token}") as ws:
        msg = ws.receive_json()
        assert msg["status"] == "completed"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
