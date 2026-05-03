#!/usr/bin/env python3
"""
P46 Layer 2 (免费):用本地 InsightFace inswapper_128 ONNX 把生成视频的某帧
人脸 swap 成用户原模特脸,输出 thumbnail.jpg。

完全 CPU 推理,无 GPU 依赖,Apache-2.0 商用许可。
模型:
  - /opt/ssp/face_models/inswapper_128.onnx
  - /opt/ssp/face_models/models/buffalo_l/(face detection + recognition)

调用约定(由 backend subprocess.run 发起,FastAPI 进程不直接 import):
  python face_swap_thumbnail.py SRC_FACE TARGET_VIDEO OUT_THUMBNAIL [FRAME_RATIO]

  SRC_FACE     :用户原模特图(jpg/png 本地路径)
  TARGET_VIDEO :生成的视频(final.mp4 本地路径)
  OUT_THUMBNAIL:输出 jpg 路径
  FRAME_RATIO  :抽帧位置 0.0-1.0,默认 0.0(首帧)

退出码:
  0   成功(OUT_THUMBNAIL 存在且非空)
  1   参数错误
  2   源图无人脸
  3   目标视频无人脸
  4   推理异常

实测(2 vCPU + 3.6G RAM):
  装载:~36s(首次,模型从磁盘 mmap 入内存)
  推理:~5s/帧
  内存峰值:~2.1GB
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

import cv2
import insightface
from insightface.app import FaceAnalysis

FACE_MODELS_ROOT = "/opt/ssp/face_models"
INSWAPPER_PATH = f"{FACE_MODELS_ROOT}/inswapper_128.onnx"


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: face_swap_thumbnail.py SRC_FACE TARGET_VIDEO OUT_THUMBNAIL [FRAME_RATIO]", file=sys.stderr)
        return 1

    src_face_path = sys.argv[1]
    target_video_path = sys.argv[2]
    out_path = sys.argv[3]
    frame_ratio = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    frame_ratio = max(0.0, min(1.0, frame_ratio))

    if not Path(src_face_path).is_file():
        print(f"src_face 不存在: {src_face_path}", file=sys.stderr)
        return 1
    if not Path(target_video_path).is_file():
        print(f"target_video 不存在: {target_video_path}", file=sys.stderr)
        return 1
    if not Path(INSWAPPER_PATH).is_file():
        print(f"inswapper 模型不存在: {INSWAPPER_PATH}", file=sys.stderr)
        return 1

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    # 显式 root 让 ssp-app 用户也能加载(默认会找 ~/.insightface)
    app = FaceAnalysis(
        name="buffalo_l",
        root=FACE_MODELS_ROOT,
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=0, det_size=(640, 640))
    swapper = insightface.model_zoo.get_model(INSWAPPER_PATH, providers=["CPUExecutionProvider"])
    print(f"[load] {time.time()-t0:.1f}s", file=sys.stderr)

    # 抽 source face
    t0 = time.time()
    src_img = cv2.imread(src_face_path)
    if src_img is None:
        print(f"src_face 读取失败: {src_face_path}", file=sys.stderr)
        return 1
    src_faces = app.get(src_img)
    if not src_faces:
        print("source face 未检测到人脸", file=sys.stderr)
        return 2
    # 选最大脸(主体)
    src_face = max(src_faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    print(f"[src_detect] {time.time()-t0:.1f}s, bbox={src_face.bbox}", file=sys.stderr)

    # 抽 target frame
    t0 = time.time()
    cap = cv2.VideoCapture(target_video_path)
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    target_idx = max(0, min(n_frames - 1, int(n_frames * frame_ratio)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, target_idx)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        print(f"target frame {target_idx} 抽取失败", file=sys.stderr)
        return 1

    tgt_faces = app.get(frame)
    if not tgt_faces:
        # 视频里没人脸(可能首帧空场景),fallback 抽中点帧再试一次
        if frame_ratio != 0.5:
            cap = cv2.VideoCapture(target_video_path)
            cap.set(cv2.CAP_PROP_POS_FRAMES, n_frames // 2)
            ok, frame = cap.read()
            cap.release()
            if ok and frame is not None:
                tgt_faces = app.get(frame)
        if not tgt_faces:
            print("target video 检测不到人脸(已 fallback 中点帧),保存原帧", file=sys.stderr)
            cv2.imwrite(out_path, frame)
            return 3
    print(f"[tgt_detect] {time.time()-t0:.1f}s, {len(tgt_faces)} 张脸", file=sys.stderr)

    # swap
    t0 = time.time()
    result = frame.copy()
    for face in tgt_faces:
        result = swapper.get(result, face, src_face, paste_back=True)
    print(f"[swap] {time.time()-t0:.1f}s", file=sys.stderr)

    # 写 jpg(质量 90)
    ok = cv2.imwrite(out_path, result, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok or not Path(out_path).is_file() or Path(out_path).stat().st_size == 0:
        print("输出 thumbnail 写盘失败", file=sys.stderr)
        return 4

    print(f"OK {out_path} {Path(out_path).stat().st_size//1024}KB")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"unexpected: {e}", file=sys.stderr)
        sys.exit(4)
