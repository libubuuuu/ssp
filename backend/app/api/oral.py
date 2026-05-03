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

from app.services.fal_service import fal_upload_with_retry

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

# 八十四续 P24:长视频支持 — 上限 60s → 300s(5 分钟)
# Step B i2v 拆 5s/段 + sem(5) 并发,5 分钟视频 = 60 段并发跑,wallclock ≈ 8-15 分钟
# (单段 1-3 min × 60 段 ÷ 并发 5 = 12-36 min wallclock,实际看 fal 限速)
# Lipsync 端点(veed/sync-v2/latentsync)对长视频时长上限未实测,3-5 分钟视频可能成功,
# 失败则后续做"拆 N 段 → N 个独立 oral session 并发 → concat"
MAX_DURATION_SECONDS = 300

# 档位允许值
TIERS = ("economy", "standard", "premium")

# P42:assets role / type 白名单
_ASSET_ROLES = (
    "anchor_model",      # 模特角色锚定(可多张:正面/侧面/全身)
    "anchor_product",    # 产品角色锚定(可多张:正反/材质/logo)
    "scene_ref",         # 场景定调(背景/光感参考图)
    "shot_ref",          # 运镜参考(镜头语言参考视频,与 driving 不同)
    "rhythm_ref",        # 节奏氛围(参考音频,影响生成节奏)
)
_ASSET_TYPES = ("image", "video", "audio")

# P41:Step B 引擎白名单 — 用户可在 /start 显式指定;None/空串走 env(默认 kling-o1-edit)
# 各引擎 fal schema 真值见 _drive_one 各分支(2026-05-03 openapi 实查)
_STEP_B_ENGINES = (
    # P58 清理(删 wan-2-2-animate-replace,实测脸/光/字漂):
    "aliyun-wan2.7-r2v",        # 🆓 阿里通义万相(免费 + multi-reference + 70% 概率)
    "kling-o3-standard-v2v",    # ⭐ Kling O3 standard v2v edit(¥5.73/5s,element 多图 verified)
    "auto-cheap",               # P47-B 免费优先链:阿里→kling-standard
    # 老引擎保留向后兼容(老 session 重做时 backend 仍能跑,前端 dropdown 不露出):
    "i2v",
    "kling-o1-edit",
    "seedance-2-r2v",
    "kling-o3-r2v",
    "kling-o3-v2v",
    "kling-2-6-i2v",
    "pixverse-swap",
    "auto",
    "auto-best",
    "kling-3-pro-i2v",
)

# P47-A:auto 模式段级 fallback 链(主引擎失败 → 切下一个)
# 顺序基于"NSFW 友好度 + 速度":pixverse 最稳 → seedance 多素材 → wan 慢但通 NSFW → kling-o1-edit 兜底
FALLBACK_CHAIN_AUTO = ("pixverse-swap", "seedance-2-r2v", "kling-o1-edit")

# P58 清理:auto-cheap 免费优先链(全 verified 真复刻,删 Wan 2.2 实测漂):
# 阿里 wan(免费,70% 概率)→ Kling O3 standard v2v edit($0.126/s 多图 verified)
FALLBACK_CHAIN_CHEAP = ("aliyun-wan2.7-r2v", "kling-o3-standard-v2v")

# P48-B:Best-of-2 同段并发引擎(InsightFace 选 max similarity)
# probe 真值(2026-05-03,14c390bb 内衣场景):阿里 wan 0.4165, kling-3-pro 0.4096(均 0.41+ 强档),
# 其他端点(pixverse 0.28, kling-o3-4k 0.28)显著低,Best-of-2 用前两个最优。
# 成本:阿里 ¥0(免费)+ kling-3-pro $0.07/s × 5s = $0.35 ≈ ¥2.5/段
BEST_OF_N_ENGINES = ("aliyun-wan2.7-r2v", "kling-3-pro-i2v")  # 阿里 + Kling 3.0 Pro i2v
KLING3_PRO_I2V_ENDPOINT = "fal-ai/kling-video/v3/pro/image-to-video"

# fallback 时统一用的通用 prompt(主引擎用 P46-L1 完整 prompt 工程,fallback 降级)
FALLBACK_PROMPT = (
    "Primary identity anchor: the woman in the reference image. "
    "Do NOT alter facial proportions, eye spacing, nose shape, jawline, hair, or skin tone. "
    "Performing the same actions, gestures, and movements as in the reference video. "
    "Preserve the original background, lighting, and camera angle. "
    "No face distortion, no wardrobe changes, no color palette shift."
)

# 状态机 — 详见规划文档 §4.2
STATUS_INITIAL = "uploaded"
STATUS_TERMINAL_OK = "completed"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED_PREFIX = "failed_"


# ==================== Pydantic 请求/响应 ====================


class AssetItem(BaseModel):
    """P42:导演级工作流的单个素材条目。
    role: 在生成中的语义角色 — anchor_model / anchor_product / scene_ref / shot_ref / rhythm_ref
    type: image / video / audio
    alias: 用户起的引用名(prompt 用 @alias),可选
    """
    role: str
    type: str
    url: str
    alias: Optional[str] = None
    ord: Optional[int] = 0


class StartRequest(BaseModel):
    """POST /api/oral/start"""
    session_id: str
    tier: str
    models: List[dict]      # [{name, image_url}, ...] 1-4 个
    products: List[dict]    # [{name, image_url}, ...] 0-4 个
    legal_consent: bool     # L1 用户责任声明,前端勾选后传 true(规划文档 Q4)
    aspect_ratio: Optional[str] = None  # P16:成片比例 "9:16"/"16:9"/"1:1"/None(跟随原视频)
    step_b_engine: Optional[str] = None  # P41:Step B 引擎覆盖,白名单见 _STEP_B_ENGINES;None=跟随 env
    assets: Optional[List[AssetItem]] = None  # P42:多素材编排(场景图/运镜视频/节奏音频),None=老路单素材
    use_topaz_upscale: Optional[bool] = False  # P43-2:出片过 fal Topaz 超分(720p×2),+$0.02/秒
    use_face_enhance: Optional[bool] = True  # P45:模特图过 codeformer 修脸 + 成片首尾帧修脸,默认开


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


