---
name: replicate fal content_checker 是 image-level 不是 prompt-level
description: 内衣/塑身类产品 fal 看输入图就拒(prompt 再干净也没用)。compose 时要有"无 base 图"fallback
type: feedback
originSessionId: b9112ba0-66ed-4dfa-ba4d-391805539fe7
---
`/video/replicate` 流程接 fal GPT-Image 2 时,**fal content_checker 检查输入图片**,不只检查 prompt。

**实证(2026-05-07):**
- Step 1A 输入 `[product_front, product_back]` → ✅ 通过
- Step 1B 输入 `[base, product_front, product_back]` → ❌ 拒(base 含运动内衣模特,触发 image NSFW)
- 任何 prompt sanitize 都无效,因为问题在图层

**How to apply:**
- compose_first_frame_for_scene 必须有 `exclude_base_image` 参数 fallback 路径
- _gen_single_scene retry 顺序:1-3 次带 base(prompt sanitize 进阶)→ attempt 4 不带 base 只传产品图
- attempt 4 身份保真靠 model_description 文字描述(脸/眼/发/肤色/眉/嘴/鼻/身材全描述)
- 只在 attempt 1-3 都失败时才走 attempt 4(非敏感品类不浪费调用)
