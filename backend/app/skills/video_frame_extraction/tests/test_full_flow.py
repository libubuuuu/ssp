"""端到端:detect → extract → compose 全流程。"""
import os
import pytest
from PIL import Image

from app.skills.video_frame_extraction import VideoFrameSkill


def test_skill_process_multi_scene(multi_scene_video, tmp_path):
    """红绿蓝拼接视频走完整流程 → 拿到 grid PNG。"""
    skill = VideoFrameSkill()
    result = skill.process(
        multi_scene_video,
        grid_size=9,
        output_dir=str(tmp_path / "frames"),
    )
    assert "scenes" in result
    assert "keyframe_paths" in result
    assert "grid_path" in result
    assert "n_frames" in result
    assert result["n_frames"] >= 2
    assert os.path.exists(result["grid_path"])
    grid_img = Image.open(result["grid_path"])
    assert grid_img.size == (1024, 1024)
    # 清理 grid(测试结束)
    if os.path.exists(result["grid_path"]):
        os.remove(result["grid_path"])


def test_skill_process_single_scene(single_scene_video, tmp_path):
    """单镜头视频也能走完 — 返 1 个 scene + grid。"""
    skill = VideoFrameSkill()
    result = skill.process(
        single_scene_video,
        grid_size=9,
        output_dir=str(tmp_path / "frames"),
    )
    assert result["n_frames"] >= 1
    assert os.path.exists(result["grid_path"])
    if os.path.exists(result["grid_path"]):
        os.remove(result["grid_path"])


def test_skill_ai_stub(single_scene_video):
    """AI 分析当前 stub,应 raise NotImplementedError。"""
    skill = VideoFrameSkill()
    with pytest.raises(NotImplementedError):
        skill.analyze_with_ai(["dummy.jpg"])


def test_skill_speech_stub(single_scene_video):
    skill = VideoFrameSkill()
    with pytest.raises(NotImplementedError):
        skill.transcribe_speech(single_scene_video)
