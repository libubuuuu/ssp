"""
Probe Group 8:fal 上我们 P44/P46 没接的视频生成端点

用户提醒:fal 已聚合阿里 Wan 2.7 / Kling 3.0 / MiniMax / Happy Horse,
不要去开 3 家国内账号,fal key 一个搞定。

候选(全部 verify 真值才下结论):
"""
from __future__ import annotations
import asyncio, time, json, urllib.request
from pathlib import Path
import fal_client

REF_FACE = "https://v3b.fal.media/files/b/0a98aee9/TSfPqBlIwUoey6rbVPUuL_ref_frame.jpg"
DRIVING_VIDEO = "https://v3b.fal.media/files/b/0a98aee9/FADIvK--d7hR-nv2ogcIr_driving_8s.mp4"

OUT_DIR = Path("/opt/ssp/uploads/probe-results/fal-new")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CANDIDATES = [
    # 阿里 Wan 2.7 — 最重要候选(最新,character consistency 更强,且无字节 partner 限制)
    {"name": "wan-2.7 r2v", "endpoint": "fal-ai/wan-2.7/reference-to-video",
     "args": {"reference_image_urls": [REF_FACE], "reference_video_url": DRIVING_VIDEO,
              "prompt": "A woman performing the same actions as in the reference video.",
              "duration": 5, "resolution": "720P"}},
    {"name": "wan-2.7 r2v (alt schema)", "endpoint": "fal-ai/wan-2.7/reference-to-video",
     "args": {"image_urls": [REF_FACE], "video_url": DRIVING_VIDEO,
              "prompt": "A woman performing the same actions as in the reference video.",
              "duration": 5}},
    {"name": "wan-2.7 i2v", "endpoint": "fal-ai/wan-2.7/image-to-video",
     "args": {"image_url": REF_FACE, "prompt": "subtle smile and head turn",
              "duration": 5}},

    # Kling O3 Standard r2v(我们用的是 pro 版,standard 可能便宜)
    {"name": "kling-o3-standard r2v", "endpoint": "fal-ai/kling-video/o3/standard/reference-to-video",
     "args": {"prompt": "@Element1 naturally showing herself.",
              "elements": [{"frontal_image_url": REF_FACE, "reference_image_urls": [REF_FACE]}],
              "duration": "5", "aspect_ratio": "9:16"}},

    # Kling 3.0 Pro i2v(最新版本,我们当前用 v2.6)
    {"name": "kling-3 pro i2v", "endpoint": "fal-ai/kling-video/v3/pro/image-to-video",
     "args": {"image_url": REF_FACE, "prompt": "subtle smile, natural pose",
              "duration": "5", "aspect_ratio": "9:16"}},

    # Kling O3 4K(原生 4K)
    {"name": "kling-o3 4k i2v", "endpoint": "fal-ai/kling-video/o3/4k/image-to-video",
     "args": {"image_url": REF_FACE, "prompt": "subtle smile, natural pose",
              "duration": "5", "aspect_ratio": "9:16"}},

    # MiniMax Hailuo 02
    {"name": "minimax hailuo-02 i2v", "endpoint": "fal-ai/minimax/hailuo-02/standard/image-to-video",
     "args": {"image_url": REF_FACE, "prompt": "subtle smile, natural pose",
              "duration": 5}},
    {"name": "minimax hailuo-02 i2v (alt)", "endpoint": "fal-ai/minimax/hailuo-02-pro/image-to-video",
     "args": {"image_url": REF_FACE, "prompt": "subtle smile, natural pose",
              "duration": 5}},

    # 阿里 Happy Horse(1080p + native audio)
    {"name": "alibaba happy-horse i2v", "endpoint": "fal-ai/alibaba/happy-horse/image-to-video",
     "args": {"image_url": REF_FACE, "prompt": "subtle smile, natural pose"}},

    # Seedance 2.0 fast i2v(我们 fast/reference-to-video 不存在,但 fast/image-to-video 可能有)
    {"name": "seedance-2.0 fast i2v", "endpoint": "fal-ai/bytedance/seedance-2.0/fast/image-to-video",
     "args": {"image_url": REF_FACE, "prompt": "subtle smile, natural pose",
              "duration": "5", "resolution": "720p"}},
]


async def probe(eng: dict, timeout: int = 600) -> dict:
    t0 = time.time()
    out = {"engine": eng["name"], "endpoint": eng["endpoint"]}
    try:
        res = await asyncio.wait_for(
            fal_client.subscribe_async(eng["endpoint"], arguments=eng["args"]),
            timeout=timeout,
        )
        out["elapsed_s"] = round(time.time() - t0, 1)
        out["ok"] = True
        out["raw_keys"] = list(res.keys()) if isinstance(res, dict) else type(res).__name__
        # 拉视频
        video = res.get("video") if isinstance(res, dict) else None
        if isinstance(video, dict) and video.get("url"):
            fname = f"{eng['name'].replace(' ', '_').replace('/', '_')}.mp4"
            local = OUT_DIR / fname
            try:
                urllib.request.urlretrieve(video["url"], str(local))
                out["fal_url"] = video["url"]
                out["local"] = str(local)
                out["size_mb"] = round(local.stat().st_size / 1e6, 2)
            except Exception:
                pass
    except asyncio.TimeoutError:
        out["ok"] = False
        out["reason"] = f"timeout_{timeout}s"
    except Exception as e:
        out["ok"] = False
        msg = str(e)
        if "not found" in msg.lower():
            out["reason"] = "endpoint_not_found"
        elif "missing" in msg.lower() or "field required" in msg.lower():
            out["reason"] = "schema_mismatch"
        elif "policy" in msg.lower() or "validation" in msg.lower():
            out["reason"] = "content_policy"
        else:
            out["reason"] = "other"
        out["error"] = msg[:500]
    return out


async def main():
    results = []
    for eng in CANDIDATES:
        print(f"\n=== {eng['name']} ({eng['endpoint']}) ===")
        r = await probe(eng)
        results.append(r)
        if r.get("ok"):
            print(f"  ✅ {r['elapsed_s']}s  size={r.get('size_mb','?')}MB")
        else:
            print(f"  ❌ {r.get('reason','?')}: {r.get('error','')[:200]}")

    out_path = Path("/root/ssp/scripts/probes/fal_new_engines_results.json")
    out_path.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    print(f"\n报告:{out_path}")

    # 总结
    print(f"\n=== 真存在且通过的端点 ===")
    for r in results:
        if r.get("ok"):
            print(f"  ✅ {r['endpoint']}  ({r['elapsed_s']}s)  → {r.get('local','?')}")


if __name__ == "__main__":
    asyncio.run(main())
