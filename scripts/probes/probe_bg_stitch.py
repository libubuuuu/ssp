"""
P86-F probe — 背景 stitch:swapped 人体 + driving 背景

输入:swapped.mp4(catvton-pixverse 输出) + driving.mp4(原视频)
处理:
  1. mediapipe 跑 swapped → 人体 mask 视频(白=人,黑=背景)
  2. ffmpeg overlay 用 mask blend:swapped × mask + driving × (1-mask)
  3. 输出 stitched.mp4
"""
import cv2
import numpy as np
import mediapipe as mp
import subprocess
import time
from pathlib import Path

DRIVING = "/tmp/seg0_inspect/driving_seg0.mp4"  # 原 5s 段(只有第一段用作 stitch 试验)
# 用户的 swapped.mp4(prod uploads 路径)
SWAPPED_LOCAL = "/tmp/seg0_inspect/swapped_29ca0659.mp4"
SWAPPED_PROD = "/opt/ssp/uploads/oral/64402546-9ead-4263-b881-a26d8ba6d5b6/29ca0659-20d/swapped.mp4"

OUT_DIR = Path("/tmp/seg0_inspect/stitch_test")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 1) 拷贝 swapped 来本地
import shutil
shutil.copy(SWAPPED_PROD, SWAPPED_LOCAL)
print(f"swapped: {SWAPPED_LOCAL}")

# 2) ffmpeg 把 swapped 切第一段 5s(driving 也是 5s)以便对齐
swap_5s = OUT_DIR / "swap_5s.mp4"
subprocess.run([
    "ffmpeg", "-y", "-loglevel", "error",
    "-i", SWAPPED_LOCAL, "-t", "5",
    "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
    str(swap_5s),
], check=True)

# 3) mediapipe 跑 swap_5s 出 mask
mp_selfie = mp.solutions.selfie_segmentation
cap = cv2.VideoCapture(str(swap_5s))
fps = cap.get(cv2.CAP_PROP_FPS)
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
print(f"swap_5s: {w}x{h} {fps:.1f}fps")

mask_path = OUT_DIR / "swap_mask.mp4"
mask_writer = cv2.VideoWriter(
    str(mask_path), cv2.VideoWriter_fourcc(*"mp4v"),
    fps, (w, h), isColor=False,
)

t0 = time.time()
with mp_selfie.SelfieSegmentation(model_selection=1) as seg:
    fi = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = seg.process(rgb)
        mask = (res.segmentation_mask * 255).astype(np.uint8)
        # 羽化:Gaussian blur 让边缘平滑
        mask_smooth = cv2.GaussianBlur(mask, (15, 15), 0)
        mask_writer.write(mask_smooth)
        fi += 1
cap.release()
mask_writer.release()
print(f"mask 生成 {fi} 帧 用时 {time.time()-t0:.1f}s")

# 4) ffmpeg compose:[0:swap] [1:driving] [2:mask] alpha blend
# 思路:mask 当成 alpha,把 swap 当 fg overlay 到 driving
out = OUT_DIR / "stitched.mp4"
# resize driving 也到 720x1280 保险
drv_5s = OUT_DIR / "drv_5s.mp4"
subprocess.run([
    "ffmpeg", "-y", "-loglevel", "error",
    "-i", DRIVING, "-t", "5",
    "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
    "-r", f"{fps:.0f}",
    str(drv_5s),
], check=True)

# 用 alphamerge:把 mask 注入 swap 的 alpha 通道
# 然后 overlay 到 driving
cmd = [
    "ffmpeg", "-y", "-loglevel", "error",
    "-i", str(swap_5s),    # 0
    "-i", str(drv_5s),     # 1
    "-i", str(mask_path),  # 2 mask (白=人 = alpha)
    "-filter_complex",
    "[2:v]format=gray[mask];"
    "[0:v][mask]alphamerge[fg];"
    "[1:v][fg]overlay=format=auto[out]",
    "-map", "[out]",
    "-c:v", "libx264", "-pix_fmt", "yuv420p",
    str(out),
]
print(" ".join(cmd))
subprocess.run(cmd, check=True)
print(f"\n✅ stitched → {out}")
print(f"   ffprobe:")
subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                "stream=width,height,duration,nb_frames", "-of", "default", str(out)])
