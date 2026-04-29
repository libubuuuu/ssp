"""口播带货工作台 — 七十七续 P1 骨架 + P2 ASR/TTS 异步链路(经济档先行)

完整规划:docs/ORAL-BROADCAST-PLAN.md

P1(Step 1):6 端点骨架 + DB 持久化 + 状态机基础(2026-04-29)
P2:
  - Step 1 ASR 真实调用(fal-ai/wizper)— ffmpeg 提取音轨 → fal upload → wizper
  - Step 3 经济档 voice-clone+TTS 一步(fal-ai/minimax/voice-clone)
  - asyncio.create_task 异步驱动状态机
P3(本续):
  - Step 4 视频换装(fal-ai/wan-vace-14b/inpainting,3 档分辨率)
  - Step 5 口型对齐(三档不同 endpoint:veed/latentsync/sync-v2)
  - Step 3/4 真并行 + Step 5 汇合(_try_advance_to_lipsync SQL 原子)
  - mask 上传端点 + media_archiver 中间产物归档
  - _step_progress 改读字段派生(避免 status 字段爆炸)

经济档先行:不依赖 ElevenLabs。标准/顶级档预留 voice_provider 字段,等 EL_API_KEY 接入再激活。
"""
import asyncio
import json
import math
import os
import re
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
# 分片上传临时目录(_uploading/<user_id>_<upload_id>/<chunk_idx>)
ORAL_UPLOAD_TMP = ORAL_UPLOAD_ROOT / "_uploading"

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

        # 3) 写库 — 不直接改 status,留给 _try_advance_to_lipsync 原子判断双完成
        _update_session(
            session_id,
            voice_provider="minimax",
            voice_id=result.get("voice_id") or "",
            new_audio_url=new_audio_url,
        )
        _log(f"_run_tts_step OK session={session_id}")

        # 4) 尝试推进到 lipsync(若 inpainting 也完成)
        if _try_advance_to_lipsync(session_id):
            _log(f"_run_tts_step: 双完成,触发 lipsync session={session_id}")
            asyncio.create_task(_run_lipsync_step(session_id))
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


# ==================== Tier → 模型参数映射 ====================


_RESOLUTION_FOR_TIER = {"economy": "480p", "standard": "580p", "premium": "720p"}


def _resolution_for_tier(tier: str) -> str:
    return _RESOLUTION_FOR_TIER.get(tier, "480p")


# ==================== Step 3/4 汇合到 Step 5 的原子推进 ====================


def _try_advance_to_lipsync(session_id: str) -> bool:
    """SQL 原子检查:new_audio_url + swapped_video_url 都有 → status='lipsync_running'。

    并行 step3/step4 完成时各自调一次,SQL 层 WHERE 保证只有一次 rowcount==1
    返回 True,触发 lipsync。第二次调用 rowcount=0 返 False,不重复触发。

    防 race condition 的核心 — 替代复合状态(both_ready)。
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE oral_sessions
               SET status = 'lipsync_running',
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
               AND new_audio_url IS NOT NULL AND new_audio_url != ''
               AND swapped_video_url IS NOT NULL AND swapped_video_url != ''
               AND status NOT IN ('lipsync_running', 'completed', 'cancelled')
               AND status NOT LIKE 'failed_%'
            """,
            (session_id,),
        )
        conn.commit()
        return cursor.rowcount == 1


# ==================== 异步驱动:Step 4 视频换装(wan-vace inpainting)====================


