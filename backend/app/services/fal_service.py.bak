"""
AI 服务封装
- 图片生成：nano-banana-2 / flux/schnell / flux/dev
- 视频生成：kling-video / kling-edit
- 数字人：hunyuan-avatar / pixverse-lipsync
- 语音：qwen3-tts / minimax-voice-clone
- 集成熔断器：连续失败 3 次自动切换
"""
import fal_client
from typing import Optional, Dict, Any
from .circuit_breaker import get_circuit_breaker
from .alert import get_alert_service


class FalImageService:
    """FAL AI 图片生成服务"""

    # 可用模型
    MODELS = {
        "nano-banana-2": {
            "endpoint": "fal-ai/nano-banana-2",
            "label": "经济模式",
            "desc": "最低成本，速度较慢",
        },
        "flux/schnell": {
            "endpoint": "fal-ai/flux/schnell",
            "label": "快速模式",
            "desc": "生成速度快，质量高",
        },
        "flux/dev": {
            "endpoint": "fal-ai/flux/dev",
            "label": "专业模式",
            "desc": "更高质量的生成效果",
        },
    }

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def generate(self, prompt: str, image_size: str = "1024x1024", model_key: str = "nano-banana-2") -> dict:
        """文生图"""
        return await self._generate_fal(prompt, image_size, model_key, None)

    async def generate_with_image(self, image_url: str, prompt: str, image_size: str = "1024x1024", model_key: str = "nano-banana-2") -> dict:
        """图生图"""
        return await self._generate_fal(prompt, image_size, model_key, image_url)

    async def _generate_fal(self, prompt: str, image_size: str, model_key: str, image_url: Optional[str] = None) -> dict:
        """FAL AI 通用生成方法"""
        circuit_breaker = get_circuit_breaker()

        # 检查模型是否可用（熔断检查）
        if not circuit_breaker.is_available(model_key):
            # 尝试切换到备用模型
            backup_model = "flux/schnell" if model_key == "nano-banana-2" else "nano-banana-2"
            if circuit_breaker.is_available(backup_model):
                model_key = backup_model
            else:
                return {"error": f"模型 {model_key} 已熔断，暂无可用备用模型"}

        try:
            model_info = self.MODELS.get(model_key)
            if not model_info:
                return {"error": f"未知模型：{model_key}"}

            endpoint = model_info["endpoint"]

            arguments = {
                "prompt": prompt,
                "image_size": image_size,
            }

            # 如果有参考图，添加到参数中
            if image_url:
                arguments["image_url"] = image_url

            result = await fal_client.run_async(
                endpoint,
                arguments=arguments,
            )

            # 提取图片 URL
            images = result.get("images", [])
            if not images:
                # flux 可能返回 data 字段
                data = result.get("data", {})
                if data:
                    images = data.get("images", [])

            if not images:
                await circuit_breaker.record_failure(model_key)
                return {"error": "No images generated"}

            # 记录成功
            await circuit_breaker.record_success(model_key)

            image_url = images[0].get("url")
            width, height = map(int, image_size.split("x"))

            return {
                "image_url": image_url,
                "width": width,
                "height": height,
                "model": endpoint,
                "model_label": model_info["label"],
            }

        except Exception as e:
            # 记录失败
            should_alert = await circuit_breaker.record_failure(model_key)
            if should_alert:
                # 触发告警
                alert_service = get_alert_service()
                if alert_service:
                    await alert_service.notify_model_failure(model_key, 3)

            return {"error": str(e)}


class FalVideoService:
    """FAL AI 视频生成服务 (Kling)"""

    # 可用模型
    MODELS = {
        "kling/image-to-video": {
            "endpoint": "fal-ai/kling-video/o3/standard/image-to-video",
            "label": "图生视频",
            "desc": "从图片生成 5 秒视频",
        },
        "kling/edit": {
            "endpoint": "fal-ai/kling-video/o3/standard/edit",
            "label": "视频编辑",
            "desc": "视频元素替换/翻拍",
        },
    }

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def generate_from_image(self, image_url: str, prompt: str = "") -> dict:
        """从图片生成视频 (Image-to-Video)"""
        return await self._generate_video("kling/image-to-video", {"image_url": image_url, "prompt": prompt})

    async def replace_element(self, video_url: str, element_image_url: str, instruction: str) -> dict:
        """
        视频元素替换
        使用 Kling O1 Edit 模型，根据自然语言指令替换视频中的元素
        """
        return await self._generate_video("kling/edit", {
            "video_url": video_url,
            "element_image_url": element_image_url,
            "instruction": instruction,
        })

    async def clone_video(self, reference_video_url: str, model_image_url: str, product_image_url: Optional[str] = None) -> dict:
        """
        视频翻拍复刻
        提取参考视频的运镜、节奏、动作，将主体替换为用户的模特和产品
        """
        arguments = {
            "reference_video_url": reference_video_url,
            "model_image_url": model_image_url,
        }
        if product_image_url:
            arguments["product_image_url"] = product_image_url

        return await self._generate_video("kling/edit", arguments)

    async def _generate_video(self, model_key: str, arguments: Dict[str, Any]) -> dict:
        """视频生成通用方法"""
        circuit_breaker = get_circuit_breaker()

        # 检查模型是否可用
        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}

        try:
            model_info = self.MODELS.get(model_key)
            if not model_info:
                return {"error": f"未知模型：{model_key}"}

            endpoint = model_info["endpoint"]

            handler = await fal_client.submit_async(
                endpoint,
                arguments=arguments,
            )

            # 记录成功
            await circuit_breaker.record_success(model_key)

            return {
                "task_id": handler.request_id,
                "status": "pending",
                "message": "视频生成任务已提交，预计需要 2-5 分钟",
                "model": endpoint,
            }

        except Exception as e:
            # 记录失败
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}

    async def get_task_status(self, task_id: str) -> dict:
        """查询任务状态"""
        try:
            result = await fal_client.fetch_result(task_id)

            if result.get("video"):
                video_url = result["video"].get("url")
                return {
                    "status": "completed",
                    "video_url": video_url,
                    "thumbnail_url": result.get("thumbnail", {}).get("url"),
                }

            return {"status": "processing"}

        except Exception as e:
            return {"status": "processing", "error": str(e)}


