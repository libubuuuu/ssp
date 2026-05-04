"""
P86 路 2.5 v3 — 去 driving OCR + 精确产品款式 prompt
真因假设:driving 上 "This Balconette Bra" 文字误导 VACE → 生成 balconette 风
修法:ffmpeg 把文字区域用周围 blur/inpaint 覆盖 → 喂干净 driving 给 VACE
"""
import asyncio
import os
import time
import urllib.request
import subprocess
import fal_client


# 用户素材
DRIVING = "https://v3b.fal.media/files/b/0a98c690/TvvnB_ZkVX5vHEz7bD8FS_seg_00.mp4"
MODEL_URL = "https://v3b.fal.media/files/b/0a98c674/5Ykt_GqOkGRDd1HuISlEf_tmp0i7f9q7o.jpg"
VTON_URL = "https://v3b.fal.media/files/b/0a98d575/dYu_HSpqG9CbLcYeW5fPM_a3be4099cd51434499f664a1ef2a8a9a.png"

CATVTON = "fal-ai/cat-vton"
SAM2 = "fal-ai/sam2/video"
VACE = "fal-ai/wan-22-vace-fun-a14b/inpainting"

BOX_TSHIRT = [{"x1": 80, "y1": 400, "x2": 680, "y2": 1200, "frame_index": 0}]

# 精确产品款式描述 + 反向锁死 balconette/underwire
PROMPT_PRECISE = (
    "A woman wearing a black soft V-neck triangle wireless bra, "
    "thick athletic straps, simple plain black fabric, no underwire, no balconette, "
    "no lace, no decoration, photorealistic, 9:16 portrait, single take, smooth motion."
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

    # Step 0: 下载 driving + ffmpeg 去 OCR (delogo 滤镜)
    # OCR 在 driving 视频上的位置(720x1280):"This Balconette Bra Makes Tops Fit Better"
    # 看 driving_first_frame.jpg 文字大约在 y=480-560(中段),x 几乎全宽
    print("\n=== Step 0: ffmpeg delogo 去 driving OCR 文字 ===")
    drv_local = "/tmp/seg0_inspect/driving_orig.mp4"
    drv_clean = "/tmp/seg0_inspect/driving_no_ocr.mp4"
    urllib.request.urlretrieve(DRIVING, drv_local)
    # delogo 一个矩形覆盖文字区域:x=80-640 y=460-590 (Balconette + Makes Tops 两行字)
    p = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", drv_local,
        "-vf", "delogo=x=80:y=460:w=560:h=140",
        drv_clean,
    ], capture_output=True, text=True, timeout=120)
    print(f"  rc={p.returncode} stderr: {p.stderr[-200:] if p.stderr else 'ok'}")

    # 验证去 OCR 效果 — 抽帧
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", drv_clean,
        "-vf", "select='eq(n,0)',scale=400:-1", "-frames:v", "1",
        "/tmp/seg0_inspect/driving_no_ocr_f0.jpg",
    ], check=True)

    # 上传干净 driving 到 fal
    print("\n=== upload clean driving to fal ===")
    import sys
    sys.path.insert(0, "/opt/ssp/backend")
    from app.services.fal_service import fal_upload_with_retry
    drv_clean_url = await fal_upload_with_retry(drv_clean)
    print(f"  drv_clean_url = {drv_clean_url}")

    # Step 1: SAM2 用干净 driving
    sm = await submit_poll(
        SAM2,
        {"video_url": drv_clean_url, "box_prompts": BOX_TSHIRT},
        "SAM2 (clean driving)",
        max_s=300,
    )
    if not sm:
        return
    mask_url = (sm.get("video") or {}).get("url") if isinstance(sm.get("video"), dict) else sm.get("video")
    print(f"  mask_url = {mask_url}")

    # Step 2: VACE Fun + 干净 driving + 精确 prompt + cat-vton 单 ref
    print(f"\nprompt({len(PROMPT_PRECISE)} chars): {PROMPT_PRECISE}")
    vc = await submit_poll(
        VACE,
        {
            "video_url": drv_clean_url,
            "mask_video_url": mask_url,
            "ref_image_urls": [VTON_URL],
            "prompt": PROMPT_PRECISE,
        },
        "VACE Fun (clean driving + precise prompt)",
        max_s=1500,
    )
    if not vc:
        return
    vid = (vc.get("video") or {}).get("url") if isinstance(vc.get("video"), dict) else None
    print(f"  VACE 输出: {vid}")
    if not vid:
        return
    urllib.request.urlretrieve(vid, "/tmp/seg0_inspect/r25c_vace.mp4")

    # Step 3: InsightFace
    print("\n=== InsightFace 后置换脸 ===")
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
    print(f"  rc={p.returncode}")
    print(f"  → /tmp/seg0_inspect/r25c_FINAL.mp4")


asyncio.run(main())
