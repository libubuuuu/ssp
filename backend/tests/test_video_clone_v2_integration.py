"""P221 A2 — 真实 ffmpeg 集成测试(必修 2)

跟 mock 测试不同,本文件:
- 真跑 ffmpeg 切片
- 真算 SHA256
- 真验证 collision 触发 ValueError

依赖 P220 测试视频 /opt/ssp/uploads/probe/p220_balance_test/result_480p_2s_input.mp4。
若不存在(纯 dev 环境),整组 skip。

CI 跑:这些测试比 mock 慢(每 case ~1 秒 ffmpeg),但每个 PR 都该跑保证红线真有效。
"""
import asyncio
import os
import shutil
import tempfile

import pytest

from app.services.video_clone_v2_processor import split_input_video
from app.services.video_clone_v2_pricing import sha256_file
from app.services.video_clone_v2_split import (
    calc_motion_score,
    suggest_trim_candidates,
)


P220_VIDEO = "/opt/ssp/uploads/probe/p220_balance_test/result_480p_2s_input.mp4"
P220_VIDEO_HTTPS = "https://ailixiao.com/uploads/probe/p220_balance_test/result_480p_2s_input.mp4"

skip_if_no_p220 = pytest.mark.skipif(
    not os.path.exists(P220_VIDEO),
    reason=f"P220 测试视频不存在:{P220_VIDEO}",
)


@skip_if_no_p220
class TestSplitRealFfmpeg:
    """红线 1 集成测试:真 ffmpeg 切片 + 真 SHA256 校验。"""

    def test_two_distinct_segments_pass(self, tmp_path):
        """切 P220 8 秒视频成 0-4 / 4-8 两段 → SHA256 必须不同。"""
        plan = [
            {"idx": 0, "start": 0.0, "duration": 4.0},
            {"idx": 1, "start": 4.0, "duration": 4.0},
        ]
        seg_files = asyncio.run(split_input_video(P220_VIDEO, plan, str(tmp_path)))
        assert len(seg_files) == 2

        # 真算 SHA256
        h0 = sha256_file(seg_files[0])
        h1 = sha256_file(seg_files[1])
        assert h0 != h1, f"P220 不同时间段竟然 hash 相同?h0={h0[:8]} h1={h1[:8]}"
        # 文件确实存在 + 非空
        assert os.path.getsize(seg_files[0]) > 0
        assert os.path.getsize(seg_files[1]) > 0

    def test_collision_triggers_when_segments_overlap_exactly(self, tmp_path):
        """红线 1 真触发:切 2 段同时间窗 → ffmpeg 输出相同内容 → ValueError。"""
        # 故意切两段从 0 开始 1.5 秒,真 ffmpeg 输出一致
        plan = [
            {"idx": 0, "start": 0.0, "duration": 1.5},
            {"idx": 1, "start": 0.0, "duration": 1.5},
        ]
        with pytest.raises(ValueError, match="hash collision"):
            asyncio.run(split_input_video(P220_VIDEO, plan, str(tmp_path)))


@skip_if_no_p220
class TestMotionScoreReal:
    """check-duration motion_score 真测(本地路径 + HTTPS URL 双路径)。"""

    def test_motion_score_local_path(self):
        """本地路径(P220 视频开头 2 秒) → 应有 0.4 左右的运动量(婴儿安静)。"""
        score = asyncio.run(calc_motion_score(P220_VIDEO, 0.0, 2.0))
        # P220 婴儿视频实测 0.4 量级
        assert 0.05 < score < 5.0, f"开头 2s 运动量异常:{score}"

    def test_motion_score_https_url(self):
        """⭐ 必修 1:验证 ffmpeg 真能流式读 HTTPS URL 算 motion(等价 fal CDN)。"""
        score = asyncio.run(calc_motion_score(P220_VIDEO_HTTPS, 0.0, 2.0))
        assert 0.05 < score < 5.0, f"HTTPS URL motion_score 异常:{score}"

    def test_motion_score_distinguishes_segments(self):
        """同一视频不同时间段 motion 应有差异(P220 中段抬头动作 > 开头静止)。"""
        head = asyncio.run(calc_motion_score(P220_VIDEO, 0.0, 2.0))
        mid  = asyncio.run(calc_motion_score(P220_VIDEO, 3.0, 2.0))
        # 至少应该有 5% 以上差异(不要求精确,只要算法能区分)
        assert abs(head - mid) > head * 0.05, \
            f"段间 motion 差异太小 head={head} mid={mid}"

    def test_suggest_trim_candidates_local(self):
        """suggest_trim_candidates 跑通 + 推荐字段就位 + 排序按 motion 升序。"""
        cands = asyncio.run(suggest_trim_candidates(P220_VIDEO, 8.058, 6.0))
        assert len(cands) >= 2  # 至少 head/tail
        # 第一个必带 recommended=True
        assert cands[0].get("recommended") is True
        # motion_score 升序
        scores = [c["motion_score"] for c in cands]
        assert scores == sorted(scores), f"motion 没升序排:{scores}"
