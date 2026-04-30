"""
AI 服务封装
"""
import asyncio
import fal_client
import os
from typing import Optional, Dict, Any, List
from .circuit_breaker import get_circuit_breaker
from .alert import get_alert_service
from .logger import log_warning


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
    # 默认 endpoints — 七十六续后改为 env 可覆盖,但默认值锁死老模型,空 env = 行为不变
    DEFAULT_ENDPOINTS = {
        "kling/image-to-video": "fal-ai/kling-video/o3/standard/image-to-video",
        "kling/edit": "fal-ai/kling-video/o1/video-to-video/edit",
        "kling/edit-o3": "fal-ai/kling-video/o3/pro/video-to-video/edit",
        "kling/reference": "fal-ai/kling-video/o1/video-to-video/reference",
    }

    LABELS = {
        "kling/image-to-video": "图生视频",
        "kling/edit": "元素替换(快速)",
        "kling/edit-o3": "翻拍复刻(高质量+中文口播)",
        "kling/reference": "最强复刻",
    }

    # 兼容老代码:有些地方可能仍引用 .MODELS,提供属性 fallback
    @property
    def MODELS(self) -> Dict[str, Dict[str, str]]:
        return {k: {"endpoint": self.DEFAULT_ENDPOINTS[k], "label": self.LABELS[k]} for k in self.DEFAULT_ENDPOINTS}

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    def _resolve_endpoint(self, model_key: str) -> tuple:
        """七十六续:解析 model_key → (endpoint, source)。
        优先级:OVERRIDE > 单 mode env > DEFAULT_ENDPOINTS。
        source ∈ {"override", "env_edit", "env_edit_o3", "default"} — 给日志用。
        """
        from ..config import get_settings
        settings = get_settings()
        # 1. OVERRIDE 最高(灰度/全量切换开关)
        override = (settings.STUDIO_VIDEO_MODEL_OVERRIDE or "").strip()
        if override and model_key in ("kling/edit", "kling/edit-o3"):
            return override, "override"
        # 2. 单 mode env 覆盖(只覆盖对应 mode,另一个不动)
        if model_key == "kling/edit":
            env_val = (settings.STUDIO_VIDEO_MODEL_EDIT or "").strip()
            if env_val:
                return env_val, "env_edit"
        if model_key == "kling/edit-o3":
            env_val = (settings.STUDIO_VIDEO_MODEL_EDIT_O3 or "").strip()
            if env_val:
                return env_val, "env_edit_o3"
        # 3. 兜底:代码默认值
        return self.DEFAULT_ENDPOINTS.get(model_key), "default"

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

    async def drive_with_reference(self, driving_video_url: str, reference_image_url: str, prompt: str = "") -> dict:
        """口播带货 V3 Step B:用 reference image 驱动 driving video 的动作。

        kling/reference 上限 10.05s/次。长视频需上层拆段。
        """
        elements = [{"frontal_image_url": reference_image_url, "reference_image_urls": [reference_image_url]}]
        if not prompt:
            prompt = "A person performing the same actions and movements as in the reference video."
        args = {
            "video_url": driving_video_url,
            "prompt": prompt,
            "elements": elements,
            "keep_audio": True,
        }
        return await self._generate_video("kling/reference", args)

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
        """七十六续:env override 路径 + 失败 3 次自动回退默认 endpoint。
        - 默认路径熔断 key 仍是 model_key("kling/edit"),不动现有 admin /models/{name}/* 接口
        - override 路径熔断 key 是 f"override:{endpoint}",独立统计,endpoint 变了重新计
        - 任何回退动作都打日志
        """
        if model_key not in self.DEFAULT_ENDPOINTS:
            return {"error": f"未知模型：{model_key}"}

        endpoint, source = self._resolve_endpoint(model_key)
        circuit_breaker = get_circuit_breaker()
        import sys

        # source != default 时:先试 override/env 路径,失败/熔断回退默认
        if source != "default":
            cb_key = f"override:{endpoint}" if source == "override" else endpoint
            if circuit_breaker.is_available(cb_key):
                try:
                    print(f"FAL_SUBMIT[{source}] endpoint={endpoint} args={arguments}", file=sys.stderr, flush=True)
                    handler = await fal_client.submit_async(endpoint, arguments=arguments)
                    await circuit_breaker.record_success(cb_key)
                    return self._fmt_submit_result(handler.request_id, endpoint, source)
                except Exception as e:
                    triggered = await circuit_breaker.record_failure(cb_key)
                    print(f"FAL_OVERRIDE_FAIL[{source}] endpoint={endpoint} err={e!r} triggered_circuit={triggered}", file=sys.stderr, flush=True)
                    # 落到下面 default 路径继续
            else:
                print(f"FAL_OVERRIDE_CIRCUIT_OPEN[{source}] endpoint={endpoint} → 自动回退默认 model_key={model_key}", file=sys.stderr, flush=True)
            # 回退默认:重新解析 endpoint
            endpoint = self.DEFAULT_ENDPOINTS[model_key]
            source = "default_after_fallback"

        # 默认路径(或 fallback 后)
        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}
        try:
            print(f"FAL_SUBMIT[{source}] endpoint={endpoint} args={arguments}", file=sys.stderr, flush=True)
            handler = await fal_client.submit_async(endpoint, arguments=arguments)
            await circuit_breaker.record_success(model_key)
            return self._fmt_submit_result(handler.request_id, endpoint, source)
        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}

    @staticmethod
    def _fmt_submit_result(request_id: str, endpoint: str, source: str) -> dict:
        endpoint_tag = (
            "edit-o3" if "o3/pro/video-to-video" in endpoint else
            "edit" if "edit" in endpoint else
            "reference" if "reference" in endpoint else
            "i2v"
        )
        return {
            "task_id": request_id,
            "endpoint_tag": endpoint_tag,
            "status": "pending",
            "message": "视频生成任务已提交，预计需要 1 分钟",
            "model": endpoint,
            "model_source": source,
        }

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
        """fal-ai/minimax/voice-clone 一步:reference_audio + text → 新音频。

        七十七续 P2:返回结构清理 — voice_id 用 fal/minimax 真返字段(custom_voice_id
        或 voice_id),旧版本用 hash 假造已废弃。如果 fal 不返,留 None,前端不依赖。
        """
        circuit_breaker = get_circuit_breaker()
        model_key = "minimax-voice-clone"
        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}
        model_info = self.MODELS.get(model_key)
        endpoint = model_info["endpoint"]

        # 八十四:fal-ai/minimax/voice-clone 偶发 "Failed to download preview audio"
        # 等 transient 故障(fal 内部 / MiniMax 服务跨境 / 超时)。最多重试 3 次,
        # 退避 1s/2s。schema 4xx 错(missing field / string_too_long 等)不重试,
        # 立刻抛让上层 100% 退款分支区分。
        last_err = None
        for attempt in range(3):
            try:
                # 八十三:字段名 audio_url(不是 reference_audio_url),fal 当前 schema 要求
                result = await fal_client.run_async(endpoint, arguments={"audio_url": reference_audio_url, "text": text})
                await circuit_breaker.record_success(model_key)
                audio_url = result.get("audio", {}).get("url") if isinstance(result.get("audio"), dict) else result.get("audio_url")
                if not audio_url:
                    return {"error": "No audio generated"}
                return {
                    "voice_id": result.get("custom_voice_id") or result.get("voice_id"),
                    "audio_url": audio_url,
                    "model": endpoint,
                }
            except Exception as e:
                last_err = e
                err_str = str(e)
                is_transient = (
                    "Failed to download" in err_str
                    or "preview audio" in err_str
                    or "timeout" in err_str.lower()
                    or "Internal Server Error" in err_str
                    or " 502" in err_str or " 503" in err_str or " 504" in err_str
                )
                if not is_transient or attempt == 2:
                    # 4xx schema 错 / 重试耗尽 → 抛
                    await circuit_breaker.record_failure(model_key)
                    return {"error": err_str}
                wait = 2 ** attempt  # 1s, 2s
                log_warning(
                    "voice_clone_retry",
                    attempt=attempt + 1, max=3, err=err_str[:200], wait=wait,
                )
                await asyncio.sleep(wait)
        # 防御:理论不会到这(循环内 attempt==2 会 return)
        await circuit_breaker.record_failure(model_key)
        return {"error": str(last_err) if last_err else "voice-clone unknown error"}

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


