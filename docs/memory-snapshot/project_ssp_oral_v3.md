---
name: SSP 口播工作台 V3 架构(P10-P30 八十四续)
description: Seedream + kling i2v / kling-o1-edit v2v 双引擎 + 无水印 + 选比例 + 历史同步 + 多文件并发 + 长视频 300s + ASR retry + lipsync 分段并发 + Step A 候选帧池,2026-04-30 → 05-01
type: project
originSessionId: c9fa48e7-3011-420b-bf5c-0b9b4cc66943
---
SSP `/video/oral-broadcast` 在 2026-04-30 八十四续(P10-P16)经历 6 轮重构,
最终用户验收通过:**8d2389eb-110 完整跑通 19s 视频成片,无水印,用户口头确认"测试了完全没问题"**。
之前已端到端跑通 2825356e-2c0(4 分 16 秒)+ 8d2389eb-110(P15+P16 无水印版)。

## 5 步管线最终架构(P16)

| 步 | 端点 | 说明 |
|---|---|---|
| 1 ASR | fal-ai/wizper | 不动 |
| 2 文案编辑 | 前端 + 1000 字符上限 | 不动 |
| 3 TTS | fal-ai/minimax/voice-clone + ORAL_BYPASS_VOICE_CLONE 兜底 | 不动 |
| **4 Step A** | **fal-ai/bytedance/seedream/v4/edit** | 多图融合(原视频首帧+模特图+产品图)→ vton 静图,产品+模特+原背景一锅出。`image_size` 按 `aspect_ratio` 选(P16) |
| **4 Step B** | **fal-ai/kling-video/o3/standard/image-to-video**(默认 i2v)| 用 vton 图作首帧,kling i2v 自由生成动作。**实测 74s/段 + 4 段并发 ≈ 3 分钟** |
| 5 lipsync | veed/lipsync(经济档)| 不动 |
| 归档 | 下载 fal final 落 `/uploads/oral/<uid>/<sid>/final.mp4` | **不烧 AIGC 水印**(用户明确要求) |

## P16 用户选比例(2026-04-30)

前端 Step 1 加 4 个 radio:自动 / 9:16 / 16:9 / 1:1。
- DB 列:`oral_sessions.aspect_ratio TEXT`(白名单 9:16/16:9/1:1/None)
- 后端:`StartRequest.aspect_ratio: Optional[str]`,`/start` 端点校验白名单 + 写库
- Step A 取 `session.aspect_ratio` 选 Seedream `image_size`(`{w:720,h:1280}` / `{w:1280,h:720}` / `{w:1024,h:1024}`),`None` 走 ffprobe 探原视频比例兜底
- Step B i2v 不传 aspect(image 决定输出比例)

## env switch:`ORAL_STEP_B_ENGINE`

- `i2v`(默认)→ kling/o3/standard image-to-video,产品锁死 + NSFW 容忍内衣
- `ltx` → ltx-2.3-22b distilled reference-v2v(动作复刻但产品会漂)
- `kling-v2v` → kling/o1/video-to-video/reference 老路(35-50min/段慢)

## 关键决策(都是踩坑后的)

