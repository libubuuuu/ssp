"""
P86-F probe — MediaPipe SelfieSegmentation (legacy API, 0.10.13) 在 driving 上效果。
"""
import cv2
import numpy as np
import mediapipe as mp
import time
from pathlib import Path

DRIVING = "/tmp/seg0_inspect/driving_seg0.mp4"
OUT_DIR = Path("/tmp/seg0_inspect/mediapipe_test")
OUT_DIR.mkdir(parents=True, exist_ok=True)

mp_selfie = mp.solutions.selfie_segmentation

t0 = time.time()
cap = cv2.VideoCapture(DRIVING)
fps = cap.get(cv2.CAP_PROP_FPS)
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
print(f"driving: {w}x{h} {fps:.1f}fps {total_frames} frames")

mask_writer = cv2.VideoWriter(
    str(OUT_DIR / "mask.mp4"),
    cv2.VideoWriter_fourcc(*"mp4v"),
    fps, (w, h), isColor=False,
)

with mp_selfie.SelfieSegmentation(model_selection=1) as seg:
    frame_idx = 0
    sampled = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = seg.process(rgb)
        # res.segmentation_mask: float32 0..1
        mask = (res.segmentation_mask * 255).astype(np.uint8)
        binary = (mask > 128).astype(np.uint8) * 255
        mask_writer.write(binary)

        if frame_idx in (0, 30, 60, 90, 120, 150):
            cv2.imwrite(str(OUT_DIR / f"mask_f{frame_idx:03d}.jpg"), binary)
            cv2.imwrite(str(OUT_DIR / f"orig_f{frame_idx:03d}.jpg"), frame)
            white = (binary > 200).sum() / binary.size * 100
            sampled.append((frame_idx, white))
        frame_idx += 1

cap.release()
mask_writer.release()

elapsed = time.time() - t0
print(f"\n处理 {frame_idx} 帧用时 {elapsed:.1f}s ({frame_idx/elapsed:.1f} fps)")
print(f"\n人体 mask 抽样(白=人体,黑=背景):")
for f, p in sampled:
    print(f"  frame {f}: white={p:.1f}%")
print(f"\nmask video → {OUT_DIR}/mask.mp4")
print(f"sample → {OUT_DIR}/mask_f*.jpg")
