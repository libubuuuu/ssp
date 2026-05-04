"""
P86-2 — 双 probe 解决用户两痛点:
  1. v2v reference 路线:driving 当动作模板 → 复刻"主动拉 T 恤"
  2. multi-image i2v 端点存在性:让产品图也作持续 reference
"""
import asyncio
import os
import time
import fal_client


# 用户素材
DRIVING_URL = "https://v3b.fal.media/files/b/0a98c690/TvvnB_ZkVX5vHEz7bD8FS_seg_00.mp4"
FUSED_FIRST = "https://v3b.fal.media/files/b/0a98d45a/mp7e7TsQnNXA2IWV8RIpc_8270d5d9907c48558c3b1c0fc97f2afe.png"
MODEL_URL = "https://v3b.fal.media/files/b/0a98c674/5Ykt_GqOkGRDd1HuISlEf_tmp0i7f9q7o.jpg"
PRODUCT_URL = "https://v3b.fal.media/files/b/0a98c675/89Dg_cYoBjv0-DjbSLr6v_tmp_w3_2wz2.jpg"

TIMING_PROMPT_CN = (
    "图中的女子用双手主动抓住她身上白色背心的下摆,慢慢用力向上拉起背心,"
    "完全展示出里面的黑色蕾丝胸罩。整段一镜到底,9:16 竖屏,客厅背景保留,"
    "自然光线,真实人像视频风格,真实手部动作。"
)


async def submit_and_poll(endpoint, args, label, max_wait_s=600):
    print(f"\n=== {label} === {endpoint}")
    print(f"  args keys: {list(args.keys())}")
    t0 = time.time()
    try:
        handler = await fal_client.submit_async(endpoint, arguments=args)
        rid = handler.request_id
        print(f"  rid={rid}")
        for _ in range(max_wait_s // 10):
            await asyncio.sleep(10)
            try:
                st = await fal_client.status_async(endpoint, rid)
            except Exception as e:
                print(f"  poll err: {str(e)[:200]}")
                continue
            elapsed = int(time.time() - t0)
            print(f"  [{elapsed:4d}s] {type(st).__name__}")
            from fal_client import Completed
            if isinstance(st, Completed):
                res = await fal_client.result_async(endpoint, rid)
                if isinstance(res, dict):
                    v = (res.get("video") or {}).get("url") or (res.get("output") or {}).get("video_url")
                    if not v and isinstance(res.get("video"), str):
                        v = res["video"]
                    print(f"  ✅ {label} OK total={elapsed}s")
                    print(f"  url={v}")
                    return v
        print(f"  ⏱ {label} timeout")
    except Exception as e:
        s = str(e)
        if "not found" in s.lower() or "404" in s:
            print(f"  🔴 NOT_FOUND: {s[:200]}")
        else:
            print(f"  ❌ {type(e).__name__}: {s[:300]}")
    return None


async def main():
    if not os.environ.get("FAL_KEY"):
        print("ERR FAL_KEY")
        return

    # ============ Probe A: kling/o1/v2v/reference (driving + ref 图 + prompt) ============
    await submit_and_poll(
        "fal-ai/kling-video/o1/video-to-video/reference",
        {
            "video_url": DRIVING_URL,
            "reference_image_urls": [FUSED_FIRST],
            "prompt": TIMING_PROMPT_CN,
        },
        "v2v reference (融合首帧)",
        max_wait_s=900,
    )

    # ============ Probe B: kling lipsync 视频驱动方式(可能也有别的 v2v) ============
    # try with multi-ref(模特+产品)
    await submit_and_poll(
        "fal-ai/kling-video/o1/video-to-video/reference",
        {
            "video_url": DRIVING_URL,
            "reference_image_urls": [MODEL_URL, PRODUCT_URL],
            "prompt": TIMING_PROMPT_CN,
        },
        "v2v reference (模特+产品双 ref)",
        max_wait_s=900,
    )

    # ============ Probe C: 各种可能的 multi-ref i2v 端点 ============
    multi_ref_candidates = [
        ("fal-ai/wan-2.5/multi-reference-to-video", {"image_urls": [MODEL_URL, PRODUCT_URL], "prompt": TIMING_PROMPT_CN}),
        ("fal-ai/wan-2.5/r2v", {"image_urls": [MODEL_URL, PRODUCT_URL], "prompt": TIMING_PROMPT_CN}),
        ("fal-ai/wan-2.5/i2v", {"image_url": MODEL_URL, "reference_image_urls": [PRODUCT_URL], "prompt": TIMING_PROMPT_CN}),
        ("fal-ai/pixverse/v5/multi-reference", {"image_urls": [MODEL_URL, PRODUCT_URL], "prompt": TIMING_PROMPT_CN}),
        ("fal-ai/minimax/hailuo-02-multi-ref", {"image_urls": [MODEL_URL, PRODUCT_URL], "prompt": TIMING_PROMPT_CN}),
        ("fal-ai/runway-gen-3-multi-ref", {"reference_image_urls": [MODEL_URL, PRODUCT_URL], "prompt": TIMING_PROMPT_CN}),
        ("fal-ai/kling-video/v2.1-master/multi-reference-to-video", {"reference_image_urls": [MODEL_URL, PRODUCT_URL], "prompt": TIMING_PROMPT_CN}),
        ("fal-ai/kling-video/o1/multi-reference-to-video", {"reference_image_urls": [MODEL_URL, PRODUCT_URL], "prompt": TIMING_PROMPT_CN}),
    ]
    print("\n========== probe multi-ref i2v 端点存在性 ==========")
    for ep, args in multi_ref_candidates:
        # 只快速 schema 试,不等结果(500s 上限)
        await submit_and_poll(ep, args, f"multi-ref candidate", max_wait_s=30)


asyncio.run(main())
