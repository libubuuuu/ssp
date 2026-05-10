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
    """commit 4(2026-05-10):中文 @ 透明转 fal 标准 @Image{N}。

    fal seedance r2v 文档约定占位符 @Image1/@Image2/...(中文 @ fal 不识别)。
    本套用例覆盖:末尾参考素材列表生成 + user_prompt 内中文 @ 引用替换。
    """

    def test_no_images(self):
        assert build_prompt("婴儿玩耍", []) == "婴儿玩耍"

    def test_single_product(self):
        result = build_prompt("展示", [{"url": "x", "role": "product"}])
        assert result == "展示(参考素材:@Image1)"

    def test_two_products_increments(self):
        result = build_prompt("展示", [
            {"url": "x", "role": "product"},
            {"url": "y", "role": "product"},
        ])
        assert result == "展示(参考素材:@Image1, @Image2)"

    def test_mixed_roles_global_indexing(self):
        result = build_prompt("拍摄", [
            {"url": "a", "role": "product"},
            {"url": "b", "role": "person"},
            {"url": "c", "role": "product"},
            {"url": "d", "role": "scene"},
        ])
        # 末尾参考素材按 image_urls 全局顺序 1-4
        assert result == "拍摄(参考素材:@Image1, @Image2, @Image3, @Image4)"

    def test_unknown_role_falls_back_reference(self):
        result = build_prompt("test", [{"url": "x", "role": "unknown"}])
        assert result == "test(参考素材:@Image1)"

    def test_missing_role_key_falls_back(self):
        result = build_prompt("test", [{"url": "x"}])
        assert result == "test(参考素材:@Image1)"

    # ─── 中文 @ 引用 → fal @Image{N} 转换 ─────────────────────────────────

    def test_user_prompt_chinese_at_product_with_number(self):
        """@产品1 → image_urls 第 1 张 product 对应的全局编号"""
        result = build_prompt("@产品1 替换裤子", [
            {"url": "p1", "role": "product"},
            {"url": "h1", "role": "person"},
        ])
        # product 第 1 张全局 idx=1,所以 @产品1 → @Image1
        assert result == "@Image1 替换裤子(参考素材:@Image1, @Image2)"

    def test_user_prompt_chinese_at_no_number_defaults_to_1(self):
        """@产品(省略数字)= @产品1"""
        result = build_prompt("@产品 替换", [{"url": "x", "role": "product"}])
        assert result == "@Image1 替换(参考素材:@Image1)"

    def test_user_prompt_mixed_roles_correct_global_idx(self):
        """混合 role:@产品1 / @人物1 各按 role 内序号查全局编号"""
        result = build_prompt("用 @产品1 和 @人物1 出镜", [
            {"url": "p", "role": "product"},   # 全局 1,product 第 1 张
            {"url": "h", "role": "person"},    # 全局 2,person 第 1 张
        ])
        assert result == "用 @Image1 和 @Image2 出镜(参考素材:@Image1, @Image2)"

    def test_user_prompt_role_internal_seq_not_global(self):
        """@产品2 = product 内第 2 张(全局可能不是第 2 张)"""
        result = build_prompt("@产品1 @产品2 同框", [
            {"url": "p1", "role": "product"},  # 全局 1
            {"url": "p2", "role": "product"},  # 全局 2
            {"url": "h",  "role": "person"},   # 全局 3
        ])
        # @产品1 → 全局 1 → @Image1;@产品2 → 全局 2 → @Image2
        assert result == "@Image1 @Image2 同框(参考素材:@Image1, @Image2, @Image3)"

    def test_user_prompt_nonexistent_image_kept_as_is(self):
        """@产品3 但只上传 2 张 product → 保留原文,不擅自重定向"""
        result = build_prompt("@产品3 替换", [
            {"url": "p1", "role": "product"},
            {"url": "p2", "role": "product"},
        ])
        assert result == "@产品3 替换(参考素材:@Image1, @Image2)"

    def test_user_prompt_unknown_chinese_at_label_kept(self):
        """不识别的中文 @(eg @作者)→ ROLE_TO_AT_LABEL 没这个 label → 保留原文"""
        result = build_prompt("@作者 出镜", [{"url": "x", "role": "product"}])
        assert result == "@作者 出镜(参考素材:@Image1)"

    def test_user_prompt_at_image_in_reference_role(self):
        """@图1 对应 reference role(ROLE_TO_AT_LABEL: reference→图)"""
        result = build_prompt("@图1 是参考", [{"url": "x", "role": "reference"}])
        assert result == "@Image1 是参考(参考素材:@Image1)"

    def test_user_prompt_double_digit_number(self):
        """@产品10 贪婪匹配两位数"""
        imgs = [{"url": f"p{i}", "role": "product"} for i in range(10)]
        result = build_prompt("@产品10 末尾", imgs)
        # product 第 10 张 = 全局 10
        assert "@Image10 末尾" in result

    def test_real_world_bug_repro(self):
        """老板 2026-05-10 19:29 真测踩 bug 的 prompt 样式 — commit 4 修复后必须通过。"""
        result = build_prompt("@产品1替换视频中的裤子(参考素材:@产品1)", [
            {"url": "p", "role": "product"},
        ])
        # 注意:用户 prompt 里已含"(参考素材:@产品1)",转换后变成"(参考素材:@Image1)"
        # build_prompt 末尾还会再追加一段"(参考素材:@Image1)" — 这是 commit 4 的设计
        # 模板文案重复对 fal 无负作用(都指向 @Image1)
        assert result == "@Image1替换视频中的裤子(参考素材:@Image1)(参考素材:@Image1)"


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
