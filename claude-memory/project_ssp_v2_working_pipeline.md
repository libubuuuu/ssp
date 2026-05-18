---
name: ssp-v2-working-pipeline
description: "2026-05-13 6 处关键修后 V2 正确链路,任何一处回退都会直接坏给用户"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7c096b06-9598-4023-b283-d95e15842516
---

2026-05-13 多轮修 V2 视频复刻,**全链路正确**。下面 7 个配置 / 代码点**任意一处回退都会立刻坏给用户**,后续会话如非用户明确授权,不要动这些。

# 7 处必须保住的点

## 1. ENABLE_VIDEO_CLONE_V2=true 永久开
- 文件:`/opt/ssp/backend/.env.enc`(加密)
- 文件:`video_clone_v2_processor.py:_record_daily_spend` 已删自动 kill 逻辑
- 详见 [[project_ssp_v2_always_on]]

## 2. FAL_GENERATE_AUDIO = False
- 文件:`backend/app/services/video_clone_v2_pricing.py:36`
- **必须 False**。True 时 fal 自生成音频会被 fal 内容策略检查,"Output audio has sensitive content" 整段拒(同 input 上次过,这次拒 — 内容策略是 stochastic 的)
- 后端用原视频音轨 mux(_build_segment_clip 自动 fallback)

## 3. split_input_video 强制重编码切片(不用 `-c copy`)
- 文件:`backend/app/services/video_clone_v2_processor.py:114-152`
- ffmpeg 命令必须含:`-c:v libx264 -c:a aac -avoid_negative_ts make_zero -reset_timestamps 1`
- **不能再尝试 `-c copy`**。-c copy 从非关键帧切会让 video stream PTS 保留原值(eg seg_0 video start_time=3.722s,audio start_time=0),fal 收到"音频 8s 视频从 3.7s 起"的怪文件,前 3.7s 视频根本不送给 fal 生成,出来的是空/原视频脏帧。
- 见 [[feedback_ssp_ffmpeg_no_copy_first_segment]] 已踩两次

## 4. run_single_ai_segment_with_retry 最多 3 次尝试,每次换 seed
- 文件:`backend/app/services/video_clone_v2_processor.py:349-380`
- MAX_ATTEMPTS=3,seed / seed+1 / seed+2
- 2026-05-13 从 1 次重试 → 2 次重试,把单段失败率从 ~5-10% 压到 ~0.1%
- 8 段长视频整体成功率 99.2%-99.9%
- 同 seed 重试无用:fal 内容策略对同输入会一致拒

## 5. AI 段部分失败 → per-seg 独立归档 + 失败段退款(老板 v3 设计)
- 文件:`backend/app/services/video_clone_v2_processor.py:1057-1150`(ultimate 路径)
- v1 旧:partial 拼接成短视频(用户看不到失败,以为是完整)— 弃
- v2 (2026-05-13 早):任意 fail → 全额退 + 不拼 — 弃(损失成功段已生成的内容)
- v3 (本):3 段中 2 段成功 1 段失败 → 给用户 2 段独立可下载 + 退失败段。
  - 全失败 → status="failed",per-seg 退款
  - 部分失败 → status="partial_completed",成功段 emit_dual_versions 双版本归档,
    失败段 _refund_partial 退款
  - 全成功 → 走原 concat 流程
- 前端 partial_completed 状态渲染每段独立卡片(视频预览 + 下载)
- 不能再回退到任一旧版

## 6. add_watermark `-c:a copy` 保音轨
- 文件:`backend/app/services/video_clone_v2_watermark.py:228-240`
- ffmpeg 必须 `-c:a copy`,**不能用 `-an`**
- -an 会把 _build_segment_clip 提前 mux 的音轨全砍掉,成片静音

## 7. _build_segment_clip AI 段优先用 fal 音轨,无则 fallback 原视频
- 文件:`backend/app/services/video_clone_v2_processor.py:870-940`
- `_has_audio_stream(fal_local)` True → 直接 re-encode fal output
- False → 抽原视频对应秒数音轨 mux(配合 #2,FAL_GENERATE_AUDIO=False 下走这条路)

## 8. AI 段 setpts 拉伸到精确 plan duration(B'' 方案 2026-05-13 落地)
- 文件:`backend/app/services/video_clone_v2_processor.py:_build_segment_clip` AI 分支
- fal seedance r2v fast 输出比 input 短 0.08-0.1s(8s 请求出 7.917s),
  不拉伸成片秒数对不上(16s 期望出 15.875s)。
- ffmpeg setpts=PTS*ratio 拉伸视频到 target_dur,有 fal 音轨时 atempo=1/ratio 同步,
  无音轨时 -an 由后续 mux 原视频音轨补齐。
- 副作用:视频整体慢 1.05%,memory project_ssp_v2_setpts_tradeoff 已记录是显式决策。
- 没这条代码用户会立即注意到"16s 视频只出 15.875s"。

# 体系信任链(一条都不能断)

```
1. splitter 切出干净段(start_time≈0,duration 精确)
   ↓
2. fal 收到完整段(video 8s + audio 8s 对齐)
   ↓
3. fal generate_audio=False(不出敏感音频)
   ↓
4. 段 fal 完成 → 下载 → re-encode → mux 原视频音轨
   ↓
5. 任意段失败 → 整单 failed + 全额退(不 salvage)
   ↓
6. 所有段成功 → concat → 加水印(保音轨)→ 双版本归档
```

任意一步坏 → 用户看到的最终结果就是坏的。

# 跟其他 memory 的关系

- [[project_ssp_v2_always_on]] — V2 永久开关
- [[project_ssp_p221_v2]] — V2 早期上线状态(已过时,以本 memory 为准)
- [[feedback_ssp_ffmpeg_no_copy_first_segment]] — split 不能 -c copy 教训
- [[reference_fal_seedance_r2v]] — fal seedance r2v 端点参数实测
- [[feedback_ssp_no_pattern_match]] — 2026-05-13 这次连犯 2 次(第一次嘴硬说"并行 OK 没问题",第二次嘴硬说"前几秒像原视频是错觉")— 都没 ffprobe 实际文件就答,后悔
- [[project_ssp_v2_fal_cost_audit]] — fal cost 对账老问题(不归本 memory)
- [[project_ssp_v2_walltime_ux]] — 单段 walltime 体验雷(不归本 memory)

# How to apply

- 任何"优化"V2 的提议,先 grep 这 7 个点确认不被影响
- 用户报"V2 没声音 / 切片错 / 残片 / 静默失败" → 先查这 7 个点是否被回退
- 部署 V2 任何代码改动前必须跑 `pytest tests/ -k v2` 全过(89 例)
- 用户报问题前**先 ffprobe 实际文件**,不要凭代码逻辑脑补
