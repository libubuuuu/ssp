"""P221 A2 — 切片算法单元测试

覆盖:
- 边界:total < 4 / total > 64 / 等
- 末段 < 4 秒并到前段
- 段数限制

⚠ 历史:本文件原有 _allowed_tiers 测试组,2026-05-10 砍单档后删除。
"""
import pytest

from app.services.video_clone_v2_split import plan_segments_v2


# ─── plan_segments_v2 ────────────────────────────────────────────────

class TestPlanSegmentsV2:
    def test_below_4s_raises(self):
        with pytest.raises(ValueError, match="视频太短"):
            plan_segments_v2(3.99)
        with pytest.raises(ValueError, match="视频太短"):
            plan_segments_v2(0)

    def test_above_15s_raises(self):
        # 单段最长 15s,超出拒绝
        with pytest.raises(ValueError, match="最长 15 秒"):
            plan_segments_v2(15.1)
        with pytest.raises(ValueError, match="最长 15 秒"):
            plan_segments_v2(64.1)

    def test_exactly_4s_single_segment(self):
        plan = plan_segments_v2(4.0)
        assert len(plan) == 1
        assert plan[0]["start"] == 0.0
        assert plan[0]["duration"] == 4.0
        assert "allowed_tiers" not in plan[0]

    def test_exactly_8s_single_segment(self):
        plan = plan_segments_v2(8.0)
        assert len(plan) == 1
        assert plan[0]["duration"] == 8.0
        assert "allowed_tiers" not in plan[0]

    def test_12s_single_segment(self):
        # ≤15s 全部返回单段
        plan = plan_segments_v2(12.0)
        assert len(plan) == 1
        assert plan[0]["start"] == 0.0
        assert plan[0]["duration"] == 12.0

    def test_15s_max_single_segment(self):
        plan = plan_segments_v2(15.0)
        assert len(plan) == 1
        assert plan[0]["duration"] == 15.0

    def test_idx_is_zero(self):
        plan = plan_segments_v2(10.0)
        assert plan[0]["idx"] == 0


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
