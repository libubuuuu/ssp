"""P125 probe:omnihuman 直接跑 N 段分镜首帧 + N 段短 audio = 真出说话视频?

verify(memory fal_probe_first):
- 输入 5 张差异化分镜首帧(storyboard probe 已生成,身份/产品一致 + 镜头不同)
- 5 段独立 TTS short audio(1.5-3s/段)
- 跑 5 个 omnihuman 并发,每个 image+audio
- 抽帧看:模特嘴动 + 镜头跟首帧一致(不是 omnihuman 把不同首帧吃掉只显示模特肖像)

两个 critical 验证点:
A. omnihuman 接受 1.5-3s 短 audio 不报错
B. omnihuman 输出视频画面真的跟输入 image 一致(不仅是模特嘴动,镜头/景别保留)
"""
import asyncio
import sys
import time

sys.path.insert(0, "/root/ssp/backend")
import fal_client


# 复用 storyboard probe 已生成的 5 张分镜图(身份一致 + 镜头不同)
FRAMES = [
    {
        "idx": 1,
        "image_url": "https://v3b.fal.media/files/b/0a98f842/CZaJJifFQ_SPPUqOB_YXl_08ab4ba26a824a88a4b30d3ceec804d4.png",
        "speech": "Tired of saggy waistlines? This trainer slims you instantly!",
        "shot": "特写正面",
    },
    {
        "idx": 2,
        "image_url": "https://v3b.fal.media/files/b/0a98f844/sRzwnuUBPxG7LgX3AcUEW_b392a5f0437e415dbe3dc495957edd67.png",
        "speech": "360 degree sculpting, no dig in.",
        "shot": "中景正面",
    },
    {
        "idx": 3,
        "image_url": "https://v3b.fal.media/files/b/0a98f841/TwmNwUzgUIROM0qer__df_b4cb38d249784831b7406fca11549709.png",
        "speech": "Wear it all day, zero squeeze.",
        "shot": "中景45侧面",
    },
    {
        "idx": 4,
        "image_url": "https://v3b.fal.media/files/b/0a98f841/W8rP66tpFRoYDrHa1pqo4_f156520e6d86466d84218a3ecf8644d1.png",
        "speech": "Even at home, no one knows.",
        "shot": "全景俯视坐姿",
    },
    {
        "idx": 5,
        "image_url": "https://v3b.fal.media/files/b/0a98f842/ydDxltsnaKRCk3XVrPK19_5e4d95a9dca64afaacbffbce3939680c.png",
        "speech": "Last fifty! Grab now!",
        "shot": "近景仰视CTA",
    },
]


async def tts_one(text: str) -> str:
    r = await fal_client.run_async(
        "fal-ai/elevenlabs/tts/multilingual-v2",
        arguments={"text": text},
    )
    audio = r.get("audio") if isinstance(r.get("audio"), dict) else None
    return audio.get("url") if audio else r.get("audio_url")


async def omnihuman_one(image_url: str, audio_url: str, idx: int) -> dict:
    print(f"[段 {idx}] omnihuman start image={image_url[:60]}... audio={audio_url[:60]}...")
    t0 = time.time()
    try:
        r = await fal_client.subscribe_async(
            "fal-ai/bytedance/omnihuman",
            arguments={"image_url": image_url, "audio_url": audio_url},
        )
        v = (r.get("video") or {}).get("url") if isinstance(r.get("video"), dict) else None
        dt = time.time() - t0
        print(f"[段 {idx}] OK {dt:.1f}s video={v}")
        return {"idx": idx, "video_url": v, "duration": dt}
    except Exception as e:
        dt = time.time() - t0
        print(f"[段 {idx}] FAIL {dt:.1f}s: {str(e)[:200]}")
        return {"idx": idx, "video_url": None, "error": str(e)[:200], "duration": dt}


async def main():
    print("=" * 72)
    print("P125 probe:omnihuman 5 段并发(每段独立 image + audio)")
    print("=" * 72)

    # Step A: N 段并发 TTS
    print("\n[Step A] 并发 5 段 elevenlabs TTS...")
    audios = await asyncio.gather(*[tts_one(f["speech"]) for f in FRAMES])
    for i, a in enumerate(audios):
        print(f"  段 {i+1} TTS: {a}")

    # Step B: 5 段并发 omnihuman
    print("\n[Step B] 并发 5 段 omnihuman...")
    t0 = time.time()
    results = await asyncio.gather(
        *[omnihuman_one(FRAMES[i]["image_url"], audios[i], i + 1) for i in range(5)],
        return_exceptions=True,
    )
    print(f"\n[Step B done] 总耗时 {time.time() - t0:.1f}s")

    print("\n" + "=" * 72)
    print("结果汇总:")
    success = 0
    for r in results:
        if isinstance(r, Exception):
            print(f"  ❌ task exception: {str(r)[:200]}")
            continue
        if r.get("video_url"):
            success += 1
            print(f"  ✅ 段 {r['idx']} {r['duration']:.1f}s {r['video_url']}")
        else:
            print(f"  ❌ 段 {r['idx']} {r.get('error', '?')[:100]}")
    print(f"\n成功率: {success}/5")
    print("=" * 72)
    return 0 if success == 5 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
