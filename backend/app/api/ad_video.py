"""
AI 带货视频 API
================

流程:
  1. /analyze   上传白底产品图 → VLM 审核 + 生成三段分镜脚本(1 积分)
  2. /preview   合成首帧预览图(产品 + 模特 + 可选背景)(2 积分)
  3. /generate  提交 Seedance 视频生成任务(走全局 jobs 队列,30 积分)
  4. /scene/regenerate   单个分镜重新生成(1 积分)

设计:
- 复用 jobs.py 全局队列 → 前端不用写新轮询,继续用 GET /api/jobs/{id}
- 复用 archive_url 媒体归档(防 fal.media 30 天过期)
- 复用 content_filter 审核 prompt
- 失败返还积分(用 @require_credits 装饰器,沿用现有模式)
- VLM 走 fal OpenRouter Vision(零新 API key,复用 FAL_KEY)
"""
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from pydantic import BaseModel, Field
from app.api.auth import get_current_user
from app.services.decorators import require_credits
from app.services.content_filter import assert_safe_prompt
from app.services.media_archiver import archive_url
from app.services.vlm_service import get_vlm_service
from app.services import ad_video_models
from app.services.logger import log_info, log_error

router = APIRouter()


# ============== Request / Response Models ==============


class Scene(BaseModel):
    id: int
    time_range: str
    purpose: str
    shot_language: str
    content: str
    visual_prompt: str
    speech: str


class Script(BaseModel):
    overall_setting: str
    model_description: str
    scenes: List[Scene]


class PreviewRequest(BaseModel):
    """首帧合成请求"""
    product_image_url: str = Field(..., description="白底产品图(已上传到 fal storage)")
    background_image_url: Optional[str] = Field(None, description="可选背景图")
    model_description: str = Field(..., min_length=1, max_length=500)
    scene_visual_prompt: str = Field(..., min_length=1, max_length=1000)


class GenerateRequest(BaseModel):
    """视频生成请求"""
    image_url: str = Field(..., description="首帧图 URL(来自 /preview 或直接上传)")
    script: Script
    duration: int = Field(15, ge=5, le=15, description="时长 5-15 秒")
    aspect_ratio: str = Field("9:16", description="9:16 / 16:9 / 1:1")
    resolution: str = Field("1080p", description="720p / 1080p")
    enable_audio: bool = Field(True, description="是否启用原生音频")


class SceneRegenerateRequest(BaseModel):
    """单镜头重新生成"""
    original_scene: dict
    instruction: str = Field(..., min_length=1, max_length=500)


# ============== API ==============


