"""
AI 带货视频专用 - Seedance 2.0 + Nano Banana Edit 封装

设计原则:
- 不动 fal_service.py 主类(它管熔断+告警,改动影响面大)
- 这里独立函数,失败时返回 {"error": ...},由 ad_video.py 处理
- 复用现有 circuit_breaker 实例(避免重复熔断逻辑)

模型:
- fal-ai/nano-banana-2/edit  - 多图融合(产品+模特+背景 → 首帧)
- fal-ai/bytedance/seedance/v2/pro/image-to-video - 视频生成

⚠ 如果 fal 上线了 v2 endpoint 不同的命名,改 SEEDANCE_ENDPOINT 即可。
当前以 fal 文档的稳定 endpoint 为准。
"""
from __future__ import annotations

import asyncio
from typing import Optional, List
import fal_client

from .circuit_breaker import get_circuit_breaker
from .logger import log_info, log_error


# P33 (2026-05-01):v2/pro/standard 实测 17min+ timeout fail,v2/fast NSFW 严拒真人,
# v1.5/pro probe 70s 出 5s 视频(15x 提速)+ NSFW 通过。换 v1.5/pro。
# 历史:fal-ai/bytedance/seedance/v2/pro/image-to-video
SEEDANCE_ENDPOINT = "fal-ai/bytedance/seedance/v1.5/pro/image-to-video"
# 八十四续 P6:nano-banana-2/edit (Google) NSFW 拦截严 + fal 端偶发 downstream
# unavailable。切字节 Seedream 4 (国产 + 稳定 + 接受 image_urls 数组多图融合)。
NANO_BANANA_EDIT_ENDPOINT = "fal-ai/bytedance/seedream/v4/edit"


# ============== Nano Banana 多图合成首帧 ==============

async def compose_first_frame(
    product_image_url: str,
    background_image_url: Optional[str],
    model_description: str,
    scene_visual_prompt: str,
    product_back_image_url: Optional[str] = None,  # P34
) -> dict:
    """
    合成视频首帧:产品 + 背景 + 模特

    参数:
        product_image_url: 产品正面图(已上传到 fal storage)
        product_back_image_url: P34 产品反面/侧面图(可选,锁住反面材质/logo/标签)
        background_image_url: 用户上传的背景图(可选)
        model_description: 模特特征描述(英文)
        scene_visual_prompt: 镜头一的 visual_prompt

    返回:
        {"image_url": "...", "model": "..."}  成功
        {"error": "..."}                       失败
    """
    circuit_breaker = get_circuit_breaker()
    cb_key = "fal/nano-banana-edit"

    if not circuit_breaker.is_available(cb_key):
        return {"error": "首帧合成服务暂时不可用,已熔断"}

    # 拼参考图列表(P34: 顺序固定 — 产品正面 → 产品反面 → 背景)
    image_urls: List[str] = [product_image_url]
    if product_back_image_url:
        image_urls.append(product_back_image_url)
    if background_image_url:
        image_urls.append(background_image_url)

    # 拼 prompt(根据图序生成精准引导)
    prompt_parts = [
        f"{model_description} holding or wearing the product shown in the reference images.",
        scene_visual_prompt,
    ]
    if product_back_image_url and background_image_url:
        prompt_parts.append(
            "First reference is product front, second is product back/side "
            "(preserve all product details from both views), third is background scene."
        )
    elif product_back_image_url:
        prompt_parts.append(
            "First reference is product front, second is product back/side "
            "(preserve product details from both angles for accurate rendering)."
        )
    elif background_image_url:
        prompt_parts.append("Use the second reference image as the background scene.")
    prompt_parts.append(
        "Photorealistic UGC selfie style, vertical 9:16 composition, "
        "natural lighting, preserve the exact product details from reference."
    )
    full_prompt = " ".join(prompt_parts)

    try:
        result = await fal_client.run_async(
            NANO_BANANA_EDIT_ENDPOINT,
            arguments={
                "prompt": full_prompt,
                "image_urls": image_urls,
                # P35: 显式 9:16 高分辨率(原默认偏小,用户反馈"比例太小")
                "image_size": {"width": 1024, "height": 1820},
            },
        )
        images = result.get("images", [])
        if not images:
            await circuit_breaker.record_failure(cb_key)
            return {"error": "首帧未生成"}

        await circuit_breaker.record_success(cb_key)
        return {
            "image_url": images[0].get("url"),
            "model": NANO_BANANA_EDIT_ENDPOINT,
        }
    except Exception as e:
        await circuit_breaker.record_failure(cb_key)
        log_error(f"Nano Banana 合成首帧失败: {e}")
        return {"error": f"首帧合成失败: {str(e)[:200]}"}


