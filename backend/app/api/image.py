"""
图片生成 API
- 经济模式：nano-banana-2
- 快速模式：flux/schnell
- 多参考图生图：权重排序机制
- 额度扣费：使用 @require_credits 装饰器自动处理
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from app.services.fal_service import get_image_service
from app.services.decorators import require_credits
from app.api.auth import get_current_user

router = APIRouter()


class ImageStyleRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=1000)
    style: Optional[str] = Field("advertising", max_length=50)
    size: Optional[str] = Field("1024x1024", max_length=20)
    color_tone: Optional[str] = Field(None, max_length=50)
    model: Optional[str] = Field("nano-banana-2", max_length=50)


class ImageRealisticRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=1000)
    refine_prompt: Optional[str] = Field(None, max_length=500)
    model: Optional[str] = Field("nano-banana-2", max_length=50)


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
    model: Optional[str] = Field("nano-banana-2", max_length=50)

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

    result = await service.generate(full_prompt, req.size, req.model)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return {
        "success": True,
        **result,
        "description": f"风格化生成：{req.prompt[:50]}...",
    }


@router.post("/realistic")
@require_credits("image/realistic")
async def generate_realistic_image(req: ImageRealisticRequest, current_user: dict = Depends(get_current_user)):
    """生成写实/可控图片"""
    service = get_image_service()

    full_prompt = req.prompt
    if req.refine_prompt:
        full_prompt += ". " + req.refine_prompt

    result = await service.generate(full_prompt, "1024x1024", req.model)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return {
        "success": True,
        **result,
        "description": f"写实生成：{req.prompt[:50]}...",
    }


@router.post("/inpaint")
async def inpaint_image(req: ImageInpaintRequest):
    """局部编辑 (inpainting)"""
    raise HTTPException(status_code=501, detail="Inpainting 功能尚未实现")


@router.post("/multi-reference")
@require_credits("image/multi-reference")
async def generate_multi_reference_image(req: ImageMultiReferenceRequest, current_user: dict = Depends(get_current_user)):
    """
    多参考图生图
    参考图顺序决定权重：第一张 50%, 第二张 30%, 第三张 20%
    """
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

    import fal_client
    try:
        fal_result = await fal_client.run_async(
            "fal-ai/nano-banana-2/edit",
            arguments={
                "prompt": full_prompt,
                "image_urls": req.reference_images,
            }
        )
        images = fal_result.get("images", [])
        if not images:
            raise HTTPException(status_code=500, detail="未生成图片")
        img_url = images[0].get("url")
        result = {
            "image_url": img_url,
            "model": "fal-ai/nano-banana-2/edit",
            "model_label": "Nano Banana 2 Edit (多图融合)",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

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
