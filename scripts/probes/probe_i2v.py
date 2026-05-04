"""
Probe Group 2: i2v 视频引擎对比
输入: Nano Banana 出的首帧(欧美模特+内衣产品+居家卧室)
prompt: 模特微笑展示产品给镜头,UGC 带货风
"""
from __future__ import annotations
import asyncio, os, time, json, sys, urllib.request, subprocess
from pathlib import Path
from collections import Counter

import fal_client

# Nano Banana round2 生成的首帧
INPUT_IMG_URL = "https://v3b.fal.media/files/b/0a9880fd/gsr4oDYkANkwwh-bFOMl6_id1Gn9cs.png"

PROMPT = (
    "The woman gently smiles and turns her head slightly while showing the product to camera. "
    "UGC vlog selfie style, natural micro-movements, advertisement video. "
    "Preserve the product's exact details and the model's identity throughout."
)

OUT_DIR = Path("/opt/ssp/uploads/probe-results/i2v")
OUT_DIR.mkdir(parents=True, exist_ok=True)

ENGINES = [
    {
        "name": "Seedance v1.5/pro i2v (生产)",
        "endpoint": "fal-ai/bytedance/seedance/v1.5/pro/image-to-video",
        "args_extra": {"duration": "5", "aspect_ratio": "9:16", "resolution": "720p"},
        "filename": "01_seedance_v15_pro.mp4",
    },
    {
        "name": "Kling o3 standard i2v (生产)",
        "endpoint": "fal-ai/kling-video/o3/standard/image-to-video",
        "args_extra": {"duration": "5", "aspect_ratio": "9:16"},
        "filename": "02_kling_o3.mp4",
    },
    {
        "name": "LTX-2.3 distilled i2v",
        "endpoint": "fal-ai/ltx-2.3-22b/distilled/image-to-video",
        "args_extra": {"resolution": "720p"},
        "filename": "03_ltx23_distilled.mp4",
    },
    {
        "name": "Kling 1.6 standard i2v",
        "endpoint": "fal-ai/kling-video/v1.6/standard/image-to-video",
        "args_extra": {"duration": "5", "aspect_ratio": "9:16"},
        "filename": "04_kling_v16.mp4",
    },
    {
        "name": "Pika v2.0 i2v",
        "endpoint": "fal-ai/pika/v2.0/image-to-video",
        "args_extra": {"duration": 5, "aspect_ratio": "9:16"},
        "filename": "05_pika.mp4",
    },
    {
        "name": "Luma Dream Machine i2v",
        "endpoint": "fal-ai/luma-dream-machine/image-to-video",
        "args_extra": {"aspect_ratio": "9:16"},
        "filename": "06_luma.mp4",
    },
    {
        "name": "Wan 2.1 14b i2v",
        "endpoint": "fal-ai/wan-2.1-14b/image-to-video",
        "args_extra": {"resolution": "720p", "aspect_ratio": "9:16"},
        "filename": "07_wan21.mp4",
    },
    {
        "name": "Hunyuan video i2v",
        "endpoint": "fal-ai/hunyuan-video/image-to-video",
        "args_extra": {"aspect_ratio": "9:16"},
        "filename": "08_hunyuan.mp4",
    },
    {
        "name": "Seedance v1.5/fast i2v",
        "endpoint": "fal-ai/bytedance/seedance/v1.5/fast/image-to-video",
        "args_extra": {"duration": "5", "aspect_ratio": "9:16", "resolution": "720p"},
        "filename": "09_seedance_v15_fast.mp4",
    },
]


