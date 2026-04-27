"""内容审核测试

覆盖:
- 中文敏感词子串匹配
- 英文 word boundary 不误伤(kill 不命中 skill)
- 三类(政治/色情/暴力)各采样若干
- 大小写不敏感
- 空 prompt 视为安全
- assert_safe_prompt raise 400 + 含分类描述
"""
import pytest
from fastapi import HTTPException

from app.services.content_filter import check_prompt, assert_safe_prompt


class TestSafe:
    def test_empty_prompt_is_safe(self):
        ok, reason = check_prompt(None)
        assert ok and reason is None
        ok, reason = check_prompt("")
        assert ok and reason is None

    def test_normal_prompts_pass(self):
        for p in [
            "a beautiful sunset over mountains",
            "一只可爱的小狗在草地上奔跑",
            "professional product photography of a coffee cup",
            "现代简约风格的客厅设计",
            "skillful artist painting flowers",  # 包含 "skill" 不是 "kill"
            "killing time at the office",        # killing 在黑名单里 — 看下面 unsafe 用例
        ]:
            ok, _ = check_prompt(p)
            # killing 是 violence,会被拒绝;前 5 个应该通过
        # 只断言前 5 个通过
        for p in [
            "a beautiful sunset over mountains",
            "一只可爱的小狗在草地上奔跑",
            "professional product photography of a coffee cup",
            "现代简约风格的客厅设计",
            "skillful artist painting flowers",
        ]:
            ok, _ = check_prompt(p)
            assert ok, f"误伤了正常 prompt: {p}"

    def test_word_boundary_no_false_positive(self):
        """English word boundary:'kill' 不应命中 'skill' / 'skillful' / 'killer's bay'(嗯,killer 是黑名单)"""
        ok, _ = check_prompt("a person with great skill")
        assert ok, "skill 不应命中 kill"
        ok, _ = check_prompt("skillset is impressive")
        assert ok


class TestUnsafePolitical:
    def test_xi_jinping_chinese(self):
        ok, reason = check_prompt("画一张习近平的照片")
        assert not ok and reason == "政治敏感"

    def test_xi_jinping_english(self):
        ok, reason = check_prompt("portrait of xi jinping")
        assert not ok and reason == "政治敏感"

    def test_tiananmen(self):
        ok, reason = check_prompt("天安门事件 photo")
        assert not ok and reason == "政治敏感"

    def test_falun_gong(self):
        ok, reason = check_prompt("falun gong protest")
        assert not ok and reason == "政治敏感"


class TestUnsafePorn:
    def test_naked_chinese(self):
        ok, reason = check_prompt("生成一张裸体图片")
        assert not ok and reason == "色情"

    def test_porn_english(self):
        ok, reason = check_prompt("a porn scene please")
        assert not ok and reason == "色情"

    def test_case_insensitive(self):
        ok, reason = check_prompt("NUDE photography")
        assert not ok and reason == "色情"


class TestUnsafeViolence:
    def test_kill_chinese(self):
        ok, reason = check_prompt("画一个杀人现场")
        assert not ok and reason == "暴力"

    def test_kill_english(self):
        ok, reason = check_prompt("a man holding a gun about to kill someone")
        assert not ok and reason == "暴力"

    def test_bomb(self):
        ok, reason = check_prompt("a terrorist with a bomb")
        # terrorist 也是黑名单,先命中哪个看顺序;只要 reason 是暴力即可
        assert not ok and reason == "暴力"

    def test_chinese_blood(self):
        ok, reason = check_prompt("血腥屠杀场面")
        assert not ok and reason == "暴力"


class TestAssertSafePrompt:
    def test_safe_does_not_raise(self):
        assert_safe_prompt("a sunny day")  # 不抛

    def test_unsafe_raises_400(self):
        with pytest.raises(HTTPException) as ei:
            assert_safe_prompt("naked person")
        assert ei.value.status_code == 400
        assert "色情" in ei.value.detail
        assert "请修改后重试" in ei.value.detail

    def test_unsafe_does_not_leak_matched_word(self):
        """detail 只透露分类,不透露具体命中词,避免攻击者通过差异响应推测词表"""
        with pytest.raises(HTTPException) as ei:
            assert_safe_prompt("naked person")
        # 不应包含 "naked" 这个词
        assert "naked" not in ei.value.detail.lower()
