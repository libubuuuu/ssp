"""
通用产品视频生成工作台(P215 2026-05-08)
================================================

跟视频复刻 / AI 带货视频的区别:
- **不限品类**(食品/日用品/化妆品/3C/服装),qwen-vl 自动识别
- **多张产品图**(主图 + 详情页 + 外包装,2-5 张)— 严守用户上传
- **模特参考**(可选):模特图 OR 模特视频(取中间帧)→ 真用户提供的人作模特
- **真 cat-vton 链路**(catvton + pixverse-swap)— 产品 100% 严守

端点:
  POST /upload/image      — 上传产品图(支持多张)
  POST /upload/video      — 上传模特视频(可选)
  POST /upload/model-image — 上传模特图(可选)
  POST /analyze           — qwen-vl 看多图 → 品类 + 卖点 + 推荐脚本
  POST /generate          — 推 JOBS 队列(type=video_general)
  GET  /analyze/status/{job_id} — 异步轮询 analyze 结果

成本:1 积分 /analyze + N 段动作复刻费(按 duration 估)
"""
import asyncio
import os
import time
import uuid
import tempfile
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.services.billing import deduct_credits
from app.services.fal_service import fal_upload_with_retry
from app.services.logger import log_info, log_error
from app.services.upload_guard import read_bounded, IMAGE_MIMES

router = APIRouter()

VIDEO_MIMES = ("video/mp4", "video/quicktime", "video/webm", "video/x-msvideo")
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_IMAGE_SIZE = 10 * 1024 * 1024   # 10 MB


@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传单张产品图(主图 / 详情页 / 包装,前端多次调用)"""
    contents = await read_bounded(file, MAX_IMAGE_SIZE, IMAGE_MIMES, "产品图")
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


@router.post("/upload/video")
async def upload_model_video(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传模特参考视频(可选,后端会抽中间帧作模特图)"""
    contents = await read_bounded(file, MAX_VIDEO_SIZE, VIDEO_MIMES, "模特视频")
    suffix = ".mp4"
    if file.filename and "." in file.filename:
        suffix = "." + file.filename.rsplit(".", 1)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        video_path = tmp.name
    try:
        # ffprobe 拿时长
        import subprocess as _sp
        import json as _j
        rr = _sp.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", video_path],
            capture_output=True, text=True, timeout=30,
        )
        duration = 5.0
        try:
            duration = float(_j.loads(rr.stdout).get("format", {}).get("duration", 5.0))
        except Exception:
            pass
        # 抽中间帧
        with tempfile.TemporaryDirectory() as tmpdir:
            mid_path = os.path.join(tmpdir, "model_mid.jpg")
            cp = _sp.run(
                ["ffmpeg", "-y", "-ss", f"{duration*0.5:.2f}", "-i", video_path, "-vframes", "1", "-q:v", "3", mid_path],
                capture_output=True, timeout=30,
            )
            if cp.returncode != 0 or not os.path.exists(mid_path):
                raise HTTPException(500, "中间帧抽取失败")
            video_url = await fal_upload_with_retry(video_path)
            model_image_url = await fal_upload_with_retry(mid_path)
    finally:
        try: os.unlink(video_path)
        except Exception: pass
    return {
        "video_url": video_url,
        "model_image_url": model_image_url,
        "duration_sec": duration,
    }


@router.post("/upload/model-image")
async def upload_model_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传模特图(可选,跟 /upload/video 二选一)"""
    contents = await read_bounded(file, MAX_IMAGE_SIZE, IMAGE_MIMES, "模特图")
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
    return {"model_image_url": url}


_ANALYZE_INSTRUCTION = """你是通用电商视频脚本专家。看完用户提供的所有图片,完成两件事:

【任务一:产品分析】
- 自动判断品类(食品/日用品/化妆品/3C 数码/服装/配饰/家居/其他)
- 提取产品核心卖点(品牌/规格/功能/材质/颜色/差异化卖点 3 条)
- 判断展示重点(按品类自适应):
  - 食品 → 包装设计 + 产品形态 + 食用场景
  - 日用品 → 使用场景 + 演示动作 + 效果对比
  - 化妆品 → 质地涂抹 + 上脸效果 + 包装外观
  - 3C → 外观 + 功能演示 + 操作场景
  - 服装 → 上身效果 + 材质细节 + 搭配场景

【任务二:生成 N 段视频脚本】
按 5 秒一段,共 N 段(总时长 user_total_duration 秒)。每段:
- shot:景别(close-up / medium / wide)
- action:模特动作(必须按品类合理 — 食品就拿/吃/展示包装,化妆品就涂/抹/对比,等等)
- visual_prompt:英文 GPT-Image 2 prompt(必含产品+动作+背景描述)
- speech:中文带货话术(中文场景,根据 region)