async def _run_inpainting_step(session_id: str) -> None:
    """Step 4 视频换装:fal-ai/wan-vace-14b/inpainting + 单帧 mask + salient tracking。

    与 _run_tts_step 真并行(/edit 端点同时启 2 个 task)。
    完成后调 _try_advance_to_lipsync 触发 Step 5。
    失败按 §7.2 退 20%。
    """
    import fal_client

    session = _get_session(session_id)
    if not session:
        _log(f"_run_inpainting_step: session {session_id} 已不存在,跳过")
        return

    try:
        if not session.get("mask_image_path"):
            raise RuntimeError("mask_image_path 缺失 — 用户必须先上传 mask 才能换装")

        models = json.loads(session.get("selected_models") or "[]")
        products = json.loads(session.get("selected_products") or "[]")
        if not models:
            raise RuntimeError("selected_models 为空")

        # 1) 上传 mask 到 fal storage
        mask_fal_url = await fal_client.upload_file_async(session["mask_image_path"])

        # 2) 上传原视频到 fal(也可以 archive_url 后用 https URL,但 fal upload 最稳)
        video_fal_url = await fal_client.upload_file_async(session["original_video_path"])

        # 3) reference_image_urls:模特 + 产品图(已经是 https URL,直接传)
        ref_urls = [m["image_url"] for m in models if m.get("image_url")]
        ref_urls += [p["image_url"] for p in products if p.get("image_url")]

        # 4) 自动 prompt
        names = [m.get("name", "") for m in models] + [p.get("name", "") for p in products]
        prompt = (
            f"Replace the person and product in the video with: {', '.join(names)}. "
            f"Maintain camera angles and movements."
        )

        # 5) 调 wan-vace inpainting
        from app.services.fal_service import get_inpainting_service
        inp_svc = get_inpainting_service()
        if not inp_svc:
            raise RuntimeError("FAL Inpainting service 未初始化")

        result = await inp_svc.inpaint(
            video_url=video_fal_url,
            mask_image_url=mask_fal_url,
            prompt=prompt,
            reference_image_urls=ref_urls if ref_urls else None,
            resolution=_resolution_for_tier(session["tier"]),
        )
        if "error" in result:
            raise RuntimeError(f"wan-vace: {result['error']}")

        swapped_url = result.get("video_url")
        if not swapped_url:
            raise RuntimeError("wan-vace 未返 video URL")

        # 6) 归档防 fal.media 30 天过期
        try:
            from app.services.media_archiver import archive_url
            swapped_url = await archive_url(swapped_url, str(session["user_id"]), "video")
        except Exception as arch_err:
            _log(f"_run_inpainting_step archive failed (continuing with fal URL): {arch_err}")

        # 7) 写库
        _update_session(
            session_id,
            swap_fal_request_id=result.get("model", ""),
            swapped_video_url=swapped_url,
        )
        _log(f"_run_inpainting_step OK session={session_id} url={swapped_url[:80]}")

        # 8) 尝试推进到 lipsync(若 TTS 也完成)
        if _try_advance_to_lipsync(session_id):
            _log(f"_run_inpainting_step: 双完成,触发 lipsync session={session_id}")
            asyncio.create_task(_run_lipsync_step(session_id))
    except Exception as e:
        _log(f"_run_inpainting_step FAIL session={session_id} err={e}")
        sess2 = _get_session(session_id)
        if not sess2 or sess2["status"] not in ("edit_submitted",):
            return
        # Step 4 失败退 20%(§7.2)
        refunded = _refund(sess2, "failed_step4")
        _update_session(
            session_id,
            status="failed_step4",
            error_step="step4",
            error_message=str(e)[:500],
            credits_refunded=refunded,
        )


# ==================== L2 合规:AIGC 水印烧录 ====================

# 深度合成规定 (2023-01-10) §16:AI 生成内容显著标识 + 不可移除。
# burn-in drawtext 满足"显著"+"不可移除";SVG 角标 / 文本注释都不达标。
_AIGC_FONT_FAMILY = "WenQuanYi Zen Hei"  # 服务器装的中文字体(/usr/share/fonts/truetype/wqy/)
_AIGC_TEXT = "AI 生成内容"


async def _apply_aigc_watermark(
    fal_video_url: str,
    user_id: str,
    session_id: str,
) -> str:
    """L2 合规:下载 fal final → ffmpeg drawtext 烧录 AIGC 水印 → 落本地归档。

    一举两得:水印烧录 + 替代 archive_url 防 fal.media 30 天过期。
    水印失败 raise — 无水印不算合格产物(深度合成规定),上层按 lipsync 失败处理。

    返:public URL `/uploads/oral/<uid>/<sid>/final.mp4`。
    """
    import httpx
    from app.api.video_studio import _run_ffmpeg

    out_dir = ORAL_UPLOAD_ROOT / user_id / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "_lipsync_raw.mp4"
    out_path = out_dir / "final.mp4"

    # 1) 下载 fal final
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        async with client.stream("GET", fal_video_url) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"download fal final {resp.status_code}")
            with raw_path.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)

    # 2) ffmpeg drawtext(右下角白字 + 黑底半透明,字号随高度,1080p ≈ 43 像素)
    vf = (
        f"drawtext=font='{_AIGC_FONT_FAMILY}':text='{_AIGC_TEXT}':"
        f"fontcolor=white@0.85:fontsize=h*0.04:"
        f"box=1:boxcolor=black@0.55:boxborderw=10:"
        f"x=w-tw-24:y=h-th-24"
    )
    ok, err = _run_ffmpeg([
        "ffmpeg", "-y", "-i", str(raw_path),
        "-vf", vf,
        "-c:a", "copy",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-movflags", "+faststart",
        str(out_path),
    ])
    if not ok:
        raise RuntimeError(f"AIGC watermark ffmpeg failed: {err[:200]}")

    raw_path.unlink(missing_ok=True)
    os.chmod(out_path, 0o644)

    public = f"/uploads/oral/{user_id}/{session_id}/final.mp4"
    _log(f"_apply_aigc_watermark OK session={session_id} -> {public}")
    return public


