"""
数字人 AI API
- 上传图片 + 脚本
- 口型精准、无多余动作
"""
from fastapi import APIRouter, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class DigitalHumanRequest(BaseModel):
    """数字人生成"""
    image_url: str  # 上传后返回的 URL
    script: str
    use_audio: Optional[bool] = False  # 是否上传音频


@router.post("/generate")
async def generate_digital_human(
    image: UploadFile = File(...),
    script: str = Form(...),
    audio: Optional[UploadFile] = File(None),
):
    """生成数字人视频"""
    # TODO: 接入 SadTalker / D-ID / HeyGen
    return {"task_id": "placeholder", "message": "数字人视频生成任务已提交"}
