"""
回到 v2v_dual 干净配方 + 你今天素材
唯一变量改动:prompt 改正向描述产品款式(不反向锁)
- driving:今天上传的(取前 5s,不动 OCR)
- refs:今天上传的 [模特图, 产品图]
- 端点:kling v2v reference(同 v2v_dual)
- 不加 InsightFace
"""
import asyncio
import os
import time
import urllib.request
import subprocess
import fal_client
import sys


DRIVING_LOCAL = "/opt/ssp/uploads/oral/64402546-9ead-4263-b881-a26d8ba6d5b6/29ca0659-20d/orig.mp4"
MODEL_URL = "https://v3b.fal.media/files/b/0a98d27f/Slzx6JwWUrncvBPoUMCRE_tmp2mh_ifh3.jpg"
PRODUCT_URL = "https://v3b.fal.media/files/b/0a98d281/NT667rZ8OxdR5RJPP2HtT_tmp1bq2ori1.jpg"

V2V_REF = "fal-ai/kling-video/o1/video-to-video/reference"

# 正向描述产品款式 — 不反向锁(no X 在 diffusion 里反而引入 X bias)
PROMPT = (
    "The woman in image 1 wears a white tank top. With both hands, she firmly grasps "
    "the bottom of her tank top and pulls it upward, revealing her plain black "
    "soft-cup V-neck wireless bra (the exact bra in image 2: simple flat black fabric, "
    "deep V cleavage, wide athletic straps). "
    "Living room background, natural lighting, single take, photorealistic 9:16 portrait."
)


async def main():
    if not os.environ.get("FAL_KEY"):
        return

    work = "/tmp/seg0_inspect/today_v2v_only"
    os.makedirs(work, exist_ok=True)

    print("Step 0: 切前 5s(不动 OCR)")
    seg5 = f"{work}/seg5.mp4"
    p = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", DRIVING_LOCAL,
        "-t", "5", "-c", "copy", seg5,
    ], capture_output=True, text=True, timeout=60)
    print(f"  rc={p.returncode}")

    print("Step 1: 上传 driving")
    sys.path.insert(0, "/opt/ssp/backend")
    from app.services.fal_service import fal_upload_with_retry
    drv_url = await fal_upload_with_retry(seg5)
    print(f"  drv_url={drv_url}")

    print(f"\nStep 2: kling v2v reference")
    print(f"  prompt({len(PROMPT)} chars):\n{PROMPT}\n")
    t0 = time.time()
    h = await fal_client.submit_async(V2V_REF, arguments={
        "video_url": drv_url,
        "reference_image_urls": [MODEL_URL, PRODUCT_URL],
        "prompt": PROMPT,
    })
    rid = h.request_id
    print(f"  rid={rid}")

    for _ in range(120):
        await asyncio.sleep(10)
        try:
            s = await fal_client.status_async(V2V_REF, rid)
        except Exception as e:
            print(f"  poll err {e}")
            continue
        elapsed = int(time.time()-t0)
        print(f"  [{elapsed:4d}s] {type(s).__name__}")
        from fal_client import Completed
        if isinstance(s, Completed):
            try:
                res = await fal_client.result_async(V2V_REF, rid)
                v = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
                print(f"\n✅ total={elapsed}s vid={v}")
                if v:
                    urllib.request.urlretrieve(v, f"{work}/today_v2v_only.mp4")
                    print(f"  → {work}/today_v2v_only.mp4")
                return
            except Exception as re:
                print(f"  ❌ {str(re)[:300]}")
                return


asyncio.run(main())