def get_video_duration(path: Path) -> float:
    """ffprobe 拿视频时长,失败返 -1"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(out.stdout.strip())
    except Exception:
        return -1.0


async def probe_one(eng: dict) -> dict:
    t0 = time.time()
    args = {"prompt": PROMPT, "image_url": INPUT_IMG_URL, **eng["args_extra"]}
    result: dict = {"engine": eng["name"], "endpoint": eng["endpoint"], "filename": eng["filename"]}
    try:
        # 用 subscribe_async 能更稳拿到结果(P36 注释说 status_async 有 SDK bug)
        res = await fal_client.subscribe_async(eng["endpoint"], arguments=args)
        elapsed = round(time.time() - t0, 1)
        # video 输出字段在不同引擎不一样:video.url / video / videos[0].url ...
        video_url = None
        if isinstance(res, dict):
            if "video" in res and isinstance(res["video"], dict):
                video_url = res["video"].get("url")
            elif "video" in res and isinstance(res["video"], str):
                video_url = res["video"]
            elif "videos" in res and res["videos"]:
                v0 = res["videos"][0]
                video_url = v0.get("url") if isinstance(v0, dict) else v0
            elif "url" in res:
                video_url = res["url"]
        if not video_url:
            return {**result, "elapsed_s": elapsed, "ok": False, "reason": "no_video_in_response", "raw_keys": list(res.keys()) if isinstance(res, dict) else None}

        local_path = OUT_DIR / eng["filename"]
        try:
            urllib.request.urlretrieve(video_url, local_path)
        except Exception as de:
            return {**result, "elapsed_s": elapsed, "ok": False, "reason": f"download_fail", "fal_url": video_url, "error": str(de)[:200]}

        dur = get_video_duration(local_path)
        if dur <= 0:
            return {**result, "elapsed_s": elapsed, "ok": False, "reason": "invalid_video", "fal_url": video_url, "local_path": str(local_path)}

        return {
            **result,
            "elapsed_s": elapsed,
            "ok": True,
            "fal_url": video_url,
            "local_path": str(local_path),
            "video_dur_s": round(dur, 1),
            "size_mb": round(local_path.stat().st_size / 1024 / 1024, 2),
        }
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        msg = str(e)[:300]
        msg_low = msg.lower()
        if "404" in msg or "not found" in msg_low:
            kind = "endpoint_404"
        elif "422" in msg or "validation" in msg_low or "field required" in msg_low:
            kind = "schema_mismatch"
        elif "401" in msg or "403" in msg:
            kind = "auth_fail"
        elif "content_policy" in msg_low or "nsfw" in msg_low:
            kind = "nsfw_rejected"
        elif "timeout" in msg_low:
            kind = "timeout"
        else:
            kind = "other_error"
        return {**result, "elapsed_s": elapsed, "ok": False, "reason": kind, "error": msg}


async def main():
    if not os.environ.get("FAL_KEY"):
        sys.exit("ERROR: FAL_KEY not set")

    print(f"[1/1] Probing {len(ENGINES)} i2v engines IN PARALLEL...")
    print(f"  Input: {INPUT_IMG_URL}")
    t_start = time.time()
    results = await asyncio.gather(*[probe_one(e) for e in ENGINES])
    print(f"  parallel total: {round(time.time() - t_start, 1)}s\n")

    out_path = Path(__file__).parent / "i2v_results.json"
    out_path.write_text(json.dumps({"input_image": INPUT_IMG_URL, "prompt": PROMPT, "results": results}, ensure_ascii=False, indent=2))

    print("=" * 95)
    print(f"{'Engine':<32} {'Time':>7} {'Status':<22} {'Output'}")
    print("-" * 95)
    for r in results:
        if r["ok"]:
            short = f"https://ailixiao.com/uploads/probe-results/i2v/{r['filename']}"
            status = f"✅ {r.get('video_dur_s')}s/{r.get('size_mb')}MB"
        else:
            short = "—"
            status = f"❌ {r.get('reason', '?')}"
        print(f"{r['engine']:<32} {r['elapsed_s']:>6}s {status:<22} {short}")
    print("=" * 95)

    summary = Counter(r.get("reason", "ok" if r["ok"] else "?") for r in results)
    ok_count = sum(1 for r in results if r["ok"])
    print(f"\n  PASS: {ok_count}/{len(results)}")
    print(f"  Reasons: {dict(summary)}")
    print(f"\nFull JSON → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
