"""
视频复刻(Seedance 2.0 r2v Fast)— P216 2026-05-08
====================================================

接入 fal.ai bytedance/seedance-2.0/fast/reference-to-video:
  - 输入:1 视频 + 0-9 图 + prompt(@Video1 / @Image1 占位符)
  - 输出:替换场景/人物/产品后的视频(动作/构图复刻原视频)
  - 价格:$0.1452/秒(720p Fast 版)

跟 /video/replicate(GPT-Image 2 + pixverse-swap 链路)的区别:
  - r2v Fast 直接 video-to-video,真复刻原视频镜头/动作
  - 不用 GPT 出图,产品/人物用户上传的图直接喂给模型

跟项目现有架构对齐:
  - 不新建 DB 表(沿用 jobs.json + JOBS + credits_ledger)
  - 上传走 fal_client.upload_file_async(SSP 已有方案,COS 直传被 P22 禁)
  - 异步 polling 走 _execute_job(SSP 现有模式,不接 webhook)

端点:
  POST /upload/video      上传参考视频(≤50MB,3-15s,mp4/mov/webm/m4v/gif)
  POST /upload/image      上传参考图(≤10MB,jpg/png/webp/avif)
  POST /generate          推 JOBS type=video_clone(异步)
  GET  /price             返实时价格预估
"""
import asyncio
import os
import re
import time
import uuid
import tempfile
from typing import List, Optional, Dict, Any

import fal_client
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.services.billing import deduct_credits
from app.services.fal_service import fal_upload_with_retry
from app.services.logger import log_info, log_error
from app.services.upload_guard import read_bounded

router = APIRouter()

VIDEO_MIMES = (
    "video/mp4", "video/quicktime", "video/webm",
    "video/x-msvideo", "video/x-m4v", "image/gif",
)
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp", "image/avif")

MAX_VIDEO_SIZE = 50 * 1024 * 1024   # 50 MB
MAX_IMAGE_SIZE = 10 * 1024 * 1024   # 10 MB
# P218.3 (2026-05-08) 不再硬卡时长 3-15s,仅 size 限。
# 时长由前端展示给用户参考;模型若拒长视频会走 jobs.py 失败路径自动退积分

# Seedance 2.0 Fast r2v 价格(720p):$0.1452/秒
PRICE_USD_PER_SEC = 0.1452
USD_TO_RMB = 7.2  # 人民币汇率(可后续从配置读)
GROSS_MARGIN = 1.5  # 毛利倍数(1.5 = 50% 毛利)
CREDITS_PER_RMB = 1  # 1 人民币 = 1 积分(暂定)


def _calc_credits(duration_sec: int, count: int = 1) -> int:
    """计算所需积分:duration × count × 单价 × 汇率 × 毛利"""
    usd = PRICE_USD_PER_SEC * duration_sec * count
    rmb = usd * USD_TO_RMB * GROSS_MARGIN
    return max(1, int(round(rmb * CREDITS_PER_RMB)))


@router.post("/upload/video")
async def upload_video(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传参考视频(≤50MB,任意时长;模型若拒长视频会自动退积分)"""
    contents = await read_bounded(file, MAX_VIDEO_SIZE, VIDEO_MIMES, "参考视频")
    suffix = ".mp4"
    if file.filename and "." in file.filename:
        suffix = "." + file.filename.rsplit(".", 1)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        video_path = tmp.name
    try:
        # ffprobe 仅探测时长展示给用户参考,不卡阈值
        import subprocess as _sp, json as _j
        try:
            rr = _sp.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", video_path],
                capture_output=True, text=True, timeout=30,
            )
            dur = float(_j.loads(rr.stdout).get("format", {}).get("duration", 0))
        except Exception:
            dur = 0
        url = await fal_upload_with_retry(video_path)
    finally:
        try: os.unlink(video_path)
        except Exception: pass
    return {"video_url": url, "duration_sec": dur}


@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传参考图(≤10MB)"""
    contents = await read_bounded(file, MAX_IMAGE_SIZE, IMAGE_MIMES, "参考图")
    suffix = ".jpg"
    if file.filename and "." in file.filename:
        suffix = "." + file.filename.rsplit(".", 1)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    try:
        url = await fal_upload_with_retry(tmp_path)
    finally:
        try: os.unlink(tmp_path)
        except Exception: pass
    return {"image_url": url}


@router.get("/price")
async def get_price(duration: int = 5, count: int = 1):
    """返实时价格预估"""
    duration = max(5, min(15, int(duration)))
    count = max(1, min(4, int(count)))
    credits = _calc_credits(duration, count)
    return {
        "duration_sec": duration,
        "count": count,
        "price_usd": round(PRICE_USD_PER_SEC * duration * count, 4),
        "price_rmb": round(PRICE_USD_PER_SEC * duration * count * USD_TO_RMB * GROSS_MARGIN, 2),
        "credits": credits,
    }


class CloneGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000,
                        description="文本指令,用 @Video1 / @Image1 / @Image2 占位符")
    reference_video_url: str = Field(..., description="参考视频 URL(已上传的 fal storage URL)")
    reference_image_urls: List[str] = Field(default_factory=list, max_length=9,
                                            description="参考图 URL 列表(最多 9 张)")
    duration: str = Field("5", description="5 / 10 / 15 / auto")
    aspect_ratio: str = Field("9:16", description="auto / 9:16 / 16:9 / 1:1")
    generate_audio: bool = Field(True, description="是否生成音频(免费)")
    count: int = Field(1, ge=1, le=4, description="生成数量")