# P42:多素材编排 DB 操作
def _save_assets(session_id: str, assets: List[dict]) -> None:
    """把 [{role, type, url, alias, ord}, ...] 写入 oral_session_assets。
    每次 /start 调用前先清空老 assets(同一 session 重启不堆叠)。"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM oral_session_assets WHERE session_id = ?", (session_id,))
        for i, a in enumerate(assets):
            cursor.execute(
                "INSERT INTO oral_session_assets (session_id, role, type, url, alias, ord) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, a["role"], a["type"], a["url"], a.get("alias"), a.get("ord") or i),
            )
        conn.commit()


def _load_assets(session_id: str) -> List[dict]:
    """读 session 的 assets 列表,按 ord 排序"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT role, type, url, alias, ord FROM oral_session_assets WHERE session_id = ? ORDER BY ord ASC",
            (session_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


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

        # 2)+3) fal upload + ASR
        # P47-C:cheap 模式优先阿里 paraformer-v2(免费 180 天),失败降级 fal whisper
        # 主路 / cheap 由 session.step_b_engine 反推(auto-cheap = 全免费链)
        prefer_aliyun_asr = (session.get("step_b_engine") or "").lower() in ("auto-cheap", "aliyun-wan2.7-r2v")

        from app.services.fal_service import get_asr_service, get_aliyun_asr_service
        asr_svc = get_asr_service()
        aliyun_asr = get_aliyun_asr_service()

        result = None
        audio_fal_url = None  # 后面 demucs 还要用
        last_err: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                audio_fal_url = await fal_upload_with_retry(audio_path)
                # P47-C:cheap 模式先试阿里 paraformer-v2(免费)
                if prefer_aliyun_asr and aliyun_asr and aliyun_asr.is_available():
                    r = await aliyun_asr.transcribe(audio_fal_url, language="zh")
                    if "error" in r:
                        _log(f"_run_asr_step P47-C aliyun paraformer 失败,降级 fal whisper: {r['error']}")
                    else:
                        _log(f"_run_asr_step P47-C aliyun paraformer OK session={session_id}")
                        result = r
                        break
                # fal whisper 主路 / 阿里降级
                if not asr_svc:
                    raise RuntimeError("FAL ASR service 未初始化")
                r = await asr_svc.transcribe(audio_fal_url)
                if "error" in r:
                    raise RuntimeError(f"wizper: {r['error']}")
                result = r
                break
            except Exception as fe:
                last_err = fe
                _log(f"_run_asr_step session={session_id} attempt={attempt}/3 err={str(fe)[:200]}")
                if attempt < 3:
                    await asyncio.sleep(5 * attempt)
        if result is None:
            raise RuntimeError(f"ASR 重试 3 次仍失败: {str(last_err)[:300]}")

        # P44:demucs 音轨分离(vocals + BGM)。失败不阻塞主链路,降级用原音轨当 vocals。
        # P47-D:cheap 模式优先用本地 demucs worker(0 元 + 30s 处理 17s 音频),
        #         失败降级 fal demucs($0.05/段)。
        vocals_path: Optional[str] = None
        bgm_path: Optional[str] = None

        # P47-D 本地 demucs 优先(cheap 模式)
        local_demucs_ok = False
        if prefer_aliyun_asr:  # cheap 模式标志,P47-C 已加
            try:
                local_worker = "/opt/ssp/scripts/audio_separator_worker.py"
                local_venv_py = "/opt/ssp/face_venv/bin/python"
                if Path(local_worker).is_file() and Path(local_venv_py).is_file():
                    sep_t0 = time.time()
                    vp = str(session_dir / "vocals.mp3")
                    bp = str(session_dir / "bgm.mp3")
                    proc = await asyncio.create_subprocess_exec(
                        local_venv_py, local_worker, audio_path, vp, bp,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        sout, serr = await asyncio.wait_for(proc.communicate(), timeout=600)
                    except asyncio.TimeoutError:
                        proc.kill()
                        _log(f"_run_asr_step P47-D 本地 demucs 600s 超时")
                    else:
                        if proc.returncode == 0 and Path(vp).exists() and Path(bp).exists():
                            vocals_path = vp
                            bgm_path = bp
                            local_demucs_ok = True
                            _log(f"_run_asr_step P47-D 本地 demucs OK session={session_id} elapsed={time.time()-sep_t0:.1f}s "
                                 f"vocals={Path(vp).stat().st_size//1024}KB bgm={Path(bp).stat().st_size//1024}KB")
                        else:
                            err = (serr or b"").decode(errors="replace")[:300]
                            _log(f"_run_asr_step P47-D 本地 demucs rc={proc.returncode} err={err}")
            except Exception as e:
                _log(f"_run_asr_step P47-D 本地 demucs 异常,降级 fal: {e}")

        if not local_demucs_ok:
            # 本地 demucs 没跑 / 失败 → 走 fal demucs 主路 / 兜底
            try:
                from app.services.fal_service import get_audio_separator_service
                sep_svc = get_audio_separator_service()
                if sep_svc:
                    sep_t0 = time.time()
                    sep_result = await sep_svc.separate(audio_fal_url)
                    if "error" not in sep_result:
                        vocals_url = sep_result.get("vocals_url")
                        bgm_stem_urls = sep_result.get("bgm_stem_urls") or []
                        sep_model = sep_result.get("model", "?")
                        if vocals_url:
                            vp = str(session_dir / "vocals.mp3")
                            await _download_url_to(vocals_url, Path(vp))
                            vocals_path = vp
                        if bgm_stem_urls:
                            # 下载 4 stem → ffmpeg amix → bgm.mp3
                            from app.api.video_studio import _run_ffmpeg
                            stem_locals: List[str] = []
                            for i, url in enumerate(bgm_stem_urls):
                                sp = str(session_dir / f"bgm_stem_{i}.mp3")
                                await _download_url_to(url, Path(sp))
                                stem_locals.append(sp)
                            bp = str(session_dir / "bgm.mp3")
                            amix_cmd = ["ffmpeg", "-y"]
                            for sp in stem_locals:
                                amix_cmd += ["-i", sp]
                            amix_cmd += [
                                "-filter_complex",
                                f"amix=inputs={len(stem_locals)}:duration=longest:dropout_transition=0:normalize=0",
                                "-c:a", "libmp3lame", "-q:a", "2", bp,
                            ]
                            ok_m, err_m = _run_ffmpeg(amix_cmd)
                            if ok_m:
                                bgm_path = bp
                            else:
                                _log(f"_run_asr_step BGM amix 失败(不阻塞): {err_m[:200]}")
                            for sp in stem_locals:
                                try:
                                    Path(sp).unlink(missing_ok=True)
                                except Exception:
                                    pass
                        _log(f"_run_asr_step fal audio-sep OK session={session_id} model={sep_model} "
                             f"vocals={'yes' if vocals_path else 'no'} bgm={'yes' if bgm_path else 'no'} "
                             f"elapsed={time.time()-sep_t0:.1f}s")
                    else:
                        _log(f"_run_asr_step fal audio-sep 失败(不阻塞,降级原音轨): {sep_result.get('error')}")
            except Exception as sep_e:
                _log(f"_run_asr_step fal audio-sep 异常(不阻塞): {sep_e}")

        # 4) 写库,推进状态
        _update_session(
            session_id,
            extracted_audio_path=audio_path,
            voice_ref_audio_path=voice_ref_path,
            asr_transcript=result.get("text", ""),
            asr_word_timestamps=json.dumps(result.get("chunks", []), ensure_ascii=False),
            vocals_path=vocals_path,
            bgm_path=bgm_path,
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
            audio_fal_url = await fal_upload_with_retry(audio_path)
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
        voice_ref_fal_url = await fal_upload_with_retry(voice_ref_path)

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

    # P42:多素材编排 — 读 oral_session_assets。空列表 = 老路单素材模板。
    session_assets = _load_assets(session_id)
    if session_assets:
        _log(f"_run_inpainting_step P42 加载 {len(session_assets)} 个 assets session={session_id}")

    try:
        models = json.loads(session.get("selected_models") or "[]")
        products = json.loads(session.get("selected_products") or "[]")
        if not models:
            raise RuntimeError("selected_models 为空(必须有模特图)")
        model_url = models[0].get("image_url")
        if not model_url:
            raise RuntimeError("模特图 image_url 缺失")

        # P45:Step 0 — codeformer 增强模特图(预处理,补回 fal r2v 真人保身份残差)
        # fidelity=0.7 平衡画质 + 身份;失败降级用原图,不阻塞 pipeline
        # 真值:probe 实测 74s/张出 1024+ 分辨率,upscale=2 自带
        if session.get("use_face_enhance"):
            try:
                from app.services.fal_service import get_codeformer_service
                cf_svc = get_codeformer_service()
                if cf_svc:
                    cf_t0 = time.time()
                    cf_res = await cf_svc.restore(image_url=model_url, fidelity=0.7, upscale=2)
                    if "error" not in cf_res and cf_res.get("image_url"):
                        enhanced_url = cf_res["image_url"]
                        _update_session(session_id, enhanced_model_url=enhanced_url)
                        # 后续所有用 model_url 的地方都切到增强版(vton + 各引擎 frontal_image_url)
                        model_url = enhanced_url
                        _log(f"_run_inpainting_step P45 codeformer OK session={session_id} elapsed={time.time()-cf_t0:.1f}s")
                    else:
                        _log(f"_run_inpainting_step P45 codeformer 失败(降级原图): {cf_res.get('error','?')}")
            except Exception as cf_e:
                _log(f"_run_inpainting_step P45 codeformer 异常(降级原图): {cf_e}")

        vid_svc = get_video_service()
        if not vid_svc:
            raise RuntimeError("FAL Video service 未初始化")

        user_id = str(session["user_id"])
        original_video_path = session["original_video_path"]

        # ---------- Step A:Seedream v4 edit 多图融合 ----------
        from app.api.video_studio import _run_ffmpeg, _get_video_duration
        # P29:首帧可能是露肤 / 撩衣等被 Seedream content checker 拒的瞬间,
        # 改用候选帧池(中点优先 + 两侧分散),逐帧送 Seedream,遇 content_policy
        # 自动换下一帧。视频是合规的不代表第 0 帧是合规静图。
        frame_tmpdir = Path(tempfile.mkdtemp(prefix=f"oral_frames_{session_id}_"))
        try:
            duration_for_frames = float(session.get("duration_seconds") or 0)
            if duration_for_frames <= 0:
                duration_for_frames = _get_video_duration(original_video_path) or 0
            # P44-2:候选帧池 5→10 帧(中点 + 三层分散),NSFW 过审率 + 选帧多样性提升
            # 顺序按"被拒概率从低到高"排:中点最稳,接近端点最容易撩衣/露肤被拒
            FRAME_POS_RATIOS = [0.5, 0.4, 0.6, 0.35, 0.65, 0.25, 0.75, 0.15, 0.85, 0.5]
            frame_paths: List[Path] = []
            for i, ratio in enumerate(FRAME_POS_RATIOS):
                pos = max(0.0, duration_for_frames * ratio - 0.05)
                fp = frame_tmpdir / f"f_{i:02d}.jpg"
                ok_f, ferr = _run_ffmpeg([
                    "ffmpeg", "-y", "-ss", f"{pos:.2f}", "-i", original_video_path,
                    "-vframes", "1", "-q:v", "2", str(fp),
                ])
                if ok_f and fp.exists() and fp.stat().st_size > 0:
                    frame_paths.append(fp)
            if not frame_paths:
                raise RuntimeError("Step A 候选帧抽取全失败")

            # 决定 Seedream 输出尺寸:用户在 /start 选的 aspect_ratio 优先,
            # 否则按原视频比例自动跟随(P16)
            user_aspect = (session.get("aspect_ratio") or "").strip().lower()
            ASPECT_PRESETS = {
                "9:16": {"width": 720,  "height": 1280},
                "16:9": {"width": 1280, "height": 720},
                "1:1":  {"width": 1024, "height": 1024},
            }
            if user_aspect in ASPECT_PRESETS:
                seed_size = ASPECT_PRESETS[user_aspect]
            else:
                seed_size = "portrait_16_9"
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

            # P56:提前算 anchor_model_extra / anchor_product_extra(prompt_parts 拼接需要)
            anchor_model_extra = [a["url"] for a in session_assets if a.get("role") == "anchor_model" and a.get("url")]
            anchor_product_extra = [a["url"] for a in session_assets if a.get("role") == "anchor_product" and a.get("url")]

            # 拼中文 prompt(Seedream 是字节模型,中文准)。prompt 与具体帧无关,
            # 候选帧循环里复用同一份。
            prompt_parts = [
                "保留第 1 张图作为画面的整体背景、光线、构图与相机角度,",
                "把画面里的人物替换为第 2 张图中的模特(完整保留模特的面部特征、发型、肤色),",
            ]
            garment_url: Optional[str] = None
            if products and products[0].get("image_url"):
                garment_url = products[0]["image_url"]
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
            # P56:多角度信息融合 — 让 Flux/Seedream 把模特多角度 + 产品多角度的细节融合进首帧
            # 核心指令:模特身份多视角参考(让模型从多张图理解模特长什么样)
            #          产品反面/材质/logo 细节(让模型知道产品的完整形态,避免单角度漂)
            n_model_extra = len(anchor_model_extra)
            n_product_extra = len(anchor_product_extra)
            if n_model_extra > 0 or n_product_extra > 0:
                base_idx = 3 if garment_url else 2
                multi_parts = []
                if n_model_extra > 0:
                    extra_idxs = list(range(base_idx + 1, base_idx + 1 + n_model_extra))
                    multi_parts.append(
                        f"参考第 {','.join(str(i) for i in extra_idxs)} 张图中模特的其他角度(侧面/全身/背面/特写),"
                        f"综合理解模特的完整面部和身材特征,"
                    )
                if n_product_extra > 0:
                    p_start = base_idx + 1 + n_model_extra
                    p_idxs = list(range(p_start, p_start + n_product_extra))
                    multi_parts.append(
                        f"参考第 {','.join(str(i) for i in p_idxs)} 张图中产品的其他角度(反面/侧面/材质/logo/标签),"
                        f"综合理解产品的完整形态、纹理、文字、品牌标识,"
                        f"在合成画面中即使只能看到产品的一面,也要保留所有正确的颜色/材质/logo 细节。"
                    )
                prompt_parts.append("".join(multi_parts))
            full_prompt = "".join(prompt_parts)

            # 候选帧逐张送 Seedream,content_policy 类错误自动换下一帧;
            # 其他错误(网络/422/输入校验)直接 raise 走原 fail_step4 退款链路
            seed_url: Optional[str] = None
            last_seed_err: Optional[str] = None
            CONTENT_POLICY_KEYS = (
                "content_policy", "content_policy_violation",
                "partner_validation_failed", "content checker",
            )
            # P56:Step A 强化 — 多角度图融合(P53 anchor_model + anchor_product)进 Seedream/Flux 多图编辑
            # 让单图引擎也能从多张图汇聚"立体感多角度信息"
            # anchor_model_extra / anchor_product_extra 已在 prompt_parts 之前定义
            for idx, fp in enumerate(frame_paths):
                frame_fal_url = await fal_upload_with_retry(str(fp))
                image_urls = [frame_fal_url, model_url]
                if garment_url:
                    image_urls.append(garment_url)
                # 加多角度图(去重 + 上限 9 张图给 Seedream/Flux)
                for u in anchor_model_extra:
                    if u not in image_urls and len(image_urls) < 9:
                        image_urls.append(u)
                for u in anchor_product_extra:
                    if u not in image_urls and len(image_urls) < 9:
                        image_urls.append(u)
                try:
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
                    _log(f"_run_inpainting_step Seedream Step A OK frame_idx={idx} ratio={FRAME_POS_RATIOS[idx]} session={session_id}")
                    break
                except Exception as se:
                    msg = str(se)
                    last_seed_err = msg[:300]
                    is_content = any(kw in msg for kw in CONTENT_POLICY_KEYS)
                    _log(f"_run_inpainting_step Seedream frame_idx={idx} ratio={FRAME_POS_RATIOS[idx]} 失败 content_policy={is_content} err={msg[:200]}")
                    if not is_content:
                        raise
            if seed_url is None:
                raise RuntimeError(
                    f"Seedream Step A 所有 {len(frame_paths)} 帧均被内容审核拒,"
                    f"建议换一段视频或换装更保守的模特图。最后一次错误: {last_seed_err}"
                )

            # 归档防 fal.media 30 天过期,失败不阻塞
            try:
                seed_url = await archive_url(seed_url, user_id, "image")
            except Exception as arch_err:
                _log(f"_run_inpainting_step Seedream archive failed (continuing): {arch_err}")

            _update_session(session_id, vton_image_url=seed_url)
            _log(f"_run_inpainting_step Seedream Step A 归档 OK session={session_id} url={seed_url[:80]}")
            reference_image = seed_url
        finally:
            shutil.rmtree(frame_tmpdir, ignore_errors=True)

        # ---------- Step B:拆段 + 并发驱动 + concat ----------
        # 八十四续 P15:i2v 引擎从 seedance 切 kling/o3/standard
        # 字节家 seedance-2.0 fast 对内衣类硬拒 NSFW(content_policy_violation),
        # 实测 5 个 fal i2v/v2v 端点对同一 vton 图,只有 seedance 拒,kling/luma/LTX 全过。
        # 选 kling/o3/standard/image-to-video:quick-ad 实证可跑 + 产品首帧锁死。
        #
        # ORAL_STEP_B_ENGINE env 三选:
        #   i2v (默认) → fal-ai/kling-video/o3/standard/image-to-video
        #                首帧锁死产品(vton 图),NSFW 容忍内衣,实测 3-8 min/段
        #   ltx        → fal-ai/ltx-2.3-22b/distilled/reference-video-to-video
        #                v2v reference,复刻原动作但产品会漂
        #   kling-v2v  → fal-ai/kling-video/o1/video-to-video/reference
        #                v2v reference 老路,35-50 min/段
        # P41:session.step_b_engine 优先(用户实测对比),回落 env,再回落 "i2v"
        engine_session = (session.get("step_b_engine") or "").strip().lower()
        engine_env = (os.getenv("ORAL_STEP_B_ENGINE", "i2v") or "i2v").lower()
        engine = engine_session or engine_env
        # P44:auto = pixverse-swap 主路 alias
        # P47-A:auto 时启用段级 fallback 链(主引擎失败自动切下一个)
        # P47-B:auto-cheap 时启用免费优先链(阿里 wan2.7-r2v 主路)
        # P48-B:auto-best 时启用 Best-of-2 (阿里 wan + kling-3-pro 并发 + similarity 选优)
        auto_fallback = False
        cheap_fallback = False
        best_n_mode = False
        if engine == "auto":
            engine = "pixverse-swap"
            auto_fallback = True
        elif engine == "auto-cheap":
            engine = "pixverse-swap"
            cheap_fallback = True
        elif engine == "auto-best":
            # 主路是 Best-of-2(阿里 wan + kling-3-pro 并发),InsightFace 评分选最高
            engine = "pixverse-swap"  # 闭包外层 prepare 占位,真路径在 _drive_one_best_of_2
            best_n_mode = True
        # 兼容老配置:seedance-i2v 别名归一
        if engine == "seedance-i2v":
            engine = "i2v"
        if engine == "kling":  # 老 ltx fallback 名归一
            engine = "kling-v2v"
        product_names = [p.get("name", "") for p in products if p.get("name")]
        if engine == "i2v":
            endpoint_default = "fal-ai/kling-video/o3/standard/image-to-video"
            seg_timeout_loops = 90   # 15 min cap (kling i2v o3 standard 实测 3-8 min)
            SEG_LEN_S = 5.0          # kling i2v 单次默认 5s
            if product_names:
                prompt = (
                    f"A young woman wearing the {', '.join(product_names)} from the reference image, "
                    f"naturally showcasing the product with subtle hand gestures, smiling at camera, "
                    f"smooth body movement, photorealistic UGC selfie style, vertical 9:16 composition, "
                    f"soft natural lighting matching the reference image."
                )
            else:
                prompt = (
                    "A young woman from the reference image, naturally showcasing herself with subtle "
                    "hand gestures, smiling at camera, smooth body movement, photorealistic UGC selfie "
                    "style, vertical 9:16 composition, soft natural lighting matching the reference image."
                )
        elif engine == "kling-o1-edit":
            # P30 (2026-05-01):fal-ai/kling-video/o1/video-to-video/edit
            # 真 v2v 视频编辑 — 直接吃原视频段 + @Element 占位符替换人/物,
            # 复刻原动作骨架 + 衣物物理感保留(i2v 从静图凭空生成做不到)。
            # Probe 实测 2026-05-01:8d2389eb-110 baseline 9s 段,3:48 出片,
            # NSFW 内衣场景过审,产品锁死、动作复刻 OK。
            endpoint_default = "fal-ai/kling-video/o1/video-to-video/edit"
            seg_timeout_loops = 60   # 10 min cap(实测 ~4 min/段)
            SEG_LEN_S = 8.0          # 文档 3-10s,留 2s 余量;长视频均匀拆段
            # P43:Comfy-Org/workflow_templates 抽出的 Kling 官方 Keep/Replace/Adjust 三段式范式 +
            # maciejdzierzek/kling-ai-prompt-generator 的 motion endpoint 收尾句 + text-fixed 防漂
            # P46:identity-locking 前缀 + negation(magichour.ai 业界共识)
            ID_LOCK = (
                "Primary identity anchor: @Element1. Do NOT alter facial proportions, "
                "eye spacing, nose shape, jawline, hair, or skin tone. "
            )
            NEG = " No face distortion, no unintended wardrobe changes."
            if product_names:
                prompt = (
                    f"{ID_LOCK}"
                    f"Keep the scene, background, lighting, camera framing, camera movement, body posture, "
                    f"hand gestures, facial expressions and all decorative details from the reference video "
                    f"completely unchanged. Replace the woman in the video with @Element1. Replace her "
                    f"clothing/outfit with @Element2 ({', '.join(product_names)}); all text labels, logos "
                    f"and printed graphics on the product remain absolutely fixed and unchanged. Adjust the "
                    f"lighting and color tone of @Element1 and @Element2 to match the original background "
                    f"for a natural, cohesive visual effect. The motion ends and settles back into the "
                    f"starting position seamlessly."
                    f"{NEG}"
                )
            else:
                prompt = (
                    f"{ID_LOCK}"
                    "Keep the scene, background, lighting, camera framing, camera movement, body posture, "
                    "hand gestures, facial expressions and all decorative details from the reference video "
                    "completely unchanged. Replace the woman in the video with @Element1. Adjust the "
                    "lighting and color tone of @Element1 to match the original background for a natural, "
                    "cohesive visual effect. The motion ends and settles back into the starting position "
                    "seamlessly."
                    f"{NEG}"
                )
        elif engine == "seedance-2-r2v":
            # P41:fal-ai/bytedance/seedance-2.0/reference-to-video
            # 多素材统一 @Index 引用:image_urls(<=9)+ video_urls(<=3,2-15s)+ audio_urls
            # 注:2026-02 起,真人 prompt 下多参考能力被 ByteDance 阉割,实测验证。
            # 价格:有 video_urls $0.1814/s,无 video $0.3024/s
            # P46:段长 8s → 5s(magichour.ai 业界共识 3-5s 最稳,8s 漂)
            endpoint_default = "fal-ai/bytedance/seedance-2.0/reference-to-video"
            seg_timeout_loops = 60
            SEG_LEN_S = 5.0
            # P46:identity-locking 前缀 + negation(magichour 实战 prompt 模板)
            ID_LOCK = (
                "Primary identity anchor: @Image1. Do NOT alter facial proportions, "
                "eye spacing, nose shape, jawline, hair, or skin tone. "
            )
            NEG = " No face distortion, no wardrobe changes from @Image2, no color palette shift."
            if product_names:
                prompt = (
                    f"{ID_LOCK}"
                    f"@Image1 wearing the {', '.join(product_names)} from @Image2, "
                    f"performing the same actions, gestures, and movements as in @Video1. "
                    f"Preserve the original background and camera angle from @Video1 exactly."
                    f"{NEG}"
                )
            else:
                prompt = (
                    f"{ID_LOCK}"
                    "@Image1 performing the same actions and movements as in @Video1. "
                    "Preserve the original background and camera angle from @Video1 exactly."
                    f"{NEG}"
                )
        elif engine == "kling-o3-r2v":
            # P41:fal-ai/kling-video/o3/pro/reference-to-video
            # 纯 r2v(无 driving video),element 多图锁身份 + generate_audio + 每元素 voice_id
            # 价格 audio off $0.112/s,audio on $0.14/s
            # P46:段长 8s → 5s + identity-locking
            endpoint_default = "fal-ai/kling-video/o3/pro/reference-to-video"
            seg_timeout_loops = 60
            SEG_LEN_S = 5.0
            ID_LOCK = (
                "Primary identity anchor: @Element1. Do NOT alter facial proportions, "
                "eye spacing, nose shape, jawline, hair, or skin tone. "
            )
            NEG = " No face distortion, no wardrobe changes."
            if product_names:
                prompt = (
                    f"{ID_LOCK}"
                    f"@Element1 wearing @Element2 ({', '.join(product_names)}), "
                    f"naturally showcasing the product, smooth body movement, photorealistic UGC selfie style."
                    f"{NEG}"
                )
            else:
                prompt = (
                    f"{ID_LOCK}"
                    "@Element1 naturally showcasing herself, smooth body movement, "
                    "photorealistic UGC selfie style."
                    f"{NEG}"
                )
        elif engine == "kling-o3-standard-v2v":
            # P56:fal-ai/kling-video/o3/standard/video-to-video/edit
            # 真 v2v + element 多图(up to 4)+ "preserves motion structure" verbatim
            # 价格 $0.126/s = ¥4.5/5s 段(比 pro 便宜 25%)
            endpoint_default = "fal-ai/kling-video/o3/standard/video-to-video/edit"
            seg_timeout_loops = 60
            SEG_LEN_S = 5.0
            ID_LOCK = (
                "Primary identity anchor: @Element1. Do NOT alter facial proportions, "
                "eye spacing, nose shape, jawline, hair, or skin tone. "
            )
            NEG = " No face distortion, no unintended wardrobe changes."
            if product_names:
                prompt = (
                    f"{ID_LOCK}"
                    f"Replace the person in @Video1 with @Element1, wearing @Element2 "
                    f"({', '.join(product_names)}). Preserve the original motion, gestures, and camera movement."
                    f"{NEG}"
                )
            else:
                prompt = (
                    f"{ID_LOCK}"
                    "Replace the person in @Video1 with @Element1. "
                    "Preserve the original motion, gestures, and camera movement."
                    f"{NEG}"
                )
        elif engine == "kling-o3-v2v":
            # P41:fal-ai/kling-video/o3/pro/video-to-video/reference
            # 真 v2v + element 多图;keep_audio 默认 true,我们 lipsync 接管所以关掉
            # 价格 $0.168/s
            # P46:段长 8s → 5s + identity-locking
            endpoint_default = "fal-ai/kling-video/o3/pro/video-to-video/reference"
            seg_timeout_loops = 60
            SEG_LEN_S = 5.0
            ID_LOCK = (
                "Primary identity anchor: @Element1. Do NOT alter facial proportions, "
                "eye spacing, nose shape, jawline, hair, or skin tone. "
            )
            NEG = " No face distortion, no wardrobe changes from @Element2, no color palette shift."
            if product_names:
                prompt = (
                    f"{ID_LOCK}"
                    f"Replace the person in @Video1 with @Element1, wearing @Element2 "
                    f"({', '.join(product_names)}). Preserve the original motion, gestures, and camera movement."
                    f"{NEG}"
                )
            else:
                prompt = (
                    f"{ID_LOCK}"
                    "Replace the person in @Video1 with @Element1. "
                    "Preserve the original motion, gestures, and camera movement."
                    f"{NEG}"
                )
        elif engine == "pixverse-swap":
            # P44:fal-ai/pixverse/swap — 专门做 person/object/bg 替换
            # 输入:video_url(driving) + image_url(reference,单图);无 prompt;无 multi-ref
            # probe 真值(2026-05-03):内衣类参考图,seedance enterprise 直接拒,
            # pixverse 111.8s 出 8s 视频成功,**NSFW 友好**,推荐主路。
            # 5s 基线 $0.20,>5s 加倍($0.40 / 6-10s);定 5s 跟 keyframe_id 一致段长
            endpoint_default = "fal-ai/pixverse/swap"
            seg_timeout_loops = 60   # ~10min cap(实测 ~14s/s 出片,5s 段约 70-120s)
            SEG_LEN_S = 5.0
            prompt = ""              # 无 prompt 字段
        elif engine == "kling-2-6-i2v":
            # P41:fal-ai/kling-video/v2.6/pro/image-to-video(Native Audio 主打)
            # 单首帧 + native audio,duration 只能 "5" 或 "10"
            # 价格 audio off $0.07/s,audio on $0.14/s,+voice_ids $0.168/s
            endpoint_default = "fal-ai/kling-video/v2.6/pro/image-to-video"
            seg_timeout_loops = 60
            SEG_LEN_S = 5.0   # v2.6 duration 仅 "5"/"10",取 5 跟 i2v 一致段长
            if product_names:
                prompt = (
                    f"A young woman wearing the {', '.join(product_names)}, "
                    f"naturally showcasing the product with subtle hand gestures, smiling at camera, "
                    f"smooth body movement, photorealistic UGC selfie style."
                )
            else:
                prompt = (
                    "A young woman naturally showcasing herself with subtle hand gestures, "
                    "smiling at camera, smooth body movement, photorealistic UGC selfie style."
                )
        else:
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
            if engine == "ltx":
                endpoint_default = "fal-ai/ltx-2.3-22b/distilled/reference-video-to-video"
                seg_timeout_loops = 180   # 30 min cap
                SEG_LEN_S = 9.0
            else:  # kling-v2v
                endpoint_default = "fal-ai/kling-video/o1/video-to-video/reference"
                seg_timeout_loops = 360   # 60 min cap
                SEG_LEN_S = 9.0

        duration = float(session.get("duration_seconds") or 0)
        if duration <= 0:
            duration = _get_video_duration(session["original_video_path"])

        import math
        seg_root = Path(tempfile.mkdtemp(prefix=f"oral_segs_{session_id}_"))
        try:
            n_segments = max(1, math.ceil(duration / SEG_LEN_S))
            seg_durations: List[float] = []
            for i in range(n_segments):
                if i < n_segments - 1:
                    seg_durations.append(SEG_LEN_S)
                else:
                    seg_durations.append(max(1.0, duration - i * SEG_LEN_S))

            # i2v 路线不切原视频段(不用 driving video);v2v 才切
            # P30:kling-o1-edit 也吃原视频段(真 v2v),加入切段路径
            # P41:seedance-2-r2v(吃 video_urls 当参考)、kling-o3-v2v(吃 video_url driving)也切段
            seg_paths: List[Optional[Path]] = []
            if engine in ("ltx", "kling-v2v", "kling-o1-edit", "seedance-2-r2v", "kling-o3-v2v", "kling-o3-standard-v2v", "pixverse-swap", "aliyun-wan2.7-r2v") or cheap_fallback:
                for i in range(n_segments):
                    start = i * SEG_LEN_S
                    seg_path = seg_root / f"seg_{i:02d}.mp4"
                    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}",
                           "-i", session["original_video_path"]]
                    if i < n_segments - 1:
                        cmd += ["-t", f"{SEG_LEN_S:.3f}"]
                    cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an", str(seg_path)]
                    ok, ferr = _run_ffmpeg(cmd)
                    if not ok or not seg_path.exists():
                        raise RuntimeError(f"ffmpeg 切段 {i} 失败: {ferr[:200]}")
                    seg_paths.append(seg_path)
            else:
                seg_paths = [None] * n_segments
            _log(f"_run_inpainting_step Step B engine={engine} endpoint={endpoint_default} segs={n_segments} duration={duration:.1f}s session={session_id}")

            sem = asyncio.Semaphore(5)  # P24:并发 3→5,长视频(60s+)拆段时摊平

            async def _drive_one(seg_idx: int, seg_path: Optional[Path]) -> str:
                async with sem:
                    if engine == "i2v":
                        # i2v(kling/o3/standard):vton 图首帧 + prompt,每段独立(不依赖 driving video)
                        # kling i2v schema 简单:image_url + prompt + generate_audio
                        args = {
                            "image_url": reference_image,
                            "prompt": prompt,
                            "generate_audio": False,
                        }
                        endpoint = endpoint_default
                        try:
                            handler = await fal_client.submit_async(endpoint, arguments=args)
                            task_id = handler.request_id
                        except Exception as e:
                            raise RuntimeError(f"i2v seg {seg_idx} submit: {e}")
                    elif engine == "ltx":
                        seg_fal_url = await fal_upload_with_retry(str(seg_path))
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
                    elif engine == "kling-o1-edit":
                        # P30:真 v2v 视频编辑(@Element 占位符语法)
                        # element 1 = 模特,element 2 = 产品(可选)
                        # 每个 element 必须 frontal + reference_image_urls(>=1),
                        # probe 实测空 reference_image_urls 会返 elementReferList size 错。
                        # P43-3:从 session_assets 读 anchor_model / anchor_product 多张
                        # 拼到 reference_image_urls(O1 schema 1-3 张多角度,身份还原拉满)
                        seg_fal_url = await fal_upload_with_retry(str(seg_path))
                        anchor_model_urls = [a["url"] for a in session_assets if a.get("role") == "anchor_model"]
                        anchor_product_urls = [a["url"] for a in session_assets if a.get("role") == "anchor_product"]
                        # 模特 element:frontal=主图,reference=主图+多角度(去重 + 上限 3)
                        model_refs: List[str] = [model_url]
                        for u in anchor_model_urls:
                            if u not in model_refs and len(model_refs) < 3:
                                model_refs.append(u)
                        elements = [
                            {"frontal_image_url": model_url, "reference_image_urls": model_refs},
                        ]
                        if garment_url:
                            product_refs: List[str] = [garment_url]
                            for u in anchor_product_urls:
                                if u not in product_refs and len(product_refs) < 3:
                                    product_refs.append(u)
                            elements.append({
                                "frontal_image_url": garment_url,
                                "reference_image_urls": product_refs,
                            })
                        args = {
                            "video_url": seg_fal_url,
                            "prompt": prompt,
                            "elements": elements,
                            "keep_audio": False,
                        }
                        endpoint = endpoint_default
                        try:
                            handler = await fal_client.submit_async(endpoint, arguments=args)
                            task_id = handler.request_id
                        except Exception as e:
                            raise RuntimeError(f"kling-o1-edit seg {seg_idx} submit: {e}")
                    elif engine == "seedance-2-r2v":
                        # P41+P42:Seedance 2.0 r2v — image_urls + video_urls + audio_urls + @Index
                        # P42:若 session_assets 非空,按 role 编排;否则老路 reference_image + garment
                        seg_fal_url = await fal_upload_with_retry(str(seg_path))
                        if session_assets:
                            # 用户编排路:image_urls 按 role 排序(anchor_model→anchor_product→scene_ref)
                            # video_urls = [driving 段] + 用户上传的 shot_ref 视频(总和 ≤3 段)
                            # audio_urls = rhythm_ref 音频
                            asset_imgs = [a["url"] for a in session_assets if a["type"] == "image"]
                            asset_videos = [a["url"] for a in session_assets if a["type"] == "video" and a["role"] == "shot_ref"]
                            asset_audios = [a["url"] for a in session_assets if a["type"] == "audio"]
                            # 把 vton 合成图(reference_image)放在最前作主参考
                            image_urls_arg = [reference_image] + asset_imgs[:8]  # 上限 9 张
                            video_urls_arg = [seg_fal_url] + asset_videos[:2]    # 上限 3 段
                            audio_urls_arg = asset_audios[:3]
                        else:
                            # 老路:vton + 产品图,无 audio
                            image_urls_arg = [reference_image]
                            if garment_url:
                                image_urls_arg.append(garment_url)
                            video_urls_arg = [seg_fal_url]
                            audio_urls_arg = []
                        # aspect_ratio 映射:用户选 9:16/16:9/1:1,跟随 → "auto"
                        seedance_aspect = (session.get("aspect_ratio") or "auto").strip().lower()
                        if seedance_aspect not in ("9:16", "16:9", "1:1", "21:9", "4:3", "3:4"):
                            seedance_aspect = "auto"
                        args = {
                            "prompt": prompt,
                            "image_urls": image_urls_arg,
                            "video_urls": video_urls_arg,
                            "duration": str(int(seg_durations[seg_idx])) if 4 <= int(seg_durations[seg_idx]) <= 15 else "auto",
                            "resolution": "720p",
                            "aspect_ratio": seedance_aspect,
                            "generate_audio": True,
                        }
                        if audio_urls_arg:
                            args["audio_urls"] = audio_urls_arg
                        endpoint = endpoint_default
                        try:
                            handler = await fal_client.submit_async(endpoint, arguments=args)
                            task_id = handler.request_id
                        except Exception as e:
                            raise RuntimeError(f"seedance-2-r2v seg {seg_idx} submit: {e}")
                    elif engine == "kling-o3-r2v":
                        # P41:Kling o3 r2v — element 多图 + start_image_url + generate_audio
                        # 不切原视频段;每段独立用 reference_image 当 start frame
                        elements = [
                            {"frontal_image_url": model_url, "reference_image_urls": [model_url]},
                        ]
                        if garment_url:
                            elements.append({
                                "frontal_image_url": garment_url,
                                "reference_image_urls": [garment_url],
                            })
                        # aspect_ratio:o3 r2v 仅 "16:9"/"9:16"/"1:1",无 "auto",跟随 → 9:16
                        o3_aspect = (session.get("aspect_ratio") or "9:16").strip().lower()
                        if o3_aspect not in ("16:9", "9:16", "1:1"):
                            o3_aspect = "9:16"
                        seg_dur = max(3, min(15, int(seg_durations[seg_idx])))
                        args = {
                            "prompt": prompt,
                            "start_image_url": reference_image,
                            "elements": elements,
                            "duration": str(seg_dur),
                            "aspect_ratio": o3_aspect,
                            "generate_audio": True,
                        }
                        endpoint = endpoint_default
                        try:
                            handler = await fal_client.submit_async(endpoint, arguments=args)
                            task_id = handler.request_id
                        except Exception as e:
                            raise RuntimeError(f"kling-o3-r2v seg {seg_idx} submit: {e}")
                    elif engine == "kling-o3-v2v" or engine == "kling-o3-standard-v2v":
                        # P41 / P56:Kling o3 v2v reference/edit — 顶层 video_url 必填(driving 3-10s)
                        # element 多图 + image_urls(reference_image)
                        # P56:加 anchor_model + anchor_product 多角度图进 element reference_image_urls
                        seg_fal_url = await fal_upload_with_retry(str(seg_path))
                        # 模特 element:主图 + 多角度
                        anchor_model_urls = [a["url"] for a in session_assets if a.get("role") == "anchor_model" and a.get("url")]
                        model_refs = [model_url] + [u for u in anchor_model_urls if u != model_url][:3]
                        elements = [
                            {"frontal_image_url": model_url, "reference_image_urls": model_refs},
                        ]
                        if garment_url:
                            anchor_product_urls = [a["url"] for a in session_assets if a.get("role") == "anchor_product" and a.get("url")]
                            product_refs = [garment_url] + [u for u in anchor_product_urls if u != garment_url][:3]
                            elements.append({
                                "frontal_image_url": garment_url,
                                "reference_image_urls": product_refs,
                            })
                        v2v_aspect = (session.get("aspect_ratio") or "auto").strip().lower()
                        if v2v_aspect not in ("16:9", "9:16", "1:1"):
                            v2v_aspect = "auto"
                        seg_dur = max(3, min(10, int(seg_durations[seg_idx])))
                        # P59:image_urls 用用户原图(模特+多角度+产品+多角度),
                        # 不再用 Seedream 合成图(合成会丢/改信息,导致脸/光/字漂)。
                        # elements 已含 frontal+reference 多图,顶层 image_urls 用原图作辅助参考即可。
                        kling_image_urls: List[str] = [model_url]
                        for u in anchor_model_urls:
                            if u not in kling_image_urls and len(kling_image_urls) < 8:
                                kling_image_urls.append(u)
                        if garment_url:
                            if garment_url not in kling_image_urls:
                                kling_image_urls.append(garment_url)
                            for u in anchor_product_urls:
                                if u not in kling_image_urls and len(kling_image_urls) < 8:
                                    kling_image_urls.append(u)
                        args = {
                            "prompt": prompt,
                            "video_url": seg_fal_url,
                            "image_urls": kling_image_urls,
                            "elements": elements,
                            "duration": str(seg_dur),
                            "aspect_ratio": v2v_aspect,
                            "keep_audio": False,
                        }
                        endpoint = endpoint_default
                        try:
                            handler = await fal_client.submit_async(endpoint, arguments=args)
                            task_id = handler.request_id
                        except Exception as e:
                            raise RuntimeError(f"{engine} seg {seg_idx} submit: {e}")
                    elif engine == "kling-2-6-i2v":
                        # P41:Kling 2.6 Pro i2v(Native Audio)— start_image_url + duration "5"/"10"
                        # 不切原视频段;generate_audio 默认 true,我们 lipsync 接管所以关掉
                        seg_dur_s = "5" if int(seg_durations[seg_idx]) <= 5 else "10"
                        args = {
                            "prompt": prompt,
                            "start_image_url": reference_image,
                            "duration": seg_dur_s,
                            "generate_audio": False,
                        }
                        endpoint = endpoint_default
                        try:
                            handler = await fal_client.submit_async(endpoint, arguments=args)
                            task_id = handler.request_id
                        except Exception as e:
                            raise RuntimeError(f"kling-2-6-i2v seg {seg_idx} submit: {e}")
                    elif engine == "pixverse-swap":
                        # P44:Pixverse Swap — driving 视频 + 单 ref 图,无 prompt
                        # 内部 schema 已通过 probe 验过(probe_seedance_enterprise_pixverse.py)
                        # NSFW 友好,~14s/s 成片,长视频 5s/段并发跑
                        seg_fal_url = await fal_upload_with_retry(str(seg_path))
                        args = {
                            "video_url": seg_fal_url,
                            "image_url": reference_image,
                        }
                        endpoint = endpoint_default
                        try:
                            handler = await fal_client.submit_async(endpoint, arguments=args)
                            task_id = handler.request_id
                        except Exception as e:
                            raise RuntimeError(f"pixverse-swap seg {seg_idx} submit: {e}")
                    else:  # kling-v2v
                        seg_fal_url = await fal_upload_with_retry(str(seg_path))
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
                        # 八十四续 P13:fal Status 对象通过 type 区分(Queued/InProgress/Completed),
                        # 不是 .status 属性。老代码用 hasattr 判 .status 永远 False → 9 个
                        # session 死在 timeout(不是模型真慢,是判断 bug)。改 type-name 判定。
                        state_name = type(status_obj).__name__
                        if state_name == "Completed":
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

            # P47-B:阿里通义万相 wan2.7-r2v 段级跑通(可作主路或 fallback)
            # 走 DashScope 异步 API,不是 fal_client。需要本地 driving video 段
            async def _drive_one_aliyun_wan(seg_idx: int, seg_path: Optional[Path]) -> str:
                from app.services.fal_service import get_aliyun_wan_service
                aliyun = get_aliyun_wan_service()
                if not aliyun or not aliyun.is_available():
                    raise RuntimeError("aliyun-wan2.7-r2v 不可用(DASHSCOPE_API_KEY 未配置)")
                if seg_path is None:
                    raise RuntimeError(f"aliyun-wan2.7-r2v seg {seg_idx} 需 driving video,主路是 i2v")
                # driving 段先上传 fal storage 拿公开 URL(阿里只接公网 URL)
                seg_fal_url = await fal_upload_with_retry(str(seg_path))
                # 提交任务
                seg_dur = max(2, min(15, int(seg_durations[seg_idx])))
                ratio_for_aliyun = (session.get("aspect_ratio") or "9:16").strip().lower()
                if ratio_for_aliyun not in ("9:16", "16:9", "1:1", "4:3", "3:4"):
                    ratio_for_aliyun = "9:16"

                # P52:multi-reference 修复 — 不传 vton 合成图,改传【模特原图 + 产品原图】两张独立 reference
                # P53:用户上传时标注 angle(正面/反面/侧面/材质/logo),拼到 prompt 让模型明确引用
                ref_imgs: List[str] = []
                ref_descs: List[str] = []  # 每张图的中文描述,prompt 引用用
                # 图1:用户原模特图(P45 codeformer 修脸增强,如有)
                main_model_url = session.get("enhanced_model_url") or model_url
                if main_model_url:
                    ref_imgs.append(main_model_url)
                    ref_descs.append("模特正面")
                # 图2:用户原产品图(主图默认正面)
                if garment_url:
                    ref_imgs.append(garment_url)
                    ref_descs.append("产品正面")
                # 图3-N:多角度 reference(P43-3 anchor_model + anchor_product role,P53 alias 存 angle)
                for a in session_assets:
                    role = a.get("role")
                    if role in ("anchor_model", "anchor_product") and a.get("url"):
                        if a["url"] not in ref_imgs and len(ref_imgs) < 9:
                            ref_imgs.append(a["url"])
                            angle = (a.get("alias") or "").strip() or ("侧面" if role == "anchor_model" else "反面")
                            kind = "模特" if role == "anchor_model" else "产品"
                            ref_descs.append(f"{kind}{angle}")

                # 中文 prompt 拼接:每张图明确角色 + 角度
                img_intro = "、".join(f"图{i+1}({d})" for i, d in enumerate(ref_descs))
                if len(ref_imgs) >= 2 and garment_url:
                    wan_prompt = (
                        f"参考素材:{img_intro};视频1是动作参考。"
                        f"图1中的女性穿着图2所示的产品(产品名称:{', '.join(product_names) if product_names else '商品'}),"
                        f"按照视频1中的动作、姿态、镜头运动和场景自然展示。"
                        f"严格保持图1中模特的面部特征、五官比例、发型、肤色不变。"
                        f"严格保留图2中产品的颜色、材质、文字、logo 等细节,产品款式不变(参考其他角度图获得完整产品形态)。"
                        f"完整保留视频1的背景、光线、机位、构图。"
                    )
                else:
                    # 单图 fallback(无产品时)
                    wan_prompt = (
                        f"参考素材:{img_intro};视频1是动作参考。"
                        f"图1中的女性按照视频1中的动作、姿态、镜头运动自然展示,"
                        f"保留视频1的背景、光线和镜头角度。"
                        f"严格保持图1中模特的面部特征、五官比例、发型、肤色不变。"
                    )

                submit = await aliyun.wan27_r2v_submit(
                    reference_image_url=ref_imgs[0] if ref_imgs else reference_image,  # 兼容老 API
                    reference_image_urls=ref_imgs,  # P52 多图 reference
                    reference_video_url=seg_fal_url,
                    prompt=wan_prompt,
                    duration=seg_dur,
                    resolution="720P",
                    ratio=ratio_for_aliyun,
                )
                if "error" in submit:
                    raise RuntimeError(f"aliyun-wan submit: {submit['error']}")
                task_id = submit["task_id"]
                # poll 上限 90 次 × 10s = 15 分钟(实测 520s/段,留余量)
                for _ in range(90):
                    await asyncio.sleep(10)
                    pr = await aliyun.poll_task(task_id)
                    status = pr.get("status")
                    if status == "SUCCEEDED":
                        url = pr.get("video_url")
                        if not url:
                            raise RuntimeError("aliyun-wan 未返 video URL")
                        return url
                    if status == "FAILED":
                        raise RuntimeError(f"aliyun-wan FAILED: {pr.get('error', '?')[:200]}")
                raise RuntimeError("aliyun-wan 超时(15 min)")

            # P47-A:简化版 fallback 引擎(主路失败后用,降级运行,不依赖外层 prompt 工程)
            async def _drive_one_simple_fallback(seg_idx: int, seg_path: Optional[Path], try_engine: str) -> str:
                if try_engine == "aliyun-wan2.7-r2v":
                    return await _drive_one_aliyun_wan(seg_idx, seg_path)
                if seg_path is None:
                    # 主路是 i2v 类(无 driving),fallback 时也无 driving 输入,直接抛
                    raise RuntimeError(f"seg {seg_idx} fallback 需 seg_path,但主路是 i2v")
                seg_fal_url = await fal_upload_with_retry(str(seg_path))
                if try_engine == "pixverse-swap":
                    args = {"video_url": seg_fal_url, "image_url": reference_image}
                    fb_endpoint = "fal-ai/pixverse/swap"
                    fb_loops = 60
                elif try_engine == "seedance-2-r2v":
                    image_urls_arg = [reference_image]
                    if garment_url:
                        image_urls_arg.append(garment_url)
                    args = {
                        "prompt": FALLBACK_PROMPT,
                        "image_urls": image_urls_arg,
                        "video_urls": [seg_fal_url],
                        "duration": "5",
                        "resolution": "720p",
                        "aspect_ratio": "auto",
                        "generate_audio": True,
                    }
                    fb_endpoint = "fal-ai/bytedance/seedance-2.0/reference-to-video"
                    fb_loops = 60
                elif try_engine == "kling-o1-edit":
                    elements = [{"frontal_image_url": model_url, "reference_image_urls": [model_url]}]
                    if garment_url:
                        elements.append({"frontal_image_url": garment_url, "reference_image_urls": [garment_url]})
                    args = {
                        "video_url": seg_fal_url,
                        "prompt": FALLBACK_PROMPT,
                        "elements": elements,
                        "keep_audio": False,
                    }
                    fb_endpoint = "fal-ai/kling-video/o1/video-to-video/edit"
                    fb_loops = 60
                elif try_engine == "kling-o3-r2v":
                    # P52:阿里 wan 失败后切 fal kling-o3-r2v(真 multi-reference,$0.112/s)
                    elements = [{"frontal_image_url": model_url, "reference_image_urls": [model_url]}]
                    if garment_url:
                        elements.append({"frontal_image_url": garment_url, "reference_image_urls": [garment_url]})
                    seg_dur_str = "5"
                    o3_aspect = (session.get("aspect_ratio") or "9:16").strip().lower()
                    if o3_aspect not in ("16:9", "9:16", "1:1"):
                        o3_aspect = "9:16"
                    args = {
                        "prompt": FALLBACK_PROMPT,
                        "start_image_url": model_url,
                        "elements": elements,
                        "duration": seg_dur_str,
                        "aspect_ratio": o3_aspect,
                        "generate_audio": False,
                    }
                    fb_endpoint = "fal-ai/kling-video/o3/pro/reference-to-video"
                    fb_loops = 60
                elif try_engine == "kling-o3-standard-v2v":
                    # P56:Kling O3 standard v2v edit($0.126/s,真复刻 + element 多图 verified)
                    # 加 P53 多角度图(anchor_model + anchor_product)进 reference_image_urls
                    anchor_model_urls = [a["url"] for a in session_assets if a.get("role") == "anchor_model" and a.get("url")]
                    model_refs = [model_url] + [u for u in anchor_model_urls if u != model_url][:3]
                    elements = [{"frontal_image_url": model_url, "reference_image_urls": model_refs}]
                    anchor_product_urls: List[str] = []
                    if garment_url:
                        anchor_product_urls = [a["url"] for a in session_assets if a.get("role") == "anchor_product" and a.get("url")]
                        product_refs = [garment_url] + [u for u in anchor_product_urls if u != garment_url][:3]
                        elements.append({"frontal_image_url": garment_url, "reference_image_urls": product_refs})
                    v2v_aspect = (session.get("aspect_ratio") or "9:16").strip().lower()
                    if v2v_aspect not in ("16:9", "9:16", "1:1"):
                        v2v_aspect = "9:16"
                    # P59:同 _drive_one — image_urls 用用户原图,不用 Seedream 合成图
                    kling_image_urls: List[str] = [model_url]
                    for u in anchor_model_urls:
                        if u not in kling_image_urls and len(kling_image_urls) < 8:
                            kling_image_urls.append(u)
                    if garment_url:
                        if garment_url not in kling_image_urls:
                            kling_image_urls.append(garment_url)
                        for u in anchor_product_urls:
                            if u not in kling_image_urls and len(kling_image_urls) < 8:
                                kling_image_urls.append(u)
                    args = {
                        "prompt": FALLBACK_PROMPT,
                        "video_url": seg_fal_url,
                        "image_urls": kling_image_urls,
                        "elements": elements,
                        "duration": "5",
                        "aspect_ratio": v2v_aspect,
                        "keep_audio": False,
                    }
                    fb_endpoint = "fal-ai/kling-video/o3/standard/video-to-video/edit"
                    fb_loops = 60
                else:
                    raise RuntimeError(f"fallback engine 不支持: {try_engine}")
                handler = await fal_client.submit_async(fb_endpoint, arguments=args)
                tid = handler.request_id
                for _ in range(fb_loops):
                    await asyncio.sleep(10)
                    s = await fal_client.status_async(fb_endpoint, tid, with_logs=False)
                    if type(s).__name__ == "Completed":
                        final = await fal_client.result_async(fb_endpoint, tid)
                        v = final.get("video") if isinstance(final, dict) else None
                        url = v.get("url") if isinstance(v, dict) else None
                        if not url:
                            raise RuntimeError(f"fallback {try_engine} 未返 video URL")
                        return url
                raise RuntimeError(f"fallback {try_engine} 超时")

            # P48-B:Best-of-2 同段并发(阿里 wan + Kling 3 Pro i2v)+ similarity 选优
            async def _drive_one_kling3_pro_i2v(seg_idx: int, seg_path: Optional[Path]) -> str:
                """Kling 3.0 Pro i2v(probe verified similarity 0.4096,跟阿里 wan 0.4165 接近"""
                # i2v 用 reference_image 当首帧,不需要 driving video
                args = {
                    "image_url": reference_image,
                    "prompt": (
                        "A young woman wearing the products, naturally showcasing them with subtle "
                        "hand gestures, smiling at camera, smooth body movement, photorealistic UGC selfie style. "
                        "Do NOT alter facial proportions, eye spacing, nose shape, jawline, hair, or skin tone."
                    ) if product_names else (
                        "A young woman naturally showing herself with subtle hand gestures, smiling at camera, "
                        "smooth body movement, photorealistic UGC selfie style. "
                        "Do NOT alter facial proportions, eye spacing, nose shape, jawline, hair, or skin tone."
                    ),
                    "duration": "5",
                    "aspect_ratio": (session.get("aspect_ratio") or "9:16").strip().lower() or "9:16",
                }
                handler = await fal_client.submit_async(KLING3_PRO_I2V_ENDPOINT, arguments=args)
                tid = handler.request_id
                for _ in range(60):  # 10 min cap
                    await asyncio.sleep(10)
                    s = await fal_client.status_async(KLING3_PRO_I2V_ENDPOINT, tid, with_logs=False)
                    if type(s).__name__ == "Completed":
                        final = await fal_client.result_async(KLING3_PRO_I2V_ENDPOINT, tid)
                        v = final.get("video") if isinstance(final, dict) else None
                        url = v.get("url") if isinstance(v, dict) else None
                        if not url:
                            raise RuntimeError("kling-3-pro-i2v 未返 video URL")
                        return url
                raise RuntimeError("kling-3-pro-i2v 超时")

            FACE_SIM_WORKER = "/opt/ssp/scripts/face_similarity_worker.py"

            async def _score_similarity(video_url: str, src_face_local: str) -> float:
                """下载视频到 /tmp 调 face_similarity_worker 评分 vs 用户原模特图"""
                import tempfile as _tf
                tmp_video = Path(_tf.mktemp(prefix="bof_", suffix=".mp4"))
                try:
                    await _download_url_to(video_url, tmp_video, timeout=120.0)
                    proc = await asyncio.create_subprocess_exec(
                        "/opt/ssp/face_venv/bin/python", FACE_SIM_WORKER,
                        src_face_local, str(tmp_video), "0.5",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    sout, _serr = await asyncio.wait_for(proc.communicate(), timeout=120)
                    line = (sout or b"").decode(errors="replace").strip()
                    for ln in line.splitlines():
                        if ln.startswith("SCORE="):
                            return float(ln[len("SCORE="):])
                    return -1.0
                except Exception:
                    return -1.0
                finally:
                    try:
                        tmp_video.unlink(missing_ok=True)
                    except Exception:
                        pass

            async def _drive_one_best_of_2(seg_idx: int, seg_path: Optional[Path]) -> tuple:
                """同段并发阿里 wan + Kling 3.0 Pro i2v,InsightFace 选 max similarity"""
                # 准备 src face local 路径(用户原模特图,P45 增强后或原图)
                src_face_local = str(ORAL_UPLOAD_ROOT / user_id / session_id / "_bof_src_face.jpg")
                Path(src_face_local).parent.mkdir(parents=True, exist_ok=True)
                if not Path(src_face_local).exists():
                    src_url = session.get("enhanced_model_url") or model_url
                    try:
                        await _download_url_to(src_url, Path(src_face_local), timeout=60.0)
                    except Exception as e:
                        _log(f"_drive_one_best_of_2 下载 src face 失败: {e}")
                        # 降级:只跑阿里 wan
                        url = await _drive_one_aliyun_wan(seg_idx, seg_path)
                        return (url, "aliyun-wan2.7-r2v")

                # 并发 2 引擎
                tasks = [
                    asyncio.create_task(_drive_one_aliyun_wan(seg_idx, seg_path)),
                    asyncio.create_task(_drive_one_kling3_pro_i2v(seg_idx, seg_path)),
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                candidates = []
                for engine_name, r in zip(["aliyun-wan2.7-r2v", "kling-3-pro-i2v"], results):
                    if isinstance(r, Exception):
                        _log(f"_drive_one_best_of_2 seg {seg_idx} {engine_name} 失败: {str(r)[:200]}")
                    else:
                        candidates.append((engine_name, r))

                if not candidates:
                    raise RuntimeError(f"seg {seg_idx} best-of-2 全失败")
                if len(candidates) == 1:
                    return (candidates[0][1], candidates[0][0])

                # similarity 评分(并发)
                sim_tasks = [_score_similarity(url, src_face_local) for _, url in candidates]
                scores = await asyncio.gather(*sim_tasks)
                scored = list(zip(candidates, scores))
                _log(f"_drive_one_best_of_2 seg {seg_idx} scores: " +
                     ", ".join(f"{e}={s:.3f}" for (e, _), s in scored))
                # 选 max(无效 -1.0 排最后)
                best = max(scored, key=lambda x: x[1])
                (best_eng, best_url), best_score = best
                return (best_url, f"{best_eng}@{best_score:.3f}")

            async def _drive_one_with_fallback(seg_idx: int, seg_path: Optional[Path]) -> tuple:
                """段级 auto fallback 链。
                主路用现有 _drive_one(P46-L1 完整 prompt 工程);
                失败 → 按 chain 顺序切下一个引擎(简化降级)。
                返回 (url, engine_used)。

                P47-B:auto-cheap 模式用 FALLBACK_CHAIN_CHEAP(阿里 wan 主路 + fal 兜底)
                P48-B:auto-best 模式走 _drive_one_best_of_2(并发选优,贵但 95 分)
                P49:engine == "kling-3-pro-i2v" 单引擎档(快速,fal kling 3 Pro,¥2.5/5s)
                """
                # P48-B 优先
                if best_n_mode:
                    return await _drive_one_best_of_2(seg_idx, seg_path)

                # 选 chain
                if cheap_fallback:
                    use_chain = FALLBACK_CHAIN_CHEAP
                elif auto_fallback:
                    use_chain = FALLBACK_CHAIN_AUTO
                else:
                    # 显式选了某引擎,不走 fallback
                    if engine == "aliyun-wan2.7-r2v":
                        url = await _drive_one_aliyun_wan(seg_idx, seg_path)
                    elif engine == "kling-3-pro-i2v":
                        # P49 fal-ai/kling-video/v3/pro/image-to-video 单跑(快速档)
                        url = await _drive_one_kling3_pro_i2v(seg_idx, seg_path)
                    else:
                        url = await _drive_one(seg_idx, seg_path)
                    return (url, engine)

                last_err: Optional[Exception] = None
                for try_idx, try_engine in enumerate(use_chain):
                    try:
                        # 主路逻辑:auto = pixverse 用现有 _drive_one;auto-cheap = 阿里直接走 _drive_one_aliyun_wan
                        if try_idx == 0 and not cheap_fallback:
                            # auto 模式主路:pixverse-swap(用闭包 engine + 完整 prepare config)
                            url = await _drive_one(seg_idx, seg_path)
                        else:
                            url = await _drive_one_simple_fallback(seg_idx, seg_path, try_engine)
                        return (url, try_engine)
                    except Exception as fb_e:
                        last_err = fb_e
                        _log(f"_drive_one_with_fallback seg {seg_idx} engine={try_engine} 失败 → 切下一个: {str(fb_e)[:200]}")
                raise RuntimeError(f"seg {seg_idx} fallback 链全失败: {str(last_err)[:200]}")

            seg_results = await asyncio.gather(*[_drive_one_with_fallback(i, p) for i, p in enumerate(seg_paths)])
            seg_urls = [r[0] for r in seg_results]
            seg_engines_used = [r[1] for r in seg_results]
            # 多段可能跑了不同引擎,按使用次数最多的当主导记录
            from collections import Counter as _Counter
            engines_count = _Counter(seg_engines_used)
            if len(engines_count) == 1:
                predominant_engine = list(engines_count.keys())[0]
            else:
                # 多引擎混跑,记 "engineA+engineB"
                predominant_engine = "+".join(e for e, _ in engines_count.most_common())
            _log(f"_run_inpainting_step Step B {n_segments} 段全部完成 session={session_id} engines={engines_count}")

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
                # P47-A/B:auto / auto-cheap 模式下记主导引擎(可能多段跑了不同 fallback 引擎,合并写)
                step_b_engine_used=(predominant_engine if (auto_fallback or cheap_fallback) else engine),
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
        # P29:Step A 与 TTS 并行,status 可能已被 _run_tts_step 推到 tts_running
        # 老代码 guard 只允许 edit_submitted → 错过 tts_running 时直接 return,
        # session 僵尸卡住、积分不退。改成"非终态都允许覆盖"。
        if not sess2:
            return
        st = sess2["status"]
        if st == STATUS_TERMINAL_OK or st == "cancelled" or st.startswith(STATUS_FAILED_PREFIX):
            return
        refunded = _refund(sess2, "failed_step4")
        _update_session(
            session_id,
            status="failed_step4",
            error_step="step4",
            error_message=str(e)[:500],
            credits_refunded=refunded,
        )


# ==================== Lipsync 成片归档 ====================

# 八十四续 P15:用户明确要求不加水印 → 仅下载 fal final 落本地,
# 防 fal.media 30 天过期。合规深度合成水印责任移交用户决定(Phase 4)。


async def _faststart_remux(in_path: "Path") -> None:
    """P30:把 mp4 的 moov atom 挪到文件头(faststart),让浏览器流式播放,
    不必整个文件下载完才能开始放(老路径会让用户看到"视频一直在转")。
    -c copy 不重编码,几秒搞定。失败不阻塞(原文件保留)。"""
    from app.api.video_studio import _run_ffmpeg
    tmp_path = in_path.with_suffix(".faststart.mp4")
    ok, err = _run_ffmpeg([
        "ffmpeg", "-y", "-i", str(in_path),
        "-c", "copy", "-movflags", "+faststart", str(tmp_path),
    ])
    if ok and tmp_path.exists() and tmp_path.stat().st_size > 0:
        shutil.move(str(tmp_path), str(in_path))
        os.chmod(in_path, 0o644)
    else:
        _log(f"_faststart_remux 失败(继续用原文件): {err[:200]}")
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


async def _archive_lipsync_final(
    fal_video_url: str,
    user_id: str,
    session_id: str,
) -> str:
    """下载 fal final → 落本地归档(无水印)+ faststart remux。返 public URL。

    P43-2:可选 Topaz 超分(session.use_topaz_upscale 优先,env 兜底)。720p → 1440p,
    +$0.02/s,画面分辨率档拉到即梦同档。失败降级用原 720p,不阻塞 pipeline。
    """
    import httpx

    # P43-2:用户 session 字段优先,env 兜底
    use_topaz = False
    try:
        sess_row = _get_session(session_id)
        if sess_row and sess_row.get("use_topaz_upscale"):
            use_topaz = True
    except Exception:
        pass
    if not use_topaz and os.getenv("ORAL_ENABLE_TOPAZ_UPSCALE", "").lower() in ("1", "true", "yes"):
        use_topaz = True

    if use_topaz:
        try:
            import fal_client
            _log(f"_archive_lipsync_final P43-2 Topaz upscale start session={session_id}")
            t0 = time.time()
            handler = await fal_client.submit_async(
                "fal-ai/topaz/upscale/video",
                arguments={"video_url": fal_video_url, "upscale_factor": 2},
            )
            tid = handler.request_id
            for _ in range(60):  # 10 min cap
                await asyncio.sleep(10)
                s = await fal_client.status_async("fal-ai/topaz/upscale/video", tid, with_logs=False)
                if type(s).__name__ == "Completed":
                    final = await fal_client.result_async("fal-ai/topaz/upscale/video", tid)
                    new_url = (final.get("video") or {}).get("url") if isinstance(final, dict) else None
                    if new_url:
                        fal_video_url = new_url
                        _log(f"_archive_lipsync_final P43-2 Topaz OK session={session_id} elapsed={time.time()-t0:.1f}s")
                    break
        except Exception as e:
            _log(f"_archive_lipsync_final P43-2 Topaz fail(降级原 video): {str(e)[:200]}")

    out_dir = ORAL_UPLOAD_ROOT / user_id / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "final.mp4"

    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        async with client.stream("GET", fal_video_url) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"download fal final {resp.status_code}")
            with out_path.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)

    # P44:若 ASR step 拆出了 BGM,这里 ffmpeg amix(lipsync 视频音轨 + BGM)
    # 实现"复刻音乐节奏 + 保留情绪"。失败不阻塞,降级原 lipsync 音轨。
    try:
        sess_bgm = _get_session(session_id)
        bgm_path = sess_bgm.get("bgm_path") if sess_bgm else None
        if bgm_path and Path(bgm_path).exists():
            from app.api.video_studio import _run_ffmpeg
            mixed = out_dir / "final_mixed.mp4"
            # -shortest 防 BGM 比 lipsync 长把视频拉伸;volume=0.5 BGM 压低
            # 让 TTS 人声(new_audio)主导,BGM 当背景
            cmd = [
                "ffmpeg", "-y",
                "-i", str(out_path),
                "-i", bgm_path,
                "-filter_complex",
                "[0:a]volume=1.0[v0];[1:a]volume=0.35[v1];"
                "[v0][v1]amix=inputs=2:duration=shortest:dropout_transition=0:normalize=0[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", str(mixed),
            ]
            ok_x, err_x = _run_ffmpeg(cmd)
            if ok_x and mixed.exists() and mixed.stat().st_size > 0:
                shutil.move(str(mixed), str(out_path))
                _log(f"_archive_lipsync_final P44 BGM amix OK session={session_id}")
            else:
                _log(f"_archive_lipsync_final P44 BGM amix 失败(降级原 lipsync 音轨): {err_x[:200]}")
    except Exception as mix_e:
        _log(f"_archive_lipsync_final P44 BGM amix 异常(降级): {mix_e}")

    os.chmod(out_path, 0o644)
    await _faststart_remux(out_path)
    public = f"/uploads/oral/{user_id}/{session_id}/final.mp4"
    _log(f"_archive_lipsync_final OK session={session_id} -> {public}")

    # P46-L2:fire-and-forget 启动本地 inswapper 生成 thumbnail
    # (免费,~16s,UI 列表页/历史页用,失败不影响 final video)
    asyncio.create_task(_generate_face_swapped_thumbnail(session_id, str(out_path), user_id))

    return public


# ==================== P46-L2:本地 inswapper thumbnail ====================
FACE_SWAP_WORKER = "/opt/ssp/scripts/face_swap_thumbnail.py"
FACE_SWAP_VENV_PY = "/opt/ssp/face_venv/bin/python"


async def _generate_face_swapped_thumbnail(session_id: str, video_path: str, user_id: str) -> None:
    """异步触发 worker 生成 face-swapped thumbnail。fire-and-forget 模式。

    用户原模特图(selected_models[0].image_url)→ 抽视频中点帧(0.5)→ inswapper
    把模特脸 swap 上去 → /uploads/oral/<uid>/<sid>/thumbnail.jpg

    完全本地 CPU 推理,无 fal 调用,免费。失败不阻塞 pipeline,不退款。
    实测:~16s/次(8.9s 装载 + 7.5s 推理)。
    """
    try:
        if not Path(FACE_SWAP_WORKER).is_file() or not Path(FACE_SWAP_VENV_PY).is_file():
            _log(f"_generate_face_swapped_thumbnail: worker 或 venv 缺失,跳过 session={session_id}")
            return

        sess = _get_session(session_id)
        if not sess:
            return
        models = json.loads(sess.get("selected_models") or "[]")
        if not models:
            _log(f"_generate_face_swapped_thumbnail: 无模特图,跳过 session={session_id}")
            return
        # 优先用 codeformer 增强后的模特图(P45),否则原图
        src_model_url = sess.get("enhanced_model_url") or models[0].get("image_url")
        if not src_model_url:
            return

        # 下载模特图到本地(若是远端 fal/CDN URL)
        out_dir = ORAL_UPLOAD_ROOT / user_id / session_id
        out_dir.mkdir(parents=True, exist_ok=True)
        src_face_local = out_dir / "_thumb_src_face.jpg"
        thumb_path = out_dir / "thumbnail.jpg"
        try:
            await _download_url_to(src_model_url, src_face_local, timeout=60.0)
        except Exception as e:
            _log(f"_generate_face_swapped_thumbnail: 下载源模特图失败: {e}")
            return

        # subprocess 调 face_venv worker(2 vCPU 跑 ~16s)
        t0 = time.time()
        proc = await asyncio.create_subprocess_exec(
            FACE_SWAP_VENV_PY, FACE_SWAP_WORKER,
            str(src_face_local), str(video_path), str(thumb_path),
            "0.5",  # 抽中点帧(更稳,首帧可能空场)
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        except asyncio.TimeoutError:
            proc.kill()
            _log(f"_generate_face_swapped_thumbnail: worker 180s 超时 session={session_id}")
            return

        elapsed = time.time() - t0
        if proc.returncode == 0 and thumb_path.exists() and thumb_path.stat().st_size > 0:
            os.chmod(thumb_path, 0o644)
            public = f"/uploads/oral/{user_id}/{session_id}/thumbnail.jpg"
            _update_session(session_id, thumbnail_url=public)
            _log(f"_generate_face_swapped_thumbnail OK session={session_id} elapsed={elapsed:.1f}s -> {public}")
        else:
            err = (stderr or b"").decode(errors="replace")[:500]
            _log(f"_generate_face_swapped_thumbnail rc={proc.returncode} elapsed={elapsed:.1f}s err={err}")

        # 清理 _thumb_src_face.jpg
        try:
            src_face_local.unlink(missing_ok=True)
        except Exception:
            pass
    except Exception as e:
        _log(f"_generate_face_swapped_thumbnail unexpected: {e}")


# ==================== P28:长视频 lipsync 分段 + concat ====================
#
# veed/lipsync 等端点对长视频时长有上限(实测一两分钟以上常 timeout/失败)。
# 长视频(> ORAL_LIPSYNC_CHUNK_THRESHOLD_S)走分段:
#   1) 下载 swapped 视频 + 新音频
#   2) ffmpeg 切 N 段视频 + 对应 N 段音频
#   3) 每段独立调 lipsync(并发 ORAL_LIPSYNC_CONCURRENCY,段级 2 次重试)
#   4) ffmpeg concat demuxer 合并(copy 优先,失败 re-encode fallback)
#   5) 归档为 final.mp4
# 段长按 ORAL_LIPSYNC_SEG_LEN_S(默认 30s)切。短视频继续走整段路径(P15 已验收)。


async def _download_url_to(url: str, out_path: "Path", timeout: float = 180.0) -> None:
    import httpx
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"download {resp.status_code} url={url[:80]}")
            with out_path.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)


