"""
P86-2 路 2 v2 — SAM2 box 精准 T 恤 + VACE Fun 英文 prompt + multi-ref
+ P85 InsightFace 后置换脸(让模特身份从 driving 模特换成用户模特图)
"""
import asyncio
import os
import time
import fal_client
import json
import urllib.request
import subprocess
import sys


DRIVING = "https://v3b.fal.media/files/b/0a98c690/TvvnB_ZkVX5vHEz7bD8FS_seg_00.mp4"
MODEL_URL = "https://v3b.fal.media/files/b/0a98c674/5Ykt_GqOkGRDd1HuISlEf_tmp0i7f9q7o.jpg"
PRODUCT_URL = "https://v3b.fal.media/files/b/0a98c675/89Dg_cYoBjv0-DjbSLr6v_tmp_w3_2wz2.jpg"

SAM2 = "fal-ai/sam2/video"
VACE = "fal-ai/wan-22-vace-fun-a14b/inpainting"

# 720x1280 driving:T 恤主体 y=400-1200(覆盖整件白 T 不含脸/手举)
BOX_TSHIRT = [{
    "x1": 80, "y1": 400, "x2": 680, "y2": 1200,
    "frame_index": 0,
}]

# VACE 主体 prompt 用英文(memory P74 verified:中文 NSFW 拒,英文+中性词通过)
VACE_PROMPT_EN = (
    "The woman in image 1 wears a white tank top. She slowly lifts up the tank top "
    "with both hands, gradually revealing the black lace inner garment shown in image 2. "
    "Preserve the original video background, living room, natural lighting, and composition. "
    "Photorealistic 9:16 portrait UGC video, single continuous take, smooth motion."
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
    print(f"  ⏱ {label} timeout")
    return None


async def main():
    if not os.environ.get("FAL_KEY"):
        return

    # Step 1: SAM2
    print(f"\n========== SAM2 box(精准 T 恤,frame=0) ==========")
    print(f"box: {BOX_TSHIRT}")
    sam_res = await submit_poll(
        SAM2,
        {"video_url": DRIVING, "box_prompts": BOX_TSHIRT},
        "SAM2 T-shirt box",
        max_s=300,
    )
    if not sam_res:
        return
    mask_url = (sam_res.get("video") or {}).get("url") if isinstance(sam_res.get("video"), dict) else sam_res.get("video")
    if not mask_url:
        print(f"  raw result: {json.dumps(sam_res, ensure_ascii=False)[:400]}")
        return
    print(f"  mask_url = {mask_url}")

    # Inspect mask
    print(f"\n下载 mask 抽样分析...")
    urllib.request.urlretrieve(mask_url, "/tmp/seg0_inspect/sam2_v2_mask.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", "/tmp/seg0_inspect/sam2_v2_mask.mp4",
        "-vf", "fps=1,scale=200:-1",
        "/tmp/seg0_inspect/sam2v2_t_%d.jpg",
    ], check=True)
    sys.path.insert(0, "/opt/ssp/face_venv/lib/python3.10/site-packages")
    from PIL import Image
    import numpy as np
    import glob
    print(f"  mask 各帧白像素占比(应是 T 恤区域 ~30-60%):")
    for f in sorted(glob.glob("/tmp/seg0_inspect/sam2v2_t_*.jpg")):
        img = np.array(Image.open(f).convert("L"))
        white = (img > 200).sum() / img.size * 100
        black = (img < 50).sum() / img.size * 100
        print(f"    {f}: white={white:.1f}% black={black:.1f}%")

    # Step 2: VACE Fun
    print(f"\n========== VACE Fun(driving + mask + 模特+产品 ref + 英文 prompt) ==========")
    print(f"prompt({len(VACE_PROMPT_EN)} chars): {VACE_PROMPT_EN[:200]}...")
    vace_res = await submit_poll(
        VACE,
        {
            "video_url": DRIVING,
            "mask_video_url": mask_url,
            "ref_image_urls": [MODEL_URL, PRODUCT_URL],
            "prompt": VACE_PROMPT_EN,
        },
        "VACE Fun (multi-ref + 英文 prompt)",
        max_s=1500,
    )
    if not vace_res:
        return
    vid = (vace_res.get("video") or {}).get("url") if isinstance(vace_res.get("video"), dict) else None
    print(f"\n🎯 VACE 输出:{vid}")
    # 拷给本地
    if vid:
        urllib.request.urlretrieve(vid, "/tmp/seg0_inspect/v3_vace_final.mp4")
        print(f"  → /tmp/seg0_inspect/v3_vace_final.mp4")


asyncio.run(main())
