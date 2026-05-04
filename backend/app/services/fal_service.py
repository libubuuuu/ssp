"""
AI 服务封装
"""
import asyncio
import fal_client
import os
import time
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
            # 八十四续 P7:fal 内容审核 / 422 / 输入校验类 exception 是终态失败
            # (无限标 processing 会让前端永远显示"处理中" + 积分不退)
            msg = str(e)
            terminal_keywords = (
                "content_policy", "content checker", "content_policy_violation",
                "did not generate", "no_media_generated",
                "ValidationError", "422", "Unprocessable",
                "Bad Request", "400",
            )
            if any(kw in msg for kw in terminal_keywords):
                return {"status": "failed", "error": msg[:300]}
            return {"status": "processing", "error": msg}


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


class AliyunWanService:
    """P47-B 主路候选(白嫖优先):阿里云通义万相 Wan2.7 r2v。

    跟 fal seedance-2-r2v 是同档级竞品,但走阿里官方 API。
    新用户开通百炼自动送 180 天免费配额(成功秒数计费,失败/异常不扣)。

    DashScope 异步任务模式(不是 fal 那套 submit/status/result):
      POST .../video-synthesis → output.task_id
      GET .../tasks/{task_id} → output.task_status (PENDING/RUNNING/SUCCEEDED/FAILED)
      SUCCEEDED → output.video_url(24h 有效,要立即下载或 fal 上传)

    端点:wan2.7-r2v(参考生视频)
    输入:media 数组 [{type: reference_image, url}, {type: reference_video, url}]
    输出:5/10/15s,720P/1080P,9:16/16:9/1:1/4:3/3:4

    probe 真值(2026-05-03,14c390bb 内衣场景):
      ✅ 520s 出 5s 视频,内衣 NSFW 通过(fal seedance enterprise 同场景被拒)
      ⚠ 比 fal pixverse-swap(70-120s)慢 4-7 倍,但免费 + 即梦同档质量

    env DASHSCOPE_API_KEY 必须设置,否则该 service 整体禁用(返 disabled)。
    """
    BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
    WAN27_R2V_ENDPOINT = f"{BASE_URL}/services/aigc/video-generation/video-synthesis"

    def __init__(self):
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def wan27_r2v_submit(
        self,
        reference_image_url: str,  # 兼容老调用,主路用 reference_image_urls
        reference_video_url: Optional[str],
        prompt: str,
        duration: int = 5,
        resolution: str = "720P",
        ratio: str = "9:16",
        reference_image_urls: Optional[List[str]] = None,  # P52:多 reference 图(模特+产品分开)
    ) -> dict:
        """提交 wan2.7-r2v 任务,返 {"task_id": ...} 或 {"error": ...}

        P52 修复:阿里 wan2.7-r2v media 数组支持多张 reference_image,
        单 vton 合成图传不出"模特+产品独立参考"语义,导致模型分不清。
        新参数 reference_image_urls 可传 [模特原图, 产品原图] 让模型用
        prompt"图1的人穿图2的产品"明确引用。
        """
        if not self.api_key:
            return {"error": "DASHSCOPE_API_KEY 未配置"}
        cb = get_circuit_breaker()
        if not cb.is_available("aliyun-wan2.7-r2v"):
            return {"error": "aliyun-wan2.7-r2v 已熔断"}
        # P52:优先用 reference_image_urls(多图);回落到老 reference_image_url(单图)兼容
        urls = reference_image_urls if reference_image_urls else ([reference_image_url] if reference_image_url else [])
        media = [{"type": "reference_image", "url": u} for u in urls if u]
        if reference_video_url:
            media.append({"type": "reference_video", "url": reference_video_url})
        body = {
            "model": "wan2.7-r2v",
            "input": {"media": media, "prompt": prompt},
            "parameters": {
                "resolution": resolution,
                "duration": int(duration),
                "ratio": ratio,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(self.WAN27_R2V_ENDPOINT, headers=headers, json=body)
            if r.status_code != 200:
                await cb.record_failure("aliyun-wan2.7-r2v")
                return {"error": f"submit {r.status_code}: {r.text[:300]}"}
            task_id = r.json().get("output", {}).get("task_id")
            if not task_id:
                await cb.record_failure("aliyun-wan2.7-r2v")
                return {"error": f"no task_id: {r.text[:300]}"}
            return {"task_id": task_id}
        except Exception as e:
            await cb.record_failure("aliyun-wan2.7-r2v")
            return {"error": str(e)[:300]}

    async def poll_task(self, task_id: str) -> dict:
        """单次 poll。返 {"status": "PENDING|RUNNING|SUCCEEDED|FAILED", "video_url": ...}"""
        if not self.api_key:
            return {"error": "DASHSCOPE_API_KEY 未配置"}
        url = f"{self.BASE_URL}/tasks/{task_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.get(url, headers=headers)
            if r.status_code != 200:
                return {"error": f"poll {r.status_code}: {r.text[:200]}"}
            data = r.json().get("output", {})
            status = data.get("task_status")
            out = {"status": status}
            if status == "SUCCEEDED":
                video_url = data.get("video_url") or (
                    data.get("results", [{}])[0].get("url") if data.get("results") else None
                )
                out["video_url"] = video_url
                cb = get_circuit_breaker()
                await cb.record_success("aliyun-wan2.7-r2v")
            elif status == "FAILED":
                out["error"] = (data.get("message") or "FAILED")[:300]
                cb = get_circuit_breaker()
                await cb.record_failure("aliyun-wan2.7-r2v")
            return out
        except Exception as e:
            return {"error": str(e)[:300]}


class AliyunQwenVLVideoService:
    """P71:阿里 DashScope qwen-vl-max-latest 视频理解 → 自动分镜 prompt。

    给 P70 vace-mask 引擎前置,自动分析 driving 视频生成精细时序描述
    (每秒一段动作 + 服装层次 + 关键时刻识别),拼到 VACE 中文 prompt
    让 VACE 看到时序信息更精确按时间轴 inpaint。

    成本:¥0.11/段 5s 视频(12K tokens),probe 实测准确识别"外层服装拉起
    + 内层文胸露出"分层时刻 + 广告文字 OCR。

    schema(2026-05-04 probe verified):
      input  : video_url(公网可访问 MP4)+ text instruction
      output : choices[0].message.content(分镜描述文本)
    """
    BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
    ENDPOINT = f"{BASE_URL}/services/aigc/multimodal-generation/generation"

    def __init__(self):
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def analyze_video(self, video_url: str, instruction: str) -> dict:
        if not self.api_key:
            return {"error": "DASHSCOPE_API_KEY 未配置"}
        body = {
            "model": "qwen-vl-max-latest",
            "input": {
                "messages": [{"role": "user", "content": [
                    {"video": video_url}, {"text": instruction},
                ]}]
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            import httpx
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(self.ENDPOINT, headers=headers, json=body)
            if r.status_code != 200:
                return {"error": f"qwen-vl {r.status_code}: {r.text[:300]}"}
            data = r.json()
            choices = data.get("output", {}).get("choices") or []
            content = choices[0].get("message", {}).get("content") if choices else ""
            if isinstance(content, list):
                text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
            else:
                text = str(content or "")
            return {"text": text.strip()}
        except Exception as e:
            return {"error": str(e)[:300]}


class AliyunASRService:
    """P47-C 免费替代 fal whisper:阿里 paraformer-v2 录音文件识别。

    probe 真值(2026-05-03):23.5s 出转写结果,新人 180 天免费配额。
    端点:async submit → poll → SUCCEEDED 取 transcription_url(JSON 文件)
    URL:dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription

    输入:file_urls 列表(支持 mp3 / wav / aac 等),公网可访问 URL
    输出:results[].transcription_url(JSON 文件,内含 text + word timestamps)
    """
    BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
    SUBMIT_URL = f"{BASE_URL}/services/audio/asr/transcription"

    def __init__(self):
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def transcribe(self, audio_url: str, language: str = "zh") -> dict:
        """阿里 paraformer-v2 ASR。返 {"text": ..., "chunks": [...]} 或 {"error": ...}"""
        if not self.api_key:
            return {"error": "DASHSCOPE_API_KEY 未配置"}
        cb = get_circuit_breaker()
        if not cb.is_available("aliyun-paraformer-v2"):
            return {"error": "aliyun-paraformer-v2 已熔断"}
        body = {
            "model": "paraformer-v2",
            "input": {"file_urls": [audio_url]},
            "parameters": {"language_hints": [language]},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(self.SUBMIT_URL, headers=headers, json=body)
                if r.status_code != 200:
                    await cb.record_failure("aliyun-paraformer-v2")
                    return {"error": f"submit {r.status_code}: {r.text[:200]}"}
                task_id = r.json().get("output", {}).get("task_id")
                if not task_id:
                    await cb.record_failure("aliyun-paraformer-v2")
                    return {"error": "no task_id"}
                # poll(平均 20-40s 完成)
                poll_url = f"{self.BASE_URL}/tasks/{task_id}"
                poll_headers = {"Authorization": f"Bearer {self.api_key}"}
                deadline = time.time() + 180
                while time.time() < deadline:
                    await asyncio.sleep(5)
                    pr = await client.get(poll_url, headers=poll_headers)
                    if pr.status_code != 200:
                        continue
                    data = pr.json().get("output", {})
                    status = data.get("task_status")
                    if status == "SUCCEEDED":
                        # results[0].transcription_url 是个 JSON 文件,要二次拉
                        results = data.get("results", [])
                        if not results:
                            return {"error": "no results"}
                        trans_url = results[0].get("transcription_url")
                        if not trans_url:
                            return {"error": "no transcription_url"}
                        tr = await client.get(trans_url, timeout=30)
                        if tr.status_code != 200:
                            return {"error": f"fetch transcription {tr.status_code}"}
                        tdata = tr.json()
                        # paraformer-v2 输出格式:transcripts[0].text + .sentences[]
                        transcripts = tdata.get("transcripts", [])
                        if not transcripts:
                            return {"error": "empty transcripts"}
                        text = transcripts[0].get("text", "")
                        sentences = transcripts[0].get("sentences", [])
                        # 转成 fal whisper 兼容的 chunks 格式(begin_time/end_time/text)
                        chunks = [{
                            "timestamp": [s.get("begin_time", 0) / 1000.0, s.get("end_time", 0) / 1000.0],
                            "text": s.get("text", ""),
                        } for s in sentences]
                        await cb.record_success("aliyun-paraformer-v2")
                        return {"text": text, "chunks": chunks, "model": "aliyun-paraformer-v2"}
                    if status == "FAILED":
                        await cb.record_failure("aliyun-paraformer-v2")
                        return {"error": f"FAILED: {data.get('message', '?')[:200]}"}
                await cb.record_failure("aliyun-paraformer-v2")
                return {"error": "timeout 180s"}
        except Exception as e:
            await cb.record_failure("aliyun-paraformer-v2")
            return {"error": str(e)[:300]}


class FalCodeformerService:
    """P45 工作流:CodeFormer 单图人脸修复(身份保真)。

    probe 实测 2026-05-03:fal-ai/codeformer 74s 出 1024×1844 图,fidelity 可调。
    输入只接 image_url(无 video_url 变体),所以视频用法是"抽帧修复 + overlay"。

    fidelity_weight 语义:
      0.0 = 最高画质(可能丢身份特征)
      1.0 = 最大保身份(画质增益小)
      0.7-0.85 推荐(平衡画质 + 身份)

    我们用法:
      - 预处理:用户原模特图 → fidelity=0.7 → 修脸增强,喂 cat-vton + 后续引擎
      - 后处理:成片首帧/末帧 → fidelity=0.85 → overlay 回视频
    """
    ENDPOINT = "fal-ai/codeformer"

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def restore(
        self,
        image_url: str,
        fidelity: float = 0.7,
        upscale: int = 2,
        only_center_face: bool = False,
    ) -> dict:
        cb = get_circuit_breaker()
        if not cb.is_available("codeformer"):
            return {"error": "模型 codeformer 已熔断"}
        try:
            args = {
                "image_url": image_url,
                "fidelity_weight": float(fidelity),
                "upscale_factor": int(upscale),
                "only_center_face": bool(only_center_face),
            }
            result = await fal_client.run_async(self.ENDPOINT, arguments=args)
            await cb.record_success("codeformer")
            image_obj = result.get("image") if isinstance(result, dict) else None
            url = image_obj.get("url") if isinstance(image_obj, dict) else None
            if not url:
                return {"error": "codeformer 未返 image URL"}
            return {"image_url": url, "model": self.ENDPOINT}
        except Exception as e:
            await cb.record_failure("codeformer")
            return {"error": str(e)}


class FalPuLIDService:
    """P45 可选:Flux-PuLID 身份保持图生成(2026 业界 SOTA)。

    与 codeformer 区别:
      - codeformer 是"修复"(只动人脸,不改图)
      - PuLID 是"重生成"(按 prompt + reference face 生成新图,身份保持)
    用途:用户原模特图质量极低(模糊/噪声多),codeformer 修不动 → PuLID 重生成
    一张摄影写真级图。默认关,用户显式开。

    probe 实测 2026-05-03:29.5s 出 1024×768 图(摄影写真级)。
    schema(probe 实查):reference_image_url + prompt
    """
    ENDPOINT = "fal-ai/flux-pulid"

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def regenerate(self, reference_image_url: str, prompt: str) -> dict:
        cb = get_circuit_breaker()
        if not cb.is_available("flux-pulid"):
            return {"error": "模型 flux-pulid 已熔断"}
        try:
            args = {
                "reference_image_url": reference_image_url,
                "prompt": prompt,
            }
            result = await fal_client.run_async(self.ENDPOINT, arguments=args)
            await cb.record_success("flux-pulid")
            images = result.get("images") if isinstance(result, dict) else None
            if isinstance(images, list) and images:
                first = images[0]
                url = first.get("url") if isinstance(first, dict) else None
                if url:
                    return {"image_url": url, "model": self.ENDPOINT}
            return {"error": "flux-pulid 未返 image URL"}
        except Exception as e:
            await cb.record_failure("flux-pulid")
            return {"error": str(e)}


class FalAudioSeparatorService:
    """P44 工作流:音轨分离(BGM ⊥ 人声)。

    主路 demucs(6 stem:vocals/drums/bass/guitar/piano/other),probe 实测
    15s wav 19.6s 出活,vocals/BGM 都能用 ffmpeg amix 重组。
    备胎 elevenlabs/audio-isolation(只出 isolated vocals,4.5s,BGM 丢),
    用于 demucs 故障兜底。

    返回:
      {"vocals_url": "...", "bgm_stem_urls": [...], "model": "demucs"}  主路成功
      {"vocals_url": "...", "bgm_stem_urls": [], "model": "elevenlabs"}  备胎成功
      {"error": "..."}  全失败
    """
    PRIMARY = "fal-ai/demucs"
    FALLBACK = "fal-ai/elevenlabs/audio-isolation"

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def separate(self, audio_url: str) -> dict:
        cb = get_circuit_breaker()

        # 主路 demucs
        if cb.is_available("demucs"):
            try:
                result = await fal_client.run_async(self.PRIMARY, arguments={"audio_url": audio_url})
                await cb.record_success("demucs")
                if isinstance(result, dict):
                    vocals = result.get("vocals", {})
                    vocals_url = vocals.get("url") if isinstance(vocals, dict) else None
                    if vocals_url:
                        # BGM 由 drums + bass + guitar + other 合成(piano 一般 -80dB 静音可丢);
                        # 这里返 stem urls,真正合成由调用方 ffmpeg amix(本地处理省 fal 流量)
                        bgm_keys = ("drums", "bass", "guitar", "other")
                        bgm_urls = []
                        for k in bgm_keys:
                            stem = result.get(k, {}) if isinstance(result, dict) else {}
                            u = stem.get("url") if isinstance(stem, dict) else None
                            if u:
                                bgm_urls.append(u)
                        return {
                            "vocals_url": vocals_url,
                            "bgm_stem_urls": bgm_urls,
                            "model": self.PRIMARY,
                        }
            except Exception as e:
                await cb.record_failure("demucs")
                log_warning(f"FalAudioSeparator demucs 失败,降级 elevenlabs: {e}")

        # 备胎 elevenlabs(只 vocal,无 BGM stems)
        if cb.is_available("elevenlabs-isolate"):
            try:
                result = await fal_client.run_async(self.FALLBACK, arguments={"audio_url": audio_url})
                await cb.record_success("elevenlabs-isolate")
                audio = result.get("audio", {}) if isinstance(result, dict) else {}
                vocals_url = audio.get("url") if isinstance(audio, dict) else None
                if vocals_url:
                    return {
                        "vocals_url": vocals_url,
                        "bgm_stem_urls": [],
                        "model": self.FALLBACK,
                    }
            except Exception as e:
                await cb.record_failure("elevenlabs-isolate")
                log_warning(f"FalAudioSeparator elevenlabs 也失败: {e}")

        return {"error": "音轨分离主路 + 备胎全部失败"}


class FalSAM2VideoService:
    """P70:fal-ai/sam2/video — 视频 mask 自动追踪传播。

    schema(2026-05-04 probe verified):
      input  : video_url + prompts/box_prompts
               prompts: [{x,y,label,frame_index}](point prompt 单点种子)
               box_prompts: [{x1,y1,x2,y2,frame_index}](矩形框定区域)
               label=1 include / label=0 exclude
      output : video.url(黑白 binary mask 视频,白=追踪区域,黑=背景)

    用法:用户在某帧画 box 圈定"内层服装可见区域" → SAM2 自动 propagate
    到所有帧 → 输出 mask 视频喂 VACE Fun inpainting。
    """
    ENDPOINT = "fal-ai/sam2/video"

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def segment(
        self,
        video_url: str,
        box_prompts: Optional[List[Dict[str, int]]] = None,
        point_prompts: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        cb = get_circuit_breaker()
        model_key = "sam2-video"
        if not cb.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}
        try:
            args: Dict[str, Any] = {"video_url": video_url}
            if box_prompts:
                args["box_prompts"] = box_prompts
            elif point_prompts:
                args["prompts"] = point_prompts
            else:
                return {"error": "SAM2 必须提供 box_prompts 或 point_prompts"}
            result = await fal_client.run_async(self.ENDPOINT, arguments=args)
            await cb.record_success(model_key)
            v = result.get("video") if isinstance(result, dict) else None
            url = v.get("url") if isinstance(v, dict) else None
            if not url:
                return {"error": "SAM2 未返 mask video URL"}
            return {"mask_video_url": url, "model": self.ENDPOINT}
        except Exception as e:
            await cb.record_failure(model_key)
            return {"error": str(e)}


class FalVaceFunInpaintingService:
    """P70:fal-ai/wan-22-vace-fun-a14b/inpainting — Wan 2.2 VACE Fun
    支持 mask_video_url(逐帧 mask)的视频 inpainting。

    schema(2026-05-04 probe verified):
      input  : video_url + mask_video_url + ref_image_urls + prompt
      output : video(720p,5.0625s,81 帧 16fps,4-5MB)
      pricing: $0.13 / 视频(固定价,非按秒)
      verified: 中文 prompt + 用户素材 probe 真换 mask 区域 + NSFW 通过
    """
    ENDPOINT = "fal-ai/wan-22-vace-fun-a14b/inpainting"

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def inpaint(
        self,
        video_url: str,
        mask_video_url: str,
        ref_image_urls: List[str],
        prompt: str,
    ) -> dict:
        cb = get_circuit_breaker()
        model_key = "wan-22-vace-fun-inpaint"
        if not cb.is_available(model_key):
            return {"error": f"模型 {model_key} 已熔断"}
        try:
            args: Dict[str, Any] = {
                "video_url": video_url,
                "mask_video_url": mask_video_url,
                "ref_image_urls": ref_image_urls,
                "prompt": prompt,
            }
            result = await fal_client.run_async(self.ENDPOINT, arguments=args)
            await cb.record_success(model_key)
            v = result.get("video") if isinstance(result, dict) else None
            url = v.get("url") if isinstance(v, dict) else None
            if not url:
                return {"error": "Wan VACE Fun 未返 video URL"}
            return {"video_url": url, "model": self.ENDPOINT}
        except Exception as e:
            await cb.record_failure(model_key)
            return {"error": str(e)}


class FalPixverseSwapService:
    """P44 工作流 Step B 主路:Pixverse Swap。

    专门做 person/object/background 替换,fal 文档列三类全 swap。
    probe 实测内衣类参考图(seedance-2/enterprise 直接拒)→ pixverse 111.8s
    出 8s 视频成功,**NSFW 友好** 是 fal 上目前最干净的真人替换路。

    schema(2026-05-03 fal 文档实查):
      input  : video_url(MP4/MOV/WebM/M4V/GIF) + image_url
      output : video.url
      duration: 5s 基线,>5s 加倍
      pricing: $0.20 / 5s @720p,$0.40 / 5s @1080p
      audio  : 自动保留原音(我们 lipsync 接管所以无所谓)

    限制(已知):
      - image_url 单图,无 multi-ref(产品多角度无法在此端点叠加)
      - 单段 5s 基线,长视频拆段后接到 _drive_one 多段并发框架
    """
    ENDPOINT = "fal-ai/pixverse/swap"

    def __init__(self, fal_key: str):
        self.fal_key = fal_key

    async def swap(self, video_url: str, image_url: str) -> dict:
        cb = get_circuit_breaker()
        if not cb.is_available("pixverse-swap"):
            return {"error": "模型 pixverse-swap 已熔断"}
        try:
            args = {"video_url": video_url, "image_url": image_url}
            result = await fal_client.run_async(self.ENDPOINT, arguments=args)
            await cb.record_success("pixverse-swap")
            video_obj = result.get("video") if isinstance(result, dict) else None
            video_url_out = (
                video_obj.get("url") if isinstance(video_obj, dict)
                else result.get("video_url") if isinstance(result, dict)
                else None
            )
            if not video_url_out:
                return {"error": "pixverse-swap 未返 video URL"}
            return {"video_url": video_url_out, "model": self.ENDPOINT}
        except Exception as e:
            await cb.record_failure("pixverse-swap")
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
_audio_separator_service: Optional["FalAudioSeparatorService"] = None
_pixverse_swap_service: Optional["FalPixverseSwapService"] = None
_codeformer_service: Optional["FalCodeformerService"] = None
_pulid_service: Optional["FalPuLIDService"] = None
_aliyun_wan_service: Optional["AliyunWanService"] = None
_aliyun_asr_service: Optional["AliyunASRService"] = None
_aliyun_qwenvl_service: Optional["AliyunQwenVLVideoService"] = None
_sam2_video_service: Optional["FalSAM2VideoService"] = None
_vace_fun_service: Optional["FalVaceFunInpaintingService"] = None


def init_fal_services(fal_key: str):
    os.environ["FAL_KEY"] = fal_key
    global _image_service, _video_service, _avatar_service, _voice_service, _asr_service, _inpainting_service, _vton_service, _lipsync_service, _audio_separator_service, _pixverse_swap_service, _codeformer_service, _pulid_service, _aliyun_wan_service, _aliyun_asr_service, _aliyun_qwenvl_service, _sam2_video_service, _vace_fun_service
    _image_service = FalImageService(fal_key)
    _video_service = FalVideoService(fal_key)
    _avatar_service = FalAvatarService(fal_key)
    _voice_service = FalVoiceService(fal_key)
    _asr_service = FalASRService(fal_key)
    _inpainting_service = FalInpaintingService(fal_key)
    _vton_service = FalVTONService(fal_key)
    _lipsync_service = FalLipsyncService(fal_key)
    _audio_separator_service = FalAudioSeparatorService(fal_key)
    _pixverse_swap_service = FalPixverseSwapService(fal_key)
    _codeformer_service = FalCodeformerService(fal_key)
    _pulid_service = FalPuLIDService(fal_key)
    # P47-B:阿里通义万相 Wan2.7(读 DASHSCOPE_API_KEY env,缺失时 service 整体禁用)
    _aliyun_wan_service = AliyunWanService()
    # P47-C:阿里 paraformer-v2 ASR(替代 fal whisper)
    _aliyun_asr_service = AliyunASRService()
    # P71:阿里 qwen-vl-max-latest 视频理解(给 vace-mask 自动分镜 prompt)
    _aliyun_qwenvl_service = AliyunQwenVLVideoService()
    # P70:Wan 2.2 VACE Fun mask inpainting + SAM2 video segmentation
    _sam2_video_service = FalSAM2VideoService(fal_key)
    _vace_fun_service = FalVaceFunInpaintingService(fal_key)

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

def get_audio_separator_service() -> "FalAudioSeparatorService":
    return _audio_separator_service

def get_pixverse_swap_service() -> "FalPixverseSwapService":
    return _pixverse_swap_service

def get_codeformer_service() -> "FalCodeformerService":
    return _codeformer_service

def get_pulid_service() -> "FalPuLIDService":
    return _pulid_service

def get_aliyun_wan_service() -> "AliyunWanService":
    return _aliyun_wan_service

def get_aliyun_asr_service() -> "AliyunASRService":
    return _aliyun_asr_service

def get_aliyun_qwenvl_service() -> "AliyunQwenVLVideoService":
    return _aliyun_qwenvl_service

def get_sam2_video_service() -> "FalSAM2VideoService":
    return _sam2_video_service

def get_vace_fun_service() -> "FalVaceFunInpaintingService":
    return _vace_fun_service


# ============== fal storage upload retry helper (P33 2026-05-01) ==============

async def fal_upload_with_retry(local_path: str, max_retries: int = 10) -> str:
    """
    fal storage upload 带 transient retry + P89 本地 fallback。

    P90(2026-05-04 fal storage v3 30min outage 后):max_retries 3 → 10,退避 1s/2s/4s/8s/16s/32s/60s/60s/60s/60s
    总抗压时间 ~5-10 分钟,扛住短时 fal outage 用户不感知。
    最后一次仍失败 → P89 本地 fallback。

    P89(2026-05-04):fal storage v3 长时间 outage retry 救不了 → fallback 本地 nginx URL。
    cat-vton / kling / VACE 等 fal 端点接受任意 https URL,本地 ailixiao.com URL 一样能用。
    """
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            return await fal_client.upload_file_async(local_path)
        except Exception as e:
            err_str = str(e)
            err_low = err_str.lower()
            is_transient = (
                "500 internal" in err_low or
                "502" in err_str or
                "503" in err_str or
                "504" in err_str or
                "timeout" in err_low or
                "connection" in err_low or
                "reset" in err_low or
                "temporarily" in err_low
            )
            if not is_transient or attempt == max_retries - 1:
                # P89 最后一次 retry 仍失败:transient 错误时 fallback 本地 nginx URL
                if is_transient:
                    try:
                        local_url = _fallback_local_upload(local_path)
                        log_warning(
                            f"fal_upload exhausted retries, P89 fallback to local nginx URL: {local_url}"
                        )
                        return local_url
                    except Exception as fb_err:
                        log_warning(f"P89 fallback local upload also failed: {fb_err}")
                raise
            last_err = e
            log_warning(
                f"fal_upload retry {attempt + 1}/{max_retries} "
                f"path={local_path[-60:]} err={err_str[:120]}"
            )
            # P90:退避 1s/2s/4s/8s/16s/32s/60s/60s/60s,总 ~5-10min 抗 outage
            backoff = min(60.0, 1.0 * (2 ** attempt))
            await asyncio.sleep(backoff)
    if last_err:
        raise last_err
    raise RuntimeError("fal_upload_with_retry: unreachable")


def _fallback_local_upload(local_path: str) -> str:
    """
    P89:fal storage outage 时本地 nginx fallback。
    把文件拷到 /opt/ssp/uploads/fal_fallback/<yyyy-mm>/<uuid>.<ext> 返回 https URL。
    """
    import shutil
    import uuid as _uuid
    from datetime import datetime
    from pathlib import Path
    src = Path(local_path)
    if not src.is_file():
        raise FileNotFoundError(f"local_path 不存在: {local_path}")
    yyyymm = datetime.utcnow().strftime("%Y-%m")
    target_dir = Path("/opt/ssp/uploads/fal_fallback") / yyyymm
    target_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower() or ".bin"
    target = target_dir / f"{_uuid.uuid4().hex}{ext}"
    shutil.copy2(src, target)
    os.chmod(target, 0o644)
    base = os.environ.get("SSP_UPLOADS_PUBLIC_BASE", "https://ailixiao.com/uploads").rstrip("/")
    return f"{base}/fal_fallback/{yyyymm}/{target.name}"