async def _run_lipsync_chunked(session_id: str, session: dict) -> str:
    """长视频分段 lipsync → concat → 归档。返 public final URL。"""
    import fal_client
    import tempfile
    from app.api.video_studio import _run_ffmpeg

    seg_len = float(os.getenv("ORAL_LIPSYNC_SEG_LEN_S", "30"))
    concurrency = max(1, int(os.getenv("ORAL_LIPSYNC_CONCURRENCY", "3")))
    duration = float(session.get("duration_seconds") or 0)
    if duration <= 0:
        raise RuntimeError("duration 未知,长视频分段需要 duration_seconds")

    n = max(1, math.ceil(duration / seg_len))
    work = Path(tempfile.mkdtemp(prefix=f"oral_lip_{session_id}_"))
    _log(f"_run_lipsync_chunked START session={session_id} duration={duration:.1f}s segs={n} seg_len={seg_len}s")

    try:
        local_video = work / "src.mp4"
        local_audio = work / "src.mp3"
        await _download_url_to(session["swapped_video_url"], local_video, timeout=300.0)
        await _download_url_to(session["new_audio_url"], local_audio, timeout=300.0)

        seg_videos: List[Path] = []
        seg_audios: List[Path] = []
        for i in range(n):
            start = i * seg_len
            seg_dur = seg_len if i < n - 1 else max(0.5, duration - start)
            sv = work / f"v_{i:02d}.mp4"
            sa = work / f"a_{i:02d}.mp3"
            ok1, e1 = _run_ffmpeg([
                "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(local_video),
                "-t", f"{seg_dur:.3f}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an", str(sv),
            ])
            if not ok1 or not sv.exists():
                raise RuntimeError(f"切视频段 {i} 失败: {e1[:200]}")
            ok2, e2 = _run_ffmpeg([
                "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(local_audio),
                "-t", f"{seg_dur:.3f}", "-acodec", "libmp3lame", "-b:a", "128k", str(sa),
            ])
            if not ok2 or not sa.exists():
                raise RuntimeError(f"切音频段 {i} 失败: {e2[:200]}")
            seg_videos.append(sv)
            seg_audios.append(sa)

        from app.services.fal_service import get_lipsync_service
        lip_svc = get_lipsync_service()
        if not lip_svc:
            raise RuntimeError("FAL Lipsync service 未初始化")

        sem = asyncio.Semaphore(concurrency)
        out_videos: List[Path] = [work / f"out_{i:02d}.mp4" for i in range(n)]

        async def _do_seg(i: int) -> None:
            async with sem:
                last_err: Optional[str] = None
                for attempt in range(1, 3):
                    try:
                        vurl = await fal_upload_with_retry(str(seg_videos[i]))
                        aurl = await fal_upload_with_retry(str(seg_audios[i]))
                        res = await lip_svc.sync(video_url=vurl, audio_url=aurl, tier=session["tier"])
                        if "error" in res:
                            last_err = str(res["error"])[:300]
                            raise RuntimeError(f"lipsync seg {i}: {last_err}")
                        await _download_url_to(res["video_url"], out_videos[i], timeout=300.0)
                        return
                    except Exception as fe:
                        last_err = str(fe)[:300]
                        _log(f"_run_lipsync_chunked seg={i} attempt={attempt}/2 err={last_err}")
                        if attempt < 2:
                            await asyncio.sleep(8)
                raise RuntimeError(f"lipsync seg {i} 重试 2 次仍失败: {last_err}")

        await asyncio.gather(*(_do_seg(i) for i in range(n)))

        concat_list = work / "concat.txt"
        with concat_list.open("w") as f:
            for v in out_videos:
                f.write(f"file '{v.resolve()}'\n")
        merged = work / "merged.mp4"
        ok, e = _run_ffmpeg([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list), "-c", "copy", str(merged),
        ])
        if not ok or not merged.exists():
            _log(f"_run_lipsync_chunked concat copy 失败,转 re-encode fallback session={session_id}")
            ok2, e2 = _run_ffmpeg([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "128k", str(merged),
            ])
            if not ok2 or not merged.exists():
                raise RuntimeError(f"ffmpeg concat 失败(copy+reencode 都崩): {e2[:200]}")

        user_id = str(session["user_id"])
        out_dir = ORAL_UPLOAD_ROOT / user_id / session_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "final.mp4"
        shutil.copy2(merged, out_path)
        os.chmod(out_path, 0o644)
        await _faststart_remux(out_path)
        public = f"/uploads/oral/{user_id}/{session_id}/final.mp4"
        _log(f"_run_lipsync_chunked OK session={session_id} segs={n} -> {public}")
        return public
    finally:
        shutil.rmtree(work, ignore_errors=True)


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

        # P28:长视频走分段路径(下载 → 切 N 段 → 并发 lipsync → concat → 归档)
        threshold = float(os.getenv("ORAL_LIPSYNC_CHUNK_THRESHOLD_S", "60"))
        duration = float(session.get("duration_seconds") or 0)
        lipsync_model_label: str
        if duration > threshold:
            archived_url = await _run_lipsync_chunked(session_id, session)
            lipsync_model_label = f"chunked-{session['tier']}"
        else:
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

            # 八十四续 P15:用户要求不加水印,只下载归档防 fal.media 30 天过期
            archived_url = await _archive_lipsync_final(
                final_url, str(session["user_id"]), session_id,
            )
            lipsync_model_label = result.get("model", "")

        _update_session(
            session_id,
            lipsync_fal_request_id=lipsync_model_label,
            final_video_url=archived_url,
            final_video_archived=archived_url,
            status="completed",
            completed_at="CURRENT_TIMESTAMP",
        )
        # P17:写 generation_history,让 oral 成片出现在 /tasks/history 页
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM generation_history WHERE id = ?", (session_id,))
                if not cursor.fetchone():
                    cursor.execute(
                        """
                        INSERT INTO generation_history (id, user_id, module, prompt, videos, cost)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            session["user_id"],
                            "oral-broadcast",
                            (session.get("edited_transcript") or session.get("asr_transcript") or "")[:500],
                            json.dumps([archived_url]),
                            int(session.get("credits_charged") or 0) - int(session.get("credits_refunded") or 0),
                        ),
                    )
                    conn.commit()
        except Exception as he:
            _log(f"_run_lipsync_step generation_history insert failed (continuing): {he}")

        _log(f"_run_lipsync_step OK session={session_id} url={archived_url[:80]}")
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
        # P20:把 COS download_file 的真实异常 + stack 打出来定位 502 根因
        from app.services.logger import log_error as _log_err
        _log_err(
            f"finalize_cos download_file FAILED user={user_id} key={body.object_key} "
            f"err_type={type(e).__name__} err={str(e)[:500]}",
            exc_info=True,
        )
        raise HTTPException(502, f"COS 拉取异常 [{type(e).__name__}]: {str(e)[:200]}")

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

    # P16:校验 aspect_ratio,只接受白名单 / None
    aspect = (req.aspect_ratio or "").strip().lower()
    if aspect and aspect not in ("9:16", "16:9", "1:1"):
        raise HTTPException(400, "aspect_ratio 必须是 9:16 / 16:9 / 1:1")

    # P41:校验 step_b_engine,白名单 / None
    step_b_engine = (req.step_b_engine or "").strip().lower()
    if step_b_engine and step_b_engine not in _STEP_B_ENGINES:
        raise HTTPException(400, f"step_b_engine 必须是 {_STEP_B_ENGINES} 之一,或不传")

    # P42:校验 assets,白名单 role + type;空列表/None 走老路单素材
    asset_dicts: List[dict] = []
    if req.assets:
        if len(req.assets) > 20:
            raise HTTPException(400, "assets 最多 20 个")
        for a in req.assets:
            if a.role not in _ASSET_ROLES:
                raise HTTPException(400, f"asset role 必须是 {_ASSET_ROLES} 之一(收到 {a.role})")
            if a.type not in _ASSET_TYPES:
                raise HTTPException(400, f"asset type 必须是 {_ASSET_TYPES} 之一(收到 {a.type})")
            if not a.url or not a.url.startswith(("http://", "https://", "/")):
                raise HTTPException(400, f"asset url 非法:{a.url[:60]}")
            asset_dicts.append({
                "role": a.role, "type": a.type, "url": a.url,
                "alias": a.alias, "ord": a.ord or 0,
            })

    # 写入 session — 状态推进到 asr_running(实际 ASR 调用 P2 实现)
    _update_session(
        req.session_id,
        tier=req.tier,
        status="asr_running",
        selected_models=json.dumps(req.models, ensure_ascii=False),
        selected_products=json.dumps(req.products, ensure_ascii=False),
        credits_charged=charge,
        aspect_ratio=(aspect or None),
        step_b_engine=(step_b_engine or None),
        use_topaz_upscale=1 if req.use_topaz_upscale else 0,
        use_face_enhance=1 if (req.use_face_enhance is None or req.use_face_enhance) else 0,
    )

    # P42:写入多素材编排表(可选,空跳过)
    if asset_dicts:
        try:
            _save_assets(req.session_id, asset_dicts)
        except Exception as e:
            _log(f"_save_assets failed session={req.session_id}: {e}")
            # 不阻塞流程,降级到老路单素材

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
            "thumbnail_url": session.get("thumbnail_url"),
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
            SELECT id, tier, status, duration_seconds, final_video_url, thumbnail_url, created_at
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
            "thumbnail_url": d["thumbnail_url"],
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
