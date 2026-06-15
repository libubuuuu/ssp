"""人脸打码 —— SCRFD 检测 + 光流跟运动 + 椭圆紧贴脸打码（视频复刻替换人脸用）。

- 检测器：insightface SCRFD（对侧脸/角度远强于 YuNet），多尺度自适应（640→320）。
- detect_every>1：每 N 帧检测一次（CPU 提速），非检测帧用 Lucas-Kanade 光流把上一框跟着运动平移。
- 椭圆紧贴脸：两侧不外扩、顶/底各内收 5% → 不超脸、不超下巴、两侧不溢出。
- 返回 dict：{ok, any_face, frames, detected_frames, masked_frames, coverage, out}；any_face=False 用原视频。

性能（服务器 2vCPU 实测，496×864）：det=3 ≈145ms/帧、覆盖95%、峰值~800MB;det=1 ≈397ms/帧。默认 det=3。
"""
import math
import os
import subprocess

import cv2
import numpy as np

from app.services.logger import log_info, log_error

_scrfd_app = None
_east_net = None
_EAST_PATH = os.path.join(os.path.dirname(__file__), "models", "frozen_east_text_detection.pb")


def _get_east():
    """懒加载 EAST 文字检测(cv2.dnn,不装新库)。模型缺失返 None → 不保字幕(只打码)。"""
    global _east_net
    if _east_net is None:
        if not os.path.exists(_EAST_PATH):
            log_error("[FACE-PRO] EAST 模型缺失,字幕保护跳过(只打码)")
            _east_net = False
        else:
            try:
                _east_net = cv2.dnn.readNet(_EAST_PATH)
                log_info("[FACE-PRO] EAST 文字检测就绪")
            except Exception as e:
                log_error(f"[FACE-PRO] EAST 加载失败: {e}")
                _east_net = False
    return _east_net or None


def _decode_east(scores, geometry, score_thr):
    rects, confs = [], []
    h, w = scores.shape[2], scores.shape[3]
    for y in range(h):
        s = scores[0, 0, y]
        x0, x1, x2, x3 = geometry[0, 0, y], geometry[0, 1, y], geometry[0, 2, y], geometry[0, 3, y]
        ang = geometry[0, 4, y]
        for x in range(w):
            sc = s[x]
            if sc < score_thr:
                continue
            ox, oy = x * 4.0, y * 4.0
            a = ang[x]
            cosA, sinA = math.cos(a), math.sin(a)
            bh, bw = x0[x] + x2[x], x1[x] + x3[x]
            endX = int(ox + cosA * x1[x] + sinA * x2[x])
            endY = int(oy - sinA * x1[x] + cosA * x2[x])
            rects.append((int(endX - bw), int(endY - bh), endX, endY))
            confs.append(float(sc))
    return rects, confs


