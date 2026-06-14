"""人脸打码/涂鸦 —— 视频复刻上传隐私保护（防肖像侵权）。

规则：
- **检测到人脸才处理**；整段/整图无人脸 → 返回 False，调用方用原文件（不浪费处理）。
- **视频**：逐帧 OpenCV 检测人脸 + 马赛克 → ffmpeg 把原音轨合回（打码后保留声音）。
- **图片**：检测人脸 + 马赛克盖脸。
- 检测器：OpenCV haar 正脸级联（自带，正脸准、侧脸可能漏）；模型文件就位时自动升级 YuNet（更精准）。

调用方（upload 端点）：处理成功(True)就用打码版上传；无人脸(False)就用原文件。
"""
import os
import random
import subprocess

import cv2
import numpy as np

from app.services.logger import log_info, log_error

# YuNet 精准模型（可选，放到 services/models/face_detection_yunet_2023mar.onnx 即自动启用）
_YUNET_PATH = os.path.join(os.path.dirname(__file__), "models", "face_detection_yunet_2023mar.onnx")
_HAAR = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


_yunet = None  # YuNet 检测器复用(create 一次,别每帧重载 onnx → 视频逐帧才不会慢爆)


def _get_yunet():
    global _yunet
    if _yunet is None and os.path.exists(_YUNET_PATH):
        try:
            _yunet = cv2.FaceDetectorYN.create(_YUNET_PATH, "", (320, 320), score_threshold=0.5)
            log_info("[FACE-BLUR] 用 YuNet 精准检测(侧脸/角度也抓)")
        except Exception as e:
            log_error(f"face_blur YuNet 初始化失败,改用 haar: {e}")
            _yunet = False
    return _yunet or None


def _detect(frame, force_haar=False):
    """返回人脸框列表 [(x, y, w, h)]。
    force_haar=True 强制用 haar(快,给视频逐帧用,避免 YuNet 逐帧超时);
    否则优先 YuNet(精准,侧脸/角度/小脸,给图片用)。"""
    h, w = frame.shape[:2]
    det = None if force_haar else _get_yunet()
    if det is not None:
        try:
            det.setInputSize((w, h))
            _, faces = det.detect(frame)
            if faces is None:
                return []
            return [tuple(max(0, int(v)) for v in f[:4]) for f in faces]
        except Exception as e:
            log_error(f"face_blur YuNet 检测失败,回退 haar: {e}")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return [tuple(int(v) for v in f) for f in
            _HAAR.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))]


def _mosaic(frame, x, y, w, h, blocks=12):
    """对人脸打【椭圆形】马赛克,紧贴脸部轮廓。
    椭圆天然避开矩形四角的头发/脖子/背景,所以只在脸的卵形区域打码、不超出。
    外扩比例小(8%)仅用于盖住脸的边缘(下巴/额头/两颊),保证脸完全锁住不漏。"""
    H, W = frame.shape[:2]
    pad = int(0.08 * max(w, h))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    roi = frame[y0:y1, x0:x1]
    if roi.size == 0:
        return
    rh, rw = roi.shape[:2]
    # 1) 整个 ROI 先像素化
    small = cv2.resize(roi, (max(1, rw // blocks), max(1, rh // blocks)), interpolation=cv2.INTER_LINEAR)
    mosaic = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
    # 2) 只在内切椭圆区域内贴马赛克,椭圆外保留原像素 → 椭圆形打码
    mask = np.zeros((rh, rw), dtype=np.uint8)
    cv2.ellipse(mask, (rw // 2, rh // 2), (rw // 2, rh // 2), 0, 0, 360, 255, thickness=-1)
    frame[y0:y1, x0:x1] = np.where(mask[..., None] > 0, mosaic, roi)


def _doodle(frame, x, y, w, h, color=(80, 90, 255)):
    """在人脸区域画红色乱涂线条(手绘涂鸦盖脸,像用户给的示例)。color 为 BGR 橙红。"""
    H, W = frame.shape[:2]
    pad = int(0.12 * max(w, h))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    if x1 <= x0 or y1 <= y0:
        return
    thickness = max(2, int(max(w, h) / 70))     # 细线条(之前太粗糊成实心块)
    n = max(10, (x1 - x0) * (y1 - y0) // 8000)  # 适中笔数(之前太多,糊死了)
    for _ in range(n):
        # 2-4 个点的长折线,跨脸来回交错,留缝隙、不糊成实心(脸隐约可见)
        pts = np.array(
            [[random.randint(x0, x1), random.randint(y0, y1)] for _ in range(random.randint(2, 4))],
            dtype=np.int32,
        )
        cv2.polylines(frame, [pts], False, color, thickness, lineType=cv2.LINE_AA)


def blur_faces_in_image(in_path: str, out_path: str) -> bool:
    """图片打码盖脸。有脸→写 out_path 返 True；无脸/读不出→返 False（用原图）。"""
    img = cv2.imread(in_path)
    if img is None:
        return False
    faces = _detect(img)
    if not len(faces):
        return False
    for (x, y, w, h) in faces:
        _doodle(img, x, y, w, h)
    cv2.imwrite(out_path, img)
    log_info(f"[FACE-BLUR-IMG] faces={len(faces)} 涂鸦盖脸 -> {os.path.basename(out_path)}")
    return True


def blur_faces_in_video(in_path: str, out_path: str, sample_every: int = 1) -> bool:
    """视频逐帧人脸打码 + 合回原音轨。有脸→写 out_path 返 True；全程无脸→返 False（用原视频）。

    sample_every>1 可隔帧检测提速（中间帧沿用上次框），默认逐帧最准。
    """
    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        return False
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    tmp_noaudio = out_path + ".noaudio.mp4"
    writer = cv2.VideoWriter(tmp_noaudio, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    any_face = False
    last_faces = []
    miss = 0
    hold = max(8, int(fps // 3))  # 锁定:检测偶尔漏帧(眨眼/侧头/动模糊)时沿用上次的框 ~0.3s,脸不闪现
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % max(1, sample_every) == 0:
            detected = _detect(frame)  # 视频也用 YuNet 精准检测(异步后台跑、不怕慢)，只打脸、少误碰
            if detected:
                last_faces = detected
                miss = 0
            else:
                miss += 1
                if miss > hold:
                    last_faces = []   # 连续多帧确实无脸,才真正停止打码
        if len(last_faces):
            any_face = True
            for (fx, fy, fw, fh) in last_faces:
                _mosaic(frame, fx, fy, fw, fh)
        writer.write(frame)
        idx += 1
    cap.release()
    writer.release()

    if not any_face:
        try: os.remove(tmp_noaudio)
        except Exception: pass
        log_info(f"[FACE-BLUR-VID] 全程无人脸,跳过 ({idx} 帧)")
        return False

    # 打码后的视频是无声的 → ffmpeg 用 h264 重编码 + 把原视频音轨合回来
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_noaudio, "-i", in_path,
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-map", "0:v:0", "-map", "1:a:0?", "-c:a", "aac", "-shortest", out_path],
            capture_output=True, timeout=600, check=True,
        )
    except Exception as e:
        log_error(f"[FACE-BLUR-VID] ffmpeg 合音失败,输出无声打码版: {e}")
        os.replace(tmp_noaudio, out_path)
        return True
    try: os.remove(tmp_noaudio)
    except Exception: pass
    log_info(f"[FACE-BLUR-VID] 打码完成 {idx} 帧 -> {os.path.basename(out_path)}")
    return True