@router.post("/analyze")
@require_credits("ad_video/analyze")
async def analyze_product(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """
    上传产品图 → VLM 审核 + 生成脚本

    流程:
      1. 接收 multipart 文件
      2. 内部上传到 fal storage 拿到 URL(VLM 端点要 URL,不接受 base64)
      3. 调 VLM(默认 Qwen3-VL,中文最强)审核 + 生成脚本

    返回:
      {
        "audit": {is_valid, category, ..., violations},
        "script": {overall_setting, model_description, scenes: [...]},
        "product_image_url": "..."  // fal storage URL,后续 /preview 复用,免重传
      }

    审核失败(violations 非空)时返还积分 → raise 400
    """
    import fal_client
    import tempfile
    import os
    from PIL import Image
    import io

    # 读取图片
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片不能超过 10MB")

    # MIME 推断
    mime_type = file.content_type or "image/jpeg"
    if mime_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        raise HTTPException(status_code=400, detail="仅支持 JPG / PNG / WebP / GIF")

    # 上传到 fal storage(VLM 端点需要 URL)
    # 用 Pillow 标准化(沿用 video.py /upload/image 的处理逻辑,保证兼容性)
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
            if os.path.getsize(tmp.name) > 10 * 1024 * 1024:
                img.save(tmp.name, "JPEG", quality=75, optimize=True)
            tmp_path = tmp.name

        try:
            product_image_url = await fal_client.upload_file_async(tmp_path)
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图片处理失败: {str(e)[:200]}")

    # 调 VLM
    service = get_vlm_service()
    if service is None:
        raise HTTPException(status_code=503, detail="VLM 视觉服务未初始化")

    result = await service.analyze_product(product_image_url)

    if "error" in result:
        # 服务故障 → 返还积分(装饰器会处理)
        raise HTTPException(status_code=500, detail=result["error"])

    # 业务审核
    audit = result.get("audit", {})
    if not audit.get("is_valid", True) or audit.get("violations"):
        violations = audit.get("violations", [])
        # 触发返还(装饰器捕获 HTTPException 自动返还)
        raise HTTPException(
            status_code=400,
            detail={
                "message": "图片未通过审核",
                "violations": violations,
                "audit": audit,
            },
        )

    # 二次过滤生成的脚本(防 VLM 写出违禁词)
    script = result.get("script", {})
    for scene in script.get("scenes", []):
        try:
            assert_safe_prompt(scene.get("content", ""))
            assert_safe_prompt(scene.get("visual_prompt", ""))
        except HTTPException:
            raise HTTPException(
                status_code=400,
                detail="AI 生成的脚本包含敏感词,请重新上传或联系客服",
            )

    log_info(f"ad_video/analyze ok user={current_user.get('id')} category={audit.get('category')}")
    return {
        "success": True,
        **result,
        "product_image_url": product_image_url,  # 给后续 /preview 复用
        "description": f"AI 带货视频分析: {audit.get('category', '产品')}",
    }


@router.post("/preview")
@require_credits("ad_video/preview")
async def preview_first_frame(
    req: PreviewRequest,
    current_user: dict = Depends(get_current_user),
):
    """合成首帧预览图(Nano Banana 2 Edit)"""
    # 内容审核
    assert_safe_prompt(req.scene_visual_prompt)
    assert_safe_prompt(req.model_description)

    result = await ad_video_models.compose_first_frame(
        product_image_url=req.product_image_url,
        background_image_url=req.background_image_url,
        model_description=req.model_description,
        scene_visual_prompt=req.scene_visual_prompt,
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    # 媒体归档(BUG-2)
    if result.get("image_url"):
        result["image_url"] = await archive_url(
            result["image_url"], current_user["id"], "image"
        )

    log_info(f"ad_video/preview ok user={current_user.get('id')}")
    return {
        "success": True,
        **result,
        "description": "AI 带货视频首帧预览",
    }


@router.post("/generate")
async def generate_ad_video(
    req: GenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    提交 Seedance 2.0 视频生成任务

    走全局 jobs 队列 → 返回 job_id,前端用 GET /api/jobs/{id} 轮询(复用现有逻辑)
    扣费在 jobs.py 的 submit_job 里做(走 ad_video/generate 的定价 30 积分)
    """
    # 二次审核
    for scene in req.script.scenes:
        assert_safe_prompt(scene.visual_prompt)
        assert_safe_prompt(scene.speech)

    # 提交到 jobs 队列(完全复用现有架构)
    from app.api.jobs import JOBS, _save_jobs, _execute_job
    from app.services.billing import get_task_cost, deduct_credits
    import uuid
    import time
    import asyncio

    user_id = current_user.get("id") or current_user.get("email", "unknown")
    user_id_str = str(user_id)
    module = "ad_video/generate"
    cost = get_task_cost(module)

    # 扣费(原子)
    if cost > 0:
        if not deduct_credits(user_id, cost):
            raise HTTPException(status_code=402, detail=f"积分不足,需要 {cost} 积分")

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id_str,
        "user_numeric_id": user_id,
        "type": "ad_video",  # ⚠ 新类型,jobs.py _execute_job 需识别
        "title": f"AI 带货视频 ({req.duration}s)",
        "params": {
            "image_url": req.image_url,
            "script": req.script.model_dump(),
            "duration": req.duration,
            "aspect_ratio": req.aspect_ratio,
            "resolution": req.resolution,
            "enable_audio": req.enable_audio,
        },
        "module": module,
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    asyncio.create_task(_execute_job(job_id))

    log_info(f"ad_video/generate submitted job={job_id} user={user_id}")
    return {
        "success": True,
        "job_id": job_id,
        "status": "pending",
        "cost": cost,
        "message": "视频生成任务已提交,预计 1-3 分钟",
    }


@router.post("/scene/regenerate")
@require_credits("ad_video/scene_regen")
async def regenerate_scene(
    req: SceneRegenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """单个分镜重新生成(用户编辑器里点'重新生成此镜头')"""
    assert_safe_prompt(req.instruction)

    service = get_vlm_service()
    if service is None:
        raise HTTPException(status_code=503, detail="VLM 服务未初始化")

    new_scene = await service.regenerate_scene(req.original_scene, req.instruction)
    if "error" in new_scene:
        raise HTTPException(status_code=500, detail=new_scene["error"])

    # 审核生成的内容
    try:
        assert_safe_prompt(new_scene.get("content", ""))
        assert_safe_prompt(new_scene.get("visual_prompt", ""))
    except HTTPException:
        raise HTTPException(status_code=400, detail="AI 重新生成的内容包含敏感词,请换个指令")

    return {"success": True, "scene": new_scene, "description": "重新生成分镜"}


@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """
    专用图片上传(产品图 / 背景图)
    复用现有 video.py 的 Pillow 处理逻辑(白底/宽高比/最小分辨率)
    """
    import fal_client
    import tempfile
    import os
    from PIL import Image
    import io

    contents = await file.read()
    img = Image.open(io.BytesIO(contents))

    # 转 RGB
    if img.mode in ("RGBA", "P", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode in ("RGBA", "LA"):
            bg.paste(img, mask=img.split()[-1])
        else:
            bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size

    # 宽高比约束 0.40 - 2.50
    ratio = w / h
    if ratio < 0.40:
        new_w = int(h * 0.45)
        new_img = Image.new("RGB", (new_w, h), (255, 255, 255))
        new_img.paste(img, ((new_w - w) // 2, 0))
        img = new_img
    elif ratio > 2.50:
        new_h = int(w / 2.45)
        new_img = Image.new("RGB", (w, new_h), (255, 255, 255))
        new_img.paste(img, (0, (new_h - h) // 2))
        img = new_img

    w, h = img.size
    if w < 300 or h < 300:
        scale = max(300 / w, 300 / h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        img.save(tmp.name, "JPEG", quality=90, optimize=True)
        if os.path.getsize(tmp.name) > 10 * 1024 * 1024:
            img.save(tmp.name, "JPEG", quality=75, optimize=True)
        tmp_path = tmp.name
    try:
        url = await fal_client.upload_file_async(tmp_path)
        return {"url": url}
    finally:
        os.unlink(tmp_path)
