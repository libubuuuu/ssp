"""
Probe Group 7:本地 InsightFace inswapper_128 ONNX face swap

目标:在生产服务器(2 vCPU + 3.6G RAM 无 GPU)实测:
  1. 装好的 insightface + onnxruntime 能否跑通
  2. 单张 face swap 真实 CPU 时间(< 5s 才有工程价值)
  3. 内存峰值(< 1.5GB 才不影响 backend)
  4. 输出质量 verify(尺寸 / 文件大小 / 跑通完整链路)

输入素材:
  source = 用户原模特图(从 14c390bb session 抽 1 帧)
  target = pixverse 出片首帧(P44 probe 已生成)

INSWAPPER_PATH = /opt/ssp/face_models/inswapper_128.onnx (Apache-2.0,可商用)
"""
from __future__ import annotations
import time
import os
import resource
from pathlib import Path
import cv2
import numpy as np
import insightface
from insightface.app import FaceAnalysis

INSWAPPER = "/opt/ssp/face_models/inswapper_128.onnx"
SOURCE_FACE = "/opt/ssp/uploads/probe-results/sd-enterprise-pixverse/ref_frame.jpg"  # 用户模特图
TARGET_VIDEO_FRAME = "/opt/ssp/uploads/probe-results/sd-enterprise-pixverse/pixverse-swap.mp4"  # 出片视频

OUT_DIR = Path("/opt/ssp/uploads/probe-results/local-inswapper")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def get_mem_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main():
    print(f"[t=0] mem={get_mem_mb():.0f}MB — 启动")

    # 1) 抽出片视频首帧、中帧、末帧
    cap = cv2.VideoCapture(TARGET_VIDEO_FRAME)
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"target video: {n_frames} frames, {fps:.1f} fps")
    test_indices = [0, n_frames // 2, max(0, n_frames - 1)]
    target_frames = []
    for idx in test_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok:
            target_frames.append((idx, frame))
    cap.release()
    print(f"[t={time.time():.1f}] 抽 {len(target_frames)} 帧完成")

    # 2) 加载 face analysis(检测+对齐)
    t0 = time.time()
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))  # CPU
    print(f"[t={time.time()-t0:.1f}s] FaceAnalysis 装载完成 mem={get_mem_mb():.0f}MB")

    # 3) 加载 inswapper
    t0 = time.time()
    swapper = insightface.model_zoo.get_model(INSWAPPER, providers=["CPUExecutionProvider"])
    print(f"[t={time.time()-t0:.1f}s] inswapper 装载完成 mem={get_mem_mb():.0f}MB")

    # 4) 处理 source face(用户模特图)
    src_img = cv2.imread(SOURCE_FACE)
    src_faces = app.get(src_img)
    if not src_faces:
        print(f"❌ source face 检测失败 - 图片可能没有人脸:{SOURCE_FACE}")
        return
    src_face = src_faces[0]
    print(f"[t={time.time():.1f}] source face 检测 OK,bbox={src_face.bbox}")

    # 5) 逐帧 swap
    timings = []
    for idx, frame in target_frames:
        t0 = time.time()
        tgt_faces = app.get(frame)
        if not tgt_faces:
            print(f"  frame {idx}: 无人脸,跳过")
            continue
        result = frame.copy()
        for face in tgt_faces:
            result = swapper.get(result, face, src_face, paste_back=True)
        elapsed = time.time() - t0
        timings.append(elapsed)
        out_path = OUT_DIR / f"swapped_frame_{idx:04d}.jpg"
        cv2.imwrite(str(out_path), result)
        print(f"  frame {idx}: swap {elapsed:.2f}s, {len(tgt_faces)} 张脸, → {out_path.name} ({out_path.stat().st_size//1024}KB)")

    # 6) 总结
    if timings:
        avg = sum(timings) / len(timings)
        print(f"\n=== 真实 CPU 性能 ===")
        print(f"avg per frame: {avg:.2f}s")
        print(f"模型 + 推理峰值内存:{get_mem_mb():.0f}MB")
        print(f"\n推算:")
        print(f"  20s 视频 25fps = 500 帧 × {avg:.2f}s = {500*avg/60:.1f} min")
        print(f"  60s 视频 25fps = 1500 帧 × {avg:.2f}s = {1500*avg/60:.1f} min")
        print(f"  抽稀 1 帧/秒 60s 视频 = 60 帧 × {avg:.2f}s = {60*avg:.0f}s ({60*avg/60:.1f} min)")
        print(f"  仅首尾 2 帧 = 2 × {avg:.2f}s = {2*avg:.1f}s")


if __name__ == "__main__":
    main()
