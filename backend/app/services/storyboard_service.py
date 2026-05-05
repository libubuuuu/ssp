"""
分镜图工作流服务

输入:1 张参考图(产品图 / 模特+产品合成图) + 文字描述(产品+用途) + 分镜数 N
流程:
  1. VLM 看图 + 描述 → 输出 N 段分镜 JSON(每段独立 visual_prompt)
  2. 并发跑 N 个 Kontext multi-image-edit(fal-ai/flux-pro/kontext/max/multi)
     喂 [reference_image] + 每段独立 prompt → N 张差异化分镜图
  3. 任意单段失败用 None 占位(返 error 字段),不阻塞其他段
返回:[{idx, title, purpose, prompt, image_url|None, error|None}]

设计取舍:
- 端点选 Kontext multi(已在 ad_video 用,熟,不引入新模型)
- 不入 task_queue(同步等 30-60s,前端 loading 即可,简化 MVP)
- 没有 audit 步骤(分镜工作台是创意工具,不强审产品类目)
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

import fal_client

from .circuit_breaker import get_circuit_breaker
from .logger import log_info, log_error, log_warning
from .vlm_service import DEFAULT_MODEL, FALLBACK_MODEL, VISION_ENDPOINT


KONTEXT_ENDPOINT = "fal-ai/flux-pro/kontext/max/multi"
VLM_KEY = "fal/openrouter-vision"
KONTEXT_KEY = "fal/kontext-max-multi"


_SYSTEM_PROMPT = (
    "你是资深短视频/广告分镜导演,擅长把一个产品/场景拆成 N 个有节奏、画面差异化大的分镜。"
    "每段镜头要做到:景别不同(全景/中景/近景/特写)、动作不同(展示/演示/对比/CTA)、"
    "构图不同(正面/侧面/俯视/仰视)。严格输出纯 JSON,不要 markdown 包裹,不要解释文字。"
)


def _build_storyboard_prompt(description: str, n_frames: int, aspect_ratio: str) -> str:
    """构造让 VLM 看图 + 文字描述输出 N 段分镜 JSON 的提示词"""
    return f"""请看这张参考图,结合下面描述,产出 {n_frames} 段分镜。

【参考图说明】参考图是要被分镜的主体(产品 / 模特+产品 / 场景),后续每段分镜都会以这张图为参考,所以每段画面要保留参考图里的核心主体特征(产品款式 / 模特身份 / 场景元素),只换角度/动作/景别。

【创作描述】{description or "(无)"}

【画幅】{aspect_ratio}(分镜图最终输出比例)

【输出】严格按下面 JSON schema 输出 {n_frames} 个分镜对象,放在 `frames` 数组里:

{{
  "overall_theme": "(一句话整体主题,如 '夏日海边瑜伽裤运动场景多镜头')",
  "frames": [
    {{
      "idx": 1,
      "title": "(8-15 字中文标题,如 '产品大特写钩子')",
      "purpose": "(这段镜头的功能,如 '抓注意 / 展示卖点 / 对比 / CTA')",
      "shot_type": "(景别+角度,如 '近景特写正面' / '全景仰视' / '中景 45 度侧面')",
      "visual_prompt": "(英文,30-80 字。极具体描述这一帧:主体在做什么/在哪/光线如何/构图怎样。**严禁通用空话** like 'high quality / amazing / good lighting'。要写 'model holding product close to camera at chest level, soft window light from left, 9:16 vertical' 这种细节)"
    }},
    ...
  ]
}}

