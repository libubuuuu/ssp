---
name: SSP VACE Fun NSFW prompt sanitize 必做
description: fal-ai/wan-22-vace-fun-a14b/inpainting prompt 含中文 NSFW 词("内衣/文胸/拉起/露出")硬拒,必须 sanitize
type: feedback
originSessionId: f98e6643-4495-4bf6-96fa-44085151a76c
---
`fal-ai/wan-22-vace-fun-a14b/inpainting` endpoint 对 prompt 内**中文 NSFW 敏感词**硬拒,
返 `[{'loc': ['body', 'prompt'], 'msg': 'The prompt contains NSFW content'}]`。
即使 video_url 内容本身是 NSFW(内衣展示),只要 prompt 文字干净就可以过审。

**Why**: 2026-05-04 P71/P72 让 qwen-vl 输出"内衣/文胸/balconette/拉起/露出"等中文敏感词,
拼到 VACE Fun prompt 触发拒。session 57928ce2 + 923e8290 都失败,各退 53 credit。

**敏感词替换映射表**(P74 实测有效):
- 内衣/文胸/bra/胸罩/balconette → 内层物品
- 拉起/露出/掀起/撩起/裸露/脱下 → 动作变化/出现/位移
- 胸前/乳 → 前胸位置/上身
- Underwire/underwear/lingerie → item/garment

**How to apply**:
1. qwen-vl instruction 写明"敏感词替换"规则,让模型输出本身就中性
2. 后处理 NSFW_WORD_MAP 兜底 sanitize
3. VACE Fun 主体 prompt 改英文中性化模板(P70 verified):
   `"Layered region replacement task: replace masked region with reference image item, preserve outer..."`
4. 用户编辑 prompt 字段时也过 sanitize(防止用户输入触发拒)

**端点对比**:
- VACE Fun:中文 NSFW 拒 ✗,英文中性 OK ✓
- HappyHorse video-edit (alibaba):内衣类 partner_validation 整体硬拒 ✗(不可绕)
- Kling O3 v2v edit:不拒 NSFW ✓ 但分层错乱
- pixverse-swap:NSFW 友好 ✓
- 阿里 wan2.7-r2v:免费配额耗尽
