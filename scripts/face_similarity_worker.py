#!/usr/bin/env python3
"""
P48-B InsightFace face similarity 评分 worker

backend 用法:Best-of-N 同段并发出 3 个候选视频,用 face similarity 选最像
  那段(vs 用户模特图)。

调用约定:
  python face_similarity_worker.py SRC_FACE TARGET_VIDEO [FRAME_RATIO]

  SRC_FACE     :用户原模特图(jpg/png)
  TARGET_VIDEO :候选段视频(mp4)
  FRAME_RATIO  :抽帧位置(默认 0.5,中点帧)

输出(stdout):
  SCORE=<float 0-1>   人脸余弦相似度
  -1                  目标视频无人脸 / 错误

退出码:
  0  成功(SCORE 输出有效)
  1  参数 / 文件错误
  2  src 无人脸
  3  target 无人脸

实测:
  ~7-10s/调用(模型装载 + 检测 + 计算)
"""
from __future__ import annotations
import os
import sys
import numpy as np
from pathlib import Path

os.environ.setdefault("INSIGHTFACE_HOME", "/opt/ssp/face_models")

import cv2
from insightface.app import FaceAnalysis

FACE_MODELS_ROOT = "/opt/ssp/face_models"


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: face_similarity_worker.py SRC_FACE TARGET_VIDEO [FRAME_RATIO]", file=sys.stderr)
        print("-1")
        return 1
    src_face_path = sys.argv[1]
    in_video = sys.argv[2]
    ratio = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
    ratio = max(0.0, min(1.0, ratio))

    if not Path(src_face_path).is_file() or not Path(in_video).is_file():
        print("-1")
        return 1

    app = FaceAnalysis(name="buffalo_l", root=FACE_MODELS_ROOT, providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))

    src = cv2.imread(src_face_path)
    if src is None:
        print("-1")
        return 1
    src_faces = app.get(src)
    if not src_faces:
        print("-1")
        return 2
    src_face = max(src_faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))

    cap = cv2.VideoCapture(in_video)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(n - 1, int(n * ratio))))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        print("-1")
        return 1
    tgt_faces = app.get(frame)
    if not tgt_faces:
        print("-1")
        return 3
    tgt_face = max(tgt_faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))

    # 余弦相似度(insightface embedding 已 L2-normalized)
    emb1 = src_face.normed_embedding
    emb2 = tgt_face.normed_embedding
    sim = float(np.dot(emb1, emb2))
    print(f"SCORE={sim:.4f}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("-1", file=sys.stderr)
        sys.exit(4)
