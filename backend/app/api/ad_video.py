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
from app.services.fal_service import fal_upload_with_retry
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
    """首帧合成请求 (P35: 改成接整个 script,后端循环出 N 张分镜首帧)"""
    product_image_url: str = Field(..., description="产品正面图(已上传到 fal storage)")
    product_back_image_url: Optional[str] = Field(None, description="P34: 产品反面/侧面图(可选)")
    background_image_url: Optional[str] = Field(None, description="可选背景图")
    script: Script


class GenerateRequest(BaseModel):
    """视频生成请求"""
    image_url: str = Field(..., description="首帧图 URL(共享/兼容,主要用 scene_image_urls)")
    # P35: 每段独立首帧 URL list,从 /preview 返回。jobs.py 多段路径优先用这个,
    # 不再调 compose_first_frame_for_scene 重复合成,避免 generate 阶段双倍 fal 钱。
    scene_image_urls: Optional[List[str]] = Field(None, description="N 段独立首帧 URL list (P35)")
    script: Script
    # P31 (2026-05-01):total_duration 上限 15 → 300。
    # >15 时 jobs.py 走 split_segments(每段 10s)+ N 段并发 + ffmpeg concat,
    # 段间一致性靠共享 overall_setting + model_description + 同一首帧 product_image_url 锁。
    duration: int = Field(15, ge=5, le=300, description="总时长 5-300 秒(>15 走多段并发拼接)")
    aspect_ratio: str = Field("9:16", description="9:16 / 16:9 / 1:1")
    resolution: str = Field("1080p", description="720p / 1080p")
    enable_audio: bool = Field(True, description="是否启用原生音频(>15 多段模式自动关,各段独立配音会跳)")


class SceneRegenerateRequest(BaseModel):
    """单镜头重新生成"""
    original_scene: dict
    instruction: str = Field(..., min_length=1, max_length=500)


# ============== API ==============