【铁律】
1. **画面差异化**:{n_frames} 段景别/角度/动作必须显著不同,不能 N 段都是"模特正面手持产品"。
2. **保留参考主体**:每段 visual_prompt 都要明确写"preserve the product/model from reference image",防止 Kontext 自由发挥换主体。
3. **节奏**:第 1 段是钩子(抓眼球的极特写或大反差),中段递进展示,末段促单/收尾(CTA 或留白)。
4. **9:16 / 16:9 构图**:visual_prompt 末尾必带 "{aspect_ratio} vertical/horizontal composition"。
5. **严禁** markdown / 注释 / 解释文字,只输出纯 JSON。"""


async def generate_storyboard(
    reference_image_url: str,
    description: str,
    n_frames: int = 5,
    aspect_ratio: str = "9:16",
    vlm_model: Optional[str] = None,
) -> dict:
    """主入口:VLM 写 N 段分镜 → 并发 Kontext 出 N 张图

    Returns:
        {"overall_theme": str, "frames": [{idx, title, purpose, shot_type, prompt, image_url|None, error|None}]}
        or {"error": str}  (VLM 阶段就失败时)
    """
    if n_frames < 2 or n_frames > 12:
        return {"error": f"分镜数 {n_frames} 超出范围(2-12)"}

    cb = get_circuit_breaker()

    # ===== 阶段 A:VLM 看图 + 描述 → N 段分镜 JSON =====
    if not cb.is_available(VLM_KEY):
        return {"error": "VLM 视觉服务暂时不可用,请稍后再试"}

    chosen_model = vlm_model or DEFAULT_MODEL
    vlm_prompt = _build_storyboard_prompt(description, n_frames, aspect_ratio)

    async def _run_vlm(model: str) -> str:
        res = await fal_client.run_async(
            VISION_ENDPOINT,
            arguments={
                "image_urls": [reference_image_url],
                "prompt": vlm_prompt,
                "system_prompt": _SYSTEM_PROMPT,
                "model": model,
            },
        )
        return (res.get("output") or "").strip()

    try:
        text = await _run_vlm(chosen_model)
    except Exception as e:
        await cb.record_failure(VLM_KEY)
        log_error(f"storyboard VLM 失败 model={chosen_model}: {e}")
        if chosen_model != FALLBACK_MODEL:
            try:
                text = await _run_vlm(FALLBACK_MODEL)
            except Exception as e2:
                return {"error": f"VLM 主备模型均失败: {str(e2)[:200]}"}
        else:
            return {"error": f"VLM 调用失败: {str(e)[:200]}"}

    if not text:
        await cb.record_failure(VLM_KEY)
        return {"error": "VLM 返回为空"}

    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        await cb.record_failure(VLM_KEY)
        log_error(f"storyboard VLM JSON 解析失败: {e} 原文前 500: {text[:500]}")
        return {"error": "AI 输出格式异常,请重试"}

    frames = data.get("frames") or []
    if not isinstance(frames, list) or len(frames) < 2:
        await cb.record_failure(VLM_KEY)
        return {"error": f"AI 输出分镜不足 ({len(frames) if isinstance(frames, list) else 0} 段)"}

    overall_theme = data.get("overall_theme") or ""
    await cb.record_success(VLM_KEY)
    log_info(
        f"storyboard VLM OK model={chosen_model} frames={len(frames)} "
        f"theme_len={len(overall_theme)}"
    )

    # ===== 阶段 B:并发跑 N 个 Kontext multi-edit =====
    if not cb.is_available(KONTEXT_KEY):
        return {"error": "图像编辑服务暂时不可用,请稍后再试"}

    async def _run_kontext_one(frame: dict, idx: int) -> dict:
        vp = (frame.get("visual_prompt") or "").strip()
        if not vp:
            return {**frame, "image_url": None, "error": "visual_prompt 空"}
        # 拼最终 prompt:VLM visual_prompt + 强保留参考主体 + 画幅
        full_prompt = (
            f"{vp} "
            f"Preserve the main subject (product / model / scene) from the reference image — "
            f"its identity, design, color, texture, and key details must remain identical, "
            f"only the camera angle, framing, and action should change. "
            f"Photorealistic, {aspect_ratio} composition, natural lighting."
        )
        try:
            res = await fal_client.run_async(
                KONTEXT_ENDPOINT,
                arguments={
                    "prompt": full_prompt,
                    "image_urls": [reference_image_url],
                    "aspect_ratio": aspect_ratio,
                    "guidance_scale": 3.5,
                    "num_images": 1,
                    "output_format": "png",
                },
            )
            images = res.get("images") or []
            if not images:
                log_warning(f"storyboard 段 {idx} Kontext 未生成图")
                return {**frame, "image_url": None, "error": "Kontext 未生成图"}
            url = images[0].get("url")
            log_info(f"storyboard 段 {idx} Kontext OK url={(url or '')[:80]}")
            return {**frame, "image_url": url, "error": None, "prompt": full_prompt}
        except Exception as e:
            log_warning(f"storyboard 段 {idx} Kontext 失败: {str(e)[:200]}")
            return {**frame, "image_url": None, "error": str(e)[:200]}

    tasks = [_run_kontext_one(f, i + 1) for i, f in enumerate(frames)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out_frames = []
    success_cnt = 0
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            out_frames.append(
                {
                    **frames[i],
                    "image_url": None,
                    "error": f"task exception: {str(r)[:200]}",
                }
            )
        else:
            out_frames.append(r)
            if r.get("image_url"):
                success_cnt += 1

    if success_cnt == 0:
        await cb.record_failure(KONTEXT_KEY)
        return {"error": "所有分镜图生成失败,请稍后重试"}

    if success_cnt < len(frames):
        log_warning(f"storyboard 部分成功 {success_cnt}/{len(frames)}")
    else:
        await cb.record_success(KONTEXT_KEY)

    return {
        "overall_theme": overall_theme,
        "frames": out_frames,
        "success_count": success_cnt,
        "total_count": len(frames),
    }