【输出 JSON】严格按以下格式,不要任何 markdown 标记:
{
  "category": "食品/日用品/化妆品/...",
  "selling_points": ["卖点1", "卖点2", "卖点3"],
  "scenes": [
    {"id": 1, "time_range": "0-5s", "duration_sec": 5, "shot": "...", "action": "...", "visual_prompt": "...", "speech": "..."},
    ...
  ]
}
"""


@router.post("/analyze")
async def analyze_submit(
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    """异步分析多张产品图 → 品类 + 卖点 + N 段脚本(推 JOBS 队列)"""
    product_image_urls = body.get("product_image_urls") or []
    if not product_image_urls or not isinstance(product_image_urls, list):
        raise HTTPException(400, "product_image_urls 必填(list)")
    total_duration = int(body.get("total_duration") or 15)
    total_duration = max(5, min(60, total_duration))
    region = body.get("region") or "CN"
    safe_region = "Global" if str(region).lower() in ("global", "en", "international", "海外") else "CN"

    user_id = str(current_user["id"])
    cost = 1
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    from app.services.fal_service import get_aliyun_qwenvl_service
    svc = get_aliyun_qwenvl_service()
    if not svc or not svc.is_available():
        from app.services.billing import add_credits
        add_credits(user_id, cost, reason="task_refund")
        raise HTTPException(503, "qwen-vl 视频理解服务不可用")

    # 推到 JOBS 队列 type=video_general_analyze
    from app.api.jobs import JOBS, _save_jobs, _execute_job
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "video_general_analyze",
        "title": "通用产品视频 · AI 分析",
        "params": {
            "product_image_urls": product_image_urls,
            "total_duration": total_duration,
            "region": safe_region,
            "instruction": _ANALYZE_INSTRUCTION.replace("user_total_duration", str(total_duration)),
            "_user_id": user_id,
        },
        "module": "video/general/analyze",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    asyncio.create_task(_execute_job(job_id))
    log_info(f"video_general/analyze submitted job={job_id} user={user_id} n_images={len(product_image_urls)}")
    return {"analyze_job_id": job_id, "status": "pending"}


@router.get("/analyze/status/{job_id}")
async def analyze_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """前端轮询 analyze 状态"""
    from app.api.jobs import JOBS
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    uid = str(current_user.get("id"))
    if job.get("user_id") != uid:
        raise HTTPException(403, "无权限")
    out = {"status": job.get("status", "pending")}
    if job.get("status") == "completed":
        out.update(job.get("result") or {})
    if job.get("status") == "failed":
        out["error"] = job.get("error", "unknown")
    return out


class GeneralGenerateRequest(BaseModel):
    product_image_urls: List[str] = Field(..., description="产品图列表(主图 + 详情页 + 包装)")
    model_image_url: Optional[str] = Field(None, description="模特图(可选,跟 model_video_url 二选一)")
    model_video_url: Optional[str] = Field(None, description="模特视频(可选,会抽中间帧作模特图)")
    category: str = Field(..., description="产品品类(qwen-vl 判出来的)")
    scenes: List[dict] = Field(..., description="N 段脚本")
    total_duration: int = Field(15, ge=5, le=60)
    region: str = Field("CN")
    aspect_ratio: str = Field("9:16")


@router.post("/generate")
async def generate(
    req: GeneralGenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """生成通用产品视频(推 JOBS 队列异步跑)"""
    if not req.product_image_urls:
        raise HTTPException(400, "至少 1 张产品图")
    if not req.scenes:
        raise HTTPException(400, "至少 1 段脚本")

    user_id = str(current_user["id"])
    # 简化定价:每段 5 积分
    cost = max(5, len(req.scenes) * 5)
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    from app.api.jobs import JOBS, _save_jobs, _execute_job
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "video_general",
        "title": f"通用产品视频 · {req.category}",
        "params": {
            "product_image_urls": req.product_image_urls,
            "model_image_url": req.model_image_url,
            "model_video_url": req.model_video_url,
            "category": req.category,
            "scenes": [s if isinstance(s, dict) else dict(s) for s in req.scenes],
            "total_duration": req.total_duration,
            "region": req.region,
            "aspect_ratio": req.aspect_ratio,
            "_user_id": user_id,
        },
        "module": "video/general",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    asyncio.create_task(_execute_job(job_id))
    log_info(f"video_general/generate submitted job={job_id} user={user_id} n_scenes={len(req.scenes)} category={req.category}")
    return {"job_id": job_id, "status": "pending", "cost": cost}
