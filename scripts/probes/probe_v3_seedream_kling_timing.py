"""
P86 真路 — V3 路线 + 时序 prompt:
  Step A: Seedream v4 edit 多图融合 [driving首帧, 模特图, 产品图] → 合成首帧
  Step B: kling-o3 i2v(融合首帧 + 时序 prompt) → 5s 视频

Verify:
  - 融合首帧是否继承 driving 背景 + 用户模特身份 + 产品款式
  - kling 视频是否复刻 driving 风格 + 按时序拉起 + 露出产品
"""
import asyncio
import os
import time
import json
import fal_client


# 用户素材
DRIVING_FIRST_FRAME = "https://ailixiao.com/uploads/probe_p86/driving_first_frame.jpg"
MODEL_URL = "https://v3b.fal.media/files/b/0a98c674/5Ykt_GqOkGRDd1HuISlEf_tmp0i7f9q7o.jpg"
PRODUCT_URL = "https://v3b.fal.media/files/b/0a98c675/89Dg_cYoBjv0-DjbSLr6v_tmp_w3_2wz2.jpg"

SEEDREAM_ENDPOINT = "fal-ai/bytedance/seedream/v4/edit"
KLING_O3_I2V = "fal-ai/kling-video/o3/standard/image-to-video"

SEEDREAM_PROMPT = (
    "把图1场景中的女子替换成图2女子的脸和发型,身材保持图2女子比例。"
    "她现在穿着一件白色无袖背心,内层是图3的黑色蕾丝胸罩(隐约可见或明确穿在内层)。"
    "保留图1的客厅背景、白墙、自然光线、构图、9:16 竖屏。"
    "整图真实人像写实风格,高分辨率。"
)

KLING_TIMING_PROMPT = (
    "图中的女子开始时双手举高站着。"
    "1 秒后,她双手缓慢下移到腰部。"
    "2-3 秒,她双手抓住白色背心下摆。"
    "3-5 秒,她慢慢向上拉起背心,露出里面穿的黑色蕾丝胸罩。"
    "整段一镜到底,9:16 竖屏,客厅背景保留,自然光线,真实人像视频风格。"
)


async def step_a_seedream():
    print(f"\n=== Step A: Seedream v4 edit 多图融合 ===")
    print(f"refs:")
    print(f"  1) driving_first_frame: {DRIVING_FIRST_FRAME}")
    print(f"  2) model: {MODEL_URL[:80]}")
    print(f"  3) product: {PRODUCT_URL[:80]}")
    print(f"prompt({len(SEEDREAM_PROMPT)} chars):\n{SEEDREAM_PROMPT}\n")

    args = {
        "image_urls": [DRIVING_FIRST_FRAME, MODEL_URL, PRODUCT_URL],
        "prompt": SEEDREAM_PROMPT,
    }
    t0 = time.time()
    handler = await fal_client.submit_async(SEEDREAM_ENDPOINT, arguments=args)
    rid = handler.request_id
    print(f"  request_id={rid}")

    for _ in range(60):
        await asyncio.sleep(5)
        st = await fal_client.status_async(SEEDREAM_ENDPOINT, rid)
        elapsed = int(time.time()-t0)
        print(f"  [{elapsed:3d}s] {type(st).__name__}")
        from fal_client import Completed
        if isinstance(st, Completed):
            res = await fal_client.result_async(SEEDREAM_ENDPOINT, rid)
            # seedream 输出 images 数组
            imgs = res.get("images") if isinstance(res, dict) else None
            if not imgs and isinstance(res, dict) and res.get("image"):
                imgs = [res["image"]]
            if not imgs:
                print(f"  ❌ 无 images: {json.dumps(res)[:300]}")
                return None
            url = imgs[0].get("url") if isinstance(imgs[0], dict) else imgs[0]
            print(f"  ✅ Seedream OK total={elapsed}s url={url}")
            return url
    print("  ⏱ timeout")
    return None


async def step_b_kling(first_frame_url: str):
    print(f"\n=== Step B: kling-o3 i2v 用融合首帧 + 时序 prompt ===")
    print(f"first_frame: {first_frame_url}")
    print(f"timing prompt({len(KLING_TIMING_PROMPT)} chars):\n{KLING_TIMING_PROMPT}\n")

    args = {
        "image_url": first_frame_url,
        "prompt": KLING_TIMING_PROMPT,
        "duration": "5",
        "aspect_ratio": "9:16",
    }
    t0 = time.time()
    handler = await fal_client.submit_async(KLING_O3_I2V, arguments=args)
    rid = handler.request_id
    print(f"  request_id={rid}")

    for _ in range(60):
        await asyncio.sleep(10)
        st = await fal_client.status_async(KLING_O3_I2V, rid)
        elapsed = int(time.time()-t0)
        print(f"  [{elapsed:3d}s] {type(st).__name__}")
        from fal_client import Completed
        if isinstance(st, Completed):
            res = await fal_client.result_async(KLING_O3_I2V, rid)
            v = (res.get("video") or {}).get("url") if isinstance(res, dict) else None
            print(f"  ✅ kling i2v OK total={elapsed}s url={v}")
            return v
    print("  ⏱ timeout")
    return None


async def main():
    if not os.environ.get("FAL_KEY"):
        print("ERR FAL_KEY required")
        return

    fused = await step_a_seedream()
    if not fused:
        print("\n❌ Step A 失败,放弃 Step B")
        return

    video = await step_b_kling(fused)
    if video:
        print(f"\n🎯 完整 V3+ 路线 verified")
        print(f"   fused first frame: {fused}")
        print(f"   final video:       {video}")


asyncio.run(main())
