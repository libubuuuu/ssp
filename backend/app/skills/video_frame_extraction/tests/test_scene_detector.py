"""test scene_detector.detect_scenes。"""
import pytest

from app.skills.video_frame_extraction import detect_scenes
from app.skills.video_frame_extraction.exceptions import SceneDetectionError


def test_detect_single_scene_fallback(single_scene_video):
    """静态/单镜头视频应返回 1 个全片 scene(fallback)。"""
    scenes = detect_scenes(single_scene_video, threshold=27.0)
    assert len(scenes) >= 1
    assert scenes[0].idx == 0
    assert scenes[0].start_seconds == 0.0
    assert scenes[0].end_seconds > 0


def test_detect_multi_scenes(multi_scene_video):
    """红绿蓝拼接视频应检测到 2-3 个 scene cuts。"""
    scenes = detect_scenes(multi_scene_video, threshold=27.0)
    assert len(scenes) >= 2, f"expected >=2 scenes for red-green-blue, got {len(scenes)}"
    # idx 连续 0..N-1
    for i, s in enumerate(scenes):
        assert s.idx == i
        assert s.end_seconds > s.start_seconds


def test_video_not_found_raises():
    with pytest.raises(SceneDetectionError):
        detect_scenes("/tmp/does-not-exist-xyz.mp4")


def test_scene_to_dict(single_scene_video):
    scenes = detect_scenes(single_scene_video)
    d = scenes[0].to_dict()
    assert set(d.keys()) == {"idx", "start_seconds", "end_seconds", "duration_seconds"}
    assert d["idx"] == 0
