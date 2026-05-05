"""probe openai/gpt-image-2/edit — verify 真能跑 + 跟 Kontext 对比

memory fal_probe_first 教训:切端点前必须 probe submit + result + 真图质量。

测试:
- 喂 base_image_url(模特+产品+背景合成首帧)
- 跑 5 段不同 visual_prompt(钩子/正面/侧面/坐姿/CTA)
- verify:身份保留 + 镜头不同 + GPT 出图质量
- 对比 Kontext probe 时间(5 段并发 ~52s)
"""
import asyncio
import sys
import time

import fal_client


BASE_IMAGE = "https://v3b.fal.media/files/b/0a98f5fb/hCx3vr24RgQbFDd3m2vCT_p119_real_base.jpg"

PROMPTS = [
    "Adjust this image to a close-up macro shot of the leopard print waist trainer. Camera tight on the texture and black panel seams. Model's hand visible holding the edge. 9:16 vertical.",
    "Adjust this image to a medium front-facing shot. Model standing centered, full waist trainer visible. Soft natural lighting. 9:16 vertical.",
    "Adjust this image to a 45-degree side angle shot. Model turning to reveal the side seam of the waist trainer. 9:16 vertical.",
    "Adjust this image to a wide shot showing the model sitting casually on a sofa edge in a bedroom. Waist trainer visible under cropped top. 9:16 vertical.",
    "Adjust this image to a low-angle close-up of the model smiling and pointing down toward the waist trainer with finger gesture. 9:16 vertical.",
]


async def gpt_edit_one(prompt: str, idx: int) -> dict:
    print(f"[段 {idx}] gpt-image-2/edit start")
    t0 = time.time()
    try:
        r = await fal_client.run_async(
            "openai/gpt-image-2/edit",
            arguments={
                "prompt": prompt,
                "image_urls": [BASE_IMAGE],
                "image_size": "portrait_16_9",
                "num_images": 1,
                "output_format": "png",
            },
        )
        imgs = r.get("images") or []
        url = imgs[0].get("url") if imgs else None
        dt = time.time() - t0
        print(f"[段 {idx}] OK {dt:.1f}s url={url}")
        return {"idx": idx, "url": url, "dt": dt}
    except Exception as e:
        dt = time.time() - t0
        print(f"[段 {idx}] FAIL {dt:.1f}s: {str(e)[:300]}")
        return {"idx": idx, "url": None, "error": str(e)[:300], "dt": dt}


async def main():
    print("=" * 72)
    print("probe openai/gpt-image-2/edit — 5 段并发")
    print(f"base_image: {BASE_IMAGE}")
    print("=" * 72)
    t0 = time.time()
    results = await asyncio.gather(*[gpt_edit_one(PROMPTS[i], i + 1) for i in range(5)], return_exceptions=True)
    print(f"\n[done] 总耗时 {time.time() - t0:.1f}s")
    print("\n结果汇总:")
    success = 0
    for r in results:
        if isinstance(r, Exception):
            print(f"  ❌ exception: {str(r)[:200]}")
            continue
        if r.get("url"):
            success += 1
            print(f"  ✅ 段 {r['idx']} {r['dt']:.1f}s {r['url']}")
        else:
            print(f"  ❌ 段 {r['idx']} {r.get('error', '?')[:200]}")
    print(f"\n成功 {success}/5")
    return 0 if success >= 4 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