class FalASRService:
    """七十七续 P2:fal-ai/wizper ASR(口播带货 Step 1)。

    定价:$0.0005 / 音频分钟,~250x realtime。返回原文 + word-level timestamps。
    """
    MODELS = {
        "wizper": {"endpoint": "fal-ai/wizper", "label": "Wizper ASR"},
    }

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def transcribe(self, audio_url: str, language: Optional[str] = None) -> dict:
        circuit_breaker = get_circuit_breaker()
        model_key = "wizper"
        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}
        try:
            endpoint = self.MODELS[model_key]["endpoint"]
            args: Dict[str, Any] = {"audio_url": audio_url, "task": "transcribe"}
            if language:
                args["language"] = language
            result = await fal_client.run_async(endpoint, arguments=args)
            await circuit_breaker.record_success(model_key)
            return {
                "text": result.get("text", ""),
                "chunks": result.get("chunks", []),
                "model": endpoint,
            }
        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}


class FalInpaintingService:
    """七十七续 P3:fal-ai/wan-vace-14b/inpainting(口播带货 Step 4 视频换装)。

    单端点参数选分辨率(详见 docs/ORAL-BROADCAST-PLAN.md §3 Step 4):
    - 480p $0.04/秒(经济档)
    - 580p $0.06/秒(标准档)
    - 720p $0.08/秒(顶级档)

    按 16fps 计算视频秒数。mask_image_url + salient tracking 自动跨帧传播(§14)。
    """
    ENDPOINT = "fal-ai/wan-vace-14b/inpainting"

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def inpaint(
        self,
        video_url: str,
        mask_image_url: str,
        prompt: str,
        reference_image_urls: Optional[List[str]] = None,
        resolution: str = "480p",
        num_frames: int = 81,
    ) -> dict:
        circuit_breaker = get_circuit_breaker()
        model_key = "wan-vace-inpainting"
        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}
        try:
            args: Dict[str, Any] = {
                "video_url": video_url,
                "mask_image_url": mask_image_url,
                "prompt": prompt,
                "resolution": resolution,
                "num_frames": num_frames,
            }
            if reference_image_urls:
                args["reference_image_urls"] = reference_image_urls
            result = await fal_client.run_async(self.ENDPOINT, arguments=args)
            await circuit_breaker.record_success(model_key)
            video_obj = result.get("video") if isinstance(result, dict) else None
            video_url_out = (
                video_obj.get("url") if isinstance(video_obj, dict)
                else result.get("video_url") if isinstance(result, dict)
                else None
            )
            if not video_url_out:
                return {"error": "wan-vace 未返 video URL"}
            return {"video_url": video_url_out, "model": self.ENDPOINT}
        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}


