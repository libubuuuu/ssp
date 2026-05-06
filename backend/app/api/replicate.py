"""
视频复刻工作台 API
================

流程:
  1. /upload/video      上传参考视频 → fal storage URL
  2. /upload/image      上传产品图 / 模特图 → fal storage URL
  3. /analyze           qwen-vl 看视频 → 出 N 段分镜(time/shot/action/visual_prompt)
  4. /generate          产品图 + 模特图 + 分镜 + aspect_ratio → 推 JOBS 队列(type=replicate)
  5. /api/jobs/list     轮询状态(用现成 JobPanel)

设计:
  - 复用 ad_video_models.compose_first_frame_for_scene(已支持 aspect_ratio)出每段首帧
  - 视频段 aliyun-wan2.7-r2v(免费)出 5/10s 视频
  - ffmpeg concat 拼最终
  - history 写 module=video/replicate
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.services.billing import get_task_cost, deduct_credits
from app.services.fal_service import fal_upload_with_retry, get_aliyun_qwenvl_service
from app.services.upload_guard import read_bounded, IMAGE_MIMES
from app.services.logger import log_info, log_error
from app.services.content_filter import assert_safe_prompt

router = APIRouter()

VIDEO_MIMES = ("video/mp4", "video/quicktime", "video/webm", "video/x-msvideo")
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100 MB


# ================= 数据模型 =================

class Scene(BaseModel):
    id: int
    time_range: str          # "0-5s"
    duration_sec: float
    shot: str                # 景别 close-up / medium / wide
    action: str              # 动作描述
    framing: str             # 构图
    visual_prompt: str       # 给 GPT-Image 2 的英文 prompt


class AnalyzeResponse(BaseModel):
    scenes: List[Scene]
    total_duration: float
    detected_aspect_ratio: str  # auto-detect 参考视频比例


class ReplicateScript(BaseModel):
    scenes: List[Scene]
    overall_setting: str = ""
    model_description: str = ""


class GenerateRequest(BaseModel):
    product_image_url: str                          # 产品正面图(必)
    product_back_image_url: Optional[str] = None    # 产品反面/侧面图(可选,锁材质/logo)
    reference_video_url: str                        # 复刻参考视频(传给 wan2.7 当 driving)
    script: ReplicateScript
    aspect_ratio: str = "9:16"
    engine: str = "pixverse-swap"  # 默认推荐(¥1.4/5s)


# ================= 端点 1:上传视频 =================

@router.post("/upload/video")
async def upload_video(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    contents = await read_bounded(file, MAX_VIDEO_SIZE, VIDEO_MIMES, "参考视频")
    import tempfile, os
    suffix = ".mp4"
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
    return {"video_url": url}


# ================= 端点 2:上传图片(产品 / 模特) =================

@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    contents = await read_bounded(file, 10 * 1024 * 1024, IMAGE_MIMES, "图片")
    import tempfile, os
    from PIL import Image
    import io
    try:
        img = Image.open(io.BytesIO(contents))
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode in ("RGBA", "LA"):
                bg.paste(img, mask=img.split()[-1])
            else:
                bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            img.save(tmp.name, "JPEG", quality=90, optimize=True)
            tmp_path = tmp.name
        try:
            url = await fal_upload_with_retry(tmp_path)
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        raise HTTPException(500, f"图片处理失败: {str(e)[:200]}")
    return {"image_url": url}


# ================= 端点 3:分析参考视频 → N 段分镜 =================

_ANALYZE_INSTRUCTION = """你是视频复刻专家。看完这段视频,按时序拆成 N 个分镜(每段 5 秒为基准,余数并入末段)。
对每段输出 JSON,严格按下面格式,不加任何 markdown:

{
  "total_duration_seconds": 总秒数,
  "scenes": [
    {
      "id": 1,
      "time_range": "0-5s",
      "duration_sec": 5.0,
      "shot": "close-up | medium-shot | wide-shot | medium close-up",
      "action": "本段主要动作的中文描述(15 字内)",
      "framing": "构图描述(中心 / 左 / 右 / 仰拍 / 俯拍 等,10 字内)",
      "visual_prompt": "完整英文 prompt,150 字内,只写镜头语言 + 动作 + 灯光 + 构图。"
    }
  ]
}

🚨 visual_prompt **绝对禁用词**(违反 fal content_checker,整句被拒):
- 服装类: bra, lingerie, underwear, panties, bikini, balconette, padded, push-up, underwire, shapewear, waist trainer
- 身体部位: chest, breast, bust, waist, hips, thigh, butt, cleavage
- 服装部件: strap, shoulder strap, support panel, mesh panel
- 服装动作: pull bra, lift strap, adjust bra, tighten strap

