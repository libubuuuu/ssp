"""
AI 服务封装
"""
import fal_client
import os
from typing import Optional, Dict, Any
from .circuit_breaker import get_circuit_breaker
from .alert import get_alert_service


class FalImageService:
    MODELS = {
        "nano-banana-2": {"endpoint": "fal-ai/nano-banana-2", "label": "经济模式", "desc": "最低成本"},
        "flux/schnell": {"endpoint": "fal-ai/flux/schnell", "label": "快速模式", "desc": "速度快"},
        "flux/dev": {"endpoint": "fal-ai/flux/dev", "label": "专业模式", "desc": "更高质量"},
    }

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def generate(self, prompt: str, image_size: str = "1024x1024", model_key: str = "nano-banana-2") -> dict:
        return await self._generate_fal(prompt, image_size, model_key, None)

    async def generate_with_image(self, image_url: str, prompt: str, image_size: str = "1024x1024", model_key: str = "nano-banana-2") -> dict:
        return await self._generate_fal(prompt, image_size, model_key, image_url)

    async def _generate_fal(self, prompt: str, image_size: str, model_key: str, image_url: Optional[str] = None) -> dict:
        circuit_breaker = get_circuit_breaker()
        if not circuit_breaker.is_available(model_key):
            backup_model = "flux/schnell" if model_key == "nano-banana-2" else "nano-banana-2"
            if circuit_breaker.is_available(backup_model):
                model_key = backup_model
            else:
                return {"error": f"模型 {model_key} 已熔断"}
        try:
            model_info = self.MODELS.get(model_key)
            if not model_info:
                return {"error": f"未知模型：{model_key}"}
            endpoint = model_info["endpoint"]
            arguments = {"prompt": prompt, "image_size": image_size}
            if image_url:
                arguments["image_url"] = image_url
            result = await fal_client.run_async(endpoint, arguments=arguments)
            images = result.get("images", [])
            if not images:
                data = result.get("data", {})
                if data:
                    images = data.get("images", [])
            if not images:
                await circuit_breaker.record_failure(model_key)
                return {"error": "No images generated"}
            await circuit_breaker.record_success(model_key)
            img_url = images[0].get("url")
            width, height = map(int, image_size.split("x"))
            return {"image_url": img_url, "width": width, "height": height, "model": endpoint, "model_label": model_info["label"]}
        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}


class FalVideoService:
    MODELS = {
        "kling/image-to-video": {"endpoint": "fal-ai/kling-video/o3/standard/image-to-video", "label": "图生视频"},
        "kling/edit": {"endpoint": "fal-ai/kling-video/o1/video-to-video/edit", "label": "元素替换(快速)"},
        "kling/edit-o3": {"endpoint": "fal-ai/kling-video/o3/pro/video-to-video/edit", "label": "翻拍复刻(高质量+中文口播)"},
        "kling/reference": {"endpoint": "fal-ai/kling-video/o1/video-to-video/reference", "label": "最强复刻"},
    }

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def generate_from_image(self, image_url: str, prompt: str = "", tail_image_url=None) -> dict:
        args = {"image_url": image_url, "prompt": prompt, "generate_audio": True}
        if tail_image_url:
            args["tail_image_url"] = tail_image_url
        return await self._generate_video("kling/image-to-video", args)

    async def replace_element(self, video_url: str, element_image_url: str, instruction: str, product_image_url: str = None) -> dict:
        elements = [{"frontal_image_url": element_image_url, "reference_image_urls": [element_image_url]}]
        if product_image_url:
            elements.append({"frontal_image_url": product_image_url, "reference_image_urls": [product_image_url]})
        args = {
            "video_url": video_url,
            "prompt": instruction,
            "elements": elements,
            "keep_audio": True,
        }
        return await self._generate_video("kling/edit", args)

    async def clone_video(self, reference_video_url: str, model_image_url: str, product_image_url: Optional[str] = None, instruction: str = None) -> dict:
        elements = [{"frontal_image_url": model_image_url, "reference_image_urls": [model_image_url]}]
        prompt = "Based on @Video1, replace the character with @Element1, maintaining the same movements and camera angles."
        if product_image_url:
            elements.append({"frontal_image_url": product_image_url, "reference_image_urls": [product_image_url]})
            prompt = "Based on @Video1, replace the character with @Element1 wearing the product from @Element2, maintaining the same movements and camera angles."
        args = {
            "video_url": reference_video_url,
            "prompt": prompt,
            "elements": elements,
            "keep_audio": True,
        }
        return await self._generate_video("kling/edit-o3", args)

    async def _generate_video(self, model_key: str, arguments: Dict[str, Any]) -> dict:
        circuit_breaker = get_circuit_breaker()
        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}
        try:
            model_info = self.MODELS.get(model_key)
            if not model_info:
                return {"error": f"未知模型：{model_key}"}
            endpoint = model_info["endpoint"]
            import sys
            print(f"FAL_SUBMIT endpoint={endpoint} args={arguments}", file=sys.stderr, flush=True)
            handler = await fal_client.submit_async(endpoint, arguments=arguments)
            await circuit_breaker.record_success(model_key)
            endpoint_tag = "edit-o3" if "o3/pro/video-to-video" in endpoint else "edit" if "edit" in endpoint else "reference" if "reference" in endpoint else "i2v"
            return {"task_id": handler.request_id, "endpoint_tag": endpoint_tag, "status": "pending", "message": "视频生成任务已提交，预计需要 1 分钟", "model": endpoint}
        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}

    async def get_task_status(self, task_id: str, endpoint_hint: Optional[str] = None) -> dict:
        try:
            if endpoint_hint and "reference" in endpoint_hint:
                endpoint = "fal-ai/kling-video/o1/video-to-video/reference"
            elif endpoint_hint and "edit-o3" in endpoint_hint:
                endpoint = "fal-ai/kling-video/o3/pro/video-to-video/edit"
            elif endpoint_hint and "edit" in endpoint_hint:
                endpoint = "fal-ai/kling-video/o1/video-to-video/edit"
            else:
                endpoint = "fal-ai/kling-video/o3/standard/image-to-video"
            status_obj = await fal_client.status_async(endpoint, task_id, with_logs=False)
            status_type = type(status_obj).__name__
            status_str = str(status_obj)
            if "Completed" in status_type or "Completed" in status_str:
                result = await fal_client.result_async(endpoint, task_id)
                video_url = None
                if isinstance(result, dict):
                    video_obj = result.get("video") or {}
                    video_url = video_obj.get("url") if isinstance(video_obj, dict) else None
                return {"status": "completed", "video_url": video_url}
            if "Failed" in status_type or "Failed" in status_str:
                return {"status": "failed", "error": "FAL 任务失败"}
            return {"status": "processing"}
        except Exception as e:
            return {"status": "processing", "error": str(e)}


