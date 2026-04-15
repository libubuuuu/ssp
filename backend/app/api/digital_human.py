"""
数字人 AI API
- 上传图片 + 脚本
- 口型精准、无多余动作
- 额度扣费：使用 @require_credits 装饰器自动处理
"""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from app.services.decorators import require_credits
from app.api.auth import get_current_user

router = APIRouter()


class DigitalHumanRequest(BaseModel):
    """数字人生成"""
    image_url: str  # 上传后返回的 URL
    script: str
    use_audio: Optional[bool] = False  # 是否上传音频


@router.post("/generate")
@require_credits("avatar/generate")
async def generate_digital_human(
    image: UploadFile = File(...),
    script: str = Form(...),
    audio: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user),
):
    """生成数字人视频"""
    # TODO: 接入 SadTalker / D-ID / HeyGen
    return {
        "success": True,
        "task_id": "placeholder",
        "message": "数字人视频生成任务已提交",
        "description": f"数字人生成：{script[:50]}...",
    }
