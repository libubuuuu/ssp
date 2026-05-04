"""P115 probe: Kling Avatar v2 专用通道 verify

流程:
1. 拿 task c7f497b0 那张被 Kling 拒的"产品+模特"首帧
2. 调 Flux Kontext edit "重构成模特肖像式构图"
3. 用重构后的图 + 一段 audio 调 Kling Avatar v2 Standard
4. 看 Kling 是否接受 + 出活 URL

不踩坑(memory feedback_ssp_fal_probe_first):
- 真 KEY 实测 submit + result
- 失败原因抓清楚
- 通过才让 jobs.py 加这个通道
"""
import asyncio
import os
import sys
import json

# 用 prod env 拿 FAL_KEY
sys.path.insert(0, "/opt/ssp/backend")
from app.config import get_settings  # noqa: E402

settings = get_settings()
os.environ["FAL_KEY"] = settings.FAL_KEY
import fal_client  # noqa: E402

# 输入:c7f497b0 那张被 Kling 拒的"产品+模特"首帧
REJECTED_IMG = "https://v3b.fal.media/files/b/0a98e77c/jz_Du0ZHcqoxQOkmDrwIm_961e3b7976a648cdabc97513a5860cfb.png"

# 测试 audio:跑 elevenlabs TTS 出一段(用真生产链路一样的方式)
TEST_AUDIO = ""  # 在 main 里 init

KONTEXT_ENDPOINT = "fal-ai/flux-pro/kontext/max/multi"
KLING_ENDPOINT = "fal-ai/kling-video/ai-avatar/v2/standard"


async def step1_reframe_portrait(input_image_url: str) -> str:
    """用 Flux Kontext 把'产品+模特'图改成'模特肖像式'(让 Kling 能识别人脸)"""
    print(f"\n[Step 1] Flux Kontext reframe → portrait composition")
    print(f"  input: {input_image_url[:80]}")
    prompt = (
        "Reframe and recompose this image into a clean upper-body portrait of the model. "
        "Model facing camera directly with a clear, well-lit face occupying the upper-center "
        "of the frame. Soft studio background, neutral color. The product can stay visible "
        "but smaller in scale - worn naturally on the body or held lightly in hands - "
        "do not let the product dominate the composition. Photorealistic, high quality, "
        "TikTok creator headshot style."
    )
    try:
        result = await fal_client.run_async(
            KONTEXT_ENDPOINT,
            arguments={
                "prompt": prompt,
                "image_urls": [input_image_url],
                "guidance_scale": 3.5,
                "num_images": 1,
                "output_format": "jpeg",
                "safety_tolerance": "5",
            },
        )
        imgs = result.get("images") or []
        if imgs and imgs[0].get("url"):
            url = imgs[0]["url"]
            print(f"  ✅ Kontext OK url={url[:80]}")
            return url
        print(f"  ❌ Kontext 无 image: {json.dumps(result)[:300]}")
        return ""
    except Exception as e:
        print(f"  ❌ Kontext 失败: {e}")
        return ""


async def step2_kling_avatar(portrait_url: str, audio_url: str) -> dict:
    """喂 Kling Avatar v2 Standard,看是否拒"""
    print(f"\n[Step 2] Kling Avatar v2 Standard")
    print(f"  image: {portrait_url[:80]}")
    print(f"  audio: {audio_url[:80]}")
    try:
        # submit + poll
        h = await fal_client.submit_async(
            KLING_ENDPOINT,
            arguments={
                "image_url": portrait_url,
                "audio_url": audio_url,
            },
        )
        task_id = h.request_id
        print(f"  submit OK task_id={task_id}")
        import time

        start = time.time()
        for i in range(60):  # 最多 5 分钟
            await asyncio.sleep(5)
            try:
                s = await fal_client.status_async(KLING_ENDPOINT, task_id)
            except Exception as e:
                print(f"  poll {i} status err: {e}")
                continue
            sn = type(s).__name__
            if sn == "Completed":
                res = await fal_client.result_async(KLING_ENDPOINT, task_id)
                v = (res.get("video") or {}).get("url") or res.get("video_url")
                print(f"  ✅ Kling OK ({int(time.time()-start)}s) video={v}")
                return {"ok": True, "video_url": v, "elapsed": int(time.time() - start)}
            if sn in ("Failed", "Cancelled"):
                print(f"  ❌ Kling failed: {s}")
                return {"ok": False, "error": str(s)}
            print(f"  poll {i} status={sn}")
        print(f"  ❌ Kling 超时 5 min")
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        # Kling 拒输入会在 submit 时直接 raise
        print(f"  ❌ Kling submit 失败: {e}")
        return {"ok": False, "error": str(e)[:300]}


async def gen_test_audio() -> str:
    """elevenlabs TTS 出一段 5s 测试 audio,模拟真生产 P104 链路"""
    print("\n[init] elevenlabs TTS 生成测试 audio(5s)")
    res = await fal_client.run_async(
        "fal-ai/elevenlabs/tts/multilingual-v2",
        arguments={"text": "Tired of saggy waistlines? This trainer slims you instantly."},
    )
    audio_obj = res.get("audio") if isinstance(res.get("audio"), dict) else None
    url = audio_obj.get("url") if audio_obj else res.get("audio_url")
    print(f"  ✅ audio_url={url[:80]}")
    return url


async def main() -> int:
    print("=" * 70)
    print("P115 probe: Kling 专用通道 (Kontext reframe → Kling)")
    print("=" * 70)

    audio_url = await gen_test_audio()

    # Step 0: 先试直接喂 Kling (重现 c7f497b0 失败) — 确认 Kling 真的拒
    print(f"\n[Step 0] 直接喂 Kling 复现 c7f497b0 失败")
    direct = await step2_kling_avatar(REJECTED_IMG, audio_url)
    if direct["ok"]:
        print("  ⚠️  奇怪: Kling 这次接受了原图(可能 Kling 模型更新了或图被拒是 audio 问题)")
        return 0

    # Step 1: Kontext reframe
    portrait = await step1_reframe_portrait(REJECTED_IMG)
    if not portrait:
        print("\n❌ Kontext 失败 — Kling 通道走不通")
        return 1

    # Step 2: 用新图试 Kling
    result = await step2_kling_avatar(portrait, audio_url)

    print("\n" + "=" * 70)
    print("最终结论:")
    print("=" * 70)
    if result["ok"]:
        print(f"✅ Kling 通道可行!")
        print(f"  reframed portrait: {portrait}")
        print(f"  output video: {result['video_url']}")
        print(f"  Kling 出活耗时: {result['elapsed']}s")
        print(f"\n下一步: jobs.py 加 'kling' endpoint 检测 → 走这个通道")
        return 0
    else:
        print(f"❌ Kling 仍拒(即使 Kontext 重构): {result.get('error', '?')[:200]}")
        print(f"  → Kling 通道不可行,放弃,继续用 omnihuman")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
