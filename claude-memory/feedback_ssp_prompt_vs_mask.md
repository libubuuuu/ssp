---
name: SSP prompt 拗不过 diffusion 架构,mask 才是硬约束
description: Kling/Sora/Wan 等 diffusion 视频模型 prompt 是软引导,精细 prompt 工程仍拗不过模型本能,要精确控制必须 mask
type: feedback
originSessionId: f98e6643-4495-4bf6-96fa-44085151a76c
---
**diffusion 视频模型(Kling O3 v2v / Sora 2 / Wan / 即梦底层)的 prompt 不能强制控制输出**:
- prompt = 软引导(模型采样时综合考虑,不严格执行时序/层次描述)
- 视觉信号(reference 图)权重 > 文字 prompt 权重
- 训练数据偏置(internet 内衣图都是显眼独立)→ 模型本能把产品图贴到最显眼位置
- 写得再精细的 LAYER_LOCK / WARDROBE_LOCK / 时序分镜 prompt 都拗不过模型本能

**Why**: 2026-05-03 P59-P64 在 Kling O3 v2v edit 上磨了 6 轮 prompt:
- P59 改 image_urls 用原图
- P61 加 BG_LOCK
- P62 加 WARDROBE_LOCK
- P63 砍 @Element2
- P64 加 LAYER_LOCK("@Element2 stays UNDER the shirt")

全部失败,因为问题在模型架构层级而不是 prompt 字句。

**How to apply**: 真要"区域级精确控制" → 用 mask 硬约束:
- fal-ai/wan-22-vace-fun-a14b/inpainting:`mask_video_url` 强制只在 mask 内 inpaint
- 配合 fal-ai/sam2/video 自动追踪(box_prompts 种子)
- prompt 仅作辅助辅助层次/产品识别(qwen-vl-max 视频理解可生成精细分镜)

提示词工程是边际改善(<10%),不是根本突破。任何 prompt 路线在用户报"模型不听 prompt"时 → 直接转 mask + reference 工程,不再投资 prompt 调优。
