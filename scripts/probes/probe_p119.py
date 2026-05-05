"""P119 probe:5s 视频 3 镜头多镜头叙事拼接

模拟 VLM 输出 3 段 micro-scenes(每段 1.5-2s),走 P119 流水线:
- 段 1(0-1.5s):talking head 模特说话开场
- 段 2(1.5-3s):Seedance i2v 产品大特写推近
- 段 3(3-5s):Seedance i2v 模特拉/调整演示动作

verify(memory fal_probe_first 教训:必须看视频质量,不只是 submit OK):
- 每段画面真的不同(钩子 vs 特写 vs 演示)
- 切换瞬间不会黑屏/崩
- 模特身份一致(共享 base_image_url)
- 音频全程连贯(用 talking 的 5s TTS)
"""
import asyncio
import os
import subprocess
import sys
import tempfile
import shutil
import re
import uuid
from pathlib import Path
from datetime import datetime

sys.path.insert(0, "/opt/ssp/backend")
import fal_client


# 复用 task 36d49f96 的 base_image — 上次 Kling Pro 拿到的 reframed 图,
# 已经是"模特+产品+背景"合成的爆款首帧
BASE_IMAGE = "https://v3b.fal.media/files/b/0a98f5fb/hCx3vr24RgQbFDd3m2vCT_p119_real_base.jpg"

# 整段 speech(5s 完整一句话,会贯穿 3 段视频音轨)
SPEECH_TEXT = "Tired of saggy waistlines? This trainer slims you instantly. Only 50 left tonight!"

# 3 个 micro-scene 的不同 visual_prompt(模拟 VLM 多镜头输出)
SCENES = [
    {
        "id": 1,
        "time_range": "0-1.5s",
        "visual_prompt": (
            "Realistic young female model wearing leopard print waist trainer over beige top, "
            "casual home setting with grey sofa, looking directly into the camera with relaxed expression, "
            "subtle natural smile, slight head movement"
        ),
        "speech": SPEECH_TEXT,
    },
    {
        "id": 2,
        "time_range": "1.5-3s",
        "visual_prompt": (
            "EXTREME MACRO close-up of the leopard print waist trainer fabric and black PU panels, "
            "smooth camera push-in revealing every detail of the elastic material texture, "
            "soft warm lighting on product details, no model face visible, product is the hero"
        ),
        "speech": "",
    },
    {
        "id": 3,
        "time_range": "3-5s",
        "visual_prompt": (
            "Model with both hands on her leopard waist trainer at her waist, "
            "demonstrates tugging and adjusting the front panel to show how it tightens, "
            "rotates her torso slightly left then right to display the slim fit, "
            "smooth camera follows from chest down, focus on body and hands action with the product"
        ),
        "speech": "",
    },
]


async def gen_audio() -> str:
    print("[Step 1] elevenlabs TTS 生成 5s audio")
    res = await fal_client.run_async(
        "fal-ai/elevenlabs/tts/multilingual-v2",
        arguments={"text": SPEECH_TEXT},
    )
    audio_obj = res.get("audio") if isinstance(res.get("audio"), dict) else None
    url = audio_obj.get("url") if audio_obj else res.get("audio_url")
    print(f"  ✅ audio_url={url[:80]}")
    return url


async def run_talking_head(image_url: str, audio_url: str) -> str:
    print("[Step 2A] talking head Kling Avatar v2 Pro(5s)")
    h = await fal_client.submit_async(
        "fal-ai/bytedance/omnihuman",
        arguments={
            "image_url": image_url,
            "audio_url": audio_url,
        },
    )
    tid = h.request_id
    for _ in range(60):
        await asyncio.sleep(5)
        try:
            s = await fal_client.status_async("fal-ai/bytedance/omnihuman", tid)
        except Exception:
            continue
        if type(s).__name__ == "Completed":
            res = await fal_client.result_async("fal-ai/bytedance/omnihuman", tid)
            v = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
            if not v:
                raise Exception("talking 未返 video_url")
            print(f"  ✅ talking video={v[:80]}")
            return v
    raise Exception("talking head 超时")


