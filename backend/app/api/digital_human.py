"""
数字人 AI API(占位 — 未真接入)

⚠ 历史 bug:此模块过去用 @require_credits 装饰器但函数返回 placeholder,
导致用户被扣 10 积分换一个假 task_id。从此一律返回 501,且**绝不**走扣费路径。

真实"图片+音频→数字人视频"功能在 app/api/avatar.py 的 /api/avatar/generate
(FAL hunyuan-avatar / pixverse-lipsync)。"图片+脚本→数字人"(SadTalker
风格)目前未实现,等接入后再启用本模块。
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from typing import Optional
from app.api.auth import get_current_user

router = APIRouter()


@router.post("/generate")
async def generate_digital_human(
    image: UploadFile = File(...),
    script: str = Form(...),
    audio: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user),
):
    raise HTTPException(
        status_code=501,
        detail="数字人(图片+脚本)功能尚未上线,请使用 /avatar 页(图片+音频)。"
                "本接口不会扣除任何积分。",
    )
