"""
Probe Group 5: 三条 fal 救命路径

1) fal-ai/seedance-2/enterprise/reference-to-video  ← 关键!4-09 解锁,有 face-input
2) fal-ai/pixverse/swap                            ← 专门做 person/object/bg 替换
3) fal-ai/bytedance/seedance-2.0/fast/reference-to-video  ← fast 档(便宜)

输入:14c390bb session 的真实素材
  - driving 视频:orig.webm 切前 8s
  - reference 图:用 final.mp4 抽一帧当模特/产品(已知通过 NSFW)
"""
from __future__ import annotations
import asyncio, time, json, urllib.request, subprocess
from pathlib import Path
import fal_client

SESSION_DIR = Path("/opt/ssp/uploads/oral/64402546-9ead-4263-b881-a26d8ba6d5b6/14c390bb-1ea")
ORIG = SESSION_DIR / "orig.webm"
FINAL = SESSION_DIR / "final.mp4"

OUT_DIR = Path("/opt/ssp/uploads/probe-results/sd-enterprise-pixverse")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 切 driving 段(8s)+ 抽 reference 帧
DRIVING = OUT_DIR / "driving_8s.mp4"
REF_IMG = OUT_DIR / "ref_frame.jpg"


def prep():
    if not DRIVING.exists():
        subprocess.run([
            "ffmpeg", "-y", "-ss", "0", "-t", "8",
            "-i", str(ORIG),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-an", str(DRIVING),
        ], check=True, capture_output=True)
    if not REF_IMG.exists():
        # 从 final.mp4 0.5s 抽一帧(已知 NSFW 通过的成片)
        subprocess.run([
            "ffmpeg", "-y", "-ss", "0.5", "-i", str(FINAL),
            "-frames:v", "1", "-q:v", "2", str(REF_IMG),
        ], check=True, capture_output=True)


async def upload(local: str) -> str:
    last = None
    for i in range(3):
        try:
            return await fal_client.upload_file_async(local)
        except Exception as e:
            last = e
            await asyncio.sleep(2 ** i)
    raise last


async def probe(name: str, endpoint: str, args: dict, timeout: int = 600) -> dict:
    t0 = time.time()
    out = {"engine": name, "endpoint": endpoint}
    try:
        res = await asyncio.wait_for(
            fal_client.subscribe_async(endpoint, arguments=args),
            timeout=timeout,
        )
        out["elapsed_s"] = round(time.time() - t0, 1)
        out["ok"] = True
        out["raw_keys"] = list(res.keys()) if isinstance(res, dict) else type(res).__name__
        # 拉视频
        video = res.get("video") if isinstance(res, dict) else None
        if isinstance(video, dict) and video.get("url"):
            fname = f"{name.replace('/', '_').replace(' ', '_')}.mp4"
            local = OUT_DIR / fname
            urllib.request.urlretrieve(video["url"], str(local))
            out["fal_url"] = video["url"]
            out["local"] = str(local)
            out["size_mb"] = round(local.stat().st_size / 1e6, 2)
    except asyncio.TimeoutError:
        out["ok"] = False
        out["reason"] = f"timeout_{timeout}s"
    except Exception as e:
        out["ok"] = False
        out["reason"] = "exception"
        out["error"] = str(e)[:800]
    return out


async def main():
    prep()
    print(f"driving: {DRIVING} ({DRIVING.stat().st_size/1e6:.1f}MB)")
    print(f"ref_img: {REF_IMG} ({REF_IMG.stat().st_size/1e3:.0f}KB)")

    drv_url = await upload(str(DRIVING))
    img_url = await upload(str(REF_IMG))
    print(f"\n driving → {drv_url}")
    print(f" ref_img → {img_url}")

    results = []

    # 1) seedance-2 enterprise reference-to-video(关键端点,有 face-input 一致性)
    print("\n=== 1) seedance-2/enterprise/reference-to-video ===")
    r = await probe(
        "seedance-enterprise-r2v",
        "fal-ai/seedance-2/enterprise/reference-to-video",
        {
            "prompt": "@Image1 performing the same actions and movements as in @Video1. "
                      "Preserve the original background and camera angle from @Video1 exactly.",
            "image_urls": [img_url],
            "video_urls": [drv_url],
            "duration": "auto",
            "resolution": "720p",
            "aspect_ratio": "auto",
            "generate_audio": False,
        },
        timeout=900,
    )
    results.append(r)
    print(json.dumps(r, ensure_ascii=False, indent=2)[:600])

    # 2) pixverse/swap(person/object/bg 替换)
    print("\n=== 2) pixverse/swap ===")
    r = await probe(
        "pixverse-swap",
        "fal-ai/pixverse/swap",
        {
            "video_url": drv_url,
            "image_url": img_url,
        },
        timeout=600,
    )
    results.append(r)
    print(json.dumps(r, ensure_ascii=False, indent=2)[:600])

    # 3) seedance-2.0/fast/reference-to-video(便宜档)
    print("\n=== 3) seedance-2.0/fast/reference-to-video ===")
    r = await probe(
        "seedance-fast-r2v",
        "fal-ai/bytedance/seedance-2.0/fast/reference-to-video",
        {
            "prompt": "@Image1 performing the same actions and movements as in @Video1. "
                      "Preserve the original background and camera angle from @Video1 exactly.",
            "image_urls": [img_url],
            "video_urls": [drv_url],
            "duration": "auto",
            "resolution": "720p",
            "aspect_ratio": "auto",
            "generate_audio": False,
        },
        timeout=600,
    )
    results.append(r)
    print(json.dumps(r, ensure_ascii=False, indent=2)[:600])

    out_path = Path("/root/ssp/scripts/probes/sd_enterprise_pixverse_results.json")
    out_path.write_text(json.dumps({
        "driving_url": drv_url,
        "ref_url": img_url,
        "results": results,
    }, ensure_ascii=False, indent=2))
    print(f"\n报告:{out_path}")


if __name__ == "__main__":
    asyncio.run(main())