class FalVTONService:
    """口播带货 V3 Step A:虚拟试穿(VTON)。

    输入模特图 + 产品图(衣服)→ 输出"模特真实穿着该衣服"的静态合成图。
    与通用 inpainting / video-to-video edit 的区别:VTON 模型是 garment-aware,
    懂版型、褶皱、贴合;wan-vace 和 kling edit 都做不出"真实穿衣"的物理感。

    端点:fal-ai/cat-vton(轻量、保留模特身份强,实测优于 idm-vton)。
    cloth_type:upper(上衣)/ lower(下装)/ overall(连衣裙)。
    """
    ENDPOINT = "fal-ai/cat-vton"

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def try_on(
        self,
        human_image_url: str,
        garment_image_url: str,
        cloth_type: str = "upper",
    ) -> dict:
        circuit_breaker = get_circuit_breaker()
        model_key = "cat-vton"
        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}
        try:
            args = {
                "human_image_url": human_image_url,
                "garment_image_url": garment_image_url,
                "cloth_type": cloth_type,
            }
            result = await fal_client.run_async(self.ENDPOINT, arguments=args)
            await circuit_breaker.record_success(model_key)
            image_obj = result.get("image") if isinstance(result, dict) else None
            image_url_out = (
                image_obj.get("url") if isinstance(image_obj, dict)
                else result.get("image_url") if isinstance(result, dict)
                else None
            )
            if not image_url_out:
                return {"error": "cat-vton 未返 image URL"}
            return {"image_url": image_url_out, "model": self.ENDPOINT}
        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}


