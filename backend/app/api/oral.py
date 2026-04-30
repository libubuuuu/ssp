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
import time
import uuid
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
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
from app.services.logger import log_warning

router = APIRouter()

# 用户上传根目录:/opt/ssp/uploads/oral/<user_id>/<sid>/
ORAL_UPLOAD_ROOT = Path(os.getenv("UPLOADS_ROOT", "/opt/ssp/uploads")) / "oral"
# 分片上传临时目录(_uploading/<user_id>_<upload_id>/<chunk_idx>)
ORAL_UPLOAD_TMP = ORAL_UPLOAD_ROOT / "_uploading"

# 八十四续:V3 VTON 管线 — Step B 单次 kling/reference 上限 10.05s,
# 长视频走 _run_inpainting_step 内 ffmpeg 拆段 + 并发驱动 + concat。
# 60s 上限对应 7 段并发(每段 9s),fal 排队 + 限速可控。
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


def _refund(session: dict, status: str, override_ratio: Optional[float] = None) -> int:
    """按 status 比例退款。返回实退积分。

    八十四:`override_ratio` 用于区分 "用户输入问题"(扣阶段费)vs "服务端故障"
    (100% 退,不让用户为我们或第三方的故障买单)。
    """
    ratio = override_ratio if override_ratio is not None else REFUND_RATIO.get(status, 0.0)
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

        # 八十四续:fal voice-clone 故障期 bypass 开关。
        # 启用后跳过 voice-clone,直接用原视频完整音轨作 new_audio_url,
        # 绕过 fal "Failed to download preview audio" 偶发故障。
        # 代价:用户编辑的新文案不生效(原音频不变)。
        # 用 extracted_audio_path(完整音轨)而不是 voice_ref_path(前 10s),
        # 确保后续 lipsync 阶段音频长度跟视频对齐。
        # 设 ORAL_BYPASS_VOICE_CLONE=true 启用,fal 服务恢复后改回 false。
        if os.environ.get("ORAL_BYPASS_VOICE_CLONE", "").lower() == "true":
            audio_path = session.get("extracted_audio_path")
            if not audio_path or not os.path.exists(audio_path):
                raise RuntimeError("extracted_audio_path 缺失或不存在,无法 bypass")
            audio_fal_url = await fal_client.upload_file_async(audio_path)
            log_warning(
                "voice_clone_bypassed",
                user=str(session.get("user_id", "")),
                session=session_id,
                note="fal voice-clone 故障期临时方案,直接用原音频",
            )
            _update_session(
                session_id,
                voice_provider="bypass",
                voice_id="",
                new_audio_url=audio_fal_url,
            )
            _log(f"_run_tts_step BYPASS session={session_id}")
            if _try_advance_to_lipsync(session_id):
                _log(f"_run_tts_step BYPASS: 双完成,触发 lipsync session={session_id}")
                asyncio.create_task(_run_lipsync_step(session_id))
            return

        # 八十三:fal-ai/minimax/voice-clone text 硬上限 1000 字符。前端编辑器已加
        # 字数计数 + 超限 disabled,这里是兜底 — 防有人绕前端直接 POST /api/oral/edit。
        # 截断而不是 raise:让用户拿到截断版本的成片,总比 fail 好。
        if len(edited_text) > 1000:
            log_warning(
                "voice_clone_text_truncated",
                user=str(session.get("user_id", "")),
                session=session_id,
                orig_len=len(edited_text),
            )
            edited_text = edited_text[:1000]

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
        # 八十四:fal/MiniMax 服务端故障 100% 退款,不让用户为第三方故障买单。
        # 用户输入问题(text 超长被截断后再失败、audio 损坏等)走原 95% 阶段费扣除。
        err_str = str(e)
        is_fal_fault = (
            "Failed to download" in err_str
            or "preview audio" in err_str
            or "timeout" in err_str.lower()
            or "Internal Server Error" in err_str
            or " 502" in err_str or " 503" in err_str or " 504" in err_str
        )
        refunded = _refund(sess2, "failed_step3", override_ratio=1.0 if is_fal_fault else None)
        error_message = err_str
        if is_fal_fault:
            error_message += " (fal 服务故障,已全额退款)"
            log_warning(
                "voice_clone_fal_fault_full_refund",
                user=str(sess2.get("user_id", "")),
                session=session_id,
                refund=refunded,
                err=err_str[:200],
            )
        _update_session(
            session_id,
            status="failed_step3",
            error_step="step3",
            error_message=error_message[:500],
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
    """Step 4 视频换装(V3 + 八十四续 P10:Seedream 多图融合 reference 路线)。

    Step A — Seedream v4 edit 多图融合(fal-ai/bytedance/seedream/v4/edit)
        [原视频首帧, 模特图, 产品图(可选)] + 中文 prompt → 合成静图(vton_image_url)
        关键:reference 图自带原视频背景 → 防止 Step B kling 把视频场景漂走
        (P9 仅靠 BG_LOCK 英文 prompt 是软提示,kling/reference 端点没有
         主体 mask / 背景锁定字段,治标不治根)
    Step B — kling/reference 动作驱动(fal-ai/kling-video/o1/video-to-video/reference)
        合成静图(reference)+ 原视频(driving)→ swapped_video_url
        单次 ≤10.05s,长视频走拆段并发 + ffmpeg concat。

    与 _run_tts_step 真并行(/edit 端点同时启 2 个 task),完成后调
    _try_advance_to_lipsync 触发 Step 5。失败按 §7.2 退 20%。
    """
    import fal_client
    import subprocess
    import tempfile
    from app.services.media_archiver import archive_url
    from app.services.fal_service import get_video_service

    session = _get_session(session_id)
    if not session:
        _log(f"_run_inpainting_step: session {session_id} 已不存在,跳过")
        return

    try:
        models = json.loads(session.get("selected_models") or "[]")
        products = json.loads(session.get("selected_products") or "[]")
        if not models:
            raise RuntimeError("selected_models 为空(必须有模特图)")
        model_url = models[0].get("image_url")
        if not model_url:
            raise RuntimeError("模特图 image_url 缺失")

        vid_svc = get_video_service()
        if not vid_svc:
            raise RuntimeError("FAL Video service 未初始化")

        user_id = str(session["user_id"])
        original_video_path = session["original_video_path"]

        # ---------- Step A:Seedream v4 edit 多图融合 ----------
        from app.api.video_studio import _run_ffmpeg, _get_video_duration
        # 1. 抽原视频首帧
        frame_fd, frame_path_str = tempfile.mkstemp(
            prefix=f"oral_frame_{session_id}_", suffix=".jpg"
        )
        os.close(frame_fd)
        frame_path = Path(frame_path_str)
        try:
            ok_f, ferr = _run_ffmpeg([
                "ffmpeg", "-y", "-i", original_video_path,
                "-vframes", "1", "-q:v", "2", str(frame_path),
            ])
            if not ok_f or not frame_path.exists() or frame_path.stat().st_size == 0:
                raise RuntimeError(f"抽首帧失败: {ferr[:200]}")
            frame_fal_url = await fal_client.upload_file_async(str(frame_path))

            # 2. 探测原视频宽高 → Seedream 输出按同比例(避免 reference 与 driving aspect 错位)
            seed_size = "portrait_16_9"  # 兜底:竖屏短视频
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height",
                     "-of", "csv=s=x:p=0", original_video_path],
                    capture_output=True, text=True, timeout=10,
                )
                w_str, h_str = probe.stdout.strip().split("x")
                w_i, h_i = int(w_str), int(h_str)
                scale = min(1.0, 2048 / max(w_i, h_i))
                seed_size = {"width": int(w_i * scale), "height": int(h_i * scale)}
            except Exception as probe_err:
                _log(f"_run_inpainting_step probe size 失败,用 portrait_16_9 兜底: {probe_err}")

            # 3. 拼中文 prompt(Seedream 是字节模型,中文准)
            image_urls = [frame_fal_url, model_url]
            prompt_parts = [
                "保留第 1 张图作为画面的整体背景、光线、构图与相机角度,",
                "把画面里的人物替换为第 2 张图中的模特(完整保留模特的面部特征、发型、肤色),",
            ]
            if products and products[0].get("image_url"):
                garment_url = products[0]["image_url"]
                image_urls.append(garment_url)
                garment_name = (products[0].get("name") or "").strip()
                garment_desc = f"({garment_name})" if garment_name else ""
                prompt_parts.append(
                    f"让模特身穿第 3 张图所示的服装{garment_desc},"
                    "服装款式、颜色、细节与图 3 完全一致,"
                )
            prompt_parts.append(
                "整体风格与第 1 张图保持一致(同一场景、同一采光、同一镜头距离),"
                "禁止改成白底、棚景、纯色背景或其他场景。"
            )
            full_prompt = "".join(prompt_parts)

            # 4. 调 Seedream v4 edit
            seed_result = await fal_client.run_async(
                "fal-ai/bytedance/seedream/v4/edit",
                arguments={
                    "prompt": full_prompt,
                    "image_urls": image_urls,
                    "image_size": seed_size,
                },
            )
            images = seed_result.get("images") if isinstance(seed_result, dict) else None
            if not images or not images[0].get("url"):
                raise RuntimeError("Seedream Step A 未返图")
            seed_url = images[0]["url"]

            # 5. 归档防 fal.media 30 天过期,失败不阻塞
            try:
                seed_url = await archive_url(seed_url, user_id, "image")
            except Exception as arch_err:
                _log(f"_run_inpainting_step Seedream archive failed (continuing): {arch_err}")

            _update_session(session_id, vton_image_url=seed_url)
            _log(f"_run_inpainting_step Seedream Step A OK session={session_id} url={seed_url[:80]}")
            reference_image = seed_url
        finally:
            try:
                frame_path.unlink(missing_ok=True)
            except Exception:
                pass

        # ---------- Step B:拆段 + kling/reference 并发驱动 + concat ----------
        # 单次 kling/reference 上限 10.05s,长视频(>10s)必拆段。
        # 段长 9s 留 1s 余量;每段独立 reference 驱动;ffmpeg concat 硬切拼回。
        # 八十四续 P9:prompt 加"保留背景"防 kling 自由发挥改背景
        product_names = [p.get("name", "") for p in products if p.get("name")]
        BG_LOCK = (
            "Preserve the original background, scene, lighting, camera angle, and composition "
            "exactly as in the reference video — do not change the environment."
        )
        if product_names:
            prompt = (
                f"A person wearing {', '.join(product_names)}, "
                f"performing the same actions, gestures, and movements as in the reference video. "
                f"{BG_LOCK}"
            )
        else:
            prompt = (
                f"A person performing the same actions and movements as in the reference video. "
                f"{BG_LOCK}"
            )

        SEG_LEN_S = 9.0
        duration = float(session.get("duration_seconds") or 0)
        if duration <= 0:
            duration = _get_video_duration(session["original_video_path"])

        # 切段到 /tmp/oral_segs_<sid>/seg_NN.mp4(根目录上层 GC 兜底)
        import math
        seg_root = Path(tempfile.mkdtemp(prefix=f"oral_segs_{session_id}_"))
        try:
            n_segments = max(1, math.ceil(duration / SEG_LEN_S))
            seg_paths: List[Path] = []
            for i in range(n_segments):
                start = i * SEG_LEN_S
                seg_path = seg_root / f"seg_{i:02d}.mp4"
                # 最后一段用 -t 不限,让 ffmpeg 自己截到末尾(规避浮点边界)
                cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}",
                       "-i", session["original_video_path"]]
                if i < n_segments - 1:
                    cmd += ["-t", f"{SEG_LEN_S:.3f}"]
                cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an", str(seg_path)]
                ok, ferr = _run_ffmpeg(cmd)
                if not ok or not seg_path.exists():
                    raise RuntimeError(f"ffmpeg 切段 {i} 失败: {ferr[:200]}")
                seg_paths.append(seg_path)
            _log(f"_run_inpainting_step Step B 拆 {n_segments} 段 duration={duration:.1f}s session={session_id}")

            # 八十四续 P11:env switch 选 v2v 引擎
            #   ltx (默认)  → fal-ai/ltx-2.3-22b/distilled/reference-video-to-video
            #                  蒸馏加速 + 1/10 定价,实测可能 5-15 min/段
            #   kling        → fal-ai/kling-video/o1/video-to-video/reference
            #                  闭源旗舰,实测 35-50 min/段(慢)
            engine = (os.getenv("ORAL_STEP_B_ENGINE", "ltx") or "ltx").lower()
            if engine == "ltx":
                endpoint_default = "fal-ai/ltx-2.3-22b/distilled/reference-video-to-video"
                seg_timeout_loops = 180  # 30 min cap (LTX 蒸馏版预期 5-15 min)
            else:
                endpoint_default = "fal-ai/kling-video/o1/video-to-video/reference"
                seg_timeout_loops = 360  # 60 min cap (kling 实测 35-50 min)
            _log(f"_run_inpainting_step Step B engine={engine} endpoint={endpoint_default} session={session_id}")

            sem = asyncio.Semaphore(3)

            async def _drive_one(seg_idx: int, seg_path: Path) -> str:
                async with sem:
                    seg_fal_url = await fal_client.upload_file_async(str(seg_path))
                    if engine == "ltx":
                        # LTX-2.3 distilled reference-v2v:single image_url + match_video_length
                        args = {
                            "video_url": seg_fal_url,
                            "image_url": reference_image,
                            "prompt": prompt,
                            "match_video_length": True,
                        }
                        endpoint = endpoint_default
                        try:
                            handler = await fal_client.submit_async(endpoint, arguments=args)
                            task_id = handler.request_id
                        except Exception as e:
                            raise RuntimeError(f"LTX seg {seg_idx} submit: {e}")
                    else:
                        drive_result = await vid_svc.drive_with_reference(
                            driving_video_url=seg_fal_url,
                            reference_image_url=reference_image,
                            prompt=prompt,
                        )
                        if "error" in drive_result:
                            raise RuntimeError(f"kling/reference seg {seg_idx}: {drive_result['error']}")
                        task_id = drive_result.get("task_id")
                        if not task_id:
                            url = drive_result.get("video_url")
                            if not url:
                                raise RuntimeError(f"seg {seg_idx} 既无 task_id 也无 video URL")
                            return url
                        endpoint = drive_result.get("model", endpoint_default)

                    for _ in range(seg_timeout_loops):
                        await asyncio.sleep(10)
                        status_obj = await fal_client.status_async(endpoint, task_id, with_logs=False)
                        state = status_obj.status if hasattr(status_obj, "status") else None
                        if state == "COMPLETED":
                            final = await fal_client.result_async(endpoint, task_id)
                            video_obj = final.get("video") if isinstance(final, dict) else None
                            url = (
                                video_obj.get("url") if isinstance(video_obj, dict)
                                else final.get("video_url") if isinstance(final, dict)
                                else None
                            )
                            if not url:
                                raise RuntimeError(f"seg {seg_idx} {engine} 未返 video URL")
                            return url
                    raise RuntimeError(f"seg {seg_idx} {engine} 超时({seg_timeout_loops*10//60} min)")

            seg_urls = await asyncio.gather(*[_drive_one(i, p) for i, p in enumerate(seg_paths)])
            _log(f"_run_inpainting_step Step B {n_segments} 段全部完成 session={session_id}")

            # 下载所有段到本地 + ffmpeg concat
            import httpx
            local_seg_paths: List[Path] = []
            async with httpx.AsyncClient(timeout=120.0) as client:
                for i, url in enumerate(seg_urls):
                    out = seg_root / f"out_{i:02d}.mp4"
                    async with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        with open(out, "wb") as f:
                            async for chunk in resp.aiter_bytes(64 * 1024):
                                f.write(chunk)
                    local_seg_paths.append(out)

            # concat list
            concat_list = seg_root / "concat.txt"
            with open(concat_list, "w") as f:
                for p in local_seg_paths:
                    f.write(f"file '{p}'\n")

            # 落最终拼接产物到 oral uploads(对外可访问)
            session_dir = ORAL_UPLOAD_ROOT / user_id / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            merged = session_dir / "swapped.mp4"
            ok, ferr = _run_ffmpeg([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an",
                str(merged),
            ])
            if not ok or not merged.exists():
                raise RuntimeError(f"ffmpeg concat 失败: {ferr[:200]}")

            swapped_url = f"https://ailixiao.com/uploads/oral/{user_id}/{session_id}/swapped.mp4"
            _update_session(
                session_id,
                swap_fal_request_id=endpoint_default,
                swapped_video_url=swapped_url,
            )
            _log(f"_run_inpainting_step Step B OK session={session_id} segments={n_segments} → {swapped_url}")
        finally:
            shutil.rmtree(seg_root, ignore_errors=True)

        # 触发 lipsync(若 TTS 也完成)
        if _try_advance_to_lipsync(session_id):
            _log(f"_run_inpainting_step: 双完成,触发 lipsync session={session_id}")
            asyncio.create_task(_run_lipsync_step(session_id))
    except Exception as e:
        _log(f"_run_inpainting_step FAIL session={session_id} err={e}")
        sess2 = _get_session(session_id)
        if not sess2 or sess2["status"] not in ("edit_submitted",):
            return
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


