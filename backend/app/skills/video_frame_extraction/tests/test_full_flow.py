"""端到端:detect → extract → compose 全流程。"""
import os
import pytest
from PIL import Image

from app.skills.video_frame_extraction import VideoFrameSkill


def test_skill_process_multi_scene(multi_scene_video, tmp_path):
    """红绿蓝拼接视频走完整流程 → 拿到至少 1 张 grid PNG。"""
    skill = VideoFrameSkill()
    result = skill.process(
        multi_scene_video,
        output_dir=str(tmp_path / "frames"),
    )
    assert "scenes" in result
    assert "keyframe_paths" in result
    assert "grid_paths" in result
    assert "n_frames" in result
    assert "n_grids" in result
    assert result["n_frames"] >= 2
    assert result["n_grids"] >= 1
    for p in result["grid_paths"]:
        assert os.path.exists(p)
        grid_img = Image.open(p)
        assert grid_img.size == (1024, 1024)


def test_skill_process_single_scene(single_scene_video, tmp_path):
    """单镜头视频也能走完 — 返 1 个 scene + 1 张 grid。"""
    skill = VideoFrameSkill()
    result = skill.process(
        single_scene_video,
        output_dir=str(tmp_path / "frames"),
    )
    assert result["n_frames"] >= 1
    assert result["n_grids"] == 1
    assert os.path.exists(result["grid_paths"][0])


def test_skill_process_long_video_paging(tmp_path):
    """模拟 12 帧 → 2 张九宫格(9 + 3 padding)。"""
    # 用 multi_scene_video fixture 不一定能出 12 段;改造一下:
    # 直接调用 compose_grid 验证分页逻辑
    from app.skills.video_frame_extraction import compose_grid
    from PIL import Image as _Img
    frames = []
    for i in range(12):
        p = str(tmp_path / f"f{i}.jpg")
        _Img.new("RGB", (160, 120), color=(i * 20 % 256, 100, 200)).save(p, "JPEG")
        frames.append(p)
    # 模拟 process 内的分页逻辑
    layout = (3, 3)
    per_grid = 9
    grids = []
    for ci, start in enumerate(range(0, len(frames), per_grid)):
        chunk = frames[start:start + per_grid]
        out = str(tmp_path / f"grid_{ci}.png")
        compose_grid(chunk, layout=layout, output_path=out)
        grids.append(out)
    assert len(grids) == 2
    for g in grids:
        assert os.path.exists(g)
        assert _Img.open(g).size == (1024, 1024)


def test_skill_ai_stub(single_scene_video):
    """AI 分析当前 stub,应 raise NotImplementedError。"""
    skill = VideoFrameSkill()
    with pytest.raises(NotImplementedError):
        skill.analyze_with_ai(["dummy.jpg"])


def test_skill_speech_stub(single_scene_video):
    skill = VideoFrameSkill()
    with pytest.raises(NotImplementedError):
        skill.transcribe_speech(single_scene_video)