async def run_seedance_for_scene(scene: dict, image_url: str, idx: int) -> str:
    print(f"[Step 2B-{idx}] Seedance v1/pro i2v scene={scene['id']}")
    res = await fal_client.subscribe_async(
        "fal-ai/bytedance/seedance/v1/pro/image-to-video",
        arguments={
            "image_url": image_url,
            "prompt": scene["visual_prompt"],
            "duration": "4",
            "resolution": "720p",
            "aspect_ratio": "9:16",
            "enable_audio": False,
        },
    )
    v = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
    if not v:
        raise Exception(f"Seedance scene{idx} 未返 video_url")
    print(f"  ✅ seedance video={v[:80]}")
    return v


def ffmpeg_concat_multi(talking_path, seedance_paths, seg_durs, out_path):
    print(f"[Step 3] ffmpeg 多镜头拼接 N={len(seg_durs)} 段")
    target_w, target_h = 1056, 1952

    paths = [talking_path] + seedance_paths
    filter_parts = []
    for i, _ in enumerate(paths):
        seg_dur = seg_durs[i]
        if i == 0:
            start, end = 0, seg_dur
        else:
            start, end = 0, seg_dur
        filter_parts.append(
            f"[{i}:v]trim={start}:{end},setpts=PTS-STARTPTS,"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]"
        )
    concat_inputs = "".join(f"[v{i}]" for i in range(len(paths)))
    filter_parts.append(f"{concat_inputs}concat=n={len(paths)}:v=1:a=0[v]")
    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for p in paths:
        cmd.extend(["-i", p])
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise Exception(f"ffmpeg failed: {r.stderr[:500]}")
    print("  ✅ 拼接 OK")


async def main():
    print("=" * 70)
    print("P119 probe:5s 视频 3 镜头多镜头叙事")
    print("=" * 70)

    audio_url = await gen_audio()

    # 并发跑 talking + 2 个 Seedance(段 2 + 段 3)
    print("\n[Step 2] 并发跑 talking + 2 个 Seedance(scene 2 + scene 3)")
    talking_task = asyncio.create_task(run_talking_head(BASE_IMAGE, audio_url))
    seedance_tasks = [
        asyncio.create_task(run_seedance_for_scene(SCENES[1], BASE_IMAGE, 2)),
        asyncio.create_task(run_seedance_for_scene(SCENES[2], BASE_IMAGE, 3)),
    ]
    results = await asyncio.gather(talking_task, *seedance_tasks, return_exceptions=True)
    if any(isinstance(r, Exception) for r in results):
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"  ❌ task[{i}] 失败: {str(r)[:200]}")
        return 1
    talking_url, seedance_urls = results[0], results[1:]

    # 下载所有视频
    print("\n[Step 3] 下载 3 个视频到 /tmp")
    work = tempfile.mkdtemp(prefix="probe_p119_")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=120) as cli:
            t_path = f"{work}/talking.mp4"
            r = await cli.get(talking_url); r.raise_for_status()
            Path(t_path).write_bytes(r.content)
            sd_paths = []
            for i, su in enumerate(seedance_urls):
                p = f"{work}/seedance_{i+2}.mp4"
                r = await cli.get(su); r.raise_for_status()
                Path(p).write_bytes(r.content)
                sd_paths.append(p)

        # ffmpeg 拼接
        seg_durs = [1.5, 1.5, 2.0]  # 段 1 talking 1.5s + 段 2/3 各 seedance trim
        final = "/tmp/probe_p119_final.mp4"
        ffmpeg_concat_multi(t_path, sd_paths, seg_durs, final)

        # ffprobe
        info = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration,size",
             "-show_entries", "stream=width,height,r_frame_rate,codec_name",
             "-of", "default=noprint_wrappers=1", final],
            capture_output=True, text=True,
        )
        print(f"\nffprobe final:\n{info.stdout}")

        # 抽 6 帧:每段开头 + 中间
        for ts, tag in [
            (0.3, "1_talking_start"),
            (1.0, "2_talking_mid"),
            (1.6, "3_seedance2_start"),  # 产品特写
            (2.5, "4_seedance2_mid"),
            (3.1, "5_seedance3_start"),  # 演示动作
            (4.5, "6_seedance3_mid"),
        ]:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", str(ts), "-i", final, "-frames:v", "1",
                 f"/tmp/probe_p119_frame_{tag}.jpg"],
                check=True,
            )
        print("✅ 抽 6 帧到 /tmp/probe_p119_frame_*.jpg")
        print("\n下一步:Read 6 帧看 段1说话 vs 段2产品特写 vs 段3演示动作 三个画面是不是真的完全不同")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
