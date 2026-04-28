"""口播带货工作台 — 七十七续 P1 骨架 + P2 ASR/TTS 异步链路(经济档先行)

完整规划:docs/ORAL-BROADCAST-PLAN.md

P1(Step 1):6 端点骨架 + DB 持久化 + 状态机基础(2026-04-29)
P2(本续):
  - Step 1 ASR 真实调用(fal-ai/wizper)— ffmpeg 提取音轨 → fal upload → wizper
  - Step 3 经济档 voice-clone+TTS 一步(fal-ai/minimax/voice-clone)
  - asyncio.create_task 异步驱动状态机
P3(下波):wan-vace inpainting + lipsync + 中间产物归档

经济档先行:不依赖 ElevenLabs。标准/顶级档预留 voice_provider 字段,等 EL_API_KEY 接入再激活。
"""
import asyncio
import json
import math
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.database import get_db
from app.services.billing import (
    PRICING,
    get_user_credits,
    check_user_credits,
    deduct_credits,
    add_credits,
)

router = APIRouter()

# 用户上传根目录:/opt/ssp/uploads/oral/<user_id>/<sid>/
ORAL_UPLOAD_ROOT = Path(os.getenv("UPLOADS_ROOT", "/opt/ssp/uploads")) / "oral"

# 视频时长硬上限(MVP 60 秒,详见规划文档 Q2)
MAX_DURATION_SECONDS = 60

# 档位允许值
TIERS = ("economy", "standard", "premium")

# 状态机 — 详见规划文档 §4.2
STATUS_INITIAL = "uploaded"
STATUS_TERMINAL_OK = "completed"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED_PREFIX = "failed_"


# ==================== Pydantic 请求/响应 ====================


class StartRequest(BaseModel):
    """POST /api/oral/start"""
    session_id: str
    tier: str
    models: List[dict]      # [{name, image_url}, ...] 1-4 个
    products: List[dict]    # [{name, image_url}, ...] 0-4 个
    legal_consent: bool     # L1 用户责任声明,前端勾选后传 true(规划文档 Q4)


class EditRequest(BaseModel):
    """POST /api/oral/edit"""
    session_id: str
    edited_transcript: str


# ==================== 计费 ====================


def compute_charge(tier: str, duration_seconds: float) -> int:
    """按 tier × 秒数算预扣积分。1 秒视频也按 1 秒收,向上取整。

    规划文档 §7.1:
      经济 ¥80/min(160 积分/min)→ 2.67 积分/秒
      标准 ¥180/min(360 积分/min)→ 6.0 积分/秒
      顶级 ¥350/min(700 积分/min)→ 11.67 积分/秒
    """
    per_min = PRICING.get(f"oral_broadcast/{tier}")
    if not per_min:
        raise ValueError(f"unknown tier: {tier}")
    per_second = per_min / 60.0
    return math.ceil(per_second * duration_seconds)


# 失败按阶段退款比例 — 规划文档 §7.2(MVP 写死,运营观察 1 个月后调)
REFUND_RATIO = {
    "failed_step1": 1.00,
    "cancelled_after_step1": 0.99,
    "failed_step3": 0.95,
    "failed_step4": 0.20,
    "failed_step5": 0.30,
    "cancelled": 0.99,         # 用户主动取消(任何阶段)
}


def _refund(session: dict, status: str) -> int:
    """按 status 比例退款。返回实退积分。"""
    ratio = REFUND_RATIO.get(status, 0.0)
    if ratio <= 0:
        return 0
    refund = int(session["credits_charged"] * ratio)
    if refund > 0:
        add_credits(session["user_id"], refund)
    return refund


# ==================== DB 操作 ====================


def _row_to_dict(row) -> dict:
    """sqlite3.Row → dict(只列入业务字段)"""
    return dict(row) if row else None


def _get_session(session_id: str) -> Optional[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM oral_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        return _row_to_dict(row)


def _create_session(
    session_id: str,
    user_id: str,
    video_path: str,
    duration: float,
) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO oral_sessions
                (id, user_id, tier, status, original_video_path, duration_seconds, credits_charged)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, str(user_id), "economy", STATUS_INITIAL, video_path, duration, 0),
        )
        conn.commit()


