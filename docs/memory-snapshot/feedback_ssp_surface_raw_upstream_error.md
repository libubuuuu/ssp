---
name: feedback_ssp_surface_raw_upstream_error
description: "出错时直接甩第三方(aiview/fal)返回的原始 error_message,不加工不脑补不编理论"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: dc4d0bff-7851-4f82-8ac9-432079e8c72e
---

用户明确要求(2026-06-17):**任务失败时,直接把第三方(aiview/fal)返回的原始 error 拿出来给用户/给我看就行,不要自己包装、推断、编故事。**

**Why:** 用户多次因为我"脑补"(从几单外推大结论、把上游错误说成可能是我方问题)被激怒。
第三方返回的 error_message 是事实,直接呈现最快最准。

**How to apply:**
- 报失败原因 = 引日志/DB 里 `error_message` 原文 + `code`/`http`,一句话。例:
  「aiview 返回:服务器内部错误,请稍后重试 (code=50001) —— 已退款 650」。
- 注意区分归属:`http=500 code=50001 服务器内部错误`、`视频提质失败: fal...`、`Generation failed`
  这些都是 **aiview 在响应里返回的**,不是我方服务器报错(我方 /health=200 时尤其要讲清)。
- 不要从单次/小样本失败外推"功能坏了/要砍档"这类大结论(已因此挨骂,见 [[feedback_ssp_no_pattern_match]])。
- 别堆分析墙、别列选项菜单(见 [[feedback_ssp_user_time]]);先给原始错误 + 当前状态(是否已退款)。

关联 [[feedback_ssp_no_pattern_match]] [[feedback_ssp_user_time]] [[project_ssp_v2_quality_upscaler]]
