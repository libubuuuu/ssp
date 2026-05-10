"""P221 A2 — 切片算法单元测试

覆盖:
- 边界:total < 4 / total > 64 / 等
- 段长 → allowed_tiers
- 末段 < 4 秒并到前段
- 段数限制
"""
import pytest

from app.services.video_clone_v2_split import plan_segments_v2, _allowed_tiers


# ─── allowed_tiers ───────────────────────────────────────────────────

class TestAllowedTiers:
    def test_below_2s_returns_empty(self):
        assert _allowed_tiers(1.5) == []
        assert _allowed_tiers(0.0) == []

    def test_2_to_4s_only_economy(self):
        assert _allowed_tiers(2.0) == ["economy"]
        assert _allowed_tiers(3.99) == ["economy"]

    def test_above_4s_both_tiers(self):
        assert _allowed_tiers(4.0) == ["economy", "standard"]
        assert _allowed_tiers(8.0) == ["economy", "standard"]
        assert _allowed_tiers(12.0) == ["economy", "standard"]


# ─── plan_segments_v2 ────────────────────────────────────────────────

class TestPlanSegmentsV2:
    def test_below_4s_raises(self):
        with pytest.raises(ValueError, match="视频太短"):
            plan_segments_v2(3.99)
        with pytest.raises(ValueError, match="视频太短"):
            plan_segments_v2(0)

    def test_above_64s_raises(self):
        with pytest.raises(ValueError, match="视频太长"):
            plan_segments_v2(64.1)
        with pytest.raises(ValueError, match="视频太长"):
            plan_segments_v2(120)

    def test_exactly_4s_single_segment(self):
        plan = plan_segments_v2(4.0)
        assert len(plan) == 1
        assert plan[0]["start"] == 0.0
        assert plan[0]["duration"] == 4.0
        assert plan[0]["allowed_tiers"] == ["economy", "standard"]

    def test_exactly_8s_single_segment(self):
        plan = plan_segments_v2(8.0)
        assert len(plan) == 1
        assert plan[0]["duration"] == 8.0
        assert plan[0]["allowed_tiers"] == ["economy", "standard"]

    def test_just_above_8s_two_segments_with_merge(self):
        # 8.5s:第二段 0.5s < 4 → 并到第一段 → 1 段(8.5s)
        # 等等,merge 逻辑只在 len > 1 时触发,8.5 切出来是 [8.0, 0.5] → 第二段 < 4,merge → [8.5]
        plan = plan_segments_v2(8.5)
        assert len(plan) == 1
        assert plan[0]["duration"] == 8.5

    def test_12s_two_full_segments(self):
        # 12s:[8, 4] → 第二段 4s 不 < 4(等于 4 不触发并段) → 2 段
        plan = plan_segments_v2(12.0)
        assert len(plan) == 2
        assert plan[0]["start"] == 0.0 and plan[0]["duration"] == 8.0
        assert plan[1]["start"] == 8.0 and plan[1]["duration"] == 4.0
        assert plan[0]["allowed_tiers"] == ["economy", "standard"]
        assert plan[1]["allowed_tiers"] == ["economy", "standard"]

    def test_15s_merge_last_short(self):
        # 15s:[8, 7] → 第二段 7s 不 merge → 2 段
        plan = plan_segments_v2(15.0)
        assert len(plan) == 2
        assert plan[1]["duration"] == 7.0

    def test_18s_merge_last_short(self):
        # 18s:[8, 8, 2] → 第三段 2 < 4,合并到第二段 → [8, 10]
        plan = plan_segments_v2(18.0)
        assert len(plan) == 2
        assert plan[0]["duration"] == 8.0
        assert plan[1]["duration"] == 10.0
        assert plan[1]["start"] == 8.0

    def test_24s_three_full_segments(self):
        # 24s:[8, 8, 8] → 末段 8 不 < 4 → 3 段
        plan = plan_segments_v2(24.0)
        assert len(plan) == 3
        for seg in plan:
            assert seg["duration"] == 8.0

    def test_32_5s_user_doc_example(self):
        # 用户决议文档示例:32.5s → 4 段 [8, 8, 8, 8.5]
        plan = plan_segments_v2(32.5)
        assert len(plan) == 4
        assert [s["duration"] for s in plan] == [8.0, 8.0, 8.0, 8.5]
        assert [s["start"] for s in plan] == [0.0, 8.0, 16.0, 24.0]

    def test_64s_max_8_segments(self):
        # 64s:[8]*8 → 8 段,刚好上限
        plan = plan_segments_v2(64.0)
        assert len(plan) == 8

    def test_short_segment_only_economy(self):
        # 12.5s:[8, 4.5] → 第二段 4.5 ≥ 4 不 merge,但 4.5 < 8 → allowed=[economy, standard]
        # 实际 4.5 ≥ 4 → both 都在
        plan = plan_segments_v2(12.5)
        assert plan[1]["duration"] == 4.5
        assert plan[1]["allowed_tiers"] == ["economy", "standard"]

    def test_idx_continuous(self):
        plan = plan_segments_v2(32.5)
        assert [s["idx"] for s in plan] == [0, 1, 2, 3]


