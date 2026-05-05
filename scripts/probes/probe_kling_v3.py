"""probe fal-ai/kling-video/v3/pro/image-to-video — verify start_image_url + generate_audio
+ 真出说话视频质量(memory fal_probe_first)
"""
import asyncio, sys, time
import fal_client

# 复用 GPT-Image 2 分镜首帧
FRAME = "https://v3b.fal.media/files/b/0a98f9a5/I-_40QR6CmgB3vSRQGYsl_szC2QFmJ.png"
PROMPT = (
    "The model is speaking enthusiastically to the camera, looking directly at viewer with warm expression. "
    "She says: 'Tired of saggy waistlines? This trainer slims you instantly!' "
    "Her lips and mouth move naturally to match the words. Subtle hand gesture toward the waist trainer."
)


async def main():
    print("=" * 72)
    print("Kling v3 pro i2v probe — start_image_url + generate_audio=true")
    print("=" * 72)
    t0 = time.time()
    try:
        r = await fal_client.subscribe_async(
            "fal-ai/kling-video/v3/pro/image-to-video",
            arguments={
                "start_image_url": FRAME,  # 关键:Kling v3 用 start_image_url
                "prompt": PROMPT,
                "duration": 5,             # Kling 用数值,不是 "4" string
                "generate_audio": True,
            },
        )
        v = (r.get("video") or {}).get("url") if isinstance(r.get("video"), dict) else r.get("video_url")
        dt = time.time() - t0
        print(f"\n✅ OK {dt:.1f}s\nvideo: {v}\nkeys: {list(r.keys())}")
        return 0
    except Exception as e:
        dt = time.time() - t0
        print(f"\n❌ FAIL {dt:.1f}s: {str(e)[:400]}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
