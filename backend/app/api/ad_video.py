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
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
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
    model_config = {"protected_namespaces": ()}
    overall_setting: str
    model_description: str
    scenes: List[Scene]


class PreviewRequest(BaseModel):
    """首帧合成请求 (P35: 改成接整个 script,后端循环出 N 张分镜首帧)"""
    product_image_url: str = Field(..., description="产品正面图(已上传到 fal storage)")
    product_back_image_url: Optional[str] = Field(None, description="P34: 产品反面/侧面图(可选)")
    background_image_url: Optional[str] = Field(None, description="可选背景图")
    style_reference_image_url: Optional[str] = Field(None, description="P186(2026-05-08):风格参考 grid(从用户上传的参考视频抽的 2x2 帧)")
    script: Script


class GenerateRequest(BaseModel):
    """视频生成请求"""
    # P37: 删 preview 首帧步骤后, image_url 不再是必填(reference-to-video 不需要首帧)
    image_url: Optional[str] = Field(None, description="兼容字段,P36 后已不使用")
    scene_image_urls: Optional[List[str]] = Field(None, description="兼容字段 (P35,已弃用)")
    # P36: 切 reference-to-video,直接拿产品+背景图喂 Seedance,跳过 Seedream 合成
    product_image_url: Optional[str] = Field(None, description="P36: 产品正面图 URL")
    product_back_image_url: Optional[str] = Field(None, description="P36: 产品反面/侧面图 URL")
    background_image_url: Optional[str] = Field(None, description="P36: 背景图 URL")
    style_reference_image_url: Optional[str] = Field(None, description="P186(2026-05-08):风格参考 grid")
    reference_video_frame_url: Optional[str] = Field(None, description="P187(2026-05-08):参考视频中间帧(强场景锁,会被当 background_image_url 用)")
    ref_video_has_people: Optional[bool] = Field(None, description="P187:参考视频是否含人物(VLM 检测,无人物 → 跳过 Kling Avatar 走 seedance)")
    script: Script
    # P31 (2026-05-01):total_duration 上限 15 → 300。
    # >15 时 jobs.py 走 split_segments(每段 10s)+ N 段并发 + ffmpeg concat,
    # 段间一致性靠共享 overall_setting + model_description + 同一首帧 product_image_url 锁。
    duration: int = Field(15, ge=5, le=300, description="总时长 5-300 秒(>15 走多段并发拼接)")
    aspect_ratio: str = Field("9:16", description="9:16 / 16:9 / 1:1")
    resolution: str = Field("1080p", description="720p / 1080p")
    enable_audio: bool = Field(True, description="是否启用原生音频(>15 多段模式自动关,各段独立配音会跳)")
    talking_head_endpoint: Optional[str] = Field(None, description="P105: 对口型模型选择(默认 fal-ai/bytedance/omnihuman 老版表情收敛)")


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
    background_file: Optional[UploadFile] = File(None),  # P111: 背景场景图(可选,VLM 也看这张定脚本场景)
    total_duration: int = 15,
    region: str = Form("CN"),  # P99: CN(国内抖音/亚洲模特/中文话术)/ Global(海外 TikTok/西方模特/英文话术)
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

    # P111: 背景场景图(可选,VLM 写脚本时参考场景定 overall_setting + 话术情境)
    background_image_url: Optional[str] = None
    if background_file is not None and background_file.filename:
        try:
            bg_contents = await read_bounded(background_file, 10 * 1024 * 1024, IMAGE_MIMES, "ad-video 背景场景图")
            img_bg = Image.open(io.BytesIO(bg_contents))
            if img_bg.mode in ("RGBA", "P", "LA"):
                bg_bg = Image.new("RGB", img_bg.size, (255, 255, 255))
                if img_bg.mode in ("RGBA", "LA"):
                    bg_bg.paste(img_bg, mask=img_bg.split()[-1])
                else:
                    bg_bg.paste(img_bg.convert("RGBA"), mask=img_bg.convert("RGBA").split()[-1])
                img_bg = bg_bg
            elif img_bg.mode != "RGB":
                img_bg = img_bg.convert("RGB")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_bg:
                img_bg.save(tmp_bg.name, "JPEG", quality=90, optimize=True)
                if os.path.getsize(tmp_bg.name) > 10 * 1024 * 1024:
                    img_bg.save(tmp_bg.name, "JPEG", quality=75, optimize=True)
                tmp_bg_path = tmp_bg.name
            try:
                background_image_url = await fal_upload_with_retry(tmp_bg_path)
            finally:
                os.unlink(tmp_bg_path)
        except Exception as e:
            # 背景图失败不阻塞主流程,降级到不传背景给 VLM
            log_info(f"ad_video/analyze 背景图处理失败,VLM 走单图: {str(e)[:150]}")
            background_image_url = None

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
    # P99:region 透传(CN=国内/Global=海外),VLM 按 region 出对应模特+话术
    safe_duration = max(5, min(300, int(total_duration)))
    safe_region = "Global" if region.lower() in ("global", "en", "international", "海外") else "CN"
    log_info(f"ad_video/analyze region raw={region!r} safe={safe_region!r} duration={safe_duration}")
    result = await service.analyze_product(
        product_image_url,
        total_duration=safe_duration,
        region=safe_region,
        background_image_url=background_image_url,  # P111
    )

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
        f"category={audit.get('category')} back={'yes' if product_back_image_url else 'no'} "
        f"bg={'yes' if background_image_url else 'no'}"
    )
    return {
        "success": True,
        **result,
        "product_image_url": product_image_url,  # 给后续 /preview 复用
        "product_back_image_url": product_back_image_url,  # P34
        "background_image_url": background_image_url,  # P111: 给后续 /preview 复用,免重传
        "description": f"AI 带货视频分析: {audit.get('category', '产品')}",
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
        aspect_ratio=getattr(req, "aspect_ratio", None) or "9:16",
        no_text=True,  # P158(2026-05-07):AI 带货视频图严禁有任何字幕/文字
        style_reference_image_url=req.style_reference_image_url,  # P186(2026-05-08)
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
                aspect_ratio=getattr(req, "aspect_ratio", None) or "9:16",
                style_reference_image_url=req.style_reference_image_url,  # P186
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
            "scene_image_urls": req.scene_image_urls,
            # P36: 透传给 jobs.py reference-to-video 用,跳过 Seedream
            "product_image_url": req.product_image_url,
            "product_back_image_url": req.product_back_image_url,
            "background_image_url": req.background_image_url,
            "style_reference_image_url": req.style_reference_image_url,  # P186(2026-05-08)
            "reference_video_frame_url": req.reference_video_frame_url,  # P187(2026-05-08)
            "ref_video_has_people": req.ref_video_has_people,  # P187(2026-05-08)
            "script": req.script.model_dump(),
            "duration": req.duration,
            "aspect_ratio": req.aspect_ratio,
            "resolution": "720p",  # P32:reference-to-video 也强 720p
            "enable_audio": req.enable_audio,
            "talking_head_endpoint": req.talking_head_endpoint or "fal-ai/bytedance/omnihuman",  # P105
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


@router.post("/extract-style-frames")
async def extract_style_frames(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """P186/P187(2026-05-08):粘贴脚本模式上传参考视频
    → 抽 4 帧拼 2x2 grid + 单独留中间帧
    → VLM 判断中间帧是否含人物(决定后续 pipeline 分流)
    → fal 上传 grid + 中间帧
    """
    import asyncio
    import tempfile, subprocess, os
    import json as _j
    from PIL import Image
    from app.services.upload_guard import read_bounded

    VIDEO_MIMES_LOCAL = ("video/mp4", "video/quicktime", "video/webm", "video/x-msvideo")
    contents = await read_bounded(file, 50 * 1024 * 1024, VIDEO_MIMES_LOCAL, "风格参考视频")

    suffix = ".mp4"
    if file.filename and "." in file.filename:
        suffix = "." + file.filename.rsplit(".", 1)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        video_path = tmp.name

    try:
        # 1. ffprobe 拿时长
        rr = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", video_path],
            capture_output=True, text=True, timeout=30,
        )
        duration = 5.0
        try:
            duration = float(_j.loads(rr.stdout).get("format", {}).get("duration", 5.0))
        except Exception:
            pass
        duration = max(2.0, duration)

        # 2. ffmpeg 抽 4 帧:5%, 35%, 65%, 95%
        timestamps = [duration * t for t in (0.05, 0.35, 0.65, 0.95)]
        with tempfile.TemporaryDirectory() as tmpdir:
            frame_paths = []
            for i, ts in enumerate(timestamps):
                fp = os.path.join(tmpdir, f"frame_{i}.jpg")
                cp = subprocess.run(
                    ["ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", video_path, "-vframes", "1", "-q:v", "3", fp],
                    capture_output=True, timeout=30,
                )
                if cp.returncode == 0 and os.path.exists(fp) and os.path.getsize(fp) > 1000:
                    frame_paths.append(fp)
            if len(frame_paths) < 2:
                raise HTTPException(500, f"抽帧失败,只拿到 {len(frame_paths)} 帧")

            # 3. PIL 拼 grid(用拿到的帧数,凑不齐 4 张就用 2x2 重复)
            while len(frame_paths) < 4:
                frame_paths.append(frame_paths[-1])
            imgs = [Image.open(fp).convert("RGB") for fp in frame_paths[:4]]
            W0, H0 = imgs[0].size
            target_w = 512
            target_h = max(1, int(H0 * target_w / W0))
            imgs_resized = [img.resize((target_w, target_h), Image.LANCZOS) for img in imgs]
            grid = Image.new("RGB", (target_w * 2, target_h * 2), (0, 0, 0))
            grid.paste(imgs_resized[0], (0, 0))
            grid.paste(imgs_resized[1], (target_w, 0))
            grid.paste(imgs_resized[2], (0, target_h))
            grid.paste(imgs_resized[3], (target_w, target_h))
            grid_path = os.path.join(tmpdir, "style_grid.jpg")
            grid.save(grid_path, "JPEG", quality=88)

            # P187(2026-05-08):中间帧单独存,后续作 background_image_url 用 + VLM 检测人物
            middle_frame_path = os.path.join(tmpdir, "middle_frame.jpg")
            imgs[1].save(middle_frame_path, "JPEG", quality=92)  # 第 2 帧 ~35% 处

            # P190(2026-05-08):并发上传 grid + middle frame + 原视频本身(给 qwen-vl 看整段)
            grid_url, middle_url, video_fal_url = await asyncio.gather(
                fal_upload_with_retry(grid_path),
                fal_upload_with_retry(middle_frame_path),
                fal_upload_with_retry(video_path),  # 原视频上 fal,给 qwen-vl 看整段时序
            )

            # P190:qwen-vl 看整段视频(多帧时序)判断是否含人物 — 比单帧准确得多
            has_people = True  # 默认有,失败保底走 Kling Avatar
            try:
                from app.services.fal_service import get_aliyun_qwenvl_service
                qwen_svc = get_aliyun_qwenvl_service()
                if qwen_svc and qwen_svc.is_available():
                    qwen_instruction = (
                        "请仔细看完整段视频。"
                        "视频里是否有 CLEARLY VISIBLE PERSON 作为主要拍摄对象?"
                        "也就是说必须看到 FACE / HEAD / 上半身大部分(肩膀+躯干)清晰出镜。"
                        "严格不算:只有手 / 只有手指 / 只有手臂 / 只有脚 / 任何 partial body part — "
                        "这些都不算有人。"
                        "只展示产品 / 物体 / 手拿产品 / 没脸没躯干的场景 → 答 no。"
                        "请用 EXACTLY 一个英文单词回答: yes 或 no(不要解释,只回一个词)。"
                    )
                    qwen_res = await asyncio.wait_for(
                        qwen_svc.analyze_video(video_fal_url, qwen_instruction),
                        timeout=180,
                    )
                    if "error" in qwen_res:
                        raise Exception(qwen_res["error"])
                    ans = (qwen_res.get("text") or "").strip().lower()
                    # 取第一个有意义的词
                    first_word = ""
                    for w in ans.replace(",", " ").replace(".", " ").split():
                        if w in ("yes", "no", "是", "否", "有", "无"):
                            first_word = w
                            break
                    if not first_word and ans:
                        first_word = ans.split()[0].strip(".,!? ").lower()
                    if first_word in ("no", "否", "无"):
                        has_people = False
                    elif first_word in ("yes", "是", "有"):
                        has_people = True
                    log_info(f"ad_video/extract-style-frames P190 qwen-vl(整段视频) has_people={has_people} ans='{ans[:80]}'")
                else:
                    log_info(f"ad_video/extract-style-frames qwen-vl 不可用,用 fal 单帧 fallback")
                    raise Exception("qwen-vl 不可用")
            except Exception as _qwen_err:
                # qwen-vl 整段视频不可用 → fallback 到 fal openrouter VLM 看单帧(P190 收紧的 prompt)
                log_info(f"ad_video/extract-style-frames qwen-vl 整段失败,fallback 单帧 VLM: {_qwen_err}")
                try:
                    import fal_client as _fc
                    vlm_res = await asyncio.wait_for(
                        _fc.run_async(
                            "openrouter/router/vision",
                            arguments={
                                "image_urls": [middle_url],
                                "prompt": (
                                    "Look at this image carefully. Is there a CLEARLY VISIBLE PERSON "
                                    "as a main subject — meaning a face, head, or substantial portion of "
                                    "the upper body (torso/shoulders) is visible in the frame? "
                                    "STRICTLY DO NOT count: a hand alone, a finger alone, an arm alone, "
                                    "a foot alone, or any partial body part — those alone do NOT mean "
                                    "there's a person in the frame. "
                                    "If the image only shows products, objects, hands holding products, "
                                    "or scenes without a face/torso/full body, answer 'no'. "
                                    "Answer with EXACTLY one word: yes or no."
                                ),
                                "model": "qwen/qwen3-vl-235b-a22b-instruct",
                            },
                        ),
                        timeout=60,
                    )
                    ans = (vlm_res.get("output") or "").strip().lower() if isinstance(vlm_res, dict) else ""
                    if ans.startswith("no"):
                        has_people = False
                    elif ans.startswith("yes"):
                        has_people = True
                    log_info(f"ad_video/extract-style-frames fallback 单帧 has_people={has_people} ans='{ans[:30]}'")
                except Exception as _vlm_err:
                    log_info(f"ad_video/extract-style-frames 单帧 VLM 也失败(默认有人物): {_vlm_err}")

            log_info(f"ad_video/extract-style-frames user={current_user.get('id')} dur={duration:.1f}s grid={grid_url[:60]} middle={middle_url[:60]} has_people={has_people}")
            return {
                "grid_image_url": grid_url,
                "middle_frame_url": middle_url,
                "has_people": has_people,
                "duration_sec": duration,
            }
    finally:
        try: os.unlink(video_path)
        except Exception: pass


@router.post("/generate-background")
@require_credits("ad_video/preview")  # 复用 preview 的 credit hook(GPT-Image 2 一次调用)
async def generate_background(
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    """P182(2026-05-08):粘贴脚本模式没上传背景图时,根据脚本里的 overall_setting
    用 GPT-Image 2 文生图生成一张干净背景(无人无产品),后续 N 段首帧共享。
    """
    import fal_client
    import asyncio as _aio
    from app.services.ad_video_models import _img_size_for_aspect

    scene_description = (body.get("scene_description") or "").strip()
    aspect_ratio = (body.get("aspect_ratio") or "9:16").strip()
    if not scene_description:
        raise HTTPException(400, "需要 scene_description(脚本里的整体场景描述)")

    # 安全 prompt:明确"无人无产品",GPT-Image 2 才不会自己加模特
    prompt = (
        f"Photorealistic empty environment background photo: {scene_description}. "
        f"Professional commercial photography style, soft natural lighting, "
        f"clean composition, no people, no products, no text, no logos, no watermarks. "
        f"Just the empty environment / scene as described."
    )
    log_info(f"ad_video/generate-bg user={current_user.get('id')} scene='{scene_description[:80]}' ratio={aspect_ratio}")

    try:
        # 360s wait_for 防 fal hang
        result = await _aio.wait_for(
            fal_client.run_async(
                "openai/gpt-image-2",  # 不带 /edit,这是 text-to-image
                arguments={
                    "prompt": prompt,
                    "image_size": _img_size_for_aspect(aspect_ratio),
                    "num_images": 1,
                    "quality": "high",
                    "output_format": "png",
                },
            ),
            timeout=360,
        )
        images = result.get("images") if isinstance(result, dict) else None
        url = None
        if isinstance(images, list) and images:
            first = images[0]
            url = first.get("url") if isinstance(first, dict) else first
        if not url:
            raise HTTPException(500, "GPT-Image 2 未返回图片 URL")
        log_info(f"ad_video/generate-bg ok url={url[:80]}")
        return {"image_url": url}
    except _aio.TimeoutError:
        raise HTTPException(504, "GPT-Image 2 超时(>6 min),请重试")
    except Exception as e:
        raise HTTPException(500, f"生成背景失败: {str(e)[:200]}")


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