def _log(msg: str) -> None:
    """带前缀的 stderr 日志,Sentry / 巡检方便看 oral pipeline 命中"""
    print(f"ORAL_PIPELINE {msg}", file=sys.stderr, flush=True)


# ==================== ffmpeg 音轨提取 ====================


def _extract_audio_track(video_path: str, audio_path: str, voice_ref_path: str) -> tuple[bool, str]:
    """七十七续 P2:从原视频提取两个音频:
    - audio_path:完整音轨,送 wizper ASR
    - voice_ref_path:前 10 秒,送 minimax voice-clone 作 reference 样本(规划文档要求 ≥10s)

    复用 video_studio._run_ffmpeg(已有的 subprocess.run 包装,300s 超时)。
    """
    from app.api.video_studio import _run_ffmpeg

    ok1, err1 = _run_ffmpeg([
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "libmp3lame", "-q:a", "2",
        audio_path,
    ])
    if not ok1:
        return False, f"完整音轨失败: {err1[:200]}"

    ok2, err2 = _run_ffmpeg([
        "ffmpeg", "-y", "-i", video_path, "-t", "10",
        "-vn", "-acodec", "libmp3lame", "-q:a", "2",
        voice_ref_path,
    ])
    if not ok2:
        return False, f"voice_ref 截取失败: {err2[:200]}"

    return True, ""


# ==================== 异步驱动:Step 1 ASR ====================


async def _run_asr_step(session_id: str) -> None:
    """Step 1:ffmpeg 提取音轨 → fal upload → wizper ASR → 写 transcript → status=asr_done。

    失败按 §7.2 退 100%。**绝不让异常逃逸**(asyncio task 异常会静默丢失日志)。
    """
    import fal_client

    session = _get_session(session_id)
    if not session:
        _log(f"_run_asr_step: session {session_id} 已不存在,跳过")
        return

    try:
        video_path = session["original_video_path"]
        session_dir = Path(video_path).parent
        audio_path = str(session_dir / "audio.mp3")
        voice_ref_path = str(session_dir / "voice_ref.mp3")

        # 1) ffmpeg 提取
        ok, err = _extract_audio_track(video_path, audio_path, voice_ref_path)
        if not ok:
            raise RuntimeError(f"ffmpeg 失败: {err}")

        # 2) 上传到 fal storage(私有 URL,不依赖我们 nginx 公开)
        audio_fal_url = await fal_client.upload_file_async(audio_path)

        # 3) 调 wizper
        from app.services.fal_service import get_asr_service
        asr_svc = get_asr_service()
        if not asr_svc:
            raise RuntimeError("FAL ASR service 未初始化")
        result = await asr_svc.transcribe(audio_fal_url)
        if "error" in result:
            raise RuntimeError(f"wizper: {result['error']}")

        # 4) 写库,推进状态
        _update_session(
            session_id,
            extracted_audio_path=audio_path,
            voice_ref_audio_path=voice_ref_path,
            asr_transcript=result.get("text", ""),
            asr_word_timestamps=json.dumps(result.get("chunks", []), ensure_ascii=False),
            status="asr_done",
        )
        _log(f"_run_asr_step OK session={session_id} text_len={len(result.get('text', ''))}")
    except Exception as e:
        _log(f"_run_asr_step FAIL session={session_id} err={e}")
        sess2 = _get_session(session_id)
        if not sess2 or sess2["status"] != "asr_running":
            return  # 已被改(比如用户 cancel),不要覆盖
        refunded = _refund(sess2, "failed_step1")
        _update_session(
            session_id,
            status="failed_step1",
            error_step="step1",
            error_message=str(e)[:500],
            credits_refunded=refunded,
        )


# ==================== 异步驱动:Step 3 经济档 voice-clone + TTS ====================


