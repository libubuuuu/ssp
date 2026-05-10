"""P221 A2 — 价格计算 + build_prompt + hash 工具单元测试"""
import os
import tempfile

import pytest

from app.services.video_clone_v2_pricing import (
    TIER_CREDITS,
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

    def test_single_economy(self):
        segs = [{"source_type": "ai", "tier": "economy"}]
        assert calc_credits(segs) == 15

    def test_single_standard(self):
        segs = [{"source_type": "ai", "tier": "standard"}]
        assert calc_credits(segs) == 20

    def test_mixed_segments(self):
        segs = [
            {"source_type": "ai", "tier": "standard"},
            {"source_type": "original"},
            {"source_type": "ai", "tier": "economy"},
            {"source_type": "original"},
        ]
        # 20 + 0 + 15 + 0 = 35
        assert calc_credits(segs) == 35

    def test_all_original_zero(self):
        segs = [{"source_type": "original"}, {"source_type": "original"}]
        assert calc_credits(segs) == 0

    def test_empty_zero(self):
        assert calc_credits([]) == 0

    def test_invalid_tier_raises(self):
        with pytest.raises(ValueError, match="非法 tier"):
            calc_credits([{"source_type": "ai", "tier": "premium"}])


# ─── build_prompt ────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_no_images(self):
        assert build_prompt("婴儿玩耍", []) == "婴儿玩耍"

    def test_single_product(self):
        result = build_prompt("展示", [{"url": "x", "role": "product"}])
        assert result == "展示(参考素材:@产品1)"

    def test_two_products_increments(self):
        result = build_prompt("展示", [
            {"url": "x", "role": "product"},
            {"url": "y", "role": "product"},
        ])
        assert result == "展示(参考素材:@产品1, @产品2)"

    def test_mixed_roles_independent_counters(self):
        result = build_prompt("拍摄", [
            {"url": "a", "role": "product"},
            {"url": "b", "role": "person"},
            {"url": "c", "role": "product"},
            {"url": "d", "role": "scene"},
        ])
        # product1, person1, product2, scene1
        assert result == "拍摄(参考素材:@产品1, @人物1, @产品2, @场景1)"

    def test_unknown_role_falls_back_reference(self):
        result = build_prompt("test", [{"url": "x", "role": "unknown"}])
        assert result == "test(参考素材:@图1)"

    def test_missing_role_key_falls_back(self):
        result = build_prompt("test", [{"url": "x"}])
        assert result == "test(参考素材:@图1)"


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
