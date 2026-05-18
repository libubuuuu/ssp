---
name: project-ssp-2026-05-17-daily
description: SSP 2026-05-17 AI爆款视频多语言全链路+视频质量改动汇总，最新 commit 4b5c055
metadata: 
  node_type: memory
  type: project
  originSessionId: 871366de-1aba-407d-999e-7185d1192573
---

# SSP 2026-05-17 大改动汇总

最新 commit: `4b5c055`，已 push + deploy（当前激活 blue）。

## 核心功能：target_lang 全链路

### 前端（video/general/page.tsx）
- `targetLang` state（默认 `"en"`），从 chat 消息文本自动提取语言关键词
- Step 3 非 chat 模式：目标市场↔分辨率之间加语言下拉
- Step 3 chat 模式：「开始AI对话」按钮前加语言下拉（确定脚本台词语言）
- chatShowParams 面板：分辨率下方加语言下拉
- 三处下拉共用 `LANG_OPTIONS` 常量（19种语言，fr/de/it/th/vi/id/ms/tr/ru/pl/nl/hi 已加）
- market 联动：选中国/中国大陆自动切 `zh`
- tab/flowStep 切换自动清 error
- **分辨率去掉480p**：只保留 1080P / 2K(+20积分) / 4K(+50积分)，默认 1080p

### 后端（video_general.py）
- `/chat` 接收 `target_lang`，注入林久 system prompt（「目标语言：English/日本語/...」）
- 林久 system prompt 修复：删掉「TikTok→英语 only」硬规则，改为尊重 target_lang
- 第一轮问题改为同时问平台+目标市场（⚠️强制LLM追问）
- `_call_copywriter`：`target_lang="zh"` 用中文 prompt，否则全英文 prompt
- `_LANG_NAMES` 7→19种
- `_call_xiaoli_search` 模型从 `gpt-4o-search-preview-2025-03-11` 改为 `gpt-4o-search-preview`
- **脚本语言强制验证**：台词含中文但 target_lang!=zh 时，自动触发重写（追加一轮对话）
- `ScriptToVideoRequest.resolution` 默认 "480p"→"1080p"
- `RESOLUTION_SURCHARGE`：删480p:0，1080p:0，新增2k:20，4k:50

### 后端（jobs.py）
- portrait 人种：`_ETHNICITY_BY_LANG` 19种映射，[模特]字段未写人种时自动前缀补全
- portrait prompt 改为 `"CRITICAL: This MUST be a {desc} person."` 人种置顶
- portrait prompt 加人种后 log `desc=...` 确认
- 每个 Task 加 `anchor=contrast/product stages=[...]` 日志
- prompt 日志 200→400 字
- **upscale 无条件执行**（原来 `if resolution!="480p"`，现在无条件）
- **fal 参数名修正**：`resolution` → `target_resolution`（bytedance-upscaler 正确字段名）
- 执行顺序：Seedance → concat → upscale → lipsync（高分辨率视频做口型）
- batch_max_duration fallback 统一改 15

### 已修 bug
- `generate_scene`：`image_size: "portrait_9_16"` → `{"width":1024,"height":1792}`（fal 拒 preset 字符串）
- `batch_max_duration`：DB app_config 里写死 `8`，直接 UPDATE 成 `15`；代码 fallback 也统一改 15
- `script-to-video` 400 时 log script 前200字（根因追踪）

## 关键架构说明（下次不用重查）

### 视频生成 pipeline（_run_script_to_video_job）
```
Seedance 分 batch 并行（portrait 同时生成）
    ↓ 串行拼接
concat（ffmpeg）→ fal_upload → video_url_out
    ↓ 无条件 upscale（target_resolution=1080p/2k/4k）
fal-ai/bytedance-upscaler/upscale/video
    ↓ 有声音时
fal-ai/musetalk（source_video_url + audio_url）
    ↓
最终 video_url_out
```

### generate_scene 端点
- 调 `fal-ai/gpt-image-2`
- `image_size` 必须传 dict：`{"width": 1024, "height": 1792}`（portrait）
- 不接受字符串 preset（`"portrait_9_16"` 会报 validation error）

### batch_max_duration
- 存在 `app_config` 表，DB 值优先于代码 fallback
- 当前值：`15`（已修）
- 查询：`SELECT value FROM app_config WHERE key='batch_max_duration'`

### portrait 生成
- 调 `fal-ai/gpt-image-2`，`quality="auto"`，`image_size="square_hd"`
- prompt: `"CRITICAL: This MUST be a {portrait_model_desc} person. Professional portrait photo..."`
- `portrait_model_desc` = 人种前缀 + model_desc（[模特]字段已含人种词则不重复加）
- portrait 放在 Task 1 image_urls 最后一位，Task 2+ 通过 last_frame 延续

### lipsync（musetalk）
- 端点：`fal-ai/musetalk`，参数：`source_video_url` + `audio_url`
- 无防抖参数，抖动根因是 MuseTalk 对齐问题
- wav2lip（`fal-ai/wav2lip`）有 `nosmooth/pads` 防抖参数，已 probe 成功，备选切换

### upscale（bytedance-upscaler）
- 端点：`fal-ai/bytedance-upscaler/upscale/video`
- 参数：`video_url` + `target_resolution`（必须是 target_resolution，不是 resolution）
- 定价：1080p=$0.0072/s，2K=$0.0144/s，4K=$0.0288/s

### 小李搜索
- 模型：`gpt-4o-search-preview`（无日期后缀）
- 灵梦「限时特价」分组有时无渠道，搜索失败时静默跳过（不阻断主流程）

**Why:** 2026-05-17 全天集中落地 target_lang 多语言支持 + 视频质量提升（upscale→lipsync、portrait人种、脚本语言验证）。
**How to apply:** 下次接触 AI 爆款视频相关代码，target_lang 全链路已通，pipeline 顺序已固定，不需要重新调研。
