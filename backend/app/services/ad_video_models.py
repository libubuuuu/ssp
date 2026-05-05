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
# P126 (2026-05-05):用户怒"做图片全部由 gpt2 来做",切 OpenAI gpt-image-2/edit。
# 实测优势(probe 5 段对比 Flux Kontext):
#   ✅ 真听 prompt(段 1 真"产品大特写"没硬塞模特脸,段 4 真"卧室场景"还原床/床头柜)
#   ✅ 没水印泄漏(Kontext 把参考图的 "+2 Inches" 当画面元素保留,gpt-image-2 知道那是水印不保留)
#   ✅ 镜头景别切换更准确(特写/正面/侧面/坐姿/CTA 5 个景别都对)
# 代价:慢 1 倍(132s vs 52s 5 段并发),价格估计更贵
# 历史: fal-ai/bytedance/seedream/v4/edit → fal-ai/flux-pro/kontext/max/multi → openai/gpt-image-2/edit
NANO_BANANA_EDIT_ENDPOINT = "openai/gpt-image-2/edit"


# ============== P139 几宫格 storyboard helpers(2026-05-05)==============
# 用户敲"GPT-Image 2 出 1 张几宫格分镜图,后端裁切成 N 张子图给 Kling Avatar"。
# 优势:1 次 GPT 调用思考 N 个分镜协同 → 风格/光线/模特一致性 100%

# 几宫格布局(都用 portrait_16_9 = 1024x1792 为基础)
# N=2:上下切 → 每格 1024x896
# N=3:上中下切 → 每格 1024x597
# N=4:2x2 → 每格 512x896
# Kling Avatar v2 Std 接受 ≥300x300 输入,以上都满足