🟢 改用通用替代词:
- 产品 → "the garment / the apparel item / the product / the item"
- 任何身体描述 → 用 "torso / outfit / upper outfit"  
- 任何动作 → "adjusting the garment / showing the item / lifting the apparel"
- **NEVER** 包含产品具体类目(bra/lingerie 等)

🚨 严禁文字叠加: 不写 "text overlay 'XXX'" / "text 'XXX' appears" 等暗示画面里有文字的词。
即使想强调卖点,改写成 "highlighting comfort feature"  / "showcasing key benefit" 等抽象描述。

时序连贯:第 1 段"开场",中间段"展示",最后段"结束"。
"""


def _detect_video_aspect(video_url: str) -> str:
    """ffprobe 探视频比例 — 失败默认 9:16"""
    try:
        import subprocess, json as _j
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", video_url],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return "9:16"
        d = _j.loads(r.stdout)
        s = d.get("streams", [{}])[0]
        w, h = s.get("width"), s.get("height")
        if not w or not h:
            return "9:16"
        ratio = w / h
        if abs(ratio - 9/16) < 0.1: return "9:16"
        if abs(ratio - 16/9) < 0.1: return "16:9"
        if abs(ratio - 1.0) < 0.1: return "1:1"
        if abs(ratio - 4/3) < 0.1: return "4:3"
        if abs(ratio - 3/4) < 0.1: return "3:4"
        return "9:16"
    except Exception:
        return "9:16"


@router.post("/analyze")
async def analyze_submit(
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    """异步:推 JOBS 队列后立刻返 analyze_job_id,前端用 /analyze/status/{id} 轮询。
    根因:qwen-vl 视频理解 30-180s,sync HTTP 必触 CF/nginx 代理超时。
    """
    video_url = body.get("video_url")
    if not video_url:
        raise HTTPException(400, "video_url 必填")
    user_id = str(current_user["id"])
    cost = 1
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    # 先做服务可用性 fail-fast(同步,不阻塞 jobs 队列)
    svc = get_aliyun_qwenvl_service()
    if not svc or not svc.is_available():
        from app.services.billing import add_credits
        add_credits(user_id, cost, reason="task_refund")
        raise HTTPException(503, "qwen-vl 视频理解服务不可用")

    # 推到 JOBS 队列 type=replicate_analyze
    from app.api.jobs import JOBS, _save_jobs, _execute_job
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "replicate_analyze",
        "title": "视频复刻 · AI 分析",
        "params": {"video_url": video_url, "instruction": _ANALYZE_INSTRUCTION, "_user_id": user_id},
        "module": "video/replicate/analyze",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    asyncio.create_task(_execute_job(job_id))
    log_info(f"replicate/analyze submitted job={job_id} user={user_id}")
    return {"analyze_job_id": job_id, "status": "pending"}


@router.get("/analyze/status/{job_id}")
async def analyze_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """前端轮询:返 pending/running/completed/failed + 完成时附 scenes."""
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


# ================= 端点 4:生成视频 → 推 JOBS =================

@router.post("/generate")
async def generate(
    req: GenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """生成视频:推 JOBS 队列异步跑。按总时长积分定价。"""
    # 内容过滤
    for sc in req.script.scenes:
        assert_safe_prompt(sc.visual_prompt)

    if not req.script.scenes:
        raise HTTPException(400, "至少 1 段")
    _ALLOWED_ENGINES = ("kling-3-pro-i2v", "pixverse-swap", "catvton-pixverse")
    if req.engine not in _ALLOWED_ENGINES:
        raise HTTPException(400, f"engine 必须是 {_ALLOWED_ENGINES} 之一(收到 {req.engine})")

    user_id = current_user.get("id") or current_user.get("email", "unknown")
    user_id_str = str(user_id)

    total_duration = sum(s.duration_sec for s in req.script.scenes)
    # 定价:¥0.5/秒 ≈ 1 积分/秒,留毛利 → 1.5 积分/秒
    cost = max(5, int(round(total_duration * 1.5)))
    module = "video/replicate"
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    job_id = str(uuid.uuid4())[:8]
    from app.api.jobs import JOBS, _save_jobs, _execute_job

    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id_str,
        "user_numeric_id": user_id,
        "type": "replicate",
        "title": f"视频复刻 ({total_duration:.0f}s,{len(req.script.scenes)} 段)",
        "params": {
            "product_image_url": req.product_image_url,
            "product_back_image_url": req.product_back_image_url,
            "reference_video_url": req.reference_video_url,
            "scenes": [s.model_dump() for s in req.script.scenes],
            "overall_setting": req.script.overall_setting,
            "model_description": req.script.model_description,
            "aspect_ratio": req.aspect_ratio,
            "engine": req.engine,
        },
        "module": module,
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    asyncio.create_task(_execute_job(job_id))

    return {"job_id": job_id, "status": "pending", "cost": cost}
