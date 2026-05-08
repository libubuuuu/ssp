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
import time
import uuid
import tempfile
from typing import List, Optional

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
VIDEO_MIN_SEC = 3.0
VIDEO_MAX_SEC = 15.0

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
    """上传参考视频(≤50MB,3-15s)"""
    contents = await read_bounded(file, MAX_VIDEO_SIZE, VIDEO_MIMES, "参考视频")
    suffix = ".mp4"
    if file.filename and "." in file.filename:
        suffix = "." + file.filename.rsplit(".", 1)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        video_path = tmp.name
    try:
        # ffprobe 检查时长
        import subprocess as _sp, json as _j
        rr = _sp.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", video_path],
            capture_output=True, text=True, timeout=30,
        )
        try:
            dur = float(_j.loads(rr.stdout).get("format", {}).get("duration", 0))
        except Exception:
            dur = 0
        if dur < VIDEO_MIN_SEC or dur > VIDEO_MAX_SEC:
            raise HTTPException(
                400,
                f"视频时长 {dur:.1f}s 超出范围(要求 {VIDEO_MIN_SEC}-{VIDEO_MAX_SEC}s)",
            )
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
