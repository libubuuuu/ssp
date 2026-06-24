---
name: SSP 不脑补 + 不甩锅,verify 真因而后说
description: 技术事实必须实测 verify 后才能下结论;出 bug 时先怀疑自己 prompt/代码,不要甩锅给模型/库/用户
type: feedback
originSessionId: 5a8aec5d-5435-40f7-906e-cab97d56e237
---
两条紧密相关的纪律,底层是同一个反模式:不 verify 真因,凭印象/直觉下结论。

## 1. 不脑补:技术事实必须 verify 后才说

涉及 fal endpoint / SDK schema / 引擎能力 / 实际行为 等技术事实时,**必须实测 verify 后才能下结论**,绝不许从命名(如 endpoint 叫 image-to-video 就推断"不带音频")、过往经验、行业印象 pattern-match 出来当事实讲。

**Why(2026-05-02):** 从 endpoint 名 `kling-video/o3/standard/image-to-video` 推断"Kling o3 不会说话",没 verify schema。实测发现 fal 接受 audio_url 没 422,推断不准。

**How to apply:** 说"X 引擎不支持 Y / 这个 endpoint 只能做 Z / 速度约 N 秒"等具体技术声明前:
1. 调真 KEY 实测(传争议参数看 422 还是接受)
2. 下载输出文件用 ffprobe / file 验证(光看 SDK 返回的 dict 不算)
3. 读 fal 官方 schema / OpenAPI spec
都做不到就**明确标"未 verify,以下是猜测"**,不要当事实陈述。

## 2. 不甩锅:出 bug 时先怀疑自己 prompt/代码

用户报视频质量问题(嘴张大 / 不参考产品 / 换背景),我立刻甩锅 — 说"Kling Avatar v2 嘴型本来就比 omnihuman 夸张(这是模型本身行为)"。用户质疑"你确定是模型得错吗？" 我才 grep 自己代码,发现:

- 嘴张大 → 我 P113 在 VLM prompt 里写的 "shocked expression" 钩子公式 → VLM 写进 visual_prompt → 我 P115 又把 visual_prompt 当 driver 喂给 Kling
- 不参考产品 → 我 P115 Kontext prompt 自己写的 "do not let product dominate"
- 换背景 → 我 P115 Kontext prompt 自己写的 "Soft studio background, neutral color"

**全是我自己 prompt 写的指令,不是模型的锅。**

**Why(2026-05-05):** 用户原话"以后做事情别总是脑补和甩锅给别人我不喜欢，自己去改"。甩锅会让用户失去对判断力的信任,而且耽误真正修 bug。

**How to apply:** 用户报问题时,**先 verify 是不是自己代码/prompt 的问题**,再考虑是模型/库/用户输入:
1. 先 grep 自己代码:有没有指令 "强制" 出现这个行为(如 prompt 里写了诱导词)
2. 看任务的真实输入:VLM 写的 visual_prompt / 调用参数到底是啥
3. 走"最近改了什么"思路 — 大概率是我刚加的 prompt/逻辑导致
4. 都排除了再考虑模型本身行为
说"这是模型本身的行为,改不了" 之前必须先做完 1-3。
