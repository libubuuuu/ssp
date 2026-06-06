"""
图片生成 API
- 唯一模型: gpt-image-2 (openai/gpt-image-2 / openai/gpt-image-2/edit, quality=medium)
- 多参考图生图: 权重排序机制
- 额度扣费: 使用 @require_credits 装饰器自动处理
"""
import asyncio
import io
import os
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List

from app.api.auth import get_current_user
from app.services.content_filter import assert_safe_prompt
from app.services.cos_upload import upload_to_cos
from app.services.decorators import require_credits
from app.services.fal_service import get_image_service
from app.services.media_archiver import archive_url
from app.services.upload_guard import read_bounded, IMAGE_MIMES

router = APIRouter()


def _process_image_to_tmp(contents: bytes) -> str:
    """Pillow 标准化处理：RGBA→RGB 白底 / 宽高比 0.40~2.50 / 最小 300px / JPEG q90。
    返回临时文件路径，调用方负责 os.unlink。"""
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
    w, h = img.size
    ratio = w / h
    if ratio < 0.40:
        new_w = int(h * 0.45)
        new_img = Image.new("RGB", (new_w, h), (255, 255, 255))
        new_img.paste(img, ((new_w - w) // 2, 0))
        img = new_img
        w, h = img.size
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
        return tmp.name


class ImageStyleRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=1000)
    style: Optional[str] = Field("advertising", max_length=50)
    size: Optional[str] = Field("1024x1024", max_length=20)
    color_tone: Optional[str] = Field(None, max_length=50)
    model: Optional[str] = Field("gpt-image-2", max_length=50)


class ImageRealisticRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=1000)
    refine_prompt: Optional[str] = Field(None, max_length=500)
    model: Optional[str] = Field("gpt-image-2", max_length=50)


class ImageInpaintRequest(BaseModel):
    image_url: str
    mask_url: Optional[str] = None
    prompt: str = Field(..., min_length=1, max_length=1000)


class ImageMultiReferenceRequest(BaseModel):
    """多参考图生图请求"""
    prompt: str = Field(..., min_length=1, max_length=1000)
    reference_images: List[str] = Field(..., min_length=1, max_length=5)
    style: Optional[str] = Field("custom", max_length=50)
    size: Optional[str] = Field("1024x1024", max_length=20)
    model: Optional[str] = Field("gpt-image-2", max_length=50)

    @field_validator("size")
    @classmethod
    def validate_size(cls, v: str) -> str:
        allowed = {"512x512", "768x768", "1024x1024", "512x768", "768x512", "1024x768", "768x1024"}
        if v not in allowed:
            raise ValueError(f"size 必须是以下值之一：{', '.join(sorted(allowed))}")
        return v


@router.post("/style")
@require_credits("image/style")
async def generate_style_image(req: ImageStyleRequest, current_user: dict = Depends(get_current_user)):
    """生成风格化/广告级图片"""
    assert_safe_prompt(req.prompt)
    service = get_image_service()

    full_prompt = req.prompt
    style_prefixes = {
        "advertising": "Professional advertising photography, high-end product shot, studio lighting, commercial grade,",
        "minimalist": "Minimalist design, clean composition, elegant simplicity,",
        "custom": "",
    }

    if req.style in style_prefixes:
        full_prompt = style_prefixes[req.style] + " " + req.prompt

    if req.color_tone:
        full_prompt += f", {req.color_tone} color tone"

    result = await service.generate(full_prompt, req.size, req.model, current_user["id"])

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    # BUG-2: 归档 fal URL 到本地 /uploads(防 fal.media 7-30 天过期)
    # subrouter 路径已直接写本地，archive_url 会自动跳过；fal 降级路径仍需归档
    if result.get("image_url"):
        result["image_url"] = await archive_url(result["image_url"], current_user["id"], "image")

    return {
        "success": True,
        **result,
        "description": f"风格化生成：{req.prompt[:50]}...",
    }


@router.post("/realistic")
@require_credits("image/realistic")
async def generate_realistic_image(req: ImageRealisticRequest, current_user: dict = Depends(get_current_user)):
    """生成写实/可控图片"""
    assert_safe_prompt(req.prompt)
    assert_safe_prompt(req.refine_prompt)
    service = get_image_service()

    full_prompt = req.prompt
    if req.refine_prompt:
        full_prompt += ". " + req.refine_prompt

    result = await service.generate(full_prompt, "1024x1024", req.model, current_user["id"])

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    if result.get("image_url"):
        result["image_url"] = await archive_url(result["image_url"], current_user["id"], "image")

    return {
        "success": True,
        **result,
        "description": f"写实生成：{req.prompt[:50]}...",
    }


@router.post("/inpaint")
async def inpaint_image(
    req: ImageInpaintRequest,
    current_user: dict = Depends(get_current_user),
):
    """局部编辑 (inpainting)

    五十八续:stub 也加鉴权防匿名扫(本端点 501 不真做事,但 attack surface)。
    """
    raise HTTPException(status_code=501, detail="Inpainting 功能尚未实现")


@router.post("/multi-reference")
@require_credits("image/multi-reference")
async def generate_multi_reference_image(req: ImageMultiReferenceRequest, current_user: dict = Depends(get_current_user)):
    """
    多参考图生图
    参考图顺序决定权重：第一张 50%, 第二张 30%, 第三张 20%
    """
    assert_safe_prompt(req.prompt)
    service = get_image_service()

    if not req.reference_images:
        raise HTTPException(status_code=400, detail="至少需要一张参考图")

    # 构建提示词（只在必要时加风格前缀）
    full_prompt = req.prompt
    style_prefixes = {
        "advertising": "Professional advertising photography, commercial lighting, ",
        "minimalist": "Minimalist design, clean background, ",
        "custom": "",
    }
    if req.style in style_prefixes and style_prefixes[req.style]:
        full_prompt = style_prefixes[req.style] + full_prompt

    # 八十四续 P6:nano-banana-2/edit (Google) NSFW 拦截极严 + fal 端 downstream
    # 偶发 unavailable。切字节 Seedream 4 edit (国产对带货宽容,稳定性更好)。
    import fal_client
    try:
        fal_result = await fal_client.run_async(
            "fal-ai/bytedance/seedream/v4/edit",
            arguments={
                "prompt": full_prompt,
                "image_urls": req.reference_images,
                "image_size": "square_hd",
            }
        )
        images = fal_result.get("images", [])
        if not images:
            raise HTTPException(status_code=500, detail="未生成图片")
        img_url = images[0].get("url")
        result = {
            "image_url": img_url,
            "model": "fal-ai/bytedance/seedream/v4/edit",
            "model_label": "Seedream 4 Edit (字节多图融合)",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    if result.get("image_url"):
        result["image_url"] = await archive_url(result["image_url"], current_user["id"], "image")

    return {
        "success": True,
        **result,
        "description": f"多参考图生成：{req.prompt[:50]}... ({len(req.reference_images)}张图)",
        "reference_count": len(req.reference_images),
    }


@router.get("/models")
async def list_models():
    """列出可用的图片生成模型"""
    service = get_image_service()
    return {"models": service.MODELS}


@router.post("/upload/cos")
async def upload_image_to_cos(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """上传图片到腾讯 COS（图片生成参考图用）"""
    contents = await read_bounded(file, max_bytes=10 * 1024 * 1024, allowed_mimes=IMAGE_MIMES, label="图片")
    tmp_path = _process_image_to_tmp(contents)
    try:
        url = await asyncio.to_thread(upload_to_cos, tmp_path)
        return {"url": url}
    finally:
        os.unlink(tmp_path)