class FalAvatarService:
    # 4 个数字人模型(2026-04-28 增量):前 2 是腾讯/Pixverse,后 2 是 Creatify/ByteDance
    # 不同模型 fal 入参字段名不同,见 generate() 里的 model_key→args 分支
    MODELS = {
        "hunyuan-avatar":   {"endpoint": "fal-ai/hunyuan-avatar",            "label": "腾讯混元数字人"},
        "pixverse-lipsync": {"endpoint": "fal-ai/pixverse/lipsync",          "label": "Pixverse 口型同步"},
        "creatify-aurora":  {"endpoint": "fal-ai/creatify/aurora",           "label": "Creatify Aurora(影棚级)"},
        "omnihuman-v1.5":   {"endpoint": "fal-ai/bytedance/omnihuman/v1.5",  "label": "ByteDance Omnihuman v1.5(强表情)"},
    }

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def generate(self, character_image_url: str, audio_url: str, model_key: str = "hunyuan-avatar") -> dict:
        circuit_breaker = get_circuit_breaker()
        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}
        try:
            model_info = self.MODELS.get(model_key)
            if not model_info:
                # 防御:无效 model_key(以前会撞 None.["endpoint"] AttributeError → 500 + 不返还积分)
                return {"error": f"未知模型:{model_key}"}
            endpoint = model_info["endpoint"]

            # 按 model_key 分发 fal 入参字段名(2026-04-28 新增 Aurora / Omnihuman 用 image_url)
            if model_key in ("hunyuan-avatar", "pixverse-lipsync"):
                arguments = {"character_image_url": character_image_url, "audio_url": audio_url}
            elif model_key in ("creatify-aurora", "omnihuman-v1.5"):
                # Omnihuman v1.5 限制音频 ≤ 30s — fal 端报错时由外层 except 捕获,
                # avatar.py /generate 的 add_credits 兜底自动返还积分,前端透明
                arguments = {"image_url": character_image_url, "audio_url": audio_url}
            else:
                return {"error": f"未配置入参 schema:{model_key}"}

            result = await fal_client.run_async(endpoint, arguments=arguments)
            await circuit_breaker.record_success(model_key)
            video_url = result.get("video", {}).get("url")
            if not video_url:
                return {"error": "No video generated"}
            return {
                "task_id": "avatar_" + str(hash(character_image_url)),
                "status": "completed",
                "video_url": video_url,
                "model": endpoint,
            }
        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}


class FalVoiceService:
    MODELS = {
        "qwen3-tts": {"endpoint": "fal-ai/qwen3-tts", "label": "通义千问 TTS"},
        "minimax-voice-clone": {"endpoint": "fal-ai/minimax/voice-clone", "label": "MiniMax 声音克隆"},
    }

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def clone_voice(self, reference_audio_url: str, text: str) -> dict:
        circuit_breaker = get_circuit_breaker()
        model_key = "minimax-voice-clone"
        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}
        try:
            model_info = self.MODELS.get(model_key)
            endpoint = model_info["endpoint"]
            result = await fal_client.run_async(endpoint, arguments={"reference_audio_url": reference_audio_url, "text": text})
            await circuit_breaker.record_success(model_key)
            audio_url = result.get("audio", {}).get("url")
            if not audio_url:
                return {"error": "No audio generated"}
            return {"voice_id": "cloned_" + str(hash(reference_audio_url)), "audio_url": audio_url, "model": endpoint}
        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}

    async def text_to_speech(self, text: str, voice_id: str = "default", speed: float = 1.0) -> dict:
        circuit_breaker = get_circuit_breaker()
        model_key = "qwen3-tts"
        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}
        try:
            model_info = self.MODELS.get(model_key)
            endpoint = model_info["endpoint"]
            result = await fal_client.run_async(endpoint, arguments={"text": text, "voice_id": voice_id, "speed": speed})
            await circuit_breaker.record_success(model_key)
            audio_url = result.get("audio", {}).get("url")
            if not audio_url:
                return {"error": "No audio generated"}
            return {"audio_url": audio_url, "duration": len(text) * 0.5 / speed, "voice_id": voice_id, "model": endpoint}
        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}


_image_service: Optional[FalImageService] = None
_video_service: Optional[FalVideoService] = None
_avatar_service: Optional[FalAvatarService] = None
_voice_service: Optional[FalVoiceService] = None


def init_fal_services(fal_key: str):
    os.environ["FAL_KEY"] = fal_key
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
