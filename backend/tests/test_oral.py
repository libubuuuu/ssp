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


def test_run_tts_step_happy_path_to_tts_done(patch_voice_ok, register, client_oral, tmp_path):
    """模拟 minimax voice-clone 成功,session 推进到 tts_done"""
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
    assert sess["status"] == "tts_done"
    assert sess["new_audio_url"] == "https://fal.media/new_audio.mp3"
    assert sess["voice_provider"] == "minimax"
    assert sess["voice_id"] == "v_abc"


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