@router.post("/analyze")
@require_credits("ad_video/analyze")
async def analyze_product(
    file: UploadFile = File(...),
    back_file: Optional[UploadFile] = File(None),  # P34: 产品反面图(可选)
    total_duration: int = 15,
    current_user: dict = Depends(get_current_user),
):
    """
    上传产品图 → VLM 审核 + 生成脚本

    P31:total_duration 透传给 VLM,>15 时按 split_segments 出 N 段 scenes
    (每段 10s 共享 overall_setting + model_description 锁角色)

    流程:
      1. 接收 multipart 文件
      2. 内部上传到 fal storage 拿到 URL(VLM 端点要 URL,不接受 base64)
      3. 调 VLM(默认 Qwen3-VL,中文最强)审核 + 生成 N 段脚本

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
    from app.services.upload_guard import read_bounded, IMAGE_MIMES

    # 读取图片(upload_guard 统一 size + MIME 校验,超 10MB raise 413)
    contents = await read_bounded(file, 10 * 1024 * 1024, IMAGE_MIMES, "ad-video 产品图")

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
            product_image_url = await fal_upload_with_retry(tmp_path)
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图片处理失败: {str(e)[:200]}")

    # P34: 第二张产品图(反面/侧面),可选,流程同正面
    product_back_image_url: Optional[str] = None
    if back_file is not None and back_file.filename:
        try:
            back_contents = await read_bounded(back_file, 10 * 1024 * 1024, IMAGE_MIMES, "ad-video 产品反面图")
            img_b = Image.open(io.BytesIO(back_contents))
            if img_b.mode in ("RGBA", "P", "LA"):
                bg_b = Image.new("RGB", img_b.size, (255, 255, 255))
                if img_b.mode in ("RGBA", "LA"):
                    bg_b.paste(img_b, mask=img_b.split()[-1])
                else:
                    bg_b.paste(img_b.convert("RGBA"), mask=img_b.convert("RGBA").split()[-1])
                img_b = bg_b
            elif img_b.mode != "RGB":
                img_b = img_b.convert("RGB")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_b:
                img_b.save(tmp_b.name, "JPEG", quality=90, optimize=True)
                if os.path.getsize(tmp_b.name) > 10 * 1024 * 1024:
                    img_b.save(tmp_b.name, "JPEG", quality=75, optimize=True)
                tmp_back_path = tmp_b.name
            try:
                product_back_image_url = await fal_upload_with_retry(tmp_back_path)
            finally:
                os.unlink(tmp_back_path)
        except Exception as e:
            # 反面图失败不阻塞主流程,降级到只用正面
            log_info(f"ad_video/analyze 反面图处理失败,降级单图: {str(e)[:150]}")
            product_back_image_url = None

    # 调 VLM
    service = get_vlm_service()
    if service is None:
        raise HTTPException(status_code=503, detail="VLM 视觉服务未初始化")

    # P31:total_duration 透传,VLM 按段长动态出 N 段 scenes
    safe_duration = max(5, min(300, int(total_duration)))
    result = await service.analyze_product(product_image_url, total_duration=safe_duration)

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

    log_info(
        f"ad_video/analyze ok user={current_user.get('id')} "
        f"category={audit.get('category')} back={'yes' if product_back_image_url else 'no'}"
    )
    return {
        "success": True,
        **result,
        "product_image_url": product_image_url,  # 给后续 /preview 复用
        "product_back_image_url": product_back_image_url,  # P34
        "description": f"AI 带货视频分析: {audit.get('category', '产品')}",
    }


@router.post("/quick-prompt")
@require_credits("ad_video/analyze")
async def quick_prompt(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """七十续:简化版 — 上传产品图,直接吐一个完整带货视频提示词字符串。

    跟 /analyze 4 步重流程不同:
    - 输出单字符串 prompt(150-300 字),用户在前端 textarea 直接编辑
    - 编辑后送 /api/video/image-to-video 生成视频
    - 跳过审核 + 3 镜头脚本结构(用户决定怎么用)

    用 /api/ad-video/analyze 同等定价(1 积分)。
    """
    import fal_client
    import tempfile
    import os
    from PIL import Image
    import io
    from app.services.upload_guard import read_bounded, IMAGE_MIMES

    contents = await read_bounded(file, 10 * 1024 * 1024, IMAGE_MIMES, "quick-prompt 产品图")

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
            product_image_url = await fal_upload_with_retry(tmp_path)
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图片处理失败: {str(e)[:200]}")

    service = get_vlm_service()
    if service is None:
        raise HTTPException(status_code=503, detail="VLM 视觉服务未初始化")

    result = await service.generate_quick_prompt(product_image_url)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    # 安全过滤(防 VLM 写出违禁词)
    try:
        assert_safe_prompt(result["prompt"])
    except HTTPException:
        raise HTTPException(
            status_code=400,
            detail="AI 生成的提示词包含敏感词,请重新上传或联系客服",
        )

    log_info(f"ad_video/quick-prompt ok user={current_user.get('id')} len={len(result['prompt'])}")
    return {
        "success": True,
        "prompt": result["prompt"],
        "product_image_url": product_image_url,  # 前端拿这个直接 send /api/video/image-to-video
        "description": "AI 带货提示词快速生成",
    }


@router.post("/preview")
@require_credits("ad_video/preview")
async def preview_first_frame(
    req: PreviewRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    P35: 合成 N 张分镜首帧(每段一张)。

    流程:
      Step A:第 1 段用 compose_first_frame 锚定模特+产品+背景(产品正反面参考)
      Step B:第 2..N 段用 compose_first_frame_for_scene 在第 1 段基础上调整为本段镜头
              (并发 5,模特身份共享 base 锚定)

    返回 N 张图 URL list,前端 step 3 网格显示。后续 /generate 直接用这 N 张
    喂 Seedance i2v,不再重复合成首帧。
    """
    import asyncio

    # 内容审核
    assert_safe_prompt(req.script.model_description)
    for sc in req.script.scenes:
        assert_safe_prompt(sc.visual_prompt)

    if not req.script.scenes:
        raise HTTPException(status_code=400, detail="脚本至少需要 1 段")

    # Step A: 第 1 段 = 共享 base(锚定模特+产品+背景)
    first_scene = req.script.scenes[0]
    base_result = await ad_video_models.compose_first_frame(
        product_image_url=req.product_image_url,
        background_image_url=req.background_image_url,
        model_description=req.script.model_description,
        scene_visual_prompt=first_scene.visual_prompt,
        product_back_image_url=req.product_back_image_url,
    )
    if "error" in base_result or not base_result.get("image_url"):
        raise HTTPException(status_code=500, detail=base_result.get("error", "首帧合成失败"))

    base_url = await archive_url(base_result["image_url"], current_user["id"], "image")
    scene_image_urls = [base_url]

    # 单段不需要 Step B
    if len(req.script.scenes) == 1:
        log_info(f"ad_video/preview ok user={current_user.get('id')} scenes=1")
        return {
            "success": True,
            "scene_image_urls": scene_image_urls,
            "image_url": base_url,  # 兼容老前端字段
            "description": "AI 带货视频首帧预览(1 段)",
        }

    # Step B: 第 2..N 段并发合成,以 base 为锚
    sem = asyncio.Semaphore(5)

    async def _gen_one(scene) -> str:
        async with sem:
            sf = await ad_video_models.compose_first_frame_for_scene(
                base_image_url=base_url,
                scene=scene.model_dump() if hasattr(scene, "model_dump") else dict(scene),
                model_description=req.script.model_description,
                overall_setting=req.script.overall_setting,
            )
            if sf.get("image_url"):
                return await archive_url(sf["image_url"], current_user["id"], "image")
            # 失败回退 base(用户能看到这段标"已回退")
            log_info(f"ad_video/preview scene fallback to base: {sf.get('error', '?')}")
            return base_url

    rest_urls = await asyncio.gather(*[_gen_one(s) for s in req.script.scenes[1:]])
    scene_image_urls.extend(rest_urls)

    n_real = sum(1 for u in scene_image_urls if u != base_url) + 1  # 真合成的(非 fallback)
    log_info(
        f"ad_video/preview ok user={current_user.get('id')} "
        f"scenes={len(scene_image_urls)} real={n_real}"
    )
    return {
        "success": True,
        "scene_image_urls": scene_image_urls,
        "image_url": base_url,  # 兼容老前端字段
        "description": f"AI 带货视频首帧预览({len(scene_image_urls)} 段)",
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
            # P35: preview 阶段已合 N 张分镜首帧,jobs.py 直接用,不再重复合
            "scene_image_urls": req.scene_image_urls,
            "script": req.script.model_dump(),
            "duration": req.duration,
            "aspect_ratio": req.aspect_ratio,
            # P32:强制 720p。v2/pro standard 1080p 太慢,720p 已切到 v1.5/pro 端点
            "resolution": "720p",
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
    from app.services.upload_guard import read_bounded, IMAGE_MIMES

    # upload_guard 统一 size + MIME 校验(防 OOM 攻击),超 10MB raise 413
    contents = await read_bounded(file, 10 * 1024 * 1024, IMAGE_MIMES, "ad-video 上传图")
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
        url = await fal_upload_with_retry(tmp_path)
        return {"url": url}
    finally:
        os.unlink(tmp_path)
