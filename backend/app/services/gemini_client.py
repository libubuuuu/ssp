"""灵梦 API 客户端（Gemini，OpenAI 兼容接口）

使用方式:
    from app.services.gemini_client import ask_gemini

    # 纯文字
    result = await ask_gemini(prompt="帮我写一段产品文案")

    # 文字 + 图片（base64）
    result = await ask_gemini(
        prompt="描述这张图片",
        image_base64="...",          # base64 字符串，不含 data:xxx 前缀
        image_mime="image/jpeg",     # 默认 image/jpeg
    )

    # 文字 + 视频 URL
    result = await ask_gemini(
        prompt="总结这个视频的内容",
        video_url="https://example.com/video.mp4",
    )
"""
from __future__ import annotations

import asyncio
import base64
from typing import Optional

from app.config import get_settings
from app.services.logger import log_info, log_error


def _build_content(
    prompt: str,
    image_base64: Optional[str] = None,
    image_mime: str = "image/jpeg",
    video_url: Optional[str] = None,
) -> list[dict]:
    """构造 messages[0].content 的多模态 parts 列表。"""
    parts: list[dict] = []

    # 图片（base64）
    if image_base64:
        # 如果调用方传了带 data URI 前缀（data:image/jpeg;base64,...），只取逗号后面的部分
        if "," in image_base64:
            image_base64 = image_base64.split(",", 1)[1]
        parts.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{image_mime};base64,{image_base64}",
            },
        })

    # 视频 URL（灵梦/Gemini 支持直接传视频 URL）
    if video_url:
        parts.append({
            "type": "image_url",
            "image_url": {
                "url": video_url,
            },
        })

    # 文字 prompt（放最后，模型读图/视频后再看指令）
    parts.append({"type": "text", "text": prompt})

    return parts


async def ask_gemini(
    prompt: str,
    *,
    image_base64: Optional[str] = None,
    image_mime: str = "image/jpeg",
    video_url: Optional[str] = None,
    system_prompt: Optional[str] = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """调用灵梦 Gemini API，返回模型生成的文字内容。

    Args:
        prompt: 用户 prompt（必填）
        image_base64: 图片 base64 字符串（可选，支持带/不带 data URI 前缀）
        image_mime: 图片 MIME 类型，默认 image/jpeg
        video_url: 视频 URL（可选）
        system_prompt: 系统 prompt（可选）
        max_tokens: 最大输出 token 数，默认 4096
        temperature: 温度，默认 0.7

    Returns:
        模型返回的文字内容字符串

    Raises:
        RuntimeError: API 调用失败
    """
    settings = get_settings()
    if not settings.LINGMENG_API_KEY:
        raise RuntimeError("LINGMENG_API_KEY 未配置")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        base_url=settings.LINGMENG_BASE_URL,
        api_key=settings.LINGMENG_API_KEY,
    )

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    content = _build_content(
        prompt=prompt,
        image_base64=image_base64,
        image_mime=image_mime,
        video_url=video_url,
    )
    # 纯文字时直接传字符串，多模态时传 list
    messages.append({
        "role": "user",
        "content": content if (image_base64 or video_url) else prompt,
    })

    log_info(
        f"gemini_client: model={settings.LINGMENG_MODEL} "
        f"has_image={bool(image_base64)} has_video={bool(video_url)} "
        f"prompt_len={len(prompt)}"
    )

    try:
        response = await client.chat.completions.create(
            model=settings.LINGMENG_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = response.choices[0].message.content or ""
        log_info(f"gemini_client: OK output_len={len(text)}")
        return text
    except Exception as e:
        log_error(f"gemini_client: 调用失败: {e}")
        raise RuntimeError(f"Gemini API 调用失败: {str(e)[:300]}")