async def compose_first_frame_for_scene(
    base_image_url: str,
    scene: dict,
    model_description: str,
    overall_setting: str,
) -> dict:
    """
    P32 一镜一图: 给单个分镜单独合成它的首帧

    在共享首帧(已含模特+产品+场景)基础上,按本段 visual_prompt 调整为本段镜头/姿态。
    模特身份和场景靠 base_image 锁定,Seedream 只调整本段独有的镜头语言。

    参数:
        base_image_url: 共享首帧 URL(/preview 出的那张,作为模特+产品 anchor)
        scene: 本段 scene dict (含 visual_prompt / shot_language / content)
        model_description: 模特特征(N 段共享,锁角色)
        overall_setting: 整体设定(N 段共享,锁场景)

    返回:
        {"image_url": "...", "model": "..."} 成功
        {"error": "..."} 失败(jobs.py 用 fallback 回退到 base_image_url)
    """
    cb = get_circuit_breaker()
    cb_key = "fal/nano-banana-edit"
    if not cb.is_available(cb_key):
        return {"error": "首帧合成服务暂时不可用,已熔断"}

    visual = (scene.get("visual_prompt") or "").strip()
    if not visual:
        return {"error": "scene 缺 visual_prompt"}

    prompt = (
        f"Adjust the reference image to show this specific shot: {visual}. "
        f"Keep the model's identity consistent ({model_description}). "
        f"Maintain the overall setting: {overall_setting}. "
        f"Photorealistic UGC selfie style, vertical 9:16 composition, "
        f"natural lighting, preserve the exact product details from reference."
    )

    try:
        result = await fal_client.run_async(
            NANO_BANANA_EDIT_ENDPOINT,
            arguments={
                "prompt": prompt,
                "image_urls": [base_image_url],
                # P35: 显式 9:16 高分辨率
                "image_size": {"width": 1024, "height": 1820},
            },
        )
        images = result.get("images", []) if isinstance(result, dict) else []
        if not images:
            await cb.record_failure(cb_key)
            return {"error": "本段首帧未生成"}
        await cb.record_success(cb_key)
        return {
            "image_url": images[0].get("url"),
            "model": NANO_BANANA_EDIT_ENDPOINT,
        }
    except Exception as e:
        await cb.record_failure(cb_key)
        log_error(f"compose_first_frame_for_scene 失败 scene={scene.get('id')}: {e}")
        return {"error": f"本段首帧合成失败: {str(e)[:200]}"}


# ============== Seedance 2.0 视频生成 ==============

def build_seedance_prompt(script: dict) -> str:
    """
    把脚本对象拼成 Seedance 能理解的 prompt
    Seedance 接受多镜头叙事,用 [Scene N] 分隔
    """
    parts = []
    overall = script.get("overall_setting", "")
    model = script.get("model_description", "")
    if overall:
        parts.append(overall)
    if model:
        parts.append(f"Model: {model}")
    parts.append("")  # 空行

    for scene in script.get("scenes", []):
        parts.append(
            f"[Scene {scene.get('id')}] {scene.get('time_range', '')} - {scene.get('purpose', '')}"
        )
        parts.append(f"Shot: {scene.get('shot_language', '')}")
        parts.append(f"Visual: {scene.get('visual_prompt', '')}")
        parts.append(f'Speech: "{scene.get("speech", "")}"')
        parts.append("")

    return "\n".join(parts).strip()


async def submit_seedance_video(
    image_url: str,
    script: dict,
    duration: int = 15,
    aspect_ratio: str = "9:16",
    resolution: str = "1080p",
    enable_audio: bool = True,
) -> dict:
    """
    提交 Seedance 2.0 视频生成任务(异步,返回 task_id)

    返回:
        {"task_id": "...", "endpoint_tag": "seedance", "status": "pending"}  成功
        {"error": "..."}                                                     失败
    """
    circuit_breaker = get_circuit_breaker()
    cb_key = "fal/seedance-v2"

    if not circuit_breaker.is_available(cb_key):
        return {"error": "Seedance 服务暂时不可用,已熔断"}

    prompt = build_seedance_prompt(script)

    try:
        handler = await fal_client.submit_async(
            SEEDANCE_ENDPOINT,
            arguments={
                "image_url": image_url,
                "prompt": prompt,
                "duration": str(duration),
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "enable_audio": enable_audio,
            },
        )
        await circuit_breaker.record_success(cb_key)
        return {
            "task_id": handler.request_id,
            "endpoint_tag": "seedance",
            "status": "pending",
            "model": SEEDANCE_ENDPOINT,
        }
    except Exception as e:
        await circuit_breaker.record_failure(cb_key)
        log_error(f"Seedance 提交失败: {e}")
        return {"error": f"视频任务提交失败: {str(e)[:200]}"}


async def poll_seedance_status(task_id: str) -> dict:
    """
    轮询 Seedance 任务状态(由 jobs.py 队列 worker 调用)

    返回:
        {"status": "completed", "video_url": "..."}  完成
        {"status": "processing"}                      进行中
        {"status": "failed", "error": "..."}          失败
    """
    try:
        status_obj = await fal_client.status_async(SEEDANCE_ENDPOINT, task_id, with_logs=False)
        status_type = type(status_obj).__name__
        status_str = str(status_obj)

        if "Completed" in status_type or "Completed" in status_str:
            result = await fal_client.result_async(SEEDANCE_ENDPOINT, task_id)
            video_url = None
            if isinstance(result, dict):
                video_obj = result.get("video") or {}
                video_url = video_obj.get("url") if isinstance(video_obj, dict) else None
            if not video_url:
                return {"status": "failed", "error": "视频 URL 为空"}
            return {"status": "completed", "video_url": video_url}

        if "Failed" in status_type or "Failed" in status_str:
            return {"status": "failed", "error": "Seedance 任务失败"}

        return {"status": "processing"}
    except Exception as e:
        # 短暂错误不算 failed,让外层重试
        return {"status": "processing", "error": str(e)[:200]}
