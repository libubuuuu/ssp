"""P116 + P117 probe:真下载视频 + 抽帧验证(不只是看 submit OK)

核心要 verify 的(用户骂的 3 件事都修了吗):
1. 嘴张大(P116):visual_prompt 不再含 shocked 等指令 → 嘴自然 ✅?
2. 不参考产品(P117):Kontext reframe 不再"do not let product dominate" → 产品在画面 ✅?
3. 不参考背景(P117):Kontext reframe 不再 "Soft studio background" → 背景跟原图一致 ✅?

probe 通道:
- A. omnihuman 通道(默认,快路径):产品图 → 直接 omnihuman talking
- B. Kling 通道(P115+P117):产品图 → Kontext reframe → Kling talking

下载视频后 ffprobe + 抽 3 帧,放到 /tmp/probe_p116/<channel>/ 让人眼对比。
"""
import asyncio
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, "/opt/ssp/backend")
from app.config import get_settings  # noqa: E402

settings = get_settings()
os.environ["FAL_KEY"] = settings.FAL_KEY
import fal_client  # noqa: E402

# 用真实 task 的产品+模特+背景图(用户测过的)
PRODUCT_IMG = "https://v3b.fal.media/files/b/0a98e184/i40d9H-R8-e-3wfQne8o0_tmpn73umniq.jpg"
BG_IMG = "https://v3b.fal.media/files/b/0a98e189/R6_EMmd9i77ojNJ5i7jHA_tmp1klw6ly2.jpg"

OUT_DIR = Path("/tmp/probe_p116")
OUT_DIR.mkdir(exist_ok=True)


async def synth_first_frame(product_url: str, bg_url: str) -> str:
    """合成"产品+模特+背景"首帧(模拟 Seedream multi-edit)"""
    print("\n[Step A] 合成首帧(产品+模特+背景)")
    # 用 Flux Kontext multi 简化合成
    res = await fal_client.run_async(
        "fal-ai/flux-pro/kontext/max/multi",
        arguments={
            "prompt": (
                "Place a Western female model (25 yrs, natural skin, brown wavy hair) "
                "wearing the product (a black waist trainer) in the bedroom scene from "
                "the second image. Cozy bedroom with warm lamp light, wooden floor. "
                "Photorealistic, full upper body visible, model facing camera."
            ),
            "image_urls": [product_url, bg_url],
            "guidance_scale": 3.5,
            "num_images": 1,
            "output_format": "jpeg",
            "safety_tolerance": "5",
        },
    )
    imgs = res.get("images") or []
    url = imgs[0]["url"] if imgs else ""
    print(f"  base_image: {url[:80]}")
    return url


async def gen_audio() -> str:
    """生成 5s 测试音频"""
    print("\n[Step B] elevenlabs TTS 生成 5s audio")
    res = await fal_client.run_async(
        "fal-ai/elevenlabs/tts/multilingual-v2",
        arguments={"text": "This trainer slims you instantly. Limited 50 left."},
    )
    a = res.get("audio") if isinstance(res.get("audio"), dict) else None
    url = a.get("url") if a else res.get("audio_url")
    print(f"  audio: {url[:80]}")
    return url


async def probe_omnihuman(image_url: str, audio_url: str) -> str:
    """A 通道:omnihuman 直接吃 base_image"""
    print("\n[A] omnihuman talking head(默认通道)")
    h = await fal_client.submit_async(
        "fal-ai/bytedance/omnihuman",
        arguments={"image_url": image_url, "audio_url": audio_url},
    )
    rid = h.request_id
    for i in range(60):
        await asyncio.sleep(5)
        s = await fal_client.status_async("fal-ai/bytedance/omnihuman", rid)
        if type(s).__name__ == "Completed":
            res = await fal_client.result_async("fal-ai/bytedance/omnihuman", rid)
            v = (res.get("video") or {}).get("url") or res.get("video_url")
            print(f"  ✅ omnihuman OK ({i*5}s) {v[:80]}")
            return v
        if type(s).__name__ in ("Failed", "Cancelled"):
            print(f"  ❌ omnihuman fail: {s}")
            return ""
    print(f"  ❌ omnihuman timeout")
    return ""