# ==================== 异步驱动:Step 5 口型对齐 ====================


async def _run_lipsync_step(session_id: str) -> None:
    """Step 5:口型对齐 + 合成最终视频(三档不同 endpoint)。

    由 _try_advance_to_lipsync 原子推进后触发。
    完成后状态 → completed。失败按 §7.2 退 30%。
    """
    import fal_client

    session = _get_session(session_id)
    if not session:
        _log(f"_run_lipsync_step: session {session_id} 已不存在,跳过")
        return

    try:
        if not session.get("swapped_video_url") or not session.get("new_audio_url"):
            raise RuntimeError("Step 4/3 产物缺失,_try_advance_to_lipsync 不该已经推进")

        from app.services.fal_service import get_lipsync_service
        lip_svc = get_lipsync_service()
        if not lip_svc:
            raise RuntimeError("FAL Lipsync service 未初始化")

        result = await lip_svc.sync(
            video_url=session["swapped_video_url"],
            audio_url=session["new_audio_url"],
            tier=session["tier"],
        )
        if "error" in result:
            raise RuntimeError(f"lipsync: {result['error']}")

        final_url = result.get("video_url")
        if not final_url:
            raise RuntimeError("lipsync 未返 video URL")

        # L2 合规:AIGC 水印烧录 + 落本地归档(替代原 archive_url 一步到位)
        # 失败 raise → 走下面 except,按 lipsync 失败退 30%(深度合成规定要求显著标识)
        watermarked_url = await _apply_aigc_watermark(
            final_url, str(session["user_id"]), session_id,
        )

        _update_session(
            session_id,
            lipsync_fal_request_id=result.get("model", ""),
            final_video_url=watermarked_url,
            final_video_archived=watermarked_url,
            status="completed",
            completed_at="CURRENT_TIMESTAMP",
        )
        _log(f"_run_lipsync_step OK session={session_id} url={watermarked_url[:80]}")
    except Exception as e:
        _log(f"_run_lipsync_step FAIL session={session_id} err={e}")
        sess2 = _get_session(session_id)
        if not sess2 or sess2["status"] != "lipsync_running":
            return
        refunded = _refund(sess2, "failed_step5")
        _update_session(
            session_id,
            status="failed_step5",
            error_step="step5",
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


# ==================== 端点 1b:POST /upload-chunk(七十七续 P5)====================
#
# Bug 修:用户反馈"上传特别慢"。诊断:服务器出口 27 Mbps,用户上行通常 5-20 Mbps,
# 60s 视频 50-100MB 走单次 multipart 上传 30-300s,且无进度反馈。
# 解:仿 video_studio /upload-chunk 模式 — 5MB 分片 + 失败补传 + 前端进度条。


@router.post("/upload-chunk")
async def upload_chunk(
    chunk: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_idx: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    current_user: dict = Depends(get_current_user),
):
    """分片上传:前端 5MB 块顺序调本端点,最后一片合并 + 创建 oral_session。

    复用 video_studio 同名端点的安全策略:
    - upload_id 16 位 hex 防路径穿越
    - 单 chunk ≤ 10MB
    - 每用户并行上传 ≤ 5
    - 累计 ≤ 200MB(60s 视频上限)
    """
    if not re.fullmatch(r"[a-f0-9]{16}", upload_id):
        raise HTTPException(400, "invalid upload_id")
    if chunk_idx < 0 or total_chunks < 1 or chunk_idx >= total_chunks:
        raise HTTPException(400, "invalid chunk_idx/total_chunks")
    if total_chunks > 1000:  # 60s 视频 ≤ 200MB,每片 5MB ≤ 40 片,留 25x 余量
        raise HTTPException(400, "too many chunks")

    user_id = str(current_user["id"])
    upload_dir = ORAL_UPLOAD_TMP / f"{user_id}_{upload_id}"

    # 同 user 并行 upload_id ≤ 5
    if not upload_dir.exists():
        ORAL_UPLOAD_TMP.mkdir(parents=True, exist_ok=True)
        existing = [p for p in ORAL_UPLOAD_TMP.glob(f"{user_id}_*") if p.is_dir()]
        if len(existing) >= 5:
            raise HTTPException(429, f"并行上传任务过多({len(existing)}/5)")

    upload_dir.mkdir(parents=True, exist_ok=True)

    # 流式写本片,单片 ≤ 10MB
    chunk_path = upload_dir / f"{chunk_idx:06d}"
    MAX_CHUNK_BYTES = 10 * 1024 * 1024
    written = 0
    try:
        with open(chunk_path, "wb") as f:
            while True:
                data = await chunk.read(1024 * 1024)
                if not data:
                    break
                written += len(data)
                if written > MAX_CHUNK_BYTES:
                    f.close()
                    chunk_path.unlink(missing_ok=True)
                    raise HTTPException(413, f"单 chunk 不得超过 {MAX_CHUNK_BYTES // (1024 * 1024)}MB")
                f.write(data)
    except HTTPException:
        raise
    except Exception:
        chunk_path.unlink(missing_ok=True)
        raise

    # 累计 200MB(60s 视频上限)
    MAX_UPLOAD_TOTAL = 200 * 1024 * 1024
    total_so_far = sum(p.stat().st_size for p in upload_dir.iterdir() if p.is_file())
    if total_so_far > MAX_UPLOAD_TOTAL:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(413, f"上传累计超过 {MAX_UPLOAD_TOTAL // (1024 * 1024)}MB")

    # 不是最后一片:回执
    if chunk_idx + 1 < total_chunks:
        return {"status": "chunk_received", "chunk_idx": chunk_idx, "received_bytes": chunk_path.stat().st_size}

    # 最后一片到达:校验所有 chunks 都在
    missing = [i for i in range(total_chunks) if not (upload_dir / f"{i:06d}").exists()]
    if missing:
        raise HTTPException(400, f"missing chunks: {missing[:5]}{'...' if len(missing) > 5 else ''}")

    # 合并到 session_dir
    session_id = str(uuid.uuid4())[:12]
    session_dir = ORAL_UPLOAD_ROOT / user_id / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    raw_ext = os.path.splitext(filename)[1] or ".mp4"
    ext = re.sub(r"[^a-zA-Z0-9.]", "", raw_ext)[:8] or ".mp4"
    video_path = session_dir / f"orig{ext}"

    with open(video_path, "wb") as out:
        for i in range(total_chunks):
            cp = upload_dir / f"{i:06d}"
            with open(cp, "rb") as f:
                shutil.copyfileobj(f, out, 1024 * 1024)

    shutil.rmtree(upload_dir, ignore_errors=True)

    size_bytes = video_path.stat().st_size
    from app.api.video_studio import _get_video_duration
    duration = _get_video_duration(str(video_path))

    if duration > MAX_DURATION_SECONDS:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(413, f"视频时长 {duration:.1f}s 超过 {MAX_DURATION_SECONDS} 秒上限")

    _create_session(session_id, user_id, str(video_path), duration)

    return {
        "status": "completed",
        "session_id": session_id,
        "duration_seconds": round(duration, 2),
        "size_mb": round(size_bytes / 1024 / 1024, 2),
    }


# ==================== 端点 1.5:POST /upload-mask ====================


@router.post("/upload-mask")
async def upload_mask(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """七十七续 P3:用户在前端 canvas 画完首帧 mask 后上传 PNG。

    fal salient tracking 沿时间轴自动传播全片(详见 docs/ORAL-BROADCAST-PLAN.md §14)。
    """
    user_id = str(current_user["id"])
    session = _get_session(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    if session["user_id"] != user_id:
        raise HTTPException(403, "无权限")

    # mask 上传不限状态(用户可能 ASR 跑完后再补 mask),但终态拒
    if session["status"] in (STATUS_TERMINAL_OK, STATUS_CANCELLED) or session["status"].startswith(STATUS_FAILED_PREFIX):
        raise HTTPException(400, f"session {session['status']},不能上传 mask")

    # 校验 PNG / JPG / WebP
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "mask 必须是图片(image/*)")

    # mask 通常 < 5MB,做基本上限保护
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(413, "mask 文件过大(>10MB)")

    video_path = Path(session["original_video_path"])
    session_dir = video_path.parent
    mask_path = session_dir / "mask.png"
    mask_path.write_bytes(contents)

    _update_session(session_id, mask_image_path=str(mask_path))

    return {"mask_image_path": str(mask_path), "size_bytes": len(contents)}


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

    # 七十七续 P3:Step 3 (TTS) + Step 4 (inpainting) 真并行
    # 各自完成后调 _try_advance_to_lipsync,SQL 原子保证 Step 5 只触发一次
    asyncio.create_task(_run_tts_step(req.session_id))
    asyncio.create_task(_run_inpainting_step(req.session_id))

    return {"status": "edit_submitted"}


# ==================== 端点 4:GET /status/{session_id} ====================


def _step_progress(status: str, session: Optional[dict] = None) -> dict:
    """5 步进度字典(前端进度条用)。

    P3 改造:**主要从 session 字段派生**(new_audio_url / swapped_video_url /
    final_video_url 是否为空),避免 status 字段需要表示 "tts_running + swap_running 同时"
    的复合状态。status 仅作为 transition 标记(asr_running / lipsync_running / completed)。
    """
    p = {"step1": "pending", "step2": "pending", "step3": "pending", "step4": "pending", "step5": "pending"}

    # 终态优先短路
    if status == "completed":
        return {k: "done" for k in p}
    if status.startswith("failed_") or status == "cancelled":
        # 失败时根据 session 字段冻结当前进度
        if session:
            if session.get("asr_transcript"):
                p["step1"] = "done"
            if session.get("edited_transcript"):
                p["step2"] = "done"
            if session.get("new_audio_url"):
                p["step3"] = "done"
            if session.get("swapped_video_url"):
                p["step4"] = "done"
        return p

    # 进行态:status 推断起点 + session 字段填实际进度
    if status == "uploaded":
        return p
    if status == "asr_running":
        p["step1"] = "running"
        return p
    if status == "asr_done":
        p["step1"] = "done"
        return p

    # 从 edit_submitted 起,Step 3/4 真并行,字段派生
    if session is None:
        # session 不传,只能保守映射(老接口兼容)
        if status == "edit_submitted":
            p["step1"] = p["step2"] = "done"
            return p
        if status == "lipsync_running":
            p["step1"] = p["step2"] = p["step3"] = p["step4"] = "done"
            p["step5"] = "running"
            return p
        return p

    p["step1"] = "done" if session.get("asr_transcript") else p["step1"]
    p["step2"] = "done" if session.get("edited_transcript") else p["step2"]

    # Step 3/4 状态由字段派生
    if session.get("new_audio_url"):
        p["step3"] = "done"
    elif session.get("edited_transcript"):
        p["step3"] = "running"

    if session.get("swapped_video_url"):
        p["step4"] = "done"
    elif session.get("edited_transcript") and session.get("mask_image_path"):
        p["step4"] = "running"

    if status == "lipsync_running":
        p["step5"] = "running"

    return p


@router.get("/status/{session_id}")
async def get_session_status(session_id: str, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["id"])
    session = _get_session(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    if session["user_id"] != user_id:
        raise HTTPException(403, "无权限")

    # 原视频公网 URL(给前端 canvas 抽首帧用)
    # /opt/ssp/uploads/oral/<uid>/<sid>/orig.mp4 → /uploads/oral/<uid>/<sid>/orig.mp4
    orig_path = session.get("original_video_path") or ""
    original_video_url = orig_path[orig_path.index("/uploads/"):] if "/uploads/" in orig_path else None

    return {
        "session_id": session_id,
        "status": session["status"],
        "tier": session["tier"],
        "duration_seconds": session["duration_seconds"],
        "credits_charged": session["credits_charged"],
        "credits_refunded": session["credits_refunded"],
        "step_progress": _step_progress(session["status"], session),
        "products": {
            "original_video_url": original_video_url,
            "asr_transcript": session.get("asr_transcript"),
            "edited_transcript": session.get("edited_transcript"),
            "new_audio_url": session.get("new_audio_url"),
            "swapped_video_url": session.get("swapped_video_url"),
            "final_video_url": session.get("final_video_url"),
            "mask_uploaded": bool(session.get("mask_image_path")),
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
