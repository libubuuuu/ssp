"""pytest fixture:用 ffmpeg testsrc/color 滤镜生成临时测试视频。"""
import os
import subprocess
import tempfile

import pytest


def _generate_video(out_path: str, duration: int = 3, kind: str = "single") -> None:
    """
    kind="single":3s testsrc 渐变(scenedetect 多半检测为 1 scene 或几个小段)
    kind="multi" :拼接 3 段不同 solid color(红/绿/蓝),每段 2s,内容差异大 → 多 scene
    """
    if kind == "single":
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=24",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            out_path,
        ]
    elif kind == "multi":
        # concat: red(2s) + green(2s) + blue(2s) = 6s, 内容跳变明显
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=color=red:size=320x240:duration=2:rate=24",
            "-f", "lavfi", "-i", "color=color=green:size=320x240:duration=2:rate=24",
            "-f", "lavfi", "-i", "color=color=blue:size=320x240:duration=2:rate=24",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[out]",
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            out_path,
        ]
    else:
        raise ValueError(f"unknown kind: {kind}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"fixture ffmpeg failed: {result.stderr[-300:]}")


@pytest.fixture
def single_scene_video(tmp_path):
    p = str(tmp_path / "single.mp4")
    _generate_video(p, duration=3, kind="single")
    return p


@pytest.fixture
def multi_scene_video(tmp_path):
    p = str(tmp_path / "multi.mp4")
    _generate_video(p, kind="multi")
    return p
