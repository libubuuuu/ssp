---
name: 国内模式(region=CN)出图必须亚洲面孔
description: ad_video / replicate 等任何用 GPT-Image 2 出模特图的功能,region=CN 时模特必须是亚洲面孔(黄皮肤/黑发/东方五官),严禁 Caucasian/blonde/西方人
type: feedback
originSessionId: bf5e5bd4-8e8a-4d86-89e7-4176ad18cde7
---
用户铁律:**只要用户选"国内模式"(region=CN),GPT-Image 2 出的模特一定要是亚洲长相**。绝不允许出 Caucasian / blonde / 西方面孔。

**Why:** 国内电商带货视频的核心要素之一,模特长相必须贴合国内用户审美。否则视频投放在抖音/小红书等国内平台会显得"不真实/广告感强"。这是产品定位的硬要求。

**当前实现(2026-05-07,P99/P100):**
- 前端 `/ad-video` 有 region 选择(CN 国内抖音 / Global 海外 TikTok)
- 后端 `vlm_service.py _build_analysis_prompt()` 在 region=CN 时强制 prompt:
  > "亚洲面孔(东方五官)/ 自然黄皮肤 / 黑色或棕黑色头发 / 真实素颜或淡妆 / 22-30 岁"
- VLM 出 model_description → 后续 GPT-Image 2 出图时会按 model_description 渲染
- 实测(2026-05-07 19:38)CN 模式 VLM 出"Asian woman, yellow skin tone, black straight hair" ✅

**How to apply:**
- 用户报"国内模式出白人"→ 立即查 `ad_video/analyze region raw=` 日志确认 region 是不是 CN
- 如果 region 真是 CN 但出白人 → VLM 没听话,加二次保险(detect 关键词 → 重试 VLM 或强制覆写 model_description)
- 视频复刻(/video/replicate)目前没有 region 选项,共享 ad_video 的 model_description 提取逻辑,要确认是否也需要 region 切换 UI
- 任何新功能用 GPT-Image 2 出模特,默认 CN 模式,prompt 强制写"Asian"或"亚洲面孔"