# 终态邮件去重(in-memory) — 防 _update_session 重复进入终态分支重发
# backend 重启后清空,但 5 个 _run_*_step 在重启后是 orphan task 不会重跑,无重发风险
_oral_notified_terminal: set = set()


async def _send_oral_terminal_email(session_id: str, status: str, refunded: int) -> None:
    """fire-and-forget 邮件通知 — 失败不影响主流程,异常吞掉。"""
    try:
        session = _get_session(session_id)
        if not session:
            return
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT email FROM users WHERE id = ?", (session["user_id"],))
            row = cursor.fetchone()
        if not row:
            return
        email = row["email"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        if not email:
            return

        from app.services.notify_email import send_oral_completion, send_oral_failure
        if status == STATUS_TERMINAL_OK:
            send_oral_completion(
                email=email,
                sid=session_id,
                tier=session.get("tier") or "",
                duration_seconds=float(session.get("duration_seconds") or 0),
                final_url=session.get("final_video_url") or "",
            )
        elif status.startswith(STATUS_FAILED_PREFIX):
            send_oral_failure(
                email=email,
                sid=session_id,
                error_step=session.get("error_step") or status,
                error_message=session.get("error_message") or "",
                refunded_credits=int(refunded or 0),
            )
    except Exception as e:
        _log(f"_send_oral_terminal_email FAIL session={session_id} status={status} err={e}")


def _update_session(session_id: str, **fields) -> None:
    """更新指定字段,自动加 updated_at。

    commit 后 fire-and-forget 两个 hook:
    1) _broadcast_session_status — WS 推送(P10)
    2) _send_oral_terminal_email — 终态(completed / failed_*)邮件通知;
       cancelled 不发(用户主动取消不需要打扰),重复进入用 in-memory set 去重

    不在 event loop 里调用(sync 测试路径)静默跳过。
    """
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
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_broadcast_session_status(session_id))

    new_status = fields.get("status")
    if not new_status:
        return
    is_complete = new_status == STATUS_TERMINAL_OK
    is_failed = new_status.startswith(STATUS_FAILED_PREFIX)
    if (is_complete or is_failed) and session_id not in _oral_notified_terminal:
        _oral_notified_terminal.add(session_id)
        refunded = int(fields.get("credits_refunded") or 0)
        loop.create_task(_send_oral_terminal_email(session_id, new_status, refunded))


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
    try:
        duration = _get_video_duration(str(video_path))
    except ValueError as e:
        # 八十一 backlog D 监控:统计"视频缺时长元数据"命中频率,
        # 一周后看是否需要上前端 ts-ebml patch
        log_warning(
            "upload_no_duration_metadata",
            user=user_id, file=file.filename, size=size_bytes, reason=str(e),
        )
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(e))
    if duration <= 0:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"视频时长无效({duration}s),请重新上传")
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

    # 八十二:孤儿目录 GC — 5/5 上限基于 fs 目录数,正常合并成功才 rmtree(L879+),
    # 任何中途失败 / 网络断 / 用户关页面 / 缺片 raise 都留孤儿。30 分钟无更新
    # 视为孤儿,先清再判 5/5。正常分片上传单 chunk 间隔 < 几秒,不会误清。
    ORAL_UPLOAD_TMP.mkdir(parents=True, exist_ok=True)
    ORPHAN_GC_AGE_SEC = 30 * 60
    _now = time.time()
    for _d in ORAL_UPLOAD_TMP.glob(f"{user_id}_*"):
        if _d.is_dir() and (_now - _d.stat().st_mtime) > ORPHAN_GC_AGE_SEC:
            shutil.rmtree(_d, ignore_errors=True)

    # 同 user 并行 upload_id ≤ 5(GC 后的真实在用数)
    if not upload_dir.exists():
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
    try:
        duration = _get_video_duration(str(video_path))
    except ValueError as e:
        # 八十一 backlog D 监控:统计"视频缺时长元数据"命中频率
        log_warning(
            "upload_no_duration_metadata",
            user=user_id, file=filename, size=size_bytes, reason=str(e),
        )
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(e))
    if duration <= 0:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"视频时长无效({duration}s),请重新上传")
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


