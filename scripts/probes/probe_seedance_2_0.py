"""probe Seedance 2.0 i2v + generate_audio=true → 真出"模特嘴动+台词"?

memory fal_probe_first:实测真视频质量,不只 submit OK。

测:喂 GPT-Image 2 分镜首帧 + visual_prompt 描述模特说话内容
verify:
- ✅ 视频里模特真的张嘴说话(lipsync)
- ✅ audio 真的对应 prompt 描述的台词
- ✅ 模特身份+产品款式跟首帧一致
- 时长 / 速度参考
"""
import asyncio
import sys
import time

import fal_client


# 复用 GPT-Image 2 probe 出的段 2(模特正面+产品)分镜首帧
FRAME = "https://v3b.fal.media/files/b/0a98f9a5/I-_40QR6CmgB3vSRQGYsl_szC2QFmJ.png"

# Visual prompt 含"模特说什么台词"的描述
VISUAL_PROMPT = (
    "The model is speaking enthusiastically to the camera, looking directly at viewer with warm expression. "
    "She says: 'Tired of saggy waistlines? This trainer slims you instantly!' "
    "Her lips and mouth move naturally to match the words. Subtle hand gesture toward the waist trainer. "
    "Soft natural indoor lighting. 9:16 vertical."
)


async def probe_seedance2(image_url: str, prompt: str) -> dict:
    print(f"probe Seedance 2.0 i2v with generate_audio=true")
    print(f"  image: {image_url[:80]}...")
    print(f"  prompt: {prompt[:120]}...")
    t0 = time.time()
    try:
        r = await fal_client.subscribe_async(
            "bytedance/seedance-2.0/image-to-video",
            arguments={
                "image_url": image_url,
                "prompt": prompt,
                "duration": "4",
                "resolution": "720p",
                "aspect_ratio": "9:16",
                "generate_audio": True,  # 关键:让模型自己生成 lipsync audio
            },
        )
        v = (r.get("video") or {}).get("url") if isinstance(r.get("video"), dict) else r.get("video_url")
        dt = time.time() - t0
        print(f"\n✅ OK {dt:.1f}s")
        print(f"video_url: {v}")
        print(f"raw response keys: {list(r.keys())}")
        return {"video_url": v, "duration": dt, "raw": r}
    except Exception as e:
        dt = time.time() - t0
        print(f"\n❌ FAIL {dt:.1f}s: {str(e)[:400]}")
        return {"video_url": None, "error": str(e)[:400], "duration": dt}


async def main():
    print("=" * 72)
    print("Seedance 2.0 i2v + generate_audio=true probe")
    print("=" * 72)
    r = await probe_seedance2(FRAME, VISUAL_PROMPT)
    if r.get("video_url"):
        print("\n下一步:下载视频 + ffprobe audio stream + 抽帧看模特嘴动")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
