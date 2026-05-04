"""
Probe Group 3: 端到端会说话视频引擎
输入:
  - character_image_url: Nano Banana 出的欧美模特持产品图
  - audio_url: 用户 oral 工作台已上传的人声音频
"""
from __future__ import annotations
import asyncio, os, time, json, sys, urllib.request, subprocess
from pathlib import Path
from collections import Counter

import fal_client

CHARACTER_IMG_URL = "https://v3b.fal.media/files/b/0a9880fd/gsr4oDYkANkwwh-bFOMl6_id1Gn9cs.png"
AUDIO_LOCAL = "/opt/ssp/uploads/oral/64402546-9ead-4263-b881-a26d8ba6d5b6/55323eb2-7fc/audio.mp3"

OUT_DIR = Path("/opt/ssp/uploads/probe-results/speaking")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 4 个端到端说话引擎(全部从生产 FalAvatarService 复用)
# schema 区分:
#   hunyuan-avatar / pixverse-lipsync → character_image_url + audio_url
#   creatify-aurora / omnihuman-v1.5  → image_url + audio_url
ENGINES = [
    {
        "name": "OmniHuman v1.5",
        "endpoint": "fal-ai/bytedance/omnihuman/v1.5",
        "img_field": "image_url",
        "filename": "01_omnihuman.mp4",
    },
    {
        "name": "Hunyuan Avatar",
        "endpoint": "fal-ai/hunyuan-avatar",
        "img_field": "character_image_url",
        "filename": "02_hunyuan_avatar.mp4",
    },
    {
        "name": "Creatify Aurora",
        "endpoint": "fal-ai/creatify/aurora",
        "img_field": "image_url",
        "filename": "03_creatify_aurora.mp4",
    },
    {
        "name": "Pixverse Lipsync",
        "endpoint": "fal-ai/pixverse/lipsync",
        "img_field": "character_image_url",
        "filename": "04_pixverse.mp4",
    },
]


def get_video_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(out.stdout.strip())
    except Exception:
        return -1.0


async def upload_with_retry(local: str) -> str:
    last = None
    for i in range(3):
        try:
            return await fal_client.upload_file_async(local)
        except Exception as e:
            last = e
            await asyncio.sleep(2 ** i)
    raise last


async def probe_one(eng: dict, audio_url: str) -> dict:
    t0 = time.time()
    args = {eng["img_field"]: CHARACTER_IMG_URL, "audio_url": audio_url}
    result = {"engine": eng["name"], "endpoint": eng["endpoint"], "filename": eng["filename"]}
    try:
        res = await fal_client.subscribe_async(eng["endpoint"], arguments=args)
        elapsed = round(time.time() - t0, 1)
        video_url = None
        if isinstance(res, dict):
            v = res.get("video")
            if isinstance(v, dict):
                video_url = v.get("url")
            elif isinstance(v, str):
                video_url = v
            else:
                vs = res.get("videos") or []
                if vs:
                    v0 = vs[0]
                    video_url = v0.get("url") if isinstance(v0, dict) else v0
        if not video_url:
            return {**result, "elapsed_s": elapsed, "ok": False, "reason": "no_video", "raw_keys": list(res.keys()) if isinstance(res, dict) else None}
        local = OUT_DIR / eng["filename"]
        try:
            urllib.request.urlretrieve(video_url, local)
        except Exception as de:
            return {**result, "elapsed_s": elapsed, "ok": False, "reason": "download_fail", "error": str(de)[:200], "fal_url": video_url}
        dur = get_video_duration(local)
        if dur <= 0:
            return {**result, "elapsed_s": elapsed, "ok": False, "reason": "invalid_video", "fal_url": video_url}
        return {
            **result,
            "elapsed_s": elapsed,
            "ok": True,
            "fal_url": video_url,
            "video_dur_s": round(dur, 1),
            "size_mb": round(local.stat().st_size / 1024 / 1024, 2),
        }
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        msg = str(e)[:300]
        ml = msg.lower()
        if "404" in msg or "not found" in ml: kind = "endpoint_404"
        elif "422" in msg or "validation" in ml: kind = "schema_mismatch"
        elif "401" in msg or "403" in msg: kind = "auth_fail"
        elif "content_policy" in ml or "nsfw" in ml: kind = "nsfw_rejected"
        elif "timeout" in ml: kind = "timeout"
        else: kind = "other_error"
        return {**result, "elapsed_s": elapsed, "ok": False, "reason": kind, "error": msg}


async def main():
    if not os.environ.get("FAL_KEY"):
        sys.exit("ERROR: FAL_KEY not set")
    if not Path(AUDIO_LOCAL).exists():
        sys.exit(f"ERROR: audio not found: {AUDIO_LOCAL}")

    print(f"[1/2] Uploading audio to fal...")
    audio_url = await upload_with_retry(AUDIO_LOCAL)
    print(f"  audio → {audio_url}")
    print(f"  image → {CHARACTER_IMG_URL}\n")

    print(f"[2/2] Probing {len(ENGINES)} speaking-video engines IN PARALLEL...")
    t_start = time.time()
    results = await asyncio.gather(*[probe_one(e, audio_url) for e in ENGINES])
    print(f"  parallel total: {round(time.time() - t_start, 1)}s\n")

    out_path = Path(__file__).parent / "speaking_results.json"
    out_path.write_text(json.dumps({"image": CHARACTER_IMG_URL, "audio": audio_url, "results": results}, ensure_ascii=False, indent=2))

    print("=" * 95)
    print(f"{'Engine':<25} {'Time':>7} {'Status':<22} {'Output'}")
    print("-" * 95)
    for r in results:
        if r["ok"]:
            short = f"https://ailixiao.com/uploads/probe-results/speaking/{r['filename']}"
            status = f"✅ {r.get('video_dur_s')}s/{r.get('size_mb')}MB"
        else:
            short = "—"
            status = f"❌ {r.get('reason', '?')}"
        print(f"{r['engine']:<25} {r['elapsed_s']:>6}s {status:<22} {short}")
    print("=" * 95)

    summary = Counter(r.get("reason", "ok" if r["ok"] else "?") for r in results)
    ok_count = sum(1 for r in results if r["ok"])
    print(f"\n  PASS: {ok_count}/{len(results)}")
    print(f"  Reasons: {dict(summary)}")
    print(f"\nFull JSON → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
