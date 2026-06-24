---
name: project_ssp_2026_06_25_daily
description: SSP 2026-06-25 大改动汇总 — 图生视频 i2v 从 fal 切 aiview Seedance 分档计费并上线
metadata: 
  node_type: memory
  type: project
  originSessionId: 59edc500-4133-44ab-8431-a48cce13d817
---

2026-06-25 图生视频(image-to-video)切供应商并上线，main 已到 `966f23e`，线上=blue。

## 改动 (commit 966f23e)
- **图生视频 i2v 从 fal kling → aiview Seedance**，按 模型×分辨率 分档计费。
  - `VIDEO_I2V_RATES`：fast 480/720=45/55 积分/秒；标准 seedance-2-0 480/720/1080=50/65/100。前后端价表必须一致。
  - `_normalize_i2v_model`(未知模型 strict 时 400)、`_resolve_i2v_resolution`(不支持的分辨率降该模型最高档，不静默回 480)。
  - `_run_aiview_i2v_job`：纯图 `submit_video`(单 `image_url`，不传 reference_video_url)，走 harvester `aiview_video` 通道轮询，失败原样透传零重试。
  - `_run_video_job`：video_i2v 切 aiview；**video_edit/video_clone 仍走 fal，V2 零改动**。
  - 前端 video 页加 模型/分辨率选择器(分辨率按模型动态)，提交带 model+resolution，扣费提示按档算。

## 上线流程(本次实践，复用)
- probe 通过才发布：解密 env `openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d -in .env.enc -pass file:/etc/ssp/master.key`，直连 `submit_video`+`query_video` 实测单图 i2v → submit OK / 出片 OK(openapi/ 公有读)。烧 550 aiview 积分(见 [[project_ssp_aiview_test_cost]])。
- deploy.sh 自己 rsync /root/ssp→standby + 在 /root/ssp/frontend build + 跑预部署 pytest + 冒烟 + drain 旧槽。源是 **/root/ssp 工作树**，不是 main/不是 /opt。
- 真蓝绿目录 `/opt/ssp-blue`(8000/3000) 与 `/opt/ssp-green`(8001/3002)，active 看 nginx sites-enabled/default 的 proxy_pass 端口。本次切到 blue。

相关：[[project_ssp_2026_06_24_daily]] [[project_ssp_video_general_pipeline_locked]] [[feedback_ssp_fal_probe_first]]
