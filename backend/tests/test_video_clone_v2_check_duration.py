"""P221 A2 — check_duration 智能切片提示 单元测试

只测纯函数 check_duration,不测 ffmpeg motion_score(那需要真视频)。
"""
import pytest

from app.services.video_clone_v2_split import check_duration


class TestCheckDurationBoundaries:
    def test_below_2s_raises(self):
        with pytest.raises(ValueError, match="太短"):
            check_duration(1.99)

    def test_above_64_raises(self):
        with pytest.raises(ValueError, match="太长"):
            check_duration(64.5)


class TestCheckDurationNoTrimCases:
    """需要返 needs_trim=False 的情况:单段 / 完美 8 倍数"""

    def test_2s_single_no_trim(self):
        r = check_duration(2.0)
        assert r["needs_trim"] is False
        assert r["target_duration"] == 2.0

    def test_4s_single_no_trim(self):
        r = check_duration(4.0)
        assert r["needs_trim"] is False

    def test_8s_no_trim(self):
        r = check_duration(8.0)
        assert r["needs_trim"] is False
        assert r["target_duration"] == 8.0

    def test_8_05s_within_tolerance_no_trim(self):
        # 8.04s 应被视为 8s(浮点容差)
        r = check_duration(8.04)
        assert r["needs_trim"] is False

    def test_16s_perfect_no_trim(self):
        r = check_duration(16.0)
        assert r["needs_trim"] is False
        assert r["target_duration"] == 16.0

    def test_16_03s_within_tolerance_no_trim(self):
        r = check_duration(16.03)
        assert r["needs_trim"] is False
        assert r["target_duration"] == 16.0

    def test_24s_perfect_no_trim(self):
        r = check_duration(24.0)
        assert r["needs_trim"] is False

    def test_64s_max_perfect_no_trim(self):
        r = check_duration(64.0)
        assert r["needs_trim"] is False


class TestCheckDurationTrimCases:
    """needs_trim=True 的情况:9-15s / 17-23s / 25-31s 等"""

    def test_9s_trims_to_8(self):
        r = check_duration(9.0)
        assert r["needs_trim"] is True
        assert r["target_duration"] == 8.0
        assert r["drop_seconds"] == 1.0

    def test_15s_trims_to_8(self):
        r = check_duration(15.0)
        assert r["needs_trim"] is True
        assert r["target_duration"] == 8.0
        assert r["drop_seconds"] == 7.0

    def test_18s_trims_to_16(self):
        r = check_duration(18.0)
        assert r["needs_trim"] is True
        assert r["target_duration"] == 16.0
        assert r["drop_seconds"] == 2.0

    def test_23_5s_trims_to_16(self):
        r = check_duration(23.5)
        assert r["needs_trim"] is True
        assert r["target_duration"] == 16.0
        assert r["drop_seconds"] == 7.5

    def test_50_3s_trims_to_48(self):
        r = check_duration(50.3)
        assert r["needs_trim"] is True
        assert r["target_duration"] == 48.0
        assert r["drop_seconds"] == 2.3
