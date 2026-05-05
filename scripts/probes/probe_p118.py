"""P118 probe:5-12s 双段拼接架构可行性

要 verify 三件事(memory fal_probe_first 教训:不只是 submit OK,要看视频质量):
1. Seedance i2v 跑 5s 真出活 + 真有动作 + 尺寸/fps
2. ffmpeg 拼接 talking head 视频前 1.5s + seedance 视频后 3.5s 是否丝滑
3. final 视频音频是连贯一句话(用 talking 的全 5s audio)

输入复用 task 36d49f96(用户刚跑过的真实输入)
"""
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, "/opt/ssp/backend")
import fal_client


PRODUCT_IMG = "https://v3b.fal.media/files/b/0a98f4e6/cMeIZJnx9aS7jkXY7WVQG_tmpt89xwwjs.jpg"
TALKING_VIDEO_LOCAL = "/tmp/kling_p116.mp4"  # 上次 36d49f96 出的 Kling Pro 视频(5s)

# 让 Seedance 演示真"爆款动作"(模特拉开/穿戴/收紧/转身),英文 prompt
SEEDANCE_PROMPT = (
    "Realistic young female model wearing a leopard print waist trainer over a beige top "
    "and blue jeans, in a soft-lit modern living room with grey sofa visible behind. "
    "Dynamic action sequence: model places hands on her waist trainer, "
    "tugs and adjusts the front panel firmly to show how it tightens, "
    "then rotates her torso slightly left and right to demonstrate the slim fit, "
    "ending with a confident hand gesture pointing down at her cinched waist. "
    "Smooth camera follows her upper body. The waist trainer is the visual focus throughout. "
    "Photorealistic, vertical 9:16 framing, TikTok product demo style."
)


async def probe_seedance_5s():
    print("\n[Probe A] Seedance v1.5 i2v 5s — 用产品图 + 动作 prompt")
    res = await fal_client.subscribe_async(
        "fal-ai/bytedance/seedance/v1/pro/image-to-video",
        arguments={
            "image_url": PRODUCT_IMG,
            "prompt": SEEDANCE_PROMPT,
            "duration": "5",
            "resolution": "720p",
            "aspect_ratio": "9:16",
            "enable_audio": False,  # 不要 seedance 自带音效,后续用 talking 的 audio
        },
    )
    video_obj = res.get("video") if isinstance(res.get("video"), dict) else None
    url = video_obj.get("url") if video_obj else None
    if not url:
        print(f"  ❌ Seedance 未返 video: {str(res)[:200]}")
        return None
    print(f"  ✅ url={url[:90]}")

    # 下载 + ffprobe
    local = "/tmp/probe_seedance_p118.mp4"
    subprocess.run(["curl", "-sf", "-o", local, url], check=True)
    info = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,duration",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1", local],
        capture_output=True, text=True,
    )
    print(f"  ffprobe:\n{info.stdout}")
    return local


def probe_concat(talking_path: str, seedance_path: str) -> str:
    """ffmpeg 拼接:0-1.5s 取 talking 视频 + 1.5-5s 取 seedance + 完整 audio 取 talking"""
    print("\n[Probe B] ffmpeg 拼接 talking 0-1.5s + seedance 1.5-5s + talking audio")

    # ffprobe 看两个视频尺寸,以 talking 为基准
    def get_wh(p):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=s=x:p=0", p],
            capture_output=True, text=True,
        )
        return r.stdout.strip()

    print(f"  talking  尺寸: {get_wh(talking_path)}")
    print(f"  seedance 尺寸: {get_wh(seedance_path)}")

    final = "/tmp/probe_p118_final.mp4"
    # 用 talking 的尺寸作基准,seedance 缩放到一致
    # 注意:setpts 重置 PTS,不同分辨率/fps 用 scale + setsar + fps 统一
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", talking_path,
        "-i", seedance_path,
        "-filter_complex",
        # 取 talking 0-1.5s 视频 → v0
        "[0:v]trim=0:1.5,setpts=PTS-STARTPTS,scale=1056:1952:force_original_aspect_ratio=decrease,pad=1056:1952:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v0];"
        # 取 seedance 1.5-5s 视频 → v1
        "[1:v]trim=1.5:5,setpts=PTS-STARTPTS,scale=1056:1952:force_original_aspect_ratio=decrease,pad=1056:1952:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v1];"
        "[v0][v1]concat=n=2:v=1:a=0[v]",
        "-map", "[v]",
        "-map", "0:a",  # audio 全程取 talking 的
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        final,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ❌ ffmpeg 失败:\n{r.stderr[:1000]}")
        return None
    info = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size",
         "-show_entries", "stream=width,height,r_frame_rate,codec_name",
         "-of", "default=noprint_wrappers=1", final],
        capture_output=True, text=True,
    )
    print(f"  ✅ 拼接 OK\n{info.stdout}")

    # 抽 4 帧:0.5s(talking) / 1.4s(talking 末) / 1.6s(seedance 始) / 4.5s(seedance 末)
    for ts, tag in [(0.5, "1_talking_start"), (1.4, "2_talking_end"),
                    (1.6, "3_seedance_start"), (4.5, "4_seedance_end")]:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(ts), "-i", final, "-frames:v", "1",
             f"/tmp/probe_p118_frame_{tag}.jpg"],
            check=True,
        )
    print(f"  抽帧 OK: /tmp/probe_p118_frame_*.jpg")
    return final


async def main():
    print("=" * 70)
    print("P118 probe:5-12s 双段拼接架构")
    print("=" * 70)

    if not os.path.exists(TALKING_VIDEO_LOCAL):
        print(f"❌ 缺 {TALKING_VIDEO_LOCAL} — 请先 curl 下载上次 Kling 视频")
        return 1

    seedance_path = await probe_seedance_5s()
    if not seedance_path:
        print("\n❌ Probe A 失败,中止")
        return 1

    final = probe_concat(TALKING_VIDEO_LOCAL, seedance_path)
    if not final:
        print("\n❌ Probe B 失败,中止")
        return 1

    print("\n" + "=" * 70)
    print("✅ P118 probe 通过!")
    print(f"   final 视频: {final}")
    print(f"   抽帧: /tmp/probe_p118_frame_*.jpg")
    print(f"   下一步:Read 4 帧自己看 talking→seedance 切换是否生硬,seedance 段动作是否真'拉/转/演示'")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
