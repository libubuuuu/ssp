---
name: 视频复刻图必须 GPT-Image 2 出,不准任何后处理
description: replicate 工作台所有 image generation 必须由 GPT-Image 2 直接产生,不准 cat-vton/seedream 等后处理修改 GPT 输出
type: feedback
originSessionId: d8a42b7d-70f3-43b8-93d8-3466f6b2b64f
---
`/video/replicate` 工作台里,所有作为视频模型 reference 的图片**必须由 GPT-Image 2 直接生成**。不允许在 GPT 出图后再调任何模型(cat-vton / seedream / inpainting 等)对图做修改。

**Why:** 用户铁律:"图一定要交给 gpt2 做"。任何后处理(包括 cat-vton)都会偏离 GPT 的语义意图,造成视频效果不可控。用户已实测 cat-vton 改图后视频质量明显差。

**How to apply:**
- pixverse-swap 引擎:`reference_image` 直接传 `frames[i]`(GPT-Image 2 出的 per-scene frame)
- 不要把 cat-vton 的 vton 输出喂给 pixverse 或别的视频引擎
- catvton-pixverse 引擎已弃用 cat-vton 中间步(直接转发 pixverse-swap),保留 engine 名仅兼容老 job
- GPT-Image 2 prompt 要写好:产品图作 image_urls 第 2/3 张让 GPT 看真品,prompt 强调"preserve product details EXACT match"
- 检查代码:`backend/app/api/jobs.py` 的 `_gen_videos_pixverse_swap` 和 `_gen_videos_catvton_pixverse`(commit 018b075)