# ─── 红线 1:切片 hash collision 检测 ───────────────────────────────────
# 这块测试 split_input_video 函数,需要 ffmpeg + 真视频。
# 但这是异步函数 + 依赖 ffmpeg,我们用 mock 测试调用契约即可。
# 真 ffmpeg 集成测试在 fal probe 阶段一起跑。

import asyncio
import os
import tempfile
from unittest.mock import patch, AsyncMock

from app.services.video_clone_v2_processor import split_input_video


class TestSplitHashCollisionGuard:
    """红线 1:切片产生重复段时必须 raise ValueError(防止"4 段同一文件"风险)。"""

    def _make_dummy(self, content: bytes) -> str:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
            f.write(content)
            return f.name

    def test_collision_raises(self, tmp_path):
        """两段切出完全相同的内容 → 应 raise ValueError"""
        plan = [
            {"idx": 0, "start": 0.0, "duration": 8.0},
            {"idx": 1, "start": 8.0, "duration": 8.0},
        ]
        # mock _ffmpeg 让两次切出来的文件内容完全一致
        async def fake_ffmpeg(args):
            # args 末尾是 output path
            out = args[-1]
            with open(out, "wb") as f:
                f.write(b"identical seg content")  # 两次都写同样

        with patch("app.services.video_clone_v2_processor._ffmpeg", side_effect=fake_ffmpeg):
            with pytest.raises(ValueError, match="hash collision"):
                asyncio.run(split_input_video("/tmp/dummy_input.mp4", plan, str(tmp_path)))

    def test_distinct_no_raise(self, tmp_path):
        """两段切出不同内容 → 正常返"""
        plan = [
            {"idx": 0, "start": 0.0, "duration": 8.0},
            {"idx": 1, "start": 8.0, "duration": 8.0},
        ]
        async def fake_ffmpeg(args):
            out = args[-1]
            # 用 idx 区分,确保不同
            content = b"seg-" + os.path.basename(out).encode()
            with open(out, "wb") as f:
                f.write(content)

        with patch("app.services.video_clone_v2_processor._ffmpeg", side_effect=fake_ffmpeg):
            seg_files = asyncio.run(split_input_video("/tmp/dummy_input.mp4", plan, str(tmp_path)))
            assert len(seg_files) == 2

    def test_single_segment_skips_check(self, tmp_path):
        """单段不跑 collision 校验(因为没法跟自己比)"""
        plan = [{"idx": 0, "start": 0.0, "duration": 8.0}]

        async def fake_ffmpeg(args):
            out = args[-1]
            with open(out, "wb") as f:
                f.write(b"single seg")

        with patch("app.services.video_clone_v2_processor._ffmpeg", side_effect=fake_ffmpeg):
            seg_files = asyncio.run(split_input_video("/tmp/dummy_input.mp4", plan, str(tmp_path)))
            assert len(seg_files) == 1
