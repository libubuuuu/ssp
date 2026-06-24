---
name: project_ssp_v2_quality_upscaler
description: "视频复刻V2画质档=全程aiview;720p/1080p必须把enhance=True转告aiview让它自己提质(不是我方提质),不走fal"
metadata: 
  node_type: memory
  type: project
  originSessionId: d7348b8d-e92c-47aa-bc16-8660248b53ba
---

SSP 视频复刻V2「画质档」最终架构(2026-06-17 用户两次纠正,以本条为准)。

**核心模型(用户原话):我们 SSP 只是个【前端/传话筒】,第三方 aiview 才是生成方。
我们只负责【把用户选的参数传过去、把结果拿回来】,自己【不做任何加工/提质/放大/后处理】。
fal 弃用,全部走 aiview。**

**关键澄清(用户最新):`enhance` 不是"我方提质",而是【告诉 aiview 去提质】的开关。
720p/1080p 必须把 `enhance=True` 转告 aiview,否则 aiview 不会提质——用户付了高清的钱却拿到 480p。
所以 enhance 必须传。**(此前一度误以为 enhance=我方加工而删掉,是错的,已纠回。)

线上(green)已生效的全链路:
- 前端选 720p/1080p → `api/video_clone_v2.py:865` 写 DB `enhance=1`(480p=0)。
- `database.py:72-73`:`resolution` + `enhance` 两列。
- `video_clone_v2_processor.py:300`:读 DB enhance → 转告 aiview;720p/1080p 轮询拉到 240×5s=20min。
- `aiview_service.py:198-199`:`if enhance: body["enhance"]=True` 真发给 aiview。
- 死代码:`upscale_video`(老 fal 放大)函数还定义着但已无人调用,可清可留。

⚠️ 重大未解(2026-06-17 实测,11 单 enhance 仅 1 单成功 ≈9%):
- **enhance 路径目前约 90% 失败,但根因在 aiview 侧不在我方**。主因是 aiview 自己的高清提质
  (aiview 内部用 fal 做)塌:error_message="视频提质失败: fal 取结果返回 HTTP 422:
  Failed to download the file"(aiview 的 fal 下载不到待提质视频)。其次 aiview 500 内部错误、
  Invalid image、ReadTimeout。我方代码没调 fal(_upscale_final 是死代码)。
- 旧记忆"enhance 90% 失败"经验值是对的,但"我方bug真因"归因是错的——是 aiview 提质管线问题。
- 体验雷:enhance 单每次 aiview 轮询 5~6min,失败后 seed+1 重试 3 次 → ~16min 才退款。
  用户转 16 分钟最后失败,钱兜得住但付费档实质不可用。
- DB 锁仍偶现一次(628aa6f/27ee23e 之后),需继续盯。
- 定价待校准:720p=75/1080p=110 是按"便宜 fal 放大"成本定的;aiview enhance 直接高清生成成本更高,
  可能偏低甚至亏。processor 已落 `credits_used` 到日志,头几单跑完【必须回校 QUALITY_RATE_TABLE】。

定价(50×系数):fast 480p=55/720p=65;2.0 480p=60/720p=75/1080p=110。

关联 [[project_ssp_video_general_pipeline_locked]] [[feedback_ssp_endpoint_capability_mismatch]]
[[feedback_ssp_no_pattern_match]]
