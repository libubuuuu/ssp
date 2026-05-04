"""
最后一招 — cat-vton 合成图作 kling v2v 的单 reference
之前 v2v_dual 用模特图+产品图双 ref → 产品款式 balconette 偏差
这次:cat-vton 先把"今天模特穿同款产品"硬合成 → kling 看到的是已穿好产品的模特
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

CATVTON = "fal-ai/cat-vton"
V2V_REF = "fal-ai/kling-video/o1/video-to-video/reference"

PROMPT = (
    "The woman in the reference image wears a white tank top. With both hands, "
    "she firmly grasps the bottom of the tank top and pulls it upward, revealing "
    "the exact black bra she wears underneath as shown in reference image. "
    "Living room background, natural lighting, single take, photorealistic 9:16 portrait."
)


async def main():
    if not os.environ.get("FAL_KEY"):
        return

    work = "/tmp/seg0_inspect/cv_kling"
    os.makedirs(work, exist_ok=True)

    # Step A: cat-vton 用今天模特 + 同款产品
    print("Step A: cat-vton(今天模特 + 同款产品)")
    t0 = time.time()
    h = await fal_client.submit_async(CATVTON, arguments={
        "human_image_url": MODEL_URL,
        "garment_image_url": PRODUCT_URL,
        "cloth_type": "upper",
    })
    rid = h.request_id
    print(f"  rid={rid}")
    vton_url = None
    for _ in range(60):
        await asyncio.sleep(5)
        try:
            s = await fal_client.status_async(CATVTON, rid)
        except Exception as e:
            continue
        elapsed = int(time.time()-t0)
        print(f"  [{elapsed:3d}s] {type(s).__name__}")
        from fal_client import Completed
        if isinstance(s, Completed):
            res = await fal_client.result_async(CATVTON, rid)
            vton_url = res.get("image", {}).get("url") if isinstance(res.get("image"), dict) else None
            if not vton_url and isinstance(res.get("output"), dict):
                vton_url = (res["output"].get("image") or {}).get("url") or res["output"].get("image_url")
            print(f"  ✅ cat-vton vton_url={vton_url}")
            break
    if not vton_url:
        print("❌ cat-vton failed")
        return
    urllib.request.urlretrieve(vton_url, f"{work}/vton.png")

    # Step B: 切前 5s driving(不动 OCR)
    print("\nStep B: 切 driving 前 5s")
    seg5 = f"{work}/seg5.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", DRIVING_LOCAL,
        "-t", "5", "-c", "copy", seg5,
    ], check=True, timeout=60)
    sys.path.insert(0, "/opt/ssp/backend")
    from app.services.fal_service import fal_upload_with_retry
    drv_url = await fal_upload_with_retry(seg5)
    print(f"  drv_url={drv_url}")

    # Step C: kling v2v reference + 单 ref(cat-vton vton 图)
    print(f"\nStep C: kling v2v reference + 单 ref(cat-vton vton)")
    print(f"  prompt: {PROMPT}")
    t0 = time.time()
    h = await fal_client.submit_async(V2V_REF, arguments={
        "video_url": drv_url,
        "reference_image_urls": [vton_url],  # 单 ref!
        "prompt": PROMPT,
    })
    rid = h.request_id
    print(f"  rid={rid}")

    for _ in range(120):
        await asyncio.sleep(10)
        try:
            s = await fal_client.status_async(V2V_REF, rid)
        except Exception as e:
            continue
        elapsed = int(time.time()-t0)
        print(f"  [{elapsed:4d}s] {type(s).__name__}")
        from fal_client import Completed
        if isinstance(s, Completed):
            try:
                res = await fal_client.result_async(V2V_REF, rid)
                v = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
                print(f"\n✅ kling v2v OK total={elapsed}s vid={v}")
                if v:
                    urllib.request.urlretrieve(v, f"{work}/cv_kling_FINAL.mp4")
                    print(f"  → {work}/cv_kling_FINAL.mp4")
                return
            except Exception as re:
                print(f"❌ {str(re)[:300]}")
                return


asyncio.run(main())