class FalAvatarService:
    """FAL AI 数字人服务"""

    MODELS = {
        "hunyuan-avatar": {
            "endpoint": "fal-ai/hunyuan-avatar",
            "label": "腾讯混元数字人",
            "desc": "高质量口型驱动，无多余动作",
        },
        "pixverse-lipsync": {
            "endpoint": "fal-ai/pixverse/lipsync",
            "label": "Pixverse 口型同步",
            "desc": "快速口型同步",
        },
    }

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def generate(self, character_image_url: str, audio_url: str, model_key: str = "hunyuan-avatar") -> dict:
        """
        数字人驱动
        上传人物半身照 + 音频文件，生成对口型视频
        """
        circuit_breaker = get_circuit_breaker()

        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}

        try:
            model_info = self.MODELS.get(model_key)
            if not model_info:
                return {"error": f"未知模型：{model_key}"}

            endpoint = model_info["endpoint"]

            result = await fal_client.run_async(
                endpoint,
                arguments={
                    "character_image_url": character_image_url,
                    "audio_url": audio_url,
                },
            )

            # 记录成功
            await circuit_breaker.record_success(model_key)

            video_url = result.get("video", {}).get("url")
            if not video_url:
                return {"error": "No video generated"}

            return {
                "task_id": "avatar_" + str(hash(character_image_url)),
                "status": "completed",
                "video_url": video_url,
                "model": endpoint,
                "model_label": model_info["label"],
            }

        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}


class FalVoiceService:
    """FAL AI 语音服务"""

    MODELS = {
        "qwen3-tts": {
            "endpoint": "fal-ai/qwen3-tts",
            "label": "通义千问 TTS",
            "desc": "高质量文本转语音",
        },
        "minimax-voice-clone": {
            "endpoint": "fal-ai/minimax/voice-clone",
            "label": "MiniMax 声音克隆",
            "desc": "5-10 秒音色提取",
        },
    }

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def clone_voice(self, reference_audio_url: str, text: str) -> dict:
        """
        声音克隆
        用户上传 5-10 秒参考音频，系统提取音色特征
        """
        circuit_breaker = get_circuit_breaker()
        model_key = "minimax-voice-clone"

        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}

        try:
            model_info = self.MODELS.get(model_key)
            if not model_info:
                return {"error": f"未知模型：{model_key}"}

            endpoint = model_info["endpoint"]

            result = await fal_client.run_async(
                endpoint,
                arguments={
                    "reference_audio_url": reference_audio_url,
                    "text": text,
                },
            )

            await circuit_breaker.record_success(model_key)

            audio_url = result.get("audio", {}).get("url")
            if not audio_url:
                return {"error": "No audio generated"}

            return {
                "voice_id": "cloned_" + str(hash(reference_audio_url)),
                "audio_url": audio_url,
                "duration": len(text) * 0.5,
                "model": endpoint,
            }

        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}

    async def text_to_speech(self, text: str, voice_id: str = "default", speed: float = 1.0) -> dict:
        """
        文本转语音
        使用预设音色或已克隆的音色生成配音
        """
        circuit_breaker = get_circuit_breaker()
        model_key = "qwen3-tts"

        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}

        try:
            model_info = self.MODELS.get(model_key)
            if not model_info:
                return {"error": f"未知模型：{model_key}"}

            endpoint = model_info["endpoint"]

            result = await fal_client.run_async(
                endpoint,
                arguments={
                    "text": text,
                    "voice_id": voice_id,
                    "speed": speed,
                },
            )

            await circuit_breaker.record_success(model_key)

            audio_url = result.get("audio", {}).get("url")
            if not audio_url:
                return {"error": "No audio generated"}

            return {
                "audio_url": audio_url,
                "duration": len(text) * 0.5 / speed,
                "voice_id": voice_id,
                "model": endpoint,
            }

        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}


# 单例
_image_service: Optional[FalImageService] = None
_video_service: Optional[FalVideoService] = None
_avatar_service: Optional[FalAvatarService] = None
_voice_service: Optional[FalVoiceService] = None


def init_fal_services(fal_key: str):
    global _image_service, _video_service, _avatar_service, _voice_service
    _image_service = FalImageService(fal_key)
    _video_service = FalVideoService(fal_key)
    _avatar_service = FalAvatarService(fal_key)
    _voice_service = FalVoiceService(fal_key)


def get_image_service() -> FalImageService:
    return _image_service


def get_video_service() -> FalVideoService:
    return _video_service


def get_avatar_service() -> FalAvatarService:
    return _avatar_service


def get_voice_service() -> FalVoiceService:
    return _voice_service
