"""
P86 — 用户今天上传的素材(session 29ca0659-20d)+ v2v_dual 最佳配方
  driving: 29ca0659 的 orig.mp4 取前 5s + ffmpeg delogo 去 OCR
  refs: [新模特图, 同款产品图]
  prompt: 精确产品款式 + 反向锁误导
"""
import asyncio
import os
import time
import urllib.request
import subprocess
import fal_client
import sys


# 用户今天的素材
DRIVING_LOCAL = "/opt/ssp/uploads/oral/64402546-9ead-4263-b881-a26d8ba6d5b6/29ca0659-20d/orig.mp4"
MODEL_URL = "https://v3b.fal.media/files/b/0a98d27f/Slzx6JwWUrncvBPoUMCRE_tmp2mh_ifh3.jpg"
PRODUCT_URL = "https://v3b.fal.media/files/b/0a98d281/NT667rZ8OxdR5RJPP2HtT_tmp1bq2ori1.jpg"

V2V_REF = "fal-ai/kling-video/o1/video-to-video/reference"

PROMPT = (
    "The woman in image 1 wears a white tank top. She uses both hands to firmly grasp "
    "the bottom edge of the white tank top and pull it upward, gradually exposing the "
    "exact black item shown in image 2 (black soft V-neck triangle wireless bra with "
    "thick athletic straps, plain black fabric, no underwire, no balconette, no lace). "
    "Preserve original background, lighting, composition. Single take, smooth motion."
)


async def main():
    if not os.environ.get("FAL_KEY"):
        return

    work = "/tmp/seg0_inspect/today"
    os.makedirs(work, exist_ok=True)

    # Step 0: 切前 5s + ffmpeg delogo 去文字 (位置同上轮 OCR)
    print("Step 0: 切 5s + delogo 去 OCR")
    seg5 = f"{work}/seg5_no_ocr.mp4"
    p = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", DRIVING_LOCAL,
        "-t", "5",
        "-vf", "delogo=x=80:y=460:w=560:h=140",
        seg5,
    ], capture_output=True, text=True, timeout=120)
    print(f"  rc={p.returncode}")

    # 抽首帧验证 driving 长啥样
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", seg5,
        "-frames:v", "1", "-vf", "scale=400:-1", f"{work}/seg5_check.jpg",
    ], check=True)

    # Step 1: 上传 fal
    print("Step 1: 上传干净 driving 到 fal")
    sys.path.insert(0, "/opt/ssp/backend")
    from app.services.fal_service import fal_upload_with_retry
    drv_url = await fal_upload_with_retry(seg5)
    print(f"  drv_url = {drv_url}")

    # Step 2: kling v2v reference
    print("\nStep 2: kling v2v + 双 ref + 精确 prompt")
    print(f"  refs: model_today + product_today")
    t0 = time.time()
    h = await fal_client.submit_async(V2V_REF, arguments={
        "video_url": drv_url,
        "reference_image_urls": [MODEL_URL, PRODUCT_URL],
        "prompt": PROMPT,
    })
    rid = h.request_id
    print(f"  rid={rid}")

    for _ in range(120):  # 20min
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
                print(f"\n✅ kling v2v OK total={elapsed}s vid={v}")
                if v:
                    urllib.request.urlretrieve(v, f"{work}/today_v2v.mp4")
                    print(f"  → {work}/today_v2v.mp4")
                    # InsightFace 后置换脸
                    print("\nStep 3: InsightFace 后置换脸")
                    swap_dir = f"{work}/swap"
                    os.makedirs(swap_dir, exist_ok=True)
                    urllib.request.urlretrieve(MODEL_URL, f"{swap_dir}/model.jpg")
                    sp = subprocess.run([
                        "/opt/ssp/face_venv/bin/python",
                        "/opt/ssp/scripts/segment_face_swap_worker.py",
                        f"{swap_dir}/model.jpg",
                        f"{work}/today_v2v.mp4",
                        f"{work}/today_FINAL.mp4",
                        "5",
                    ], capture_output=True, text=True, timeout=300)
                    print(f"  rc={sp.returncode}")
                    if sp.returncode != 0:
                        print(f"  stderr: {sp.stderr[-300:]}")
                    print(f"  → {work}/today_FINAL.mp4")
                return
            except Exception as re:
                print(f"  ❌ {str(re)[:300]}")
                return


asyncio.run(main())