def _detect_text_boxes(frame, score_thr=0.5, nms_thr=0.4, cap_side=640):
    """EAST 文字检测,返回原图坐标文字框 [[x1,y1,x2,y2],...]。无模型/失败返 []。"""
    net = _get_east()
    if net is None:
        return []
    H, W = frame.shape[:2]
    scale = min(1.0, cap_side / float(max(W, H)))
    tW = max(32, (int(W * scale) // 32) * 32)
    tH = max(32, (int(H * scale) // 32) * 32)
    rW, rH = W / float(tW), H / float(tH)
    try:
        blob = cv2.dnn.blobFromImage(frame, 1.0, (tW, tH), (123.68, 116.78, 103.94), True, False)
        net.setInput(blob)
        scores, geometry = net.forward(["feature_fusion/Conv_7/Sigmoid", "feature_fusion/concat_3"])
        rects, confs = _decode_east(scores, geometry, score_thr)
        if not rects:
            return []
        xywh = [[x1, y1, x2 - x1, y2 - y1] for (x1, y1, x2, y2) in rects]
        idxs = cv2.dnn.NMSBoxes(xywh, confs, score_thr, nms_thr)
        out = []
        for i in np.array(idxs).flatten():
            x1, y1, x2, y2 = rects[int(i)]
            out.append([max(0, int(x1 * rW)), max(0, int(y1 * rH)),
                        min(W, int(x2 * rW)), min(H, int(y2 * rH))])
        return out
    except Exception as e:
        log_error(f"[FACE-PRO] EAST 检测失败: {str(e)[:120]}")
        return []


def _get_scrfd():
    global _scrfd_app
    if _scrfd_app is None:
        from insightface.app import FaceAnalysis
        app = FaceAnalysis(allowed_modules=["detection"], providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=-1, det_size=(640, 640), det_thresh=0.4)
        _scrfd_app = app
        log_info("[FACE-PRO] SCRFD detector 就绪")
    return _scrfd_app


def _iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _nms(boxes, iou_thr=0.4):
    keep = []
    for b in sorted(boxes, key=lambda x: x[4], reverse=True):
        if all(_iou(b, k) < iou_thr for k in keep):
            keep.append(b)
    return keep


def _detect_one(app, frame, ds):
    try:
        bboxes, _ = app.det_model.detect(frame, input_size=ds)
    except Exception as e:
        log_error(f"[FACE-PRO] detect ds={ds} 失败: {str(e)[:120]}")
        return []
    if bboxes is None:
        return []
    return [[float(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4])] for b in bboxes]


def _detect_faces(app, frame):
    """自适应多尺度 SCRFD：先 640，检到就返回；没检到才补 320（占满整帧的大脸）。"""
    out = _detect_one(app, frame, (640, 640))
    if not out:
        out = _detect_one(app, frame, (320, 320))
    return _nms(out)


def _ellipse_mosaic(frame, x1, y1, x2, y2, blocks=12, expand=0.2):
    """椭圆形马赛克，紧贴脸部、不超出脸框。两侧不外扩，顶/底各内收 5%。"""
    H, W = frame.shape[:2]
    w, h = x2 - x1, y2 - y1
    pad_side = 0
    top_inset = int(0.05 * h)
    bottom_inset = int(0.05 * h)
    X1, Y1 = max(0, int(x1 - pad_side)), max(0, int(y1 + top_inset))
    X2, Y2 = min(W, int(x2 + pad_side)), min(H, int(y2 - bottom_inset))
    roi = frame[Y1:Y2, X1:X2]
    if roi.size == 0:
        return
    rh, rw = roi.shape[:2]
    small = cv2.resize(roi, (max(1, rw // blocks), max(1, rh // blocks)), interpolation=cv2.INTER_LINEAR)
    mosaic = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
    mask = np.zeros((rh, rw), dtype=np.uint8)
    cv2.ellipse(mask, (rw // 2, rh // 2), (rw // 2, rh // 2), 0, 0, 360, 255, thickness=-1)
    frame[Y1:Y2, X1:X2] = np.where(mask[..., None] > 0, mosaic, roi)


def _shift_box_by_flow(prev_gray, gray, box):
    """非检测帧：用 Lucas-Kanade 光流把上一帧脸框跟着运动平移。跟不动则原位保持。"""
    x1, y1, x2, y2 = [int(v) for v in box]
    H, W = prev_gray.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(W, x2), min(H, y2)
    if x2 - x1 < 10 or y2 - y1 < 10:
        return box
    roi = prev_gray[y1:y2, x1:x2]
    pts = cv2.goodFeaturesToTrack(roi, maxCorners=40, qualityLevel=0.01, minDistance=5)
    if pts is None or len(pts) < 4:
        return box
    pts[:, 0, 0] += x1
    pts[:, 0, 1] += y1
    pts = pts.astype(np.float32)
    try:
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev_gray, gray, pts, None)
    except Exception:
        return box
    if nxt is None or st is None:
        return box
    st = st.flatten()
    old, new = pts[st == 1], nxt[st == 1]
    if len(new) < 4:
        return box
    dx = float(np.median(new[:, 0, 0] - old[:, 0, 0]))
    dy = float(np.median(new[:, 0, 1] - old[:, 0, 1]))
    return [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]


def mask_faces_in_video_pro(in_path, out_path, *, detect_every=3, expand=0.2, blocks=12, preserve_subtitle=True):
    """视频人脸打码。返回统计 dict；全程无脸返 any_face=False（调用方用原视频）。
    detect_every：每 N 帧检测一次（CPU 提速），非检测帧靠光流跟 + hold。默认 3（服务器实测最优）。
    preserve_subtitle：存字幕原始像素 → 打码(连字幕底下也打) → 贴回字幕原始像素 → 字幕全程清晰、脸全程盖住。
                       需 EAST 模型;缺失则自动跳过(只打码)。"""
    app = _get_scrfd()
    de = max(1, int(detect_every))
    text_on = bool(preserve_subtitle) and (_get_east() is not None)
    text_every = max(de * 2, 5)   # 字幕位置较稳,稀疏采样并取并集
    all_text = []
    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        return {"ok": False, "error": "无法打开视频"}
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # ── Pass 1：每 de 帧检测人脸；非检测帧/漏检帧用光流把上一框跟着运动平移
    max_hold = max(int(fps * 0.5), 10)
    per_frame = []
    last_boxes = []
    prev_gray = None
    miss = hit = f = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        dets = _detect_faces(app, frame) if (f % de == 0) else []
        if dets:
            last_boxes = [[float(x1), float(y1), float(x2), float(y2)] for (x1, y1, x2, y2, sc) in dets]
            miss = 0
            hit += 1
        else:
            miss += 1
            if miss > max_hold or not last_boxes:
                last_boxes = []
            elif prev_gray is not None:
                last_boxes = [_shift_box_by_flow(prev_gray, gray, b) for b in last_boxes]
        if text_on and (f % text_every == 0):
            all_text.extend(_detect_text_boxes(frame))
        per_frame.append([list(b) for b in last_boxes])
        prev_gray = gray
        f += 1
    cap.release()
    n = f
    if n == 0:
        return {"ok": False, "error": "空视频"}
    masked_frames = sum(1 for b in per_frame if b)

    if masked_frames == 0:
        log_info(f"[FACE-PRO] {n} 帧全程无脸,跳过")
        return {"ok": True, "any_face": False, "frames": n, "out": None}

    # 字幕区并集遮罩(小幅扩边盖描边,不大扩 → 贴回时不带出脸)。打码后这些像素贴回原始 = 字幕全程清晰。
    text_mask = None
    if text_on and all_text:
        text_mask = np.zeros((H, W), dtype=np.uint8)
        for (tx1, ty1, tx2, ty2) in all_text:
            m = 4
            cv2.rectangle(text_mask, (max(0, tx1 - m), max(0, ty1 - m)),
                          (min(W, tx2 + m), min(H, ty2 + m)), 255, thickness=-1)
        log_info(f"[FACE-PRO] 字幕区 {len(all_text)} 框 → 打码后贴回原始像素(字幕保清晰)")

    # ── Pass 2：逐帧 ①存原始 ②打椭圆码 ③把字幕区贴回原始像素 → 写无声 mp4
    cap = cv2.VideoCapture(in_path)
    tmp = out_path + ".noaudio.mp4"
    writer = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    f = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        orig = frame.copy() if text_mask is not None else None
        for b in (per_frame[f] if f < len(per_frame) else []):
            _ellipse_mosaic(frame, b[0], b[1], b[2], b[3], blocks=blocks, expand=expand)
        if text_mask is not None:
            sel = text_mask > 0
            frame[sel] = orig[sel]    # 字幕原始像素贴回最上层(脸已在底下打码)
        writer.write(frame)
        f += 1
    cap.release()
    writer.release()

    # ── 合回原音轨（h264 重编码）
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp, "-i", in_path,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
             "-map", "0:v:0", "-map", "1:a:0?", "-c:a", "aac", "-shortest", out_path],
            capture_output=True, timeout=600, check=True,
        )
        try:
            os.remove(tmp)
        except Exception:
            pass
    except Exception as e:
        log_error(f"[FACE-PRO] ffmpeg mux 失败,输出无声版: {e}")
        os.replace(tmp, out_path)

    coverage = masked_frames / n
    log_info(f"[FACE-PRO] {n}帧 命中{hit} 覆盖{masked_frames}({coverage:.0%}) det_every={de} -> {os.path.basename(out_path)}")
    return {
        "ok": True, "any_face": True, "frames": n,
        "detected_frames": hit, "masked_frames": masked_frames,
        "coverage": round(coverage, 3), "out": out_path,
    }
