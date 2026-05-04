"""
P86 路 2.5 v3 retry — 直接复用 mask + 干净 driving + 精确 prompt 重 submit VACE
"""
import asyncio
import os
import time
import urllib.request
import subprocess
import fal_client


# 之前已就绪
DRV_CLEAN = "https://v3b.fal.media/files/b/0a98d64e/xnavBB0isCoaEym16xqtu_driving_no_ocr.mp4"
MASK_URL = "https://fal.media/files/tiger/gCw3w_oFyr6OKvJlykk5p_output0.mp4"
VTON_URL = "https://v3b.fal.media/files/b/0a98d575/dYu_HSpqG9CbLcYeW5fPM_a3be4099cd51434499f664a1ef2a8a9a.png"
MODEL_URL = "https://v3b.fal.media/files/b/0a98c674/5Ykt_GqOkGRDd1HuISlEf_tmp0i7f9q7o.jpg"

VACE = "fal-ai/wan-22-vace-fun-a14b/inpainting"

PROMPT_PRECISE = (
    "A woman wearing a black soft V-neck triangle wireless bra, "
    "thick athletic straps, simple plain black fabric, no underwire, no balconette, "
    "no lace, no decoration, photorealistic, 9:16 portrait, single take, smooth motion."
)


async def main():
    if not os.environ.get("FAL_KEY"):
        return

    print("Resubmit VACE Fun...")
    t0 = time.time()
    handler = await fal_client.submit_async(VACE, arguments={
        "video_url": DRV_CLEAN,
        "mask_video_url": MASK_URL,
        "ref_image_urls": [VTON_URL],
        "prompt": PROMPT_PRECISE,
    })
    rid = handler.request_id
    print(f"  rid={rid}")

    for _ in range(150):  # 25 min
        await asyncio.sleep(10)
        try:
            st = await fal_client.status_async(VACE, rid)
        except Exception as e:
            print(f"  poll err: {e}")
            continue
        elapsed = int(time.time() - t0)
        print(f"  [{elapsed:4d}s] {type(st).__name__}")
        from fal_client import Completed
        if isinstance(st, Completed):
            try:
                res = await fal_client.result_async(VACE, rid)
                vid = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
                print(f"  ✅ VACE OK total={elapsed}s vid={vid}")
                if vid:
                    urllib.request.urlretrieve(vid, "/tmp/seg0_inspect/r25c_vace.mp4")
                    print("  → /tmp/seg0_inspect/r25c_vace.mp4")
                    # InsightFace
                    swap_dir = "/tmp/seg0_inspect/r25c_swap"
                    os.makedirs(swap_dir, exist_ok=True)
                    urllib.request.urlretrieve(MODEL_URL, f"{swap_dir}/model.jpg")
                    p = subprocess.run([
                        "/opt/ssp/face_venv/bin/python",
                        "/opt/ssp/scripts/segment_face_swap_worker.py",
                        f"{swap_dir}/model.jpg",
                        "/tmp/seg0_inspect/r25c_vace.mp4",
                        "/tmp/seg0_inspect/r25c_FINAL.mp4",
                        "5",
                    ], capture_output=True, text=True, timeout=300)
                    print(f"  InsightFace rc={p.returncode}")
                    print(f"  → /tmp/seg0_inspect/r25c_FINAL.mp4")
                return
            except Exception as re:
                print(f"  ❌ result err: {str(re)[:300]}")
                return


asyncio.run(main())
