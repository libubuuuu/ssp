"""
数字人 API
- 克制型数字人（只对口型，无多余动作）
- 语音克隆
- TTS 文本转语音
- 额度扣费：任务提交时扣费，失败返还
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from app.services.billing import get_task_cost, check_user_credits, deduct_credits, add_credits, create_consumption_record
from app.services.fal_service import get_avatar_service, get_voice_service
from app.api.auth import get_current_user
import uuid

router = APIRouter()


class AvatarGenerateRequest(BaseModel):
    """数字人生成请求"""
    character_image_url: str  # 人物半身照
    audio_url: str  # 音频文件 URL
    model: Optional[str] = "hunyuan-avatar"  # hunyuan-avatar | pixverse-lipsync


class VoiceCloneRequest(BaseModel):
    """声音克隆请求"""
    reference_audio_url: str  # 5-10 秒参考音频
    text: str  # 要转换的文案
    model: Optional[str] = "qwen3-tts"  # qwen3-tts | minimax-voice-clone


class TTSRequest(BaseModel):
    """文本转语音请求"""
    text: str
    voice_id: Optional[str] = "default"  # 音色 ID
    speed: Optional[float] = 1.0  # 语速
    pitch: Optional[float] = 1.0  # 音调


@router.post("/generate")
async def generate_avatar(req: AvatarGenerateRequest, current_user: dict = Depends(get_current_user)):
    """
    数字人驱动
    上传人物半身照 + 音频文件，生成对口型视频
    约束：无多余手势和身体晃动，仅做面部表情驱动与精准唇形同步
    """
    # 获取任务成本
    cost = get_task_cost("avatar/generate")

    # 扣费(原子:SQL 层 WHERE credits >= ?,无竞态)
    if not deduct_credits(current_user["id"], cost):
        raise HTTPException(status_code=402, detail=f"积分不足,需要 {cost} 积分")

    try:
        # 调用 FAL AI 数字人服务
        avatar_service = get_avatar_service()
        result = await avatar_service.generate(
            character_image_url=req.character_image_url,
            audio_url=req.audio_url,
            model_key=req.model or "hunyuan-avatar"
        )

        if "error" in result:
            # 失败返还
            add_credits(current_user["id"], cost)
            raise HTTPException(status_code=500, detail=result["error"])

        # 创建消费记录
        task_id = result.get("task_id", str(uuid.uuid4()))
        create_consumption_record(
            user_id=current_user["id"],
            task_id=task_id,
            module="avatar/generate",
            cost=cost,
            description=f"数字人生成：{req.character_image_url[:50]}..."
        )

        return {
            "task_id": task_id,
            "status": result.get("status", "pending"),
            "video_url": result.get("video_url"),
            "model": result.get("model"),
            "cost": cost,
        }
    except HTTPException:
        raise
    except Exception as e:
        # 失败返还
        add_credits(current_user["id"], cost)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/voice/clone")
async def clone_voice(req: VoiceCloneRequest, current_user: dict = Depends(get_current_user)):
    """
    声音克隆
    用户上传 5-10 秒参考音频，系统提取音色特征
    输入任意文案，即可生成该音色的配音
    """
    # 获取任务成本
    cost = get_task_cost("voice/clone")

    # 扣费(原子:SQL 层 WHERE credits >= ?,无竞态)
    if not deduct_credits(current_user["id"], cost):
        raise HTTPException(status_code=402, detail=f"积分不足,需要 {cost} 积分")

    try:
        # 调用 FAL AI 语音服务
        voice_service = get_voice_service()
        result = await voice_service.clone_voice(
            reference_audio_url=req.reference_audio_url,
            text=req.text
        )

        if "error" in result:
            # 失败返还
            add_credits(current_user["id"], cost)
            raise HTTPException(status_code=500, detail=result["error"])

        # 创建消费记录
        task_id = str(uuid.uuid4())
        create_consumption_record(
            user_id=current_user["id"],
            task_id=task_id,
            module="voice/clone",
            cost=cost,
            description=f"声音克隆：{req.text[:50]}..."
        )

        return {
            "voice_id": result.get("voice_id"),
            "audio_url": result.get("audio_url"),
            "duration": result.get("duration"),
            "model": result.get("model"),
            "cost": cost,
        }
    except HTTPException:
        raise
    except Exception as e:
        # 失败返还
        add_credits(current_user["id"], cost)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/voice/tts")
async def text_to_speech(req: TTSRequest, current_user: dict = Depends(get_current_user)):
    """
    文本转语音
    使用预设音色或已克隆的音色生成配音
    """
    # 获取任务成本
    cost = get_task_cost("voice/tts")

    # 扣费(原子:SQL 层 WHERE credits >= ?,无竞态)
    if not deduct_credits(current_user["id"], cost):
        raise HTTPException(status_code=402, detail=f"积分不足,需要 {cost} 积分")

    try:
        # 调用 FAL AI 语音服务
        voice_service = get_voice_service()
        result = await voice_service.text_to_speech(
            text=req.text,
            voice_id=req.voice_id,
            speed=req.speed
        )

        if "error" in result:
            # 失败返还
            add_credits(current_user["id"], cost)
            raise HTTPException(status_code=500, detail=result["error"])

        # 创建消费记录
        task_id = str(uuid.uuid4())
        create_consumption_record(
            user_id=current_user["id"],
            task_id=task_id,
            module="voice/tts",
            cost=cost,
            description=f"TTS: {req.text[:50]}..."
        )

        return {
            "audio_url": result.get("audio_url"),
            "duration": result.get("duration"),
            "voice_id": req.voice_id,
            "model": result.get("model"),
            "cost": cost,
        }
    except HTTPException:
        raise
    except Exception as e:
        # 失败返还
        add_credits(current_user["id"], cost)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/voice/presets")
async def get_voice_presets():
    """获取预设音色列表"""
    return {
        "voices": [
            {"id": "female_1", "name": "温柔女声", "gender": "female", "style": "温暖"},
            {"id": "female_2", "name": "知性女声", "gender": "female", "style": "专业"},
            {"id": "male_1", "name": "沉稳男声", "gender": "male", "style": "权威"},
            {"id": "male_2", "name": "活力男声", "gender": "male", "style": "活泼"},
        ]
    }
