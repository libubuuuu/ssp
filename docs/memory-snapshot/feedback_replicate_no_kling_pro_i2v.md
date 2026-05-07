---
name: 视频复刻 AI 自由生成不准用 Kling 3 Pro i2v($0.80 太贵)
description: /video/replicate 的"AI 自由生成"引擎价格上限 $0.20-0.30/5s,Kling 3 Pro i2v $0.80/5s 被毙;当前底层是 Seedance v1 Lite $0.18/5s
type: feedback
originSessionId: bf5e5bd4-8e8a-4d86-89e7-4176ad18cde7
---
`/video/replicate` 的"AI 自由生成"引擎(前端 label,不暴露引擎名)价格红线:**$0.20-0.30/5s**。

**实证(2026-05-07,P164):** 用户测了 kling-3-pro-i2v($0.80/5s ≈ ¥5.6),反馈"太贵了找平替,顶多 0.2~0.3,自由发挥能接受"。换成 `fal-ai/bytedance/seedance/v1/lite/image-to-video` $0.18/5s。

**Why:** 用户对单段 5s 视频成本 < ¥2 是硬要求,Kling 3 Pro 即使效果好也用不起。

**How to apply:**
- 不要再提议 `fal-ai/kling-video/v3/pro/image-to-video` 给"AI 自由生成"档(就算用户说效果一般想升级)
- 候选平替按价排:
  - Seedance v1 Lite i2v($0.18/5s)← 当前在用
  - Pixverse v3.5/v4 i2v($0.20/5s)
  - Wan 2.2 5B i2v($0.10/5s,更便宜但不熟)
- 如果 Seedance Lite 效果不行,备选先试 Pixverse v3.5 i2v(同价位区间)
- engine value `kling-3-pro-i2v` 后端兼容映射到 seedance lite,新 value 是 `seedance-lite-i2v`