class FalLipsyncService:
    """七十七续 P3:口型对齐(三档不同 endpoint)。

    详见 docs/ORAL-BROADCAST-PLAN.md §3 Step 5:
    - economy → veed/lipsync           $0.40 / 视频分钟
    - standard → fal-ai/latentsync     ≤40s 固定 $0.20,>40s $0.005/秒
    - premium → fal-ai/sync-lipsync/v2 $3.00 / 分钟(Pro $5/min)

    三个端点输入字段统一:video_url + audio_url。
    """
    TIER_ENDPOINTS = {
        "economy":  "veed/lipsync",
        "standard": "fal-ai/latentsync",
        "premium":  "fal-ai/sync-lipsync/v2",
    }

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    def endpoint_for(self, tier: str) -> str:
        ep = self.TIER_ENDPOINTS.get(tier)
        if not ep:
            raise ValueError(f"未知 tier: {tier}")
        return ep

    async def sync(self, video_url: str, audio_url: str, tier: str) -> dict:
        circuit_breaker = get_circuit_breaker()
        model_key = f"lipsync-{tier}"
        if not circuit_breaker.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}
        try:
            endpoint = self.endpoint_for(tier)
            args = {"video_url": video_url, "audio_url": audio_url}
            result = await fal_client.run_async(endpoint, arguments=args)
            await circuit_breaker.record_success(model_key)
            video_obj = result.get("video") if isinstance(result, dict) else None
            video_url_out = (
                video_obj.get("url") if isinstance(video_obj, dict)
                else result.get("video_url") if isinstance(result, dict)
                else None
            )
            if not video_url_out:
                return {"error": "lipsync 未返 video URL"}
            return {"video_url": video_url_out, "model": endpoint}
        except Exception as e:
            await circuit_breaker.record_failure(model_key)
            return {"error": str(e)}


_image_service: Optional[FalImageService] = None
_video_service: Optional[FalVideoService] = None
_avatar_service: Optional[FalAvatarService] = None
_voice_service: Optional[FalVoiceService] = None
_asr_service: Optional[FalASRService] = None
_inpainting_service: Optional[FalInpaintingService] = None
_vton_service: Optional["FalVTONService"] = None
_lipsync_service: Optional[FalLipsyncService] = None


def init_fal_services(fal_key: str):
    os.environ["FAL_KEY"] = fal_key
    global _image_service, _video_service, _avatar_service, _voice_service, _asr_service, _inpainting_service, _vton_service, _lipsync_service
    _image_service = FalImageService(fal_key)
    _video_service = FalVideoService(fal_key)
    _avatar_service = FalAvatarService(fal_key)
    _voice_service = FalVoiceService(fal_key)
    _asr_service = FalASRService(fal_key)
    _inpainting_service = FalInpaintingService(fal_key)
    _vton_service = FalVTONService(fal_key)
    _lipsync_service = FalLipsyncService(fal_key)

def get_image_service() -> FalImageService:
    return _image_service

def get_video_service() -> FalVideoService:
    return _video_service

def get_avatar_service() -> FalAvatarService:
    return _avatar_service

def get_voice_service() -> FalVoiceService:
    return _voice_service

def get_asr_service() -> FalASRService:
    return _asr_service

def get_inpainting_service() -> FalInpaintingService:
    return _inpainting_service

def get_vton_service() -> "FalVTONService":
    return _vton_service

def get_lipsync_service() -> FalLipsyncService:
    return _lipsync_service
