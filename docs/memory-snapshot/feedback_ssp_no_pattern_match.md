---
name: SSP 不脑补,verify 而后说
description: 涉及 fal endpoint / SDK schema / 引擎能力等技术事实,必须实测 verify 后才能下结论,不许从命名/经验/印象 pattern-match
type: feedback
originSessionId: 5a8aec5d-5435-40f7-906e-cab97d56e237
---
回答涉及 fal endpoint / SDK schema / 引擎能力 / 实际行为 等技术事实时,**必须实测 verify 后才能下结论**,绝不许从命名(如 endpoint 叫 image-to-video 就推断"不带音频")、过往经验、行业印象 pattern-match 出来当事实讲。

**Why:** 2026-05-02 我从 endpoint 名字 `kling-video/o3/standard/image-to-video` 推断出"Kling o3 不会说话",没去 verify schema。用户立刻识破:"你确定?冷静思考过了吗?"。实测结果跟我推断不完全一致(fal 接受了 audio_url 参数没 422),证明我脑补了。这种事重复几次会被严重质疑判断力。

**How to apply:** 凡是说"X 引擎不支持 Y / 这个 endpoint 只能做 Z / 速度大约 N 秒"等具体技术声明,先做下面任一项再说:
1. 调一次真 KEY 实测(传争议参数看是 422 还是接受)
2. 下载输出文件用 ffprobe / file 等工具验证(不能光看 SDK 返回的 dict 字段)
3. 读 fal 官方 schema / OpenAPI spec
都做不到就**明确标"未 verify,以下是猜测"**,而不是当事实陈述。

跟 `feedback_ssp_fal_probe_first.md`(改 endpoint 前必须 probe)是同一个原则,但范围更广 — 不只是改之前 probe,**回答之前也要 probe**。
