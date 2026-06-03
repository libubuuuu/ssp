"""P221 A2 — check_duration 智能切片提示 单元测试

只测纯函数 check_duration,不测 ffmpeg motion_score(那需要真视频)。

当前算法(v2):末段 < 4s 时并入前段(合并后 ≤ 15s)→ 几乎不需要 trim。
这组测试反映当前实际行为,旧的"8s 倍数截断"逻辑已废弃。
"""
import pytest

from app.services.video_clone_v2_split import check_duration


class TestCheckDurationBoundaries:
    def test_below_4s_raises(self):
        with pytest.raises(ValueError, match="太短"):
            check_duration(3.99)

    def test_above_15_raises(self):
        with pytest.raises(ValueError, match="最长 15 秒"):
            check_duration(15.1)


class TestCheckDurationNoTrimCases:
    """当前算法:末段可并入前段,绝大多数情况 needs_trim=False"""

    def test_4s_minimum_no_trim(self):
        r = check_duration(4.0)
        assert r["needs_trim"] is False

    def test_8s_no_trim(self):
        r = check_duration(8.0)
        assert r["needs_trim"] is False
        assert r["target_duration"] == 8.0

    def test_8_04s_within_tolerance_no_trim(self):
        r = check_duration(8.04)
        assert r["needs_trim"] is False

    def test_9s_no_trim(self):
        # 9s = [8s, 1s];末段 1s < 4s 但并入前段 = 9s ≤ 15s → 无需 trim
        r = check_duration(9.0)
        assert r["needs_trim"] is False

    def test_15s_no_trim(self):
        # 15s = [8s, 7s];末段 7s ≥ 4s → 无需 trim
        r = check_duration(15.0)
        assert r["needs_trim"] is False

    def test_16s_raises(self):
        # 单段最长 15s,>15s 全部拒绝
        with pytest.raises(ValueError, match="最长 15 秒"):
            check_duration(16.0)

    def test_18s_raises(self):
        with pytest.raises(ValueError, match="最长 15 秒"):
            check_duration(18.0)

    def test_64s_raises(self):
        with pytest.raises(ValueError, match="最长 15 秒"):
            check_duration(64.0)