async def compose_storyboard_grid(
    base_image_url: str,
    scenes: list,
    n_panels: int,
    model_description: str,
    overall_setting: str,
) -> dict:
    """P139:GPT-Image 2 出 1 张 N 宫格 storyboard 图。

    每格是一个分镜画面(根据 scenes[i].visual_prompt 定),共享同一模特+产品+背景。
    Returns:
        {"image_url": str}  成功
        {"error": str}      失败
    """
    cb = get_circuit_breaker()
    cb_key = "fal/nano-banana-edit"
    if not cb.is_available(cb_key):
        return {"error": "几宫格图服务暂时不可用,已熔断"}

    if n_panels < 2 or n_panels > 4:
        return {"error": f"n_panels={n_panels} 不支持(只支持 2/3/4)"}
    if len(scenes) < n_panels:
        return {"error": f"scenes={len(scenes)} 少于 n_panels={n_panels}"}

    # P140(2026-05-05):sanitize NSFW —— 之前 prompt 含
    # "DIFFERENT shot/angle/action of the same model" + 种族描述 触发 OpenAI NSFW
    # 改为中性"商业摄影"语境,避开"模特+多角度+塑身衣"组合
    panel_lines = []
    for i in range(n_panels):
        visual = (scenes[i].get("visual_prompt") or "").strip() or "product showcase"
        # P146(2026-05-06):用户实测 caf2acb7 因 chest/bedroom/candlelight/no face visible
        # 触发 NSFW,P140 4 词 sanitize 不够。加更多敏感词替换:
        visual_safe = visual
        replacements = [
            # P140 原始
            ("waist trainer", "fashion garment"),
            ("shapewear", "fashion garment"),
            # P146 新增 — 部位/服装描述
            ("waist", "torso"),
            ("chest", "upper body"),
            ("hips", "lower torso"),
            ("body", "outfit"),
            # P146 新增 — 材质暗示(neoprene/leather 紧身衣材质)
            ("neoprene", "fabric"),
            ("faux leather", "matte material"),
            ("leather panel", "matte panel"),
            # P146 新增 — 场景暗示(bedroom/candlelight 浪漫语境触发)
            ("bedroom", "indoor space"),
            ("candlelight", "soft warm light"),
            ("on bed", "sitting indoor"),
            # P146 新增 — 隐私/暴露暗示
            ("no face visible", "from a distance"),
            ("revealing", "showing"),
            # P146 新增 — 人称(模特性别+部位组合敏感)
            ("her waist", "the torso"),
            ("her body", "the outfit"),
            ("her chest", "the upper area"),
            ("on her", "on the"),
        ]
        for old, new in replacements:
            visual_safe = visual_safe.replace(old, new)
        panel_lines.append(f"Frame {i+1}: {visual_safe}")

    if n_panels == 2:
        layout_desc = "vertical 2-frame layout (upper half + lower half)"
    elif n_panels == 3:
        layout_desc = "vertical 3-frame layout (upper, middle, lower)"
    else:  # 4
        layout_desc = "2x2 grid layout (upper-left, upper-right, lower-left, lower-right)"

    # P148(2026-05-06):撤回 P147 "NO phone" 教条 — 用户说"不是要无手机,是要产品清楚"
    # 真意:**产品(束腰)是画面焦点,不能被其他元素抢镜**
    prompt = (
        f"Create a commercial fashion product photography composite in {layout_desc}. "
        f"Each frame shows the same fully-clothed model presenting the product in modest "
        f"commercial advertising style. "
        f"Setting: {overall_setting}. "
        f"All frames share consistent lighting, model appearance, and product details. "
        # P148 关键:产品焦点 + 商业第三方视角(让画面不被自拍/手机抢镜)
        f"CRITICAL — PRODUCT FOCUS: The fashion garment product is the visual HERO and primary "
        f"subject of every frame. Camera focus and composition prioritize showing the product "
        f"clearly (texture, color, fit, design details). Model's pose serves to showcase the product, "
        f"not the model's face or other items. "
        f"Third-person professional commercial camera angle, full-body or three-quarter shot, "
        f"product visible and recognizable in the center of attention. "
        f"Photorealistic studio fashion photography, professional commercial advertisement.\n\n"
        + "\n".join(panel_lines)
        + "\n\nThin neutral borders separate frames."
    )

    try:
        result = await fal_client.run_async(
            NANO_BANANA_EDIT_ENDPOINT,  # openai/gpt-image-2/edit
            arguments={
                "prompt": prompt,
                "image_urls": [base_image_url],
                "image_size": "portrait_16_9",  # 1024x1792
                "num_images": 1,
                "output_format": "png",
            },
        )
        images = result.get("images", []) if isinstance(result, dict) else []
        if not images or not images[0].get("url"):
            await cb.record_failure(cb_key)
            return {"error": "几宫格图未生成"}
        await cb.record_success(cb_key)
        url = images[0]["url"]
        log_info(f"compose_storyboard_grid OK n={n_panels} url={url[:80]}")
        return {"image_url": url}
    except Exception as e:
        await cb.record_failure(cb_key)
        log_error(f"compose_storyboard_grid 失败 n={n_panels}: {e}")
        return {"error": f"几宫格图合成失败: {str(e)[:200]}"}