async def _run_tts_step(session_id: str) -> None:
    """Step 3 经济档:fal-ai/minimax/voice-clone 一步完成 clone + TTS。

    标准/顶级档(ElevenLabs)留下波(P6,等用户拿到 EL API key)。
    失败按 §7.2 退 95%。
    """
    import fal_client

    session = _get_session(session_id)
    if not session:
        _log(f"_run_tts_step: session {session_id} 已不存在,跳过")
        return

    try:
        # 推进状态
        _update_session(session_id, status="tts_running")

        if session["tier"] != "economy":
            # 标准/顶级档暂不支持(等 ElevenLabs key)
            raise RuntimeError(f"tier={session['tier']} 暂未支持 — 等 ElevenLabs API key 接入(P6)")

        voice_ref_path = session["voice_ref_audio_path"]
        edited_text = session["edited_transcript"]
        if not voice_ref_path or not edited_text:
            raise RuntimeError("voice_ref / edited_transcript 缺失,数据不一致")

        # 1) 上传 voice_ref 到 fal storage
        voice_ref_fal_url = await fal_client.upload_file_async(voice_ref_path)

        # 2) 调 minimax voice-clone 一步生成新音频
        from app.services.fal_service import get_voice_service
        voice_svc = get_voice_service()
        if not voice_svc:
            raise RuntimeError("FAL Voice service 未初始化")
        result = await voice_svc.clone_voice(voice_ref_fal_url, edited_text)
        if "error" in result:
            raise RuntimeError(f"voice-clone: {result['error']}")

        new_audio_url = result.get("audio_url")
        if not new_audio_url:
            raise RuntimeError("voice-clone 未返 audio_url")

        # 3) 写库,推进状态(P3 下波接 Step 4 inpainting + Step 5 lipsync 才能到 completed)
        _update_session(
            session_id,
            voice_provider="minimax",
            voice_id=result.get("voice_id") or "",
            new_audio_url=new_audio_url,
            status="tts_done",
        )
        _log(f"_run_tts_step OK session={session_id}")
    except Exception as e:
        _log(f"_run_tts_step FAIL session={session_id} err={e}")
        sess2 = _get_session(session_id)
        if not sess2 or sess2["status"] not in ("edit_submitted", "tts_running"):
            return
        refunded = _refund(sess2, "failed_step3")
        _update_session(
            session_id,
            status="failed_step3",
            error_step="step3",
            error_message=str(e)[:500],
            credits_refunded=refunded,
        )


def _update_session(session_id: str, **fields) -> None:
    """更新指定字段,自动加 updated_at。"""
    if not fields:
        return
    fields["updated_at"] = "CURRENT_TIMESTAMP"
    set_parts = []
    values = []
    for k, v in fields.items():
        if v == "CURRENT_TIMESTAMP":
            set_parts.append(f"{k} = CURRENT_TIMESTAMP")
        else:
            set_parts.append(f"{k} = ?")
            values.append(v)
    values.append(session_id)
    sql = f"UPDATE oral_sessions SET {', '.join(set_parts)} WHERE id = ?"
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, values)
        conn.commit()


# ==================== 端点 1:POST /upload ====================