async def probe_kling_channel(image_url: str, audio_url: str) -> str:
    """B 通道:Kontext reframe(P117 新 prompt)→ Kling Avatar"""
    print("\n[B] Kling 通道:Kontext(P117 新 prompt)→ Kling")
    # P117 新 Kontext prompt(保留背景 + 产品)
    print("  [B.1] Kontext reframe(P117 prompt)")
    kres = await fal_client.run_async(
        "fal-ai/flux-pro/kontext/max/multi",
        arguments={
            "prompt": (
                "Adjust the camera framing of this image to make the model's face clearly visible "
                "in the upper-center of the frame, while KEEPING the original background scene "
                "EXACTLY as it is (do NOT replace background with studio or any other scene), "
                "and KEEPING the product visible and recognizable in the frame "
                "(worn naturally on the body or held in hands as in the original). "
                "Only zoom/recompose the framing — do not change colors, lighting, "
                "background elements, or the product. Model facing camera with a relaxed "
                "neutral expression (NO open mouth, NO shocked face). Photorealistic."
            ),
            "image_urls": [image_url],
            "guidance_scale": 3.5,
            "num_images": 1,
            "output_format": "jpeg",
            "safety_tolerance": "5",
        },
    )
    portrait = (kres.get("images") or [{}])[0].get("url", "")
    if not portrait:
        print(f"  ❌ Kontext fail")
        return ""
    print(f"  Kontext OK {portrait[:80]}")

    print("  [B.2] Kling Avatar v2 standard")
    h = await fal_client.submit_async(
        "fal-ai/kling-video/ai-avatar/v2/standard",
        arguments={
            "image_url": portrait,
            "audio_url": audio_url,
            "prompt": (
                "natural relaxed talking pose, slight head movements, "
                "subtle natural expressions, no exaggerated mouth or face"
            ),
        },
    )
    rid = h.request_id
    for i in range(60):
        await asyncio.sleep(5)
        s = await fal_client.status_async("fal-ai/kling-video/ai-avatar/v2/standard", rid)
        if type(s).__name__ == "Completed":
            res = await fal_client.result_async("fal-ai/kling-video/ai-avatar/v2/standard", rid)
            v = (res.get("video") or {}).get("url") or res.get("video_url")
            print(f"  ✅ Kling OK ({i*5}s) {v[:80]}")
            return v
        if type(s).__name__ in ("Failed", "Cancelled"):
            print(f"  ❌ Kling fail: {s}")
            return ""
    print(f"  ❌ Kling timeout")
    return ""


def download_and_extract_frames(video_url: str, channel: str) -> list[Path]:
    """下载视频 + 抽 3 帧(开头/中间/结尾)"""
    if not video_url:
        return []
    cdir = OUT_DIR / channel
    cdir.mkdir(exist_ok=True)
    vp = cdir / "video.mp4"
    print(f"\n  [Verify {channel}] 下载视频...")
    urllib.request.urlretrieve(video_url, vp)
    sz = vp.stat().st_size
    # ffprobe 看时长
    dur = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(vp)],
        text=True,
    ).strip()
    print(f"    file: {vp} ({sz/1024:.0f} KB, dur={dur}s)")
    # 抽 3 帧:开头 / 中间 / 结尾
    frames = []
    dur_f = float(dur)
    for label, t in [("start", 0.5), ("mid", dur_f / 2), ("end", max(0, dur_f - 0.5))]:
        fp = cdir / f"frame_{label}.jpg"
        subprocess.run(
            ["ffmpeg", "-ss", str(t), "-i", str(vp), "-frames:v", "1",
             "-q:v", "2", "-y", str(fp)],
            check=True, capture_output=True,
        )
        frames.append(fp)
        print(f"    frame {label}: {fp}")
    return frames


async def main() -> int:
    print("=" * 70)
    print("P116+P117 probe(下载视频抽帧 verify)")
    print("=" * 70)

    # 准备:合首帧 + 生 audio
    base_img = await synth_first_frame(PRODUCT_IMG, BG_IMG)
    if not base_img:
        print("❌ 首帧合成失败")
        return 1
    audio = await gen_audio()
    if not audio:
        print("❌ audio 生成失败")
        return 1

    # 下载 base_img 也保存一份对比
    Path(OUT_DIR / "_base_first_frame.jpg").write_bytes(
        urllib.request.urlopen(base_img).read()
    )
    print(f"\n  base 首帧已存: {OUT_DIR}/_base_first_frame.jpg")

    # A 通道 omnihuman + B 通道 Kling 并行
    a_v, b_v = await asyncio.gather(
        probe_omnihuman(base_img, audio),
        probe_kling_channel(base_img, audio),
    )

    print("\n" + "=" * 70)
    print("下载视频 + 抽帧")
    print("=" * 70)
    download_and_extract_frames(a_v, "A_omnihuman")
    download_and_extract_frames(b_v, "B_kling_p117")

    print("\n" + "=" * 70)
    print(f"完成,产物在 {OUT_DIR}")
    print("=" * 70)
    print("自检 checklist(对照 _base_first_frame.jpg):")
    print("  A_omnihuman/frame_*.jpg:")
    print("    [ ] 嘴自然不张大?")
    print("    [ ] 背景跟 base 一致(卧室)?")
    print("    [ ] 产品在画面?")
    print("  B_kling_p117/frame_*.jpg:")
    print("    [ ] 嘴自然不张大?")
    print("    [ ] 背景仍是卧室(P117 修了应该 yes)?")
    print("    [ ] 产品仍可见(P117 修了应该 yes)?")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