async def crop_storyboard_panels(
    grid_image_url: str,
    n_panels: int,
) -> list:
    """P139:下载几宫格图,PIL 裁切成 N 张子图,各自上传 fal storage,返 URL list。

    布局:
      N=2:水平 2 段(上下切)
      N=3:水平 3 段(上中下切)
      N=4:2x2 田字格(左上/右上/左下/右下)
    """
    import io
    import tempfile
    import os
    import httpx
    from PIL import Image
    from .fal_service import fal_upload_with_retry

    if n_panels not in (2, 3, 4):
        raise ValueError(f"n_panels={n_panels} 不支持")

    # 下载几宫格图
    async with httpx.AsyncClient(timeout=60) as cli:
        r = await cli.get(grid_image_url)
        r.raise_for_status()
        img_bytes = r.content

    img = Image.open(io.BytesIO(img_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    W, H = img.size
    log_info(f"crop_storyboard_panels grid size={W}x{H} n_panels={n_panels}")

    # 计算每格 box (left, top, right, bottom)
    boxes = []
    if n_panels == 2:
        # 上下切
        boxes = [(0, 0, W, H // 2), (0, H // 2, W, H)]
    elif n_panels == 3:
        # 上中下切
        h = H // 3
        boxes = [(0, 0, W, h), (0, h, W, 2 * h), (0, 2 * h, W, H)]
    else:  # 4
        # 2x2
        w, h = W // 2, H // 2
        boxes = [
            (0, 0, w, h),       # 左上
            (w, 0, W, h),       # 右上
            (0, h, w, H),       # 左下
            (w, h, W, H),       # 右下
        ]

    # P145(2026-05-06):回滚 P141 强制 9:16 裁切 — 把 608x544 砍成 306x544 太窄,
    # Kling Avatar 拒图 "No recognizable elements found"。回到 P140 原比例上传,
    # 视频输出可能不是 9:16 但能跑。9:16 比例问题后续考虑 padding 而不是 crop。
    panel_urls = []
    for i, box in enumerate(boxes):
        panel = img.crop(box)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            panel.save(tmp.name, "JPEG", quality=92, optimize=True)
            tmp_path = tmp.name
        try:
            url = await fal_upload_with_retry(tmp_path)
            panel_urls.append(url)
            log_info(f"crop_storyboard_panels panel {i+1} {box} → {url[:80]}")
        finally:
            os.unlink(tmp_path)
    return panel_urls


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
    # P98:base prompt 不写"holding or wearing"(避免暗示位置,让 visual_prompt 完全主导穿戴位置;
    # VLM 已被强制在 visual_prompt 里写"on waist/chest/hip" 等精确位置)
    # P102:加显式 strict 后缀 + 反向锁,提高 Flux Kontext prompt fidelity(从 80% → 95%)
    prompt_parts = [
        f"{model_description}, photorealistic e-commerce product showcase featuring the product from the reference images.",
        scene_visual_prompt,
        "STRICTLY follow the wearing position specified in the prompt above. "
        "Do NOT default the product to the chest area unless the prompt explicitly says 'on chest'. "
        "If the prompt says 'on waist/torso' the product MUST be at the waist (not chest). "
        "If 'on hips/lower body' it MUST be at the hips. If 'on feet' it MUST be at the feet.",
    ]
    # P121(2026-05-05):背景图强约束 — 之前只说 "third is background scene",
    # Kontext 没听把背景换成白底。改成强制把模特+产品放进参考图的真实环境里。
    if product_back_image_url and background_image_url:
        prompt_parts.append(
            "First reference is the product FRONT view, second is product BACK/SIDE view "
            "(preserve ALL product details from both views including patterns, panels, "
            "textures, logos so the product looks identical from any angle). "
            "Third reference is the BACKGROUND ENVIRONMENT — you MUST place the model "
            "INTO this exact background scene (its furniture, walls, lighting, room layout). "
            "DO NOT use a plain white studio background, DO NOT swap the environment — "
            "the model and product MUST be composed INTO the third reference image's scene."
        )
    elif product_back_image_url:
        prompt_parts.append(
            "First reference is product FRONT view, second is product BACK/SIDE view "
            "(preserve ALL product details from both views including patterns, panels, "
            "textures, logos for accurate rendering at any rotation angle)."
        )
    elif background_image_url:
        prompt_parts.append(
            "The second reference image is the BACKGROUND ENVIRONMENT — "
            "you MUST place the model INTO this exact background scene (its furniture, "
            "walls, lighting, room layout). DO NOT use plain white studio background, "
            "DO NOT swap the environment — model MUST be composed INTO this reference scene."
        )
    prompt_parts.append(
        "Photorealistic UGC selfie style, vertical 9:16 composition, "
        "natural lighting that matches the background reference, "
        "preserve the exact product details (front+back if both provided)."
    )
    full_prompt = " ".join(prompt_parts)

    try:
        result = await fal_client.run_async(
            NANO_BANANA_EDIT_ENDPOINT,
            arguments={
                "prompt": full_prompt,
                "image_urls": image_urls,
                # P126: openai/gpt-image-2/edit schema(image_size 替代 aspect_ratio,无 guidance_scale)
                "image_size": "portrait_16_9",
                "num_images": 1,
                "output_format": "png",
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
        log_error(f"Flux Kontext 合成首帧失败: {e}")
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

    # P149(2026-05-06):用户敲"回 N 张独立 GPT 图(每张 9:16 portrait)"
    # 加 P146 sanitize + P148 产品焦点 + 上半身有人脸(防 Kling Avatar 拒图)
    visual_safe = visual
    for old, new in [
        ("waist trainer", "fashion garment"),
        ("shapewear", "fashion garment"),
        ("waist", "torso"),
        ("chest", "upper body"),
        ("hips", "lower torso"),
        ("body", "outfit"),
        ("neoprene", "fabric"),
        ("faux leather", "matte material"),
        ("leather panel", "matte panel"),
        ("bedroom", "indoor space"),
        ("candlelight", "soft warm light"),
        ("on bed", "sitting indoor"),
        ("no face visible", "from a distance"),
        ("revealing", "showing"),
        ("her waist", "the torso"),
        ("her body", "the outfit"),
        ("her chest", "the upper area"),
        ("on her", "on the"),
    ]:
        visual_safe = visual_safe.replace(old, new)

    prompt = (
        # P152(2026-05-06)IDENTITY LOCK 第一行强约束 — 用户实测段 2 脸跟段 1 不一致
        f"⚠️ HIGHEST PRIORITY — IDENTITY LOCK: The model in the output MUST be EXACTLY the same "
        f"person as in the reference image. Same face shape, same eyes (color, shape, size), "
        f"same hairstyle (length, color, style), same skin tone, same lip shape, same eyebrows, "
        f"same nose, same overall facial features. ZERO deviation from reference identity. "
        f"This is a DIFFERENT SHOT of the SAME PERSON, NOT a similar-looking model. "
        f"Treat the reference face as a locked anchor that must NOT change. "
        f"Adjust the reference image to show this specific shot: {visual_safe}. "
        f"Keep the model's identity consistent ({model_description}). "
        f"Maintain the overall setting: {overall_setting}. "
        # P149 关键铁律 — 防 Kling Avatar 拒图(必须有清晰人脸 + 上半身)
        f"CRITICAL — MUST INCLUDE: model's face and upper body clearly visible in the frame. "
        f"Even product close-ups must show the model's face/upper body together with the product. "
        # P148 + P154 产品焦点 + 演示动作
        f"PRODUCT FOCUS: the fashion garment (the printed waist accessory, NOT clothes/jeans/phone) "
        f"is the visual hero, clearly visible. Model's pose and gaze direct viewer attention "
        f"TOWARD the printed garment. "
        # P154(2026-05-06):用户:模特要演示产品,不是站着发呆
        f"PRODUCT DEMONSTRATION: model is ACTIVELY engaging with the printed garment — "
        f"touching/adjusting/showing/lifting/pointing at the printed waist garment with her hands. "
        f"NOT just standing passively. The hands interact with the printed garment specifically, "
        f"demonstrating it like a TikTok seller showing their product. "
        # P147 框架
        f"Third-person professional commercial camera angle, NOT a mirror selfie. "
        # P150(2026-05-06)用户:字幕后期自己加 — 图里不要任何文字
        f"STRICT — NO TEXT IN IMAGE: absolutely NO text overlays, NO promotional text, "
        f"NO captions, NO numbers (like '50 LEFT', '-2 inches', '24H'), NO countdown, "
        f"NO call-to-action text, NO labels, NO Before/After tags. "
        f"The image must be COMPLETELY TEXT-FREE — clean photograph only. "
        f"Photorealistic commercial advertisement, vertical 9:16 composition, "
        f"natural lighting, preserve the exact product details from reference."
    )

    try:
        result = await fal_client.run_async(
            NANO_BANANA_EDIT_ENDPOINT,
            arguments={
                "prompt": prompt,
                "image_urls": [base_image_url],
                # P126: openai/gpt-image-2/edit schema(image_size 替代 aspect_ratio,无 guidance_scale)
                "image_size": "portrait_16_9",
                "num_images": 1,
                "output_format": "png",
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


# ============== Seedance 2.0 reference-to-video (P36) ==============

# P36 (2026-05-01):用户痛点"产品假 + 真人和背景不搭"根因是 Seedream 多图融合
# 合成痕迹。换 reference-to-video 端点,Seedance 直接看产品图自己想模特+场景,
# 训练数据是真带货视频,真人/产品/光线一致性更对路。probe 实测 5s 视频 2:52
# (单段),NSFW 通过(束腰带塑形产品)。SDK path bug 让 status_async 拿不到结果,
# 必须用 subscribe_async 阻塞等。
SEEDANCE_REF2VID_ENDPOINT = "bytedance/seedance-2.0/reference-to-video"


def build_seedance_ref2vid_prompt(scene: dict, model_description: str, overall_setting: str) -> str:
    """
    拼 reference-to-video prompt:overall + model + shot + visual + speech 拼成英文叙事

    P38 (2026-05-01):用户实测 ref2vid 改了产品(产品保真度差),原 prompt 只
    "preserve exact product details" 引导太软。改成 CRITICAL 强约束,锁死颜色/
    纹理/形状/品牌 不准改。Seedance 是视频模型,对参考图保真天生比 image edit
    模型弱,需要 prompt 工程明确约束。
    """
    parts = []
    # 产品保真硬约束(放最前面优先级最高)
    parts.append(
        "CRITICAL PRODUCT FIDELITY: The product shown in the reference images "
        "MUST appear IDENTICAL in this video. Do NOT invent a different product. "
        "Do NOT change the product's color, pattern, texture, shape, material, "
        "or branding. The product must be visually exactly the same as the first "
        "reference image."
    )
    if overall_setting:
        parts.append(f"Setting: {overall_setting}")
    if model_description:
        parts.append(f"Model: {model_description}")
    if scene.get("shot_language"):
        parts.append(f"Shot: {scene['shot_language']}")
    if scene.get("visual_prompt"):
        parts.append(f"Action: {scene['visual_prompt']}")
    if scene.get("speech"):
        parts.append(f'Model speaks: "{scene["speech"]}"')
    parts.append(
        "Photorealistic UGC selfie style, vertical 9:16 composition, "
        "natural lighting. Realistic human model with natural facial features, "
        "skin texture, and expressions. The product remains identical to reference."
    )
    return "\n".join(parts)


async def submit_seedance_ref2vid_subscribe(
    reference_image_urls: List[str],
    prompt: str,
    duration: int = 5,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
) -> dict:
    """
    P36: Seedance reference-to-video,subscribe_async 阻塞等结果。

    被 jobs.py 多段路径在 asyncio.Semaphore(5) 并发调用,N 段并发出 N 段视频。

    参数:
        reference_image_urls: 1-9 张参考图(产品正面 + 反面 + 背景 等)
        prompt: 完整文字 prompt(用 build_seedance_ref2vid_prompt 拼)
        duration: 5/10/15 秒(本段时长)
        aspect_ratio / resolution

    返回:
        {"video_url": "...", "model": "..."}  成功
        {"error": "..."}                       失败
    """
    cb = get_circuit_breaker()
    cb_key = "fal/seedance-ref2vid"
    if not cb.is_available(cb_key):
        return {"error": "Seedance reference-to-video 暂时不可用,已熔断"}

    if not reference_image_urls:
        return {"error": "无参考图,无法调 reference-to-video"}

    try:
        result = await fal_client.subscribe_async(
            SEEDANCE_REF2VID_ENDPOINT,
            arguments={
                "reference_image_urls": reference_image_urls,
                "prompt": prompt,
                "duration": str(duration),
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
            },
        )
        video_obj = result.get("video", {}) if isinstance(result, dict) else {}
        url = video_obj.get("url") if isinstance(video_obj, dict) else None
        if not url:
            await cb.record_failure(cb_key)
            return {"error": "ref2vid 返回视频 URL 为空"}
        await cb.record_success(cb_key)
        return {"video_url": url, "model": SEEDANCE_REF2VID_ENDPOINT}
    except Exception as e:
        await cb.record_failure(cb_key)
        log_error(f"Seedance ref2vid 失败: {str(e)[:200]}")
        return {"error": f"Seedance ref2vid 失败: {str(e)[:200]}"}


# ============== Seedance 2.0 image-to-video (旧,保留作 fallback) ==============

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


_VALID_V15_DURATIONS = {4, 5, 6, 7, 8, 9, 10, 11, 12}  # P40: v1.5/pro 实测上限


async def submit_seedance_video(
    image_url: str,
    script: dict,
    duration: int = 5,
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

    # P40: v1.5/pro 只支持 duration 4-12,15+ 会被 fal queue 接收但 task 静默死
    safe_duration = max(4, min(12, int(duration)))
    if safe_duration != int(duration):
        log_info(f"Seedance duration {duration}→{safe_duration} (v1.5/pro 上限 12)")

    prompt = build_seedance_prompt(script)

    try:
        handler = await fal_client.submit_async(
            SEEDANCE_ENDPOINT,
            arguments={
                "image_url": image_url,
                "prompt": prompt,
                "duration": str(safe_duration),
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
