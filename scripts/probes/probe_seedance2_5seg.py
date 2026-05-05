"""probe Seedance 2.0 i2v 5 段并发 — verify 总耗时是否能接受

单段 186s,5 段并发理论能 ~3-4 分钟,但 fal 队列可能排队
"""
import asyncio
import sys
import time
import fal_client


# 5 张 GPT-Image 2 分镜首帧(差异化镜头 + 身份保留)
FRAMES = [
    {"idx": 1, "image": "https://v3b.fal.media/files/b/0a98f9a5/qZfN5maV0T9Yq5BAqs8WP_IM7KCQ6b.png",
     "prompt": "Close-up macro shot. Model speaking enthusiastically: 'Tired of saggy waistlines?' "
               "Her hand pulls at the leopard waist trainer. Lips and mouth move naturally."},
    {"idx": 2, "image": "https://v3b.fal.media/files/b/0a98f9a5/I-_40QR6CmgB3vSRQGYsl_szC2QFmJ.png",
     "prompt": "Medium front shot. Model speaking warmly: '360 degree sculpting, no dig in.' "
               "Subtle hand gesture toward waist."},
    {"idx": 3, "image": "https://v3b.fal.media/files/b/0a98f9a5/R7FdaPFpt8Njl-96ULNUE_Zi3t4hT6.png",
     "prompt": "45-degree side angle. Model turning slightly while saying: 'Wear it all day, zero squeeze.' "
               "Lips moving in sync."},
    {"idx": 4, "image": "https://v3b.fal.media/files/b/0a98f9a5/aNqzppFrJUR6VZF5m0akZ_Oj8lPXJr.png",
     "prompt": "Wide bedroom shot. Model on bed edge speaking: 'Even at home, no one knows.' "
               "Casual relaxed tone, lips moving."},
    {"idx": 5, "image": "https://v3b.fal.media/files/b/0a98f9a5/f0JWtRpjiCyZXxOZYxx2h_EJZeUcMf.png",
     "prompt": "Low-angle close-up. Model smiling pointing at waist trainer: 'Last fifty! Grab now!' "
               "Excited tone, clear lip sync."},
]


async def seg_i2v(idx: int, image: str, prompt: str) -> dict:
    print(f"[段 {idx}] start")
    t0 = time.time()
    try:
        r = await fal_client.subscribe_async(
            "bytedance/seedance-2.0/image-to-video",
            arguments={
                "image_url": image,
                "prompt": prompt,
                "duration": "4",
                "resolution": "720p",
                "aspect_ratio": "9:16",
                "generate_audio": True,
            },
        )
        v = (r.get("video") or {}).get("url") if isinstance(r.get("video"), dict) else None
        dt = time.time() - t0
        print(f"[段 {idx}] OK {dt:.1f}s")
        return {"idx": idx, "video_url": v, "dt": dt}
    except Exception as e:
        dt = time.time() - t0
        print(f"[段 {idx}] FAIL {dt:.1f}s: {str(e)[:200]}")
        return {"idx": idx, "video_url": None, "error": str(e)[:200], "dt": dt}


async def main():
    print("=" * 72)
    print("Seedance 2.0 i2v 5 段并发 probe")
    print("=" * 72)
    t0 = time.time()
    results = await asyncio.gather(
        *[seg_i2v(f["idx"], f["image"], f["prompt"]) for f in FRAMES],
        return_exceptions=True,
    )
    print(f"\n[total] {time.time() - t0:.1f}s")
    success = 0
    for r in results:
        if isinstance(r, Exception):
            print(f"  ❌ exc: {str(r)[:200]}")
            continue
        if r.get("video_url"):
            success += 1
            print(f"  ✅ 段 {r['idx']} {r['dt']:.1f}s {r['video_url']}")
        else:
            print(f"  ❌ 段 {r['idx']} {r.get('error', '?')[:100]}")
    print(f"\n成功 {success}/5")
    return 0 if success == 5 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
