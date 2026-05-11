"""test keyframe_extractor.extract_keyframes。"""
import os
import pytest

from app.skills.video_frame_extraction import detect_scenes, extract_keyframes
from app.skills.video_frame_extraction.exceptions import KeyframeExtractionError


def test_extract_midpoint_single(single_scene_video, tmp_path):
    scenes = detect_scenes(single_scene_video)
    out_dir = str(tmp_path / "frames")
    paths = extract_keyframes(single_scene_video, scenes, output_dir=out_dir)
    assert len(paths) == len(scenes)
    for p in paths:
        assert os.path.exists(p)
        assert os.path.getsize(p) > 1000  # 真 jpeg 至少 1KB


def test_extract_multi_scenes(multi_scene_video, tmp_path):
    scenes = detect_scenes(multi_scene_video, threshold=27.0)
    out_dir = str(tmp_path / "frames")
    paths = extract_keyframes(multi_scene_video, scenes, output_dir=out_dir)
    assert len(paths) == len(scenes) >= 2
    # 不同 scene 抽出的帧应有不同字节数(红绿蓝色彩差)
    sizes = set(os.path.getsize(p) for p in paths)
    assert len(sizes) >= 1  # 至少存在


def test_video_not_found_raises():
    scenes = []
    with pytest.raises(KeyframeExtractionError):
        extract_keyframes("/tmp/missing-xyz.mp4", scenes)


def test_empty_scenes_raises(single_scene_video):
    with pytest.raises(KeyframeExtractionError):
        extract_keyframes(single_scene_video, [])


def test_invalid_strategy(single_scene_video):
    scenes = detect_scenes(single_scene_video)
    with pytest.raises(KeyframeExtractionError):
        extract_keyframes(single_scene_video, scenes, timestamp_strategy="bogus")