1. **Step A 用 Seedream 而不是 cat-vton**:cat-vton 输出图带模特图棚景,送 Step B 后 kling 把视频背景漂走(P10)
2. **Step B 用 i2v 而不是 v2v**:任何 v2v reference 模型(LTX/kling/字节)都会"漂"产品细节,这是 v2v 架构通病。i2v 首帧物理锁死产品(P12)
3. **i2v 用 kling/o3/standard 而不是字节 seedance**:字节 seedance-2.0 fast 对内衣类硬拒 content_policy_violation,kling/luma/LTX 5 端点实测都过(P15)
4. **fal Status 对象判定用 type 不是 hasattr**:Status 类(Queued/InProgress/Completed)没有 `.status` 属性,通过 `type(s).__name__` 区分。老代码 `hasattr(s,'status')` 永远 False 让 9 个 session 死循环 timeout(P13)
5. **不烧水印**:用户明确要求,合规水印责任移 Phase 4 用户主导(P15)
6. **用户能选成片比例**:9:16/16:9/1:1/自动,默认跟随原视频(P16)
7. **成片同步到 generation_history**:`module="oral-broadcast"`,/tasks/history 页能看到(P17)
8. **上传 UI 卡死兜底**:外层 try/finally 包整个 createNew,任何路径(COS/chunk/失败)都保 setActiveUploads -1 / setPhase idle(P18)
9. **多文件并发上传**:主页 file input multiple + 任意时刻同时跑最多 5 个(activeUploads counter,Promise.all)。按钮文字"+ 新建(N/5)",满 5 灰掉(P19/P23)
10. **COS 直传暂禁**:子账号 ssp-sts-signer 只授权 PutObject 没 GetObject → backend finalize-cos 拉文件 403 → 前端 fallback chunk 反而**双倍流量**。临时让 /api/storage/presigned-put 返 503,前端立刻 fallback chunk 单次上传。**永久修法**:用户去腾讯云 CAM 给子账号加 cos:GetObject 权限,然后删除 storage.py 里 P22 的 raise(P22)
11. **长视频支持 5 分钟**:MAX_DURATION_SECONDS 60→300,Step B sem(3→5)。3 分钟视频拆 36 段并发 5 跑,wallclock ≈ 10-15 分钟。lipsync(veed/...)长度上限未实测,失败需做"拆 N 段 → N 个独立 oral session 并发 → ffmpeg concat"(P24)
12. **ASR step 加 3 次重试**:fal-ai/wizper 偶发返 500/网关抖动让 _run_asr_step 直接 fail_step1;虽然退款但用户得重新 /start 选档/模特/产品/比例,体验差。3 次尝试 + 5s/10s 退避包 fal upload + transcribe 整体块(P27)
13. **长视频 lipsync 分段并发 + ffmpeg concat**(P28):duration > ORAL_LIPSYNC_CHUNK_THRESHOLD_S(默认 60s)走分段路径——下载 swapped+new_audio → ffmpeg 切 N 段(段长 ORAL_LIPSYNC_SEG_LEN_S,默认 30s)→ 每段并发(ORAL_LIPSYNC_CONCURRENCY 默认 3)调 lip_svc.sync 段级 2 次重试 → ffmpeg concat demuxer(`-c copy` 优先,失败 re-encode fallback)→ 归档为 final.mp4。短视频继续走 P15 的整段路径不动。**2026-05-01 02:59 实测一次过**:session 59157836-c51,75.4s 视频 → 3 段 30+30+15.4s,并发 3 → wallclock **11 分 24 秒**(02:47:35 创建 → 02:59:00 completed),final.mp4 38MB / 720×1280 / 24fps / 75.435s(误差 64ms)。**段边界 artifact 未真听确认**——下次用户报"30s/60s 处嘴型跳"再缩段长 / 加 audio crossfade。
15. **kling-o1-edit 真 v2v 引擎**(P30,2026-05-01 04:54):用户反馈 i2v 出片"衣服像图,不是穿在身上的物理感",原视频里模特衣物是自然飘动的。**i2v 的根本限制:从一张静图重新想动作,模型没看过原视频**。Probe `fal-ai/kling-video/o1/video-to-video/edit`(8d2389eb-110 baseline 9s 段)实测 ✅:NSFW 内衣过审、$0.168/s × 9s = $1.51、3:48 出片、@Element 占位符语法换人换产品。同 probe `alibaba/happy-horse/video-edit` ❌(input + output 都硬审,关 safety 也拒)、`grok-imagine` 排除(无 reference 图)。
   - **架构**:Step A 仍跑(Seedream 多图融合,浪费 ~$0.04 但代码改动最小);Step B 加 `kling-o1-edit` 分支,直接吃原视频段 + 2 个 elements(模特 + 产品),完全不依赖 reference_image。SEG_LEN_S=8.0 留 2s 余量(文档 3-10s)。每个 element 必须 frontal_image_url + reference_image_urls(>=1),空数组返 `elementReferList: size must be between 1 and 3`。
   - **env 切换**:`ORAL_STEP_B_ENGINE=kling-o1-edit` 已写入 .env.enc(2026-05-01),green 接管,默认全局生效。回滚:删那行 + deploy。i2v 老路代码完整保留。
   - **价格**:60s 视频约 $10(vs i2v $3),长视频 1-3 分钟成本 $10-$30/段。值不值得用户决定。
   - **改的文件**:`/root/ssp/backend/app/api/oral.py`(L660 engine 决策 + L723 切段 + L772 _drive_one 新分支)。

