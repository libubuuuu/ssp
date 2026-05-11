"""P221 A2 — 价格计算 + build_prompt + hash 工具单元测试"""
import os
import tempfile

import pytest

from app.services.video_clone_v2_pricing import (
    SEGMENT_CREDITS,
    calc_credits,
    calc_total_credits,
    build_prompt,
    sha256_file,
    sha256_url_first8,
)


# ─── calc_credits ────────────────────────────────────────────────────

class TestCalcCredits:
    def test_alias_equality(self):
        assert calc_credits is calc_total_credits

    def test_single_ai_segment(self):
        """1 段 ai:扣 SEGMENT_CREDITS"""
        segs = [{"source_type": "ai"}]
        assert calc_credits(segs) == SEGMENT_CREDITS

    def test_four_ai_segments(self):
        """4 段 ai(典型 ultimate 30s 视频):扣 4 × SEGMENT_CREDITS"""
        segs = [{"source_type": "ai"} for _ in range(4)]
        assert calc_credits(segs) == 4 * SEGMENT_CREDITS

    def test_mixed_ai_and_original(self):
        """混合 AI/原片:只对 ai 段扣费,original 不扣"""
        segs = [
            {"source_type": "ai"},
            {"source_type": "original"},
            {"source_type": "ai"},
            {"source_type": "original"},
        ]
        assert calc_credits(segs) == 2 * SEGMENT_CREDITS

    def test_all_original_zero(self):
        """全 original:0 积分"""
        segs = [{"source_type": "original"}, {"source_type": "original"}]
        assert calc_credits(segs) == 0

    def test_empty_zero(self):
        """空 list:0 积分"""
        assert calc_credits([]) == 0


# ─── build_prompt ────────────────────────────────────────────────────

class TestBuildPrompt:
    """2026-05-11:build_prompt 简化为直接透传 user_prompt。

    根因:老板 fal playground 实测成功配方是裸 prompt(eg "视频中的裤子换成上传的图片")
          + image_urls。commit 4 引入的"中文 @ → @Image{N} 转换 + 末尾追加(参考素材:...)"
          反而干扰 fal 模型(老板 4 次真测均失败 vs playground 同端点同输入完美)。
    """

    def test_no_images_passthrough(self):
        assert build_prompt("婴儿玩耍", []) == "婴儿玩耍"

    def test_single_image_passthrough(self):
        result = build_prompt("视频中的裤子换成上传的图片", [{"url": "x", "role": "product"}])
        assert result == "视频中的裤子换成上传的图片"

    def test_multi_image_passthrough(self):
        result = build_prompt("换裤子和人物", [
            {"url": "a", "role": "product"},
            {"url": "b", "role": "person"},
            {"url": "c", "role": "scene"},
        ])
        assert result == "换裤子和人物"

    def test_chinese_at_label_not_transformed(self):
        """中文 @ 不再转换 @Image — 老板 playground 实证 fal 不需要 @ 引用,
        裸 prompt + image_urls 数组就够。"""
        result = build_prompt("@产品1 替换裤子", [{"url": "p", "role": "product"}])
        assert result == "@产品1 替换裤子"

    def test_no_trailing_reference_list(self):
        """不再追加 (参考素材:@Image1, @Image2, ...) — commit 4 的多余尾巴已删。"""
        result = build_prompt("展示", [{"url": "x", "role": "product"}])
        assert "(参考素材:" not in result
        assert result == "展示"

    def test_empty_prompt_passthrough(self):
        assert build_prompt("", [{"url": "x", "role": "product"}]) == ""

    def test_playground_winning_prompt(self):
        """老板 fal playground 成功配方 verbatim — 直接透传,fal 跑出完美对象替换。"""
        result = build_prompt("视频中的裤子换成上传的图片", [{"url": "p", "role": "product"}])
        assert result == "视频中的裤子换成上传的图片"


# ─── hash 工具(4 道红线核心)──────────────────────────────────────────

class TestHashUtils:
    def test_sha256_file_known(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello world")
            path = f.name
        try:
            # 已知 SHA256 of "hello world"
            assert sha256_file(path) == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        finally:
            os.unlink(path)

    def test_sha256_file_distinct_for_different_content(self):
        with tempfile.NamedTemporaryFile(delete=False) as f1:
            f1.write(b"AAAA")
            p1 = f1.name
        with tempfile.NamedTemporaryFile(delete=False) as f2:
            f2.write(b"BBBB")
            p2 = f2.name
        try:
            assert sha256_file(p1) != sha256_file(p2)
        finally:
            os.unlink(p1); os.unlink(p2)

    def test_sha256_file_same_content_same_hash(self):
        with tempfile.NamedTemporaryFile(delete=False) as f1:
            f1.write(b"identical content here")
            p1 = f1.name
        with tempfile.NamedTemporaryFile(delete=False) as f2:
            f2.write(b"identical content here")
            p2 = f2.name
        try:
            assert sha256_file(p1) == sha256_file(p2)
        finally:
            os.unlink(p1); os.unlink(p2)

    def test_sha256_file_streaming_large(self):
        # 测试 chunk 边界:10 MB
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"x" * (10 * 1024 * 1024))
            path = f.name
        try:
            h = sha256_file(path)
            assert len(h) == 64
            # 用小 chunk 跑一遍应该一致
            assert sha256_file(path, chunk_size=1024) == h
        finally:
            os.unlink(path)

    def test_sha256_url_first8_length(self):
        h = sha256_url_first8("https://example.com/video.mp4")
        assert len(h) == 8
        assert all(c in "0123456789abcdef" for c in h)

    def test_sha256_url_first8_distinct(self):
        h1 = sha256_url_first8("https://x.com/a.mp4")
        h2 = sha256_url_first8("https://x.com/b.mp4")
        assert h1 != h2
