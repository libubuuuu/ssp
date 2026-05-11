"""ffmpeg 抽帧封装 — 每个 scene 抽 1 张关键帧。

输入:视频路径 + scene list
输出:帧 jpg 路径 list

依赖:系统 ffmpeg
"""
import os
import subprocess
from typing import List

from .scene_detector import Scene
from .exceptions import KeyframeExtractionError


def extract_keyframes(
    video_path: str,
    scenes: List[Scene],
    output_dir: str = "/tmp/v3_frames",
    timestamp_strategy: str = "midpoint",
    jpeg_quality: int = 2,
) -> List[str]:
    """从视频每个 scene 抽 1 张关键帧。

    Args:
        video_path: 源视频路径
        scenes: detect_scenes() 输出
        output_dir: 输出目录(自动创建)
        timestamp_strategy: "midpoint"(默认,场景中点)| "start"(场景起点)
        jpeg_quality: ffmpeg -q:v 值,1-31,越小质量越高,默认 2

    Returns:
        帧文件路径 list,顺序跟 scenes 一致

    Raises:
        KeyframeExtractionError: 视频不存在 / ffmpeg 失败 / 输出帧损坏
    """
    if not os.path.exists(video_path):
        raise KeyframeExtractionError(f"video not found: {video_path}")
    if not scenes:
        raise KeyframeExtractionError("scenes list is empty")

    os.makedirs(output_dir, exist_ok=True)

    frame_paths: List[str] = []
    for s in scenes:
        if timestamp_strategy == "midpoint":
            ts = s.midpoint_seconds
        elif timestamp_strategy == "start":
            ts = s.start_seconds
        else:
            raise KeyframeExtractionError(
                f"unknown timestamp_strategy: {timestamp_strategy!r}"
            )

        out_path = os.path.join(output_dir, f"frame_{s.idx:03d}.jpg")

        # ffmpeg -ss <ts> -i video.mp4 -vframes 1 -q:v 2 out.jpg
        # -ss 在 -i 之前是快速 seek(关键帧近似,精度足够)
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{ts:.3f}",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", str(jpeg_quality),
            out_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired as e:
            raise KeyframeExtractionError(
                f"ffmpeg timed out on scene {s.idx} at {ts:.3f}s"
            ) from e

        if result.returncode != 0:
            raise KeyframeExtractionError(
                f"ffmpeg scene {s.idx} at {ts:.3f}s rc={result.returncode}: "
                f"{result.stderr[-300:]}"
            )
        if not os.path.exists(out_path) or os.path.getsize(out_path) < 100:
            raise KeyframeExtractionError(
                f"ffmpeg scene {s.idx} produced empty/missing output: {out_path}"
            )
        frame_paths.append(out_path)

    return frame_paths
