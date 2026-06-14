"""人脸打码 PRO —— 独立公共方法(SCRFD 多尺度检测 + 两遍跟踪插值 + 椭圆打码)。

设计目标:**可靠覆盖人脸,含侧脸/转头/小脸**,给视频复刻上传做隐私遮挡。
- 检测器:insightface SCRFD(对角度/侧脸远强于 YuNet),多尺度合并(大脸小脸都抓)。
- 两遍处理:第一遍逐帧检测连成轨迹;第二遍对漏检帧用前后帧**线性插值**补框 → 转头那几帧也不漏。
- 椭圆打码:贴脸卵形,矩形四角的头发/背景不碰。

⚠️ 这是【独立新功能】用的公共方法,**不改动 face_blur.py / video_clone_v2 现有打码链路**。
   验证效果满意后,再由调用方决定是否合并进生产。

检测器做成可插拔:换模型只动 `_detect_faces`。
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
    """懒加载 EAST 文字检测(走现有 cv2.dnn,不装新库)。模型缺失返 None → 文字检测跳过=整脸打码。"""
    global _east_net
    if _east_net is None:
        if not os.path.exists(_EAST_PATH):
            log_error("[FACE-PRO] EAST 模型缺失,文字检测跳过(B 方案退化为整脸打码)")
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
    """EAST 输出解码成轴对齐框(忽略旋转角,取外接矩形,够用于抠字幕)。返回 (rects, confs)。"""
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
            sx, sy = int(endX - bw), int(endY - bh)
            rects.append((sx, sy, endX, endY))
            confs.append(float(sc))
    return rects, confs


def _detect_text_boxes(frame, score_thr=0.5, nms_thr=0.4, cap_side=640):
    """EAST 文字检测,返回原图坐标的轴对齐文字框 [[x1,y1,x2,y2],...]。无模型/失败返 []。"""
    net = _get_east()
    if net is None:
        return []
    H, W = frame.shape[:2]
    scale = min(1.0, cap_side / float(max(W, H)))  # 控速:限制送入边长
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
    """懒加载 SCRFD(insightface),复用单例。det_thresh 调低到 0.4 多抓侧脸,后续靠跟踪去噪。"""
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
    """[[x1,y1,x2,y2,score],...] → 跨尺度去重,按分降序。"""
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
    """自适应多尺度 SCRFD:先 640(常规小脸),检到就返回;没检到才补 320(占满整帧的大脸)。
    常规视频省掉第二次检测,提速近一半;close-up 仍靠 320 兜底。"""
    out = _detect_one(app, frame, (640, 640))
    if not out:
        out = _detect_one(app, frame, (320, 320))
    return _nms(out)


def _build_tracks(dets_per_frame, n_frames, max_link_gap, iou_link=0.25):
    """把逐帧检测连成轨迹(贪心 IoU 关联,允许跨 max_link_gap 帧的短暂漏检)。"""
    tracks = []  # {"boxes": {fidx: [x1,y1,x2,y2,score]}, "last_f": int, "last_box": [...]}
    for f in range(n_frames):
        dets = list(dets_per_frame[f])
        used = set()
        for tr in sorted(tracks, key=lambda t: t["last_f"], reverse=True):  # 优先匹配最近轨迹
            if f <= tr["last_f"] or f - tr["last_f"] > max_link_gap:
                continue
            best, best_iou = -1, iou_link
            for i, d in enumerate(dets):
                if i in used:
                    continue
                v = _iou(tr["last_box"], d)
                if v >= best_iou:
                    best, best_iou = i, v
            if best >= 0:
                tr["boxes"][f] = dets[best]
                tr["last_f"] = f
                tr["last_box"] = dets[best]
                used.add(best)
        for i, d in enumerate(dets):
            if i not in used:
                tracks.append({"boxes": {f: d}, "last_f": f, "last_box": d})
    return tracks


def _interpolate(tracks, n_frames, max_gap):
    """轨迹内相邻命中帧之间线性插值补框 → 漏检帧也有框。返回 per_frame[f] = [box,...]"""
    per_frame = [[] for _ in range(n_frames)]
    for tr in tracks:
        fs = sorted(tr["boxes"].keys())
        for f in fs:
            per_frame[f].append(tr["boxes"][f])
        for a, b in zip(fs, fs[1:]):
            gap = b - a
            if gap <= 1 or gap > max_gap:
                continue
            ba, bb = tr["boxes"][a], tr["boxes"][b]
            for f in range(a + 1, b):
                t = (f - a) / gap
                per_frame[f].append([ba[j] + (bb[j] - ba[j]) * t for j in range(4)] + [min(ba[4], bb[4])])
    return per_frame


def _ellipse_mosaic(frame, x1, y1, x2, y2, blocks=12, expand=0.12):
    """椭圆形马赛克,**紧贴脸部、不超出脸框**(不管字幕)。
    两侧不外扩(椭圆宽=脸宽,不溢出两侧/头发),顶部和底部都向内收一点
    (底部不超过下巴、顶部不顶到发际线),椭圆只盖脸的卵形核心区。"""
    H, W = frame.shape[:2]
    w, h = x2 - x1, y2 - y1
    pad_side = 0                    # 两侧不外扩:椭圆最宽=脸框宽,不超出脸两侧
    top_inset = int(0.05 * h)       # 顶部内收:不顶到发际线
    bottom_inset = int(0.05 * h)    # 底部内收:不超过下巴
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
    """漏检帧:用 Lucas-Kanade 光流把上一帧的脸框跟着运动平移(跟住快速移动)。跟不动则原位保持。"""
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


def mask_faces_in_video_pro(in_path, out_path, *, detect_every=1, expand=0.2, blocks=12):
    """视频人脸打码 PRO(两遍)。返回统计 dict;全程无脸返 any_face=False(调用方用原视频)。

    detect_every: 每 N 帧检测一次(>1 提速,中间帧靠插值);默认逐帧最准。
    expand:       椭圆边缘外扩比例;blocks: 马赛克粗细(越小越糊)。
    """
    app = _get_scrfd()
    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        return {"ok": False, "error": "无法打开视频"}
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # ── Pass 1:【每帧】检测人脸 + 漏检帧用光流跟住运动(不管字幕,只紧贴脸打码)
    max_hold = max(int(fps * 0.5), 10)   # 漏检最多跟 ~0.5s,超了才停(防把背景当脸长期糊)
    per_frame = []
    last_boxes = []
    prev_gray = None
    miss = hit = f = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        dets = _detect_faces(app, frame)  # 每帧都检,杜绝插值漂移
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

    # ── Pass 2:重读原视频逐帧打椭圆码(整块文字区从遮罩抠掉=字幕全程清晰)→ 写无声 mp4
    cap = cv2.VideoCapture(in_path)
    tmp = out_path + ".noaudio.mp4"
    writer = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    f = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        for b in (per_frame[f] if f < len(per_frame) else []):
            _ellipse_mosaic(frame, b[0], b[1], b[2], b[3], blocks=blocks, expand=expand)
        writer.write(frame)
        f += 1
    cap.release()
    writer.release()

    # ── 合回原音轨(h264 重编码)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp, "-i", in_path,
             # veryfast + crf26:文件比 ultrafast 小一大截 → COS 上传快得多(编码只略慢)
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
    log_info(f"[FACE-PRO] {n}帧 检测命中{hit} 打码覆盖{masked_frames}({coverage:.0%}) -> {os.path.basename(out_path)}")
    return {
        "ok": True, "any_face": True, "frames": n,
        "detected_frames": hit, "masked_frames": masked_frames,
        "coverage": round(coverage, 3), "out": out_path,
    }