@router.post("/generate")
async def generate(
    req: CloneGenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """推 JOBS 队列异步跑 video_clone(Seedance 2.0 r2v Fast)"""
    # duration 解析(用于扣费)
    if req.duration == "auto":
        duration_sec = 10  # auto 估 10s 扣费
    else:
        try:
            duration_sec = int(req.duration)
        except Exception:
            duration_sec = 5
    duration_sec = max(5, min(15, duration_sec))

    user_id = str(current_user["id"])
    cost = _calc_credits(duration_sec, req.count)
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost} 积分")

    from app.api.jobs import JOBS, _save_jobs, _execute_job
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "video_clone",
        "title": "视频复刻 · Seedance 2.0 Fast",
        "params": {
            "prompt": req.prompt,
            "reference_video_url": req.reference_video_url,
            "reference_image_urls": req.reference_image_urls,
            "duration": req.duration,
            "aspect_ratio": req.aspect_ratio,
            "generate_audio": req.generate_audio,
            "count": req.count,
            "cost_credits": cost,
            "duration_sec_for_billing": duration_sec,
            "_user_id": user_id,
        },
        "module": "video/clone",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    asyncio.create_task(_execute_job(job_id))
    log_info(f"video_clone/generate submitted job={job_id} user={user_id} duration={req.duration} count={req.count} cost={cost}")
    return {
        "job_id": job_id,
        "status": "pending",
        "cost_credits": cost,
        "estimated_time_sec": 30 * req.count + 60,
    }


# ============== P217 (2026-05-08) AI 帮我写 prompt ==============
# 走 fal openrouter/router(纯文本端点,无需 image_urls,比 vision 端点快 + 不会卡 placeholder 404)
# 默认 qwen3-vl-235b-instruct(快+便宜,中文强),失败降级 gemini-2.5-flash,再失败本地模板。
# 用户若想换 claude-sonnet-4.6,把 _PROMPT_LLM_MODEL 改成 "anthropic/claude-sonnet-4.6" 即可
_PROMPT_LLM_ENDPOINT = "openrouter/router"
_PROMPT_LLM_MODEL = "qwen/qwen3-vl-235b-a22b-instruct"
_PROMPT_LLM_FALLBACK = "google/gemini-2.5-flash"