# ==================== 端点 1.6:POST /finalize-cos(八十四续 P5)====================
# 浏览器直传 COS 完成后调本端点,后端从 COS 拉文件到本地 oral session 目录
# (同区域 GB/s),ffprobe 拿 duration + 创建 session。
# 优势:用户上行不再被腾讯云轻量服务器 32Mbps 出口锁死,也不经过 CF。

class FinalizeCosBody(BaseModel):
    object_key: str       # 形如 "uploads/<user_id>/<ts>_<safe_name>"
    filename: str         # 原始文件名,用于推断 ext
    file_size: int        # 浏览器报的字节数,服务端会复核


@router.post("/finalize-cos")
async def finalize_cos_upload(
    body: FinalizeCosBody,
    current_user: dict = Depends(get_current_user),
):
    """浏览器 COS 直传完成后调用 — 后端从 COS 同区域拉文件到本地 + 建 session。"""
    import httpx
    from app.services.storage_sts import _check_enabled
    from app.config import get_settings
    settings = get_settings()

    try:
        _check_enabled()
    except Exception as e:
        raise HTTPException(503, f"COS 未启用: {e}")

    user_id = str(current_user["id"])
    # object_key 必须以 uploads/<user_id>/ 开头,防止越权拉别人的文件
    expected_prefix = f"{settings.STORAGE_BUCKET_PREFIX.rstrip('/')}/{user_id}/"
    if not body.object_key.startswith(expected_prefix):
        raise HTTPException(403, f"object_key 必须以 {expected_prefix} 开头")
    if len(body.object_key) > 400:
        raise HTTPException(400, "object_key 过长")
    if body.file_size <= 0 or body.file_size > 500 * 1024 * 1024:
        raise HTTPException(413, f"file_size {body.file_size} 不合法(0 < 且 ≤ 500MB)")

    # 私有 bucket → 用 COS SDK 主账号 SecretKey 签名 GET(同区域内网,GB/s)
    # 八十四续 P8 fix:之前直接 httpx GET public URL 被 COS 403 拒
    # (私有 bucket 任何 GET 都要签名,我之前误以为同区域有内部权限)
    from qcloud_cos import CosConfig, CosS3Client
    cos_config = CosConfig(
        Region=settings.STORAGE_REGION,
        SecretId=settings.STORAGE_SECRET_ID,
        SecretKey=settings.STORAGE_SECRET_KEY,
    )
    cos_client = CosS3Client(cos_config)

    # 拉到本地 session_dir
    session_id = str(uuid.uuid4())[:12]
    session_dir = ORAL_UPLOAD_ROOT / user_id / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    raw_ext = os.path.splitext(body.filename)[1] or ".mp4"
    ext = re.sub(r"[^a-zA-Z0-9.]", "", raw_ext)[:8] or ".mp4"
    video_path = session_dir / f"orig{ext}"

    try:
        # COS SDK download_file 内部走 GetObject,带签名,流式写本地
        cos_client.download_file(
            Bucket=settings.STORAGE_BUCKET,
            Key=body.object_key,
            DestFilePath=str(video_path),
        )
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(502, f"COS 拉取异常: {str(e)[:200]}")

    actual_size = video_path.stat().st_size
    if actual_size == 0:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(502, "COS 拉取后文件为空")

    # ffprobe 拿 duration(失败已带 ffmpeg remux 兜底)
    from app.api.video_studio import _get_video_duration
    try:
        duration = _get_video_duration(str(video_path))
    except ValueError as e:
        log_warning(
            "upload_no_duration_metadata",
            user=user_id, file=body.filename, size=actual_size, reason=str(e),
        )
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(400, str(e))

    if duration <= 0:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(400, f"视频时长无效({duration}s),请重新上传")
    if duration > MAX_DURATION_SECONDS:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(413, f"视频时长 {duration:.1f}s 超过 {MAX_DURATION_SECONDS} 秒上限")

    _create_session(session_id, user_id, str(video_path), duration)

    return {
        "status": "completed",
        "session_id": session_id,
        "duration_seconds": round(duration, 2),
        "size_mb": round(actual_size / 1024 / 1024, 2),
    }