14. **Step A 候选帧池**(P29):首帧不一定是合规静图(用户视频里"撩衣 / 露肤瞬间"会被 fal Seedream content_policy_violation 拒,虽然视频本身合规)。改用 `[0.5, 0.25, 0.75, 0.1, 0.9]` 5 帧候选(中点优先 + 两侧分散),逐帧送 Seedream,遇 content_policy 类错误自动换下一帧。**2026-05-01 02:54 实测**:session 59157836-c51,前两帧 (0.5/0.25) 都被拒,第三帧 (0.75) 过——这组比例覆盖率 OK,**不要乱改**。同 commit 修了 _run_inpainting_step 失败 guard 的 bug:老 guard 只允许 edit_submitted 状态被覆盖,但 TTS 并行已把 status 推到 tts_running → guard 直接 return → 僵尸 session 不退款(d419e6e7 踩过,手动 100% 退 202 积分)。改成"非终态都允许覆盖"(只拒 STATUS_TERMINAL_OK / cancelled / failed_*)。

## 关键文件位置

- `/root/ssp/backend/app/api/oral.py` — 主管线代码
  - `_run_asr_step` 内部 3 次尝试 + 5s/10s 退避(P27)
  - `_run_lipsync_chunked` + `_download_url_to`:长视频分段并发 + concat(P28)
  - `_run_lipsync_step` 入口按 duration 分流到 chunked / 整段(P28)
  - `_run_inpainting_step` Step A 候选帧池循环 + content_policy fallback(P29)
  - `_run_inpainting_step` 失败 guard 改"非终态都覆盖"(P29 修 bug)
  - L55 `MAX_DURATION_SECONDS = 300`(P24)
  - StartRequest 加 aspect_ratio 字段
  - L500-525 Step A `seed_size` 按 session.aspect_ratio 决定(ASPECT_PRESETS)
  - engine switch + endpoint/SEG_LEN_S/timeout 配置
  - _drive_one i2v / ltx / kling-v2v 三分支
  - L675 `Semaphore(5)` Step B 并发(P24)
  - _archive_lipsync_final(无水印归档,P15)
  - _run_lipsync_step 完成后 INSERT generation_history(P17)
  - finalize_cos_upload except 加 log_error stack(P20)
- `/root/ssp/backend/app/database.py` — aspect_ratio ALTER TABLE patch
- `/root/ssp/backend/app/api/storage.py:49` — presigned-put 直接 raise 503(P22 待用户加权限后删)
- `/root/ssp/frontend/src/app/video/oral-broadcast/page.tsx` — 主页:multi-file input + activeUploads 并发计数(P19/P23)
- `/root/ssp/frontend/src/app/video/oral-broadcast/[id]/page.tsx` — 详情页:Step 1 比例 radio(P16)+ 顶部右上"+ 新建"(P21)+ 完成区"+ 再做一个"(P17)
- `/root/ssp/frontend/src/app/tasks/history/page.tsx` — moduleLabel 加 "oral-broadcast"(P17)

## 实测 SLA(2026-04-30 P16 后,session 8d2389eb-110)

- 19s 视频 → 4 段并发 i2v(每段 5s)→ wallclock 3 分钟
- + lipsync ~1 分钟
- = 总 4-5 分钟出片
- 用户验收通过(无水印 + 产品锁死 + 模特身份保留 + 背景一致 + 嘴型对得上)

**Why:** P15-P16 是从"V3 架构 9 个 session 全死 poll bug + NSFW 拒 + 带水印"到"用户验收通过"的分水岭。
**How to apply:** 改 oral 链路时先看本笔记,别再回 cat-vton(P10 弃)/ seedance i2v(P15 弃,NSFW 不容忍内衣)/ wan-vace(P0 弃)。改默认 engine 改 ORAL_STEP_B_ENGINE env(.env.enc 里),不要改硬编码。改 fal 端点前必须先 probe(见 feedback_ssp_fal_probe_first.md)。