_PROMPT_LLM_SYSTEM = """你是 Seedance 2.0 视频生成模型的 prompt 工程师。用户会上传一段参考视频和若干图片,并用大白话描述他想要的复刻效果。你的任务是输出一个专业的英文 prompt 给 Seedance 2.0。

⚠️ 关键铁律(diffusion 模型软引导,中段容易回到原视频,必须在 prompt 里反复强调):
1. 用 @Video1 引用参考视频,用 @Image1 / @Image2 等引用图片
2. 必须明确指示模型保留参考视频的镜头、运镜、节奏、光线、构图
3. 必须指明每张图的角色(谁是人物、哪个是产品、哪个是场景)
4. ⚠️ 产品/人物替换必须在 prompt 中至少出现 3 次,且明确说明"throughout the entire video / from start to end / in every frame"
   反例(产品中段会漏):"Replace the chair with @Image1"
   正例(产品贯穿全程):"Replace the chair with @Image1 throughout the entire video, keeping it visible and identical from frame 1 to the last frame, in every shot and every cut"
5. 必须明确禁止模型回到原产品/原人物:加一句 "Do NOT show the original chair/product/person from @Video1 at any point"
6. Prompt 长度 80-150 词(略长换严格度)
7. 用电影分镜 + 强约束的混合语言
8. 输出纯 prompt,不要解释,不要 markdown,不要前缀"Prompt:"或编号

输出格式:纯文本 prompt,以 "Based on @Video1," 开头。"""


class GeneratePromptImageItem(BaseModel):
    id: str = Field(..., description="稳定 id(前端生成,删图重排不影响)")
    label: str = Field(..., description="占位符标签,如 Image1 / Image2")
    user_label: Optional[str] = Field(None, description="用户对该图的描述,可选")
    filename: Optional[str] = Field(None, description="原文件名,可选")


class GeneratePromptRequest(BaseModel):
    user_description: str = Field(..., min_length=1, max_length=1000,
                                  description="用户大白话需求")
    reference_video_url: Optional[str] = Field(None, description="参考视频 URL(已上传)")
    reference_video_summary: Optional[str] = Field(None, max_length=500,
                                                   description="可选:视频内容简介")
    image_urls: List[GeneratePromptImageItem] = Field(default_factory=list, max_length=9)


def _fallback_prompt(images: List[GeneratePromptImageItem]) -> str:
    """LLM 调用失败时的本地兜底模板"""
    refs = ", ".join(f"@{img.label}" for img in images) or "the reference media"
    return (
        f"Based on @Video1, recreate the same scene with elements from {refs}, "
        f"preserving the original camera motion, pacing, lighting, and composition."
    )


@router.post("/generate-prompt")
async def generate_prompt(
    req: GeneratePromptRequest,
    current_user: dict = Depends(get_current_user),
):
    """大白话 → 专业 Seedance 2.0 prompt(含 @Video1/@Image1 占位符)"""
    images_desc = ", ".join(
        f"@{img.label}: {img.user_label or img.filename or '参考图'}"
        for img in req.image_urls
    ) or "(无参考图)"
    video_desc = req.reference_video_summary or (
        "用户上传的参考视频" if req.reference_video_url else "(无参考视频)"
    )

    user_msg = (
        f"用户描述: {req.user_description}\n"
        f"视频信息: {video_desc}\n"
        f"图片列表: {images_desc}\n\n"
        f"请输出一个专业的英文 prompt,严格遵守上述规则。"
    )

    text = ""
    used_model = _PROMPT_LLM_MODEL
    err: Optional[str] = None
    for model in (_PROMPT_LLM_MODEL, _PROMPT_LLM_FALLBACK):
        try:
            result = await asyncio.wait_for(
                fal_client.run_async(
                    _PROMPT_LLM_ENDPOINT,
                    arguments={
                        "prompt": user_msg,
                        "system_prompt": _PROMPT_LLM_SYSTEM,
                        "model": model,
                        "temperature": 0.8,
                    },
                ),
                timeout=40,  # 单模型 40s 上限,留足两次 + CF 100s 余量
            )
            text = (result.get("output") or "").strip()
            if text:
                used_model = model
                break
        except Exception as e:
            err = str(e)[:200]
            log_error(f"generate-prompt LLM={model} 失败: {err}")
            continue

    has_mentions = False
    if text:
        # 去 markdown / 引号外壳(以防模型不听话)
        text = re.sub(r"^```(?:[a-z]*)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        text = text.strip('"').strip()
        has_mentions = bool(re.search(r"@(Video|Image)\d+", text))
    else:
        text = _fallback_prompt(req.image_urls)
        has_mentions = True
        used_model = "fallback-template"

    log_info(
        f"generate-prompt user={current_user.get('id')} "
        f"model={used_model} len={len(text)} mentions={has_mentions} "
        f"desc_len={len(req.user_description)}"
    )
    return {
        "generated_prompt": text,
        "has_mentions": has_mentions,
        "model": used_model,
    }