# ==================== 端点 1.5:POST /upload-mask ====================


@router.post("/upload-mask")
async def upload_mask(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    kind: str = Form("person"),
    current_user: dict = Depends(get_current_user),
):
    """七十七续 P3 + P9b:用户在前端 canvas 画完首帧 mask 后上传 PNG。

    P9b 双 mask 双轮 inpaint:
    - kind=person → 写 person_mask_image_path(必填,换人)
    - kind=product → 写 product_mask_image_path(可选,换产品)
    - 兼容老前端:kind 默认 "person",同时回写 mask_image_path = person_mask_path
      让旧 status 派生 / GC 路径仍能识别

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

    if kind not in ("person", "product"):
        raise HTTPException(400, "kind 必须是 person | product")

    # 校验 PNG / JPG / WebP
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "mask 必须是图片(image/*)")

    # mask 通常 < 5MB,做基本上限保护
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(413, "mask 文件过大(>10MB)")

    video_path = Path(session["original_video_path"])
    session_dir = video_path.parent
    filename = "mask.png" if kind == "person" else "product_mask.png"
    mask_path = session_dir / filename
    mask_path.write_bytes(contents)

    if kind == "person":
        # legacy mask_image_path 同步写,兼容老派生路径
        _update_session(session_id, person_mask_image_path=str(mask_path), mask_image_path=str(mask_path))
    else:
        _update_session(session_id, product_mask_image_path=str(mask_path))

    return {"kind": kind, "mask_image_path": str(mask_path), "size_bytes": len(contents)}


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

    # 防御兜底:历史 duration=0 脏 session(C 修复前已写库)走到这一步会让
    # compute_charge=0 → deduct_credits 拒 amount<=0 → 误导成 500"扣费失败"。
    # 早 raise 4xx 让前端能展示明确文案。
    if session["duration_seconds"] <= 0:
        raise HTTPException(
            status_code=400,
            detail="session 视频时长无效(可能是历史脏数据),请删除该任务后重新上传",
        )

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


def _build_status_payload(session: dict) -> dict:
    """status 端点 + WS 推送共用 — 保证前端只看一种数据格式。"""
    orig_path = session.get("original_video_path") or ""
    original_video_url = orig_path[orig_path.index("/uploads/"):] if "/uploads/" in orig_path else None
    return {
        "session_id": session["id"],
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
            "swap1_video_url": session.get("swap1_video_url"),
            "swapped_video_url": session.get("swapped_video_url"),
            "final_video_url": session.get("final_video_url"),
            # 八十四续 V3:VTON 管线无需 mask,这三个字段恒返 True 兼容老前端
            # (新前端不再读这些字段;旧 cache 期内的浏览器也不会卡校验)
            "mask_uploaded": True,
            "person_mask_uploaded": True,
            "product_mask_uploaded": True,
            "vton_image_url": session.get("vton_image_url"),
        },
        "error": session.get("error_message") if session["status"].startswith("failed_") else None,
    }


@router.get("/status/{session_id}")
async def get_session_status(session_id: str, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["id"])
    session = _get_session(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    if session["user_id"] != user_id:
        raise HTTPException(403, "无权限")
    return _build_status_payload(session)


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


# ==================== WebSocket 实时进度推送 ====================

# session_id → 订阅 WS 集合(替代原 4s 轮询,_update_session 出口统一推送)
_oral_ws_connections: dict = {}

# 终态状态前缀/取值,推完 final 后关连接
_TERMINAL_STATUSES = {STATUS_TERMINAL_OK, STATUS_CANCELLED}


def _is_terminal(status: str) -> bool:
    return status in _TERMINAL_STATUSES or status.startswith(STATUS_FAILED_PREFIX)


async def _broadcast_session_status(session_id: str) -> None:
    """从 DB 读最新 session,构造 status payload 推给所有订阅者。

    终态(completed / cancelled / failed_*)推完后关闭连接清理订阅。
    """
    conns = _oral_ws_connections.get(session_id)
    if not conns:
        return
    session = _get_session(session_id)
    if not session:
        return
    payload = _build_status_payload(session)
    dead = []
    for ws in list(conns):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        conns.discard(ws)
    if not conns:
        _oral_ws_connections.pop(session_id, None)
        return
    if _is_terminal(session["status"]):
        conns = _oral_ws_connections.pop(session_id, set())
        for ws in list(conns):
            try:
                await ws.close(code=1000, reason="session done")
            except Exception:
                pass


@router.websocket("/ws/{session_id}")
async def websocket_session_updates(websocket: WebSocket, session_id: str):
    """口播 session 实时进度推送。

    鉴权:JWT(?token=...)+ 直接查 oral_sessions.user_id 验归属
    - 4401:token 缺失 / 无效 / 过期 / 类型错(refresh 不能调业务)
    - 4403:token 有效但 user 不是该 session 的 owner / session 不存在
    """
    from app.services.auth import decode_jwt_token

    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=4401, reason="token required")
        return
    payload = decode_jwt_token(token)
    if not payload:
        await websocket.close(code=4401, reason="invalid or expired token")
        return
    user_id = payload.get("user_id") or payload.get("sub")
    if not user_id:
        await websocket.close(code=4401, reason="invalid token payload")
        return

    session = _get_session(session_id)
    if not session or session["user_id"] != str(user_id):
        await websocket.close(code=4403, reason="not your session")
        return

    await websocket.accept()
    if session_id not in _oral_ws_connections:
        _oral_ws_connections[session_id] = set()
    _oral_ws_connections[session_id].add(websocket)

    try:
        await websocket.send_json(_build_status_payload(session))
        if _is_terminal(session["status"]):
            await websocket.close(code=1000, reason="session done")
            _oral_ws_connections[session_id].discard(websocket)
            if not _oral_ws_connections[session_id]:
                _oral_ws_connections.pop(session_id, None)
            return
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        conns = _oral_ws_connections.get(session_id)
        if conns is not None:
            conns.discard(websocket)
            if not conns:
                _oral_ws_connections.pop(session_id, None)
