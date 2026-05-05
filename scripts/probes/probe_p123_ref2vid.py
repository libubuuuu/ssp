"""P123 probe:Seedance ref2vid 多图参考能否真展示产品反面

verify(memory fal_probe_first 教训:必须看视频质量):
- 喂 [base_image(模特+产品+背景), 产品正面, 产品反面, 背景图]
- prompt 引导"模特 360° 转身露出产品反面"
- 抽帧看是否真出现反面视角(豹纹反面有特定 logo/标签等可识别)

对比 i2v probe(P119):同样输入,i2v 出来 6 帧几乎一样,无反面。
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/opt/ssp/backend")
import fal_client


# 复用 task 36d49f96 的输入(用户真实上传的 3 张图)
BASE_IMAGE = "https://v3b.fal.media/files/b/0a98f5fb/hCx3vr24RgQbFDd3m2vCT_p119_real_base.jpg"
PRODUCT_FRONT = "https://v3b.fal.media/files/b/0a98f4e6/cMeIZJnx9aS7jkXY7WVQG_tmpt89xwwjs.jpg"
PRODUCT_BACK = "https://v3b.fal.media/files/b/0a98f4e6/5LeSq03-a8fOY3FqwyVoR_tmpxj2pc76e.jpg"
BACKGROUND = "https://v3b.fal.media/files/b/0a98f4e6/yPCdOYYS5kFjzdw4fM3o8_tmpnjenwb1n.jpg"

REF_IMAGES = [BASE_IMAGE, PRODUCT_FRONT, PRODUCT_BACK, BACKGROUND]

# 让 ref2vid 真展示反面的 prompt
PROMPT = """CRITICAL PRODUCT FIDELITY: The product shown in the reference images
MUST appear IDENTICAL in this video. Do NOT invent a different product.
Reference 1 is composed scene (model + product + background).
Reference 2 is product FRONT view, Reference 3 is product BACK/SIDE view.
Reference 4 is the background environment.

Action: model wearing the leopard print waist trainer slowly rotates her body
180 degrees to show the back of the waist trainer. The camera follows her
rotation. The back panel design (visible in reference 3) MUST be clearly shown
when she turns away from camera. Smooth body rotation, natural lighting from
the background scene.

Photorealistic UGC selfie style, vertical 9:16, natural lighting matching
the background reference."""


async def main():
    print("=" * 70)
    print("P123 probe:ref2vid 多图参考展示产品反面")
    print("=" * 70)
    print(f"\nReference images ({len(REF_IMAGES)} 张):")
    for i, u in enumerate(REF_IMAGES, 1):
        print(f"  {i}. {u[:80]}")
    print(f"\nprompt: {PROMPT[:200]}...")

    print("\n[Step] 调 ref2vid (subscribe_async 阻塞等)...")
    res = await fal_client.subscribe_async(
        "bytedance/seedance-2.0/reference-to-video",
        arguments={
            "reference_image_urls": REF_IMAGES,
            "prompt": PROMPT,
            "duration": "5",
            "aspect_ratio": "9:16",
            "resolution": "720p",
        },
    )
    video_obj = res.get("video") if isinstance(res.get("video"), dict) else None
    url = video_obj.get("url") if video_obj else None
    if not url:
        print(f"❌ 未返 video: {str(res)[:300]}")
        return 1
    print(f"✅ 视频: {url[:100]}")

    # 下载
    local = "/tmp/probe_p123_ref2vid.mp4"
    subprocess.run(["curl", "-sf", "-o", local, url], check=True)
    info = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-show_entries", "stream=width,height,r_frame_rate",
         "-of", "default=noprint_wrappers=1", local],
        capture_output=True, text=True,
    )
    print(f"\nffprobe:\n{info.stdout}")

    # 抽 5 帧:0.3 / 1.5 / 2.5 / 3.5 / 4.5(贯穿 5 秒,看转身过程)
    for ts, tag in [
        (0.3, "1_start"),
        (1.5, "2_quarter"),
        (2.5, "3_mid"),
        (3.5, "4_three_quarter"),
        (4.5, "5_end"),
    ]:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(ts), "-i", local, "-frames:v", "1",
             f"/tmp/probe_p123_frame_{tag}.jpg"],
            check=True,
        )
    print("✅ 抽 5 帧 /tmp/probe_p123_frame_*.jpg")
    print("\n下一步:Read 5 帧看转身过程 + 是否真出现反面视角")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
