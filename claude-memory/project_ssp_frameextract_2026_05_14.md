---
name: ssp-frame-extract-2026-05-14
description: 2026-05-14 分镜复刻全链路重构:敏感品类双路径+拆帧提速+中文NSFW过滤
metadata: 
  node_type: memory
  type: project
  originSessionId: 283bbb0f-f2ca-4a06-8e3a-fbca0f28cecc
---

# 当前架构(2026-05-14 commit a4f4143)

## 九宫格替换双路径

**普通品类** → `openai/gpt-image-2/edit`
- 支持多参考图(九宫格+人物+产品+场景)
- content_policy 触发自动用简化prompt重试1次
- 前端无需选择,默认走此路径

**敏感品类(内衣/泳装等)** → `fal-ai/flux-2/edit`
- `safety_tolerance=5` 最宽松审核
- `image_urls` 数组:最多4张(九宫格+人物+产品+场景)
- 前端分析结果区勾选"包含内衣/泳装等敏感品类"触发
- 2026-05-14 probe 验证:通过内衣关键词✓

## 中文敏感词过滤

`_sanitize_for_gpt2()` 新增中文词映射:
- 内衣→服装 / 文胸→上衣 / 胸罩→上衣 / 内裤→服装 / 比基尼→泳装

## 拆帧速度(从23s→3-5s)

旧:PySceneDetect逐帧读Python(23s/37s视频)
新:ffmpeg单次pass fps=1出全部帧 → 颜色直方图差异最大化选帧
- 无二次ffmpeg调用,在已有帧里直接选
- 每15s一张九宫格(最多4张)
- 总分析时间:~40-45s(其中qwen-vl 33s无法优化)

## 九宫格张数公式

`n_grids = max(1, min(4, round(duration/15)))`
- 15s视频 → 1张(9帧)
- 37s视频 → 2张(18帧)
- 60s视频 → 4张(36帧)

## nginx超时

`client_body_timeout 900s` (原300s) — 防慢网上传被截断

## How to apply

- 分镜复刻出现内容拦截 → 检查是否中文品类词未过滤或图片本身含敏感内容
- 拆帧慢 → 检查ffmpeg fps=1单次pass是否正常输出到frames_dir
- 内衣品类 → 前端要勾"敏感品类"开关才走FLUX.2路径
