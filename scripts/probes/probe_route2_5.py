"""
P86 路 2.5 终极组合:
  Step A: cat-vton 合成"用户模特穿用户产品"静图(单 ref,语义清晰)
  Step B: SAM2 出 T 恤 mask
  Step C: VACE Fun(driving + mask + cat-vton 单 ref + 时序英文 prompt)
  Step D: InsightFace 后置换脸(driving 模特脸 → 用户模特脸)
"""
import asyncio
import os
import time
import urllib.request
import subprocess
import fal_client


DRIVING = "https://v3b.fal.media/files/b/0a98c690/TvvnB_ZkVX5vHEz7bD8FS_seg_00.mp4"
MODEL_URL = "https://v3b.fal.media/files/b/0a98c674/5Ykt_GqOkGRDd1HuISlEf_tmp0i7f9q7o.jpg"
PRODUCT_URL = "https://v3b.fal.media/files/b/0a98c675/89Dg_cYoBjv0-DjbSLr6v_tmp_w3_2wz2.jpg"

CATVTON = "fal-ai/cat-vton"
SAM2 = "fal-ai/sam2/video"
VACE = "fal-ai/wan-22-vace-fun-a14b/inpainting"

BOX_TSHIRT = [{"x1": 80, "y1": 400, "x2": 680, "y2": 1200, "frame_index": 0}]

VACE_PROMPT_EN = (
    "The woman in the reference image lifts her white tank top up with both hands, "
    "gradually revealing the inner garment from the reference image. "
    "Preserve the original video background, living room, natural lighting, composition. "
    "Photorealistic 9:16 portrait, single take, smooth motion, remove text overlay."
)


async def submit_poll(endpoint, args, label, max_s=900):
    print(f"\n=== {label} ===")
    t0 = time.time()
    handler = await fal_client.submit_async(endpoint, arguments=args)
    rid = handler.request_id
    print(f"  rid={rid}")
    for _ in range(max_s // 10):
        await asyncio.sleep(10)
        try:
            st = await fal_client.status_async(endpoint, rid)
        except Exception as e:
            print(f"  poll err: {e}")
            continue
        elapsed = int(time.time() - t0)
        print(f"  [{elapsed:4d}s] {type(st).__name__}")
        from fal_client import Completed
        if isinstance(st, Completed):
            try:
                res = await fal_client.result_async(endpoint, rid)
                print(f"  ✅ {label} OK total={elapsed}s")
                return res
            except Exception as re:
                print(f"  ❌ result err: {str(re)[:300]}")
                return None
    return None


async def main():
    if not os.environ.get("FAL_KEY"):
        return

    # Step A: cat-vton 合成
    print("\n========== Step A: cat-vton 合成模特穿产品 ==========")
    cv = await submit_poll(
        CATVTON,
        {
            "human_image_url": MODEL_URL,
            "garment_image_url": PRODUCT_URL,
            "cloth_type": "upper",
        },
        "cat-vton",
        max_s=300,
    )
    if not cv:
        return
    vton_url = cv.get("image", {}).get("url") if isinstance(cv.get("image"), dict) else cv.get("image_url")
    if not vton_url:
        # try alternate keys
        if isinstance(cv.get("output"), dict):
            vton_url = cv["output"].get("image_url") or (cv["output"].get("image") or {}).get("url")
    if not vton_url:
        import json as J
        print(f"raw: {J.dumps(cv)[:400]}")
        return
    print(f"  vton_image_url = {vton_url}")
    urllib.request.urlretrieve(vton_url, "/tmp/seg0_inspect/r25_vton.png")

    # Step B: SAM2 mask
    print("\n========== Step B: SAM2 T-shirt mask ==========")
    sm = await submit_poll(
        SAM2,
        {"video_url": DRIVING, "box_prompts": BOX_TSHIRT},
        "SAM2",
        max_s=300,
    )
    if not sm:
        return
    mask_url = (sm.get("video") or {}).get("url") if isinstance(sm.get("video"), dict) else sm.get("video")
    print(f"  mask_url = {mask_url}")

    # Step C: VACE Fun with single ref (cat-vton 合成图)
    print("\n========== Step C: VACE Fun(mask + cat-vton 单 ref + 英文 prompt) ==========")
    vc = await submit_poll(
        VACE,
        {
            "video_url": DRIVING,
            "mask_video_url": mask_url,
            "ref_image_urls": [vton_url],   # 单 ref!cat-vton 已 hard-注入产品到模特身上
            "prompt": VACE_PROMPT_EN,
        },
        "VACE Fun (单 cat-vton ref)",
        max_s=1500,
    )
    if not vc:
        return
    vid = (vc.get("video") or {}).get("url") if isinstance(vc.get("video"), dict) else None
    print(f"\n  VACE 输出: {vid}")
    urllib.request.urlretrieve(vid, "/tmp/seg0_inspect/r25_vace.mp4")

    # Step D: InsightFace 后置换脸
    print("\n========== Step D: InsightFace 后置换脸(driving → 用户模特) ==========")
    swap_dir = "/tmp/seg0_inspect/r25_swap"
    os.makedirs(swap_dir, exist_ok=True)
    urllib.request.urlretrieve(MODEL_URL, f"{swap_dir}/model.jpg")

    p = subprocess.run([
        "/opt/ssp/face_venv/bin/python",
        "/opt/ssp/scripts/segment_face_swap_worker.py",
        f"{swap_dir}/model.jpg",
        "/tmp/seg0_inspect/r25_vace.mp4",
        "/tmp/seg0_inspect/r25_FINAL.mp4",
        "5",
    ], capture_output=True, text=True, timeout=300)
    print(f"  rc={p.returncode}")
    print(f"  stderr_tail: {p.stderr[-500:]}")
    print(f"  → /tmp/seg0_inspect/r25_FINAL.mp4")


asyncio.run(main())
