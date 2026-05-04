"""
Probe Group 1: 首帧多图融合引擎对比
跑法: FAL_KEY=xxx /opt/ssp/backend/venv/bin/python probe_first_frame.py
输出: 同目录下 first_frame_results.json + 终端 markdown 表格
"""
from __future__ import annotations
import asyncio, os, time, json, sys
from pathlib import Path

import fal_client

FIXTURE = "/opt/ssp/uploads/64402546-9ead-4263-b881-a26d8ba6d5b6/2026-04/image_0ace9f1a22a44a7e86e9388e3fdb2333.png"

PROMPT = (
    "An Asian woman in her 30s holding the product shown in the reference image, "
    "photorealistic UGC selfie style, vertical 9:16 composition, "
    "natural lighting, preserve the exact product details from reference."
)

ENGINES = [
    {
        "name": "Flux Kontext multi (P39 current)",
        "endpoint": "fal-ai/flux-pro/kontext/max/multi",
        "args_extra": {
            "aspect_ratio": "9:16",
            "guidance_scale": 3.5,
            "num_images": 1,
            "output_format": "png",
        },
    },
    {
        "name": "Nano Banana edit",
        "endpoint": "fal-ai/nano-banana-2/edit",
        "args_extra": {
            "num_images": 1,
            "output_format": "png",
        },
    },
    {
        "name": "Seedream v4 edit",
        "endpoint": "fal-ai/bytedance/seedream/v4/edit",
        "args_extra": {
            "image_size": {"width": 1024, "height": 1820},
            "num_images": 1,
            "output_format": "png",
        },
    },
]


async def upload_fixture() -> str:
    last = None
    for i in range(3):
        try:
            return await fal_client.upload_file_async(FIXTURE)
        except Exception as e:
            last = e
            print(f"  upload retry {i+1}: {e}")
            await asyncio.sleep(2 ** i)
    raise last


async def probe_one(engine: dict, img_url: str) -> dict:
    t0 = time.time()
    args = {"prompt": PROMPT, "image_urls": [img_url], **engine["args_extra"]}
    try:
        res = await fal_client.run_async(engine["endpoint"], arguments=args)
        elapsed = round(time.time() - t0, 1)
        imgs = res.get("images", []) if isinstance(res, dict) else []
        out_url = imgs[0].get("url") if imgs else None
        return {
            "engine": engine["name"],
            "endpoint": engine["endpoint"],
            "elapsed_s": elapsed,
            "url": out_url,
            "ok": bool(out_url),
            "raw_keys": list(res.keys()) if isinstance(res, dict) else None,
        }
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        return {
            "engine": engine["name"],
            "endpoint": engine["endpoint"],
            "elapsed_s": elapsed,
            "url": None,
            "ok": False,
            "error": str(e)[:300],
        }


async def main():
    if not os.environ.get("FAL_KEY"):
        print("ERROR: FAL_KEY env not set", file=sys.stderr)
        sys.exit(1)
    if not Path(FIXTURE).exists():
        print(f"ERROR: fixture not found: {FIXTURE}", file=sys.stderr)
        sys.exit(1)

    print(f"[1/2] Uploading fixture to fal storage...")
    img_url = await upload_fixture()
    print(f"  → {img_url}\n")

    print(f"[2/2] Probing {len(ENGINES)} engines (sequential, to keep fal load light)...")
    results = []
    for eng in ENGINES:
        print(f"\n=== {eng['name']} ===")
        r = await probe_one(eng, img_url)
        if r["ok"]:
            print(f"  ✅ {r['elapsed_s']}s → {r['url']}")
        else:
            print(f"  ❌ {r['elapsed_s']}s → {r.get('error', 'no images returned')}")
        results.append(r)

    out_path = Path(__file__).parent / "first_frame_results.json"
    out_path.write_text(json.dumps({
        "fixture": FIXTURE,
        "fixture_uploaded_url": img_url,
        "prompt": PROMPT,
        "results": results,
    }, ensure_ascii=False, indent=2))
    print(f"\nSaved → {out_path}\n")

    print("=" * 60)
    print("| Engine | Time | Status | Output URL |")
    print("|---|---|---|---|")
    for r in results:
        status = "✅" if r["ok"] else "❌"
        url = r.get("url") or r.get("error", "—")
        print(f"| {r['engine']} | {r['elapsed_s']}s | {status} | {url} |")


if __name__ == "__main__":
    asyncio.run(main())
