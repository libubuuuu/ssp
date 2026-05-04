"""
Probe Group 1 Round 2: 扩展候选池 + 占位图检测 + 抗错
prompt: 欧美模特 + 9:16 + 居家卧室
"""
from __future__ import annotations
import asyncio, os, time, json, sys, urllib.request
from pathlib import Path
from collections import Counter

import fal_client
from PIL import Image

FIXTURE = "/opt/ssp/uploads/64402546-9ead-4263-b881-a26d8ba6d5b6/2026-04/image_0ace9f1a22a44a7e86e9388e3fdb2333.png"

PROMPT = (
    "A Caucasian woman in her 30s holding the product shown in the reference image, "
    "in a cozy bedroom home setting with soft natural light from window and warm tones, "
    "photorealistic UGC selfie style, vertical 9:16 composition, "
    "natural lighting, preserve the exact product details from reference."
)

OUT_DIR = Path("/opt/ssp/uploads/probe-results/round2")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 候选清单 — 每个加 args_extra(schema 不一致)
ENGINES = [
    {
        "name": "Nano Banana edit (re-probe)",
        "endpoint": "fal-ai/nano-banana-2/edit",
        "args_extra": {"num_images": 1, "output_format": "png"},
        "filename": "01_nanobanana.png",
    },
    {
        "name": "Qwen-Image-Edit Plus",
        "endpoint": "fal-ai/qwen-image-edit-plus",
        "args_extra": {"num_images": 1, "output_format": "png"},
        "filename": "02_qwen.png",
    },
    {
        "name": "HiDream-I1 Edit",
        "endpoint": "fal-ai/hidream-i1/edit",
        "args_extra": {"num_images": 1, "output_format": "png"},
        "filename": "03_hidream.png",
    },
    {
        "name": "Bria Genfill",
        "endpoint": "fal-ai/bria/genfill",
        "args_extra": {"num_images": 1, "output_format": "png"},
        "filename": "04_bria.png",
    },
    {
        "name": "Ideogram v3 Edit",
        "endpoint": "fal-ai/ideogram/v3/edit",
        "args_extra": {"num_images": 1, "output_format": "png"},
        "filename": "05_ideogram.png",
    },
    {
        "name": "OmniGen v2",
        "endpoint": "fal-ai/omnigen-v2",
        "args_extra": {"num_images": 1, "output_format": "png"},
        "filename": "06_omnigen.png",
    },
    {
        "name": "Recraft v3 image-to-image",
        "endpoint": "fal-ai/recraft/v3/image-to-image",
        "args_extra": {"num_images": 1, "output_format": "png"},
        "filename": "07_recraft.png",
    },
    {
        "name": "Wan VACE 14b inpainting",
        "endpoint": "fal-ai/wan-vace-14b/inpainting",
        "args_extra": {"num_images": 1, "output_format": "png"},
        "filename": "08_wan_vace.png",
    },
]


async def upload_with_retry(local_path: str) -> str:
    last = None
    for i in range(3):
        try:
            return await fal_client.upload_file_async(local_path)
        except Exception as e:
            last = e
            await asyncio.sleep(2 ** i)
    raise last


def is_placeholder(local_png: Path) -> bool:
    """检测是否纯色/纯黑占位图(主色占比 >95% 即视为占位失败)"""
    try:
        with Image.open(local_png) as im:
            im_small = im.convert("RGB").resize((64, 64))
            colors = im_small.getcolors(maxcolors=64 * 64)
            if not colors:
                return False
            total = sum(c for c, _ in colors)
            top = max(c for c, _ in colors)
            return (top / total) > 0.95
    except Exception:
        return False


async def probe_one(eng: dict, img_url: str) -> dict:
    t0 = time.time()
    args = {"prompt": PROMPT, "image_urls": [img_url], **eng["args_extra"]}
    result: dict = {
        "engine": eng["name"],
        "endpoint": eng["endpoint"],
        "filename": eng["filename"],
    }
    try:
        res = await fal_client.run_async(eng["endpoint"], arguments=args)
        elapsed = round(time.time() - t0, 1)
        imgs = res.get("images", []) if isinstance(res, dict) else []
        out_url = imgs[0].get("url") if imgs else None
        if not out_url:
            return {**result, "elapsed_s": elapsed, "ok": False, "reason": "no_images_returned", "raw_keys": list(res.keys()) if isinstance(res, dict) else None}
        # 下载本地
        local_path = OUT_DIR / eng["filename"]
        try:
            urllib.request.urlretrieve(out_url, local_path)
        except Exception as de:
            return {**result, "elapsed_s": elapsed, "ok": False, "reason": f"download_fail: {de}", "fal_url": out_url}
        # 占位图检测
        if is_placeholder(local_path):
            return {**result, "elapsed_s": elapsed, "ok": False, "reason": "silent_fail_placeholder", "fal_url": out_url, "local_path": str(local_path)}
        return {**result, "elapsed_s": elapsed, "ok": True, "fal_url": out_url, "local_path": str(local_path), "size_kb": round(local_path.stat().st_size / 1024)}
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        msg = str(e)[:300]
        # 区分错误类型
        msg_low = msg.lower()
        if "404" in msg or "not found" in msg_low:
            kind = "endpoint_404"
        elif "422" in msg or "validation" in msg_low or "field required" in msg_low:
            kind = "schema_mismatch"
        elif "401" in msg or "403" in msg or "unauthor" in msg_low:
            kind = "auth_fail"
        elif "content_policy" in msg_low or "nsfw" in msg_low:
            kind = "nsfw_rejected"
        else:
            kind = "other_error"
        return {**result, "elapsed_s": elapsed, "ok": False, "reason": kind, "error": msg}


async def main():
    if not os.environ.get("FAL_KEY"):
        print("ERROR: FAL_KEY env not set", file=sys.stderr)
        sys.exit(1)

    print(f"[1/2] Uploading fixture...")
    img_url = await upload_with_retry(FIXTURE)
    print(f"  → {img_url}\n")

    print(f"[2/2] Probing {len(ENGINES)} engines IN PARALLEL...")
    t_start = time.time()
    results = await asyncio.gather(*[probe_one(e, img_url) for e in ENGINES])
    print(f"  parallel total: {round(time.time() - t_start, 1)}s\n")

    # 汇总
    out_path = Path(__file__).parent / "first_frame_round2_results.json"
    out_path.write_text(json.dumps({"fixture": FIXTURE, "fixture_url": img_url, "prompt": PROMPT, "results": results}, ensure_ascii=False, indent=2))

    print("=" * 80)
    print(f"{'Engine':<30} {'Time':>6} {'Status':<25} {'Output'}")
    print("-" * 80)
    for r in results:
        if r["ok"]:
            short = f"https://ailixiao.com/uploads/probe-results/round2/{r['filename']}"
            status = f"✅ ok ({r.get('size_kb', '?')}KB)"
        else:
            short = "—"
            status = f"❌ {r.get('reason', 'unknown')}"
        print(f"{r['engine']:<30} {r['elapsed_s']:>5}s {status:<25} {short}")
    print("=" * 80)

    summary = Counter(r.get("reason", "ok" if r["ok"] else "?") for r in results)
    ok_count = sum(1 for r in results if r["ok"])
    print(f"\n  PASS: {ok_count}/{len(results)}")
    print(f"  Reasons: {dict(summary)}")
    print(f"\nFull JSON → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
