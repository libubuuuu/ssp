---
name: AI 带货视频出图严禁字幕
description: /ad-video 功能 GPT-Image 2 出图绝对不能含任何文字/字幕/CTA/数字标签,字幕用户后期自加
type: feedback
originSessionId: b9112ba0-66ed-4dfa-ba4d-391805539fe7
---
AI 带货视频(`/ad-video`)生成的 base 帧 + 每段分镜图必须**完全无文字**:
- 禁:text overlay / captions / subtitles / CTA(BUY NOW / ADD TO CART)/ 数字(50 LEFT / -2 inches)/ Before-After 标签 / 价签
- 即使 visual_prompt 里写了 "text overlay 'XXX'",GPT-2 也必须 IGNORE

**Why:** 用户铁律 — 字幕由用户后期自己加,AI 出图加字幕反而是干扰。多次强调"不要加任何字幕"。

**How to apply:**
- `compose_first_frame` 调用时传 `no_text=True` 才生效(默认 False 不影响其他功能)
- 仅 `/ad-video` 路径开启(`api/ad_video.py:305` + `api/jobs.py` ad_video worker)
- `/replicate` 路径不开启(replicate per-scene 已有自己的 NO TEXT 规则,base 帧没要求)
- 不要混用 — 不要把 no_text 全局打开,要保留 compose_first_frame 在其他场景的灵活性
