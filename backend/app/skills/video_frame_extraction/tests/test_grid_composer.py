"""test grid_composer.compose_grid。"""
import os
import pytest
from PIL import Image

from app.skills.video_frame_extraction import compose_grid
from app.skills.video_frame_extraction.exceptions import GridCompositionError


def _make_solid(path, color, size=(160, 120)):
    Image.new("RGB", size, color=color).save(path, "JPEG")


def test_compose_9_grid(tmp_path):
    """9 张帧拼 3x3 → 1024x1024 PNG。"""
    frames = []
    for i in range(9):
        p = str(tmp_path / f"f{i}.jpg")
        _make_solid(p, (i * 25 % 256, 100, 200))
        frames.append(p)
    out = str(tmp_path / "grid.png")
    result = compose_grid(frames, layout=(3, 3), output_path=out)
    assert result == out
    img = Image.open(out)
    assert img.size == (1024, 1024)
    assert img.mode == "RGB"


def test_compose_4_grid(tmp_path):
    """4 张帧拼 2x2 OK。"""
    frames = []
    for i in range(4):
        p = str(tmp_path / f"f{i}.jpg")
        _make_solid(p, (200, i * 50, 100))
        frames.append(p)
    out = str(tmp_path / "grid4.png")
    compose_grid(frames, layout=(2, 2), output_path=out)
    assert os.path.exists(out)
    assert Image.open(out).size == (1024, 1024)


def test_compose_padding(tmp_path):
    """3 张帧塞 9 格 → 自动用最后一张填满,不报错。"""
    frames = []
    for i in range(3):
        p = str(tmp_path / f"f{i}.jpg")
        _make_solid(p, (50, 50, 200))
        frames.append(p)
    out = str(tmp_path / "padded.png")
    compose_grid(frames, layout=(3, 3), output_path=out)
    assert os.path.exists(out)


def test_compose_empty_raises():
    with pytest.raises(GridCompositionError):
        compose_grid([], layout=(3, 3))


def test_compose_missing_frame_raises(tmp_path):
    with pytest.raises(GridCompositionError):
        compose_grid(["/tmp/no-such-frame-xyz.jpg"], layout=(1, 1))


def test_compose_no_index(tmp_path):
    """show_index=False 应该没编号水印,size 还是 1024。"""
    p = str(tmp_path / "f.jpg")
    _make_solid(p, (10, 200, 10))
    out = str(tmp_path / "no-label.png")
    compose_grid([p], layout=(1, 1), output_path=out, show_index=False)
    assert Image.open(out).size == (1024, 1024)