@router.post("/upload")
async def upload_video(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传原视频,创建 session(tier 留空,由 /start 锁定)。

    限制:60 秒硬上限(规划文档 Q2)。
    """
    from app.services.upload_guard import stream_bounded_to_path, LONG_VIDEO_MIMES

    session_id = str(uuid.uuid4())[:12]
    user_id = str(current_user["id"])

    session_dir = ORAL_UPLOAD_ROOT / user_id / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    ext = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"
    video_path = session_dir / f"orig{ext}"

    try:
        size_bytes = await stream_bounded_to_path(
            file,
            target_path=video_path,
            max_bytes=200 * 1024 * 1024,  # 60 秒视频通常 < 200MB
            allowed_mimes=LONG_VIDEO_MIMES,
            label="口播带货",
        )
    except HTTPException:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise

    # 复用 video_studio._get_video_duration 思路(ffprobe)
    from app.api.video_studio import _get_video_duration
    duration = _get_video_duration(str(video_path))

    if duration > MAX_DURATION_SECONDS:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(
            status_code=413,
            detail=f"视频时长 {duration:.1f}s 超过 {MAX_DURATION_SECONDS} 秒上限",
        )

    _create_session(session_id, user_id, str(video_path), duration)

    return {
        "session_id": session_id,
        "duration_seconds": round(duration, 2),
        "size_mb": round(size_bytes / 1024 / 1024, 2),
    }


# ==================== 端点 2:POST /start ====================


@router.post("/start")
async def start_pipeline(
    req: StartRequest,
    current_user: dict = Depends(get_current_user),
):
    """选档位 + 提交模特/产品,**预扣积分**,触发 Step 1 ASR(P2 实现)。"""
    user_id = str(current_user["id"])

    # L1 用户责任声明必勾(规划文档 Q4)
    if not req.legal_consent:
        raise HTTPException(400, "需勾选用户责任声明才能开始")

    if req.tier not in TIERS:
        raise HTTPException(400, f"tier 必须是 {TIERS}")

    if not req.models or len(req.models) > 4:
        raise HTTPException(400, "models 必须 1-4 个")
    if len(req.products) > 4:
        raise HTTPException(400, "products 最多 4 个")

    session = _get_session(req.session_id)
    if not session:
        raise HTTPException(404, "session not found")
    if session["user_id"] != user_id:
        raise HTTPException(403, "无权限")
    if session["status"] != STATUS_INITIAL:
        raise HTTPException(400, f"session 状态 {session['status']},不能再 start")

    # 计费
    charge = compute_charge(req.tier, session["duration_seconds"])
    if not check_user_credits(user_id, charge):
        raise HTTPException(402, f"积分不足,需 {charge} 积分")

    # 原子扣费
    if not deduct_credits(user_id, charge):
        raise HTTPException(500, "扣费失败,请重试")

    # 写入 session — 状态推进到 asr_running(实际 ASR 调用 P2 实现)
    _update_session(
        req.session_id,
        tier=req.tier,
        status="asr_running",
        selected_models=json.dumps(req.models, ensure_ascii=False),
        selected_products=json.dumps(req.products, ensure_ascii=False),
        credits_charged=charge,
    )

    # 写 audit_log(L1 责任声明已确认)
    try:
        from app.services.audit import log_action
        log_action(
            actor_user_id=user_id,
            action="oral_legal_consent",
            target_type="oral_session",
            target_id=req.session_id,
            details=json.dumps({
                "tier": req.tier,
                "duration_seconds": session["duration_seconds"],
                "consent_version": "v1",
            }),
        )
    except Exception:
        pass  # audit 失败不阻塞主流程

    # 七十七续 P2:触发 ASR 异步任务(non-blocking)
    asyncio.create_task(_run_asr_step(req.session_id))

    estimated_eta = int(session["duration_seconds"] * 8) + 60  # 粗估 8x realtime + 1min 缓冲

    return {
        "status": "asr_running",
        "credits_charged": charge,
        "estimated_eta_seconds": estimated_eta,
    }


# ==================== 端点 3:POST /edit ====================


@router.post("/edit")
async def submit_edited_transcript(
    req: EditRequest,
    current_user: dict = Depends(get_current_user),
):
    """用户提交编辑后的文案,触发 Step 3+4 并行(P2/P3 实现)。"""
    user_id = str(current_user["id"])

    session = _get_session(req.session_id)
    if not session:
        raise HTTPException(404, "session not found")
    if session["user_id"] != user_id:
        raise HTTPException(403, "无权限")
    if session["status"] != "asr_done":
        raise HTTPException(400, f"session 状态 {session['status']},不能在此阶段提交编辑")

    if not req.edited_transcript or len(req.edited_transcript) > 5000:
        raise HTTPException(400, "edited_transcript 必填且不超过 5000 字符")

    _update_session(
        req.session_id,
        edited_transcript=req.edited_transcript,
        status="edit_submitted",
    )

    # 七十七续 P2:触发 Step 3 TTS 异步任务
    # P3 下波加并行 Step 4 inpainting:asyncio.create_task(_run_inpainting_step(req.session_id))
    asyncio.create_task(_run_tts_step(req.session_id))

    return {"status": "edit_submitted"}


# ==================== 端点 4:GET /status/{session_id} ====================


def _step_progress(status: str) -> dict:
    """状态字符串 → 5 步进度字典(前端进度条用)"""
    p = {"step1": "pending", "step2": "pending", "step3": "pending", "step4": "pending", "step5": "pending"}
    if status == "uploaded":
        return p
    if status == "asr_running":
        p["step1"] = "running"; return p
    if status == "asr_done":
        p["step1"] = "done"; return p
    if status == "edit_submitted":
        p["step1"] = "done"; p["step2"] = "done"; return p
    if status in ("tts_running", "swap_running"):
        p["step1"] = "done"; p["step2"] = "done"
        p["step3"] = "running" if "tts" in status else "pending"
        p["step4"] = "running" if "swap" in status else "pending"
        return p
    if status == "tts_done":
        p["step1"] = p["step2"] = p["step3"] = "done"; return p
    if status == "swap_done":
        p["step1"] = p["step2"] = p["step4"] = "done"; return p
    if status == "both_ready":
        p["step1"] = p["step2"] = p["step3"] = p["step4"] = "done"; return p
    if status == "lipsync_running":
        p["step1"] = p["step2"] = p["step3"] = p["step4"] = "done"
        p["step5"] = "running"; return p
    if status == "completed":
        return {k: "done" for k in p}
    if status.startswith("failed_") or status == "cancelled":
        # 失败时把当前进度冻结
        return p
    return p


@router.get("/status/{session_id}")
async def get_session_status(session_id: str, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["id"])
    session = _get_session(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    if session["user_id"] != user_id:
        raise HTTPException(403, "无权限")

    return {
        "session_id": session_id,
        "status": session["status"],
        "tier": session["tier"],
        "duration_seconds": session["duration_seconds"],
        "credits_charged": session["credits_charged"],
        "credits_refunded": session["credits_refunded"],
        "step_progress": _step_progress(session["status"]),
        "products": {
            "asr_transcript": session.get("asr_transcript"),
            "edited_transcript": session.get("edited_transcript"),
            "new_audio_url": session.get("new_audio_url"),
            "swapped_video_url": session.get("swapped_video_url"),
            "final_video_url": session.get("final_video_url"),
        },
        "error": session.get("error_message") if session["status"].startswith("failed_") else None,
    }


# ==================== 端点 5:POST /cancel/{session_id} ====================


@router.post("/cancel/{session_id}")
async def cancel_session(session_id: str, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["id"])
    session = _get_session(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    if session["user_id"] != user_id:
        raise HTTPException(403, "无权限")

    # 终态不允许再取消
    if session["status"] in (STATUS_TERMINAL_OK, STATUS_CANCELLED) or session["status"].startswith(STATUS_FAILED_PREFIX):
        raise HTTPException(400, f"session 已是 {session['status']},不能取消")

    refunded = _refund(session, "cancelled")
    _update_session(
        session_id,
        status=STATUS_CANCELLED,
        credits_refunded=refunded,
    )

    return {"status": STATUS_CANCELLED, "credits_refunded": refunded}


# ==================== 端点 6:GET /list ====================


@router.get("/list")
async def list_sessions(current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["id"])
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tier, status, duration_seconds, final_video_url, created_at
              FROM oral_sessions
             WHERE user_id = ?
          ORDER BY created_at DESC
             LIMIT 100
            """,
            (user_id,),
        )
        rows = cursor.fetchall()

    sessions = []
    for r in rows:
        d = dict(r)
        sessions.append({
            "session_id": d["id"],
            "tier": d["tier"],
            "status": d["status"],
            "duration_seconds": d["duration_seconds"],
            "final_video_url": d["final_video_url"],
            "title": f"口播带货 {d['duration_seconds']:.0f}s ({d['tier']})",
            "created_at": d["created_at"],
        })
    return {"sessions": sessions}
