项目进度日志,每次收工前更新

## 2026-04-29 七十七续(口播带货工作台 P1~P5 续 — 经济档完整端到端)

完整规划见 `docs/ORAL-BROADCAST-PLAN.md`(1002 行,3 档定价 + 5 步 pipeline +
mask 方案研究)。本续从骨架到端到端可跑,共 7 个 commit。

### P1 — 数据层 + 6 端点骨架(commit `186e8d5`,03:09)

- `database.py` 新表 `oral_sessions`(32 字段)+ 2 索引;init_db 启动自动建。
- `billing.PRICING` 加 3 档:economy 160 / standard 360 / premium 700 积分/分钟,
  按秒 ceil(`oral.compute_charge`)。
- `/api/oral/{upload, start, edit, status, cancel, list}` 6 端点骨架。
- L1 用户责任声明强制勾选 + audit_log 写入(action=oral_legal_consent)。
- 测试 +33(422 → 455 全过)。
- ✅ 蓝绿 deploy(green → blue,03:08)。

### P2 — ASR + 经济档 voice-clone + 异步状态机(commit `5e4b4bb`,03:19)

- `FalASRService`(fal-ai/wizper,$0.0005/min)+ 注册到 init_fal_services。
- `FalVoiceService.clone_voice` 修返回结构(读 result.custom_voice_id,旧版 hash
  假造已废弃);audio.url 兼容 dict/str。
- `_extract_audio_track`:ffmpeg 完整音轨 → audio.mp3(送 wizper),前 10 秒 →
  voice_ref.mp3(送 minimax voice-clone)。
- `_run_asr_step` / `_run_tts_step` 异步驱动,asyncio.create_task 在 /start /
  /edit 末尾触发;失败按规划 §7.2 退款(ASR 100% / TTS 95%);状态被改(cancel)
  时不覆盖。
- 测试 +7(455 → 462 全过),fal 链路全 monkeypatch。
- ✅ 蓝绿 deploy(blue → green,03:19)。

### P3 — wan-vace inpainting + lipsync + Step 3/4 真并行(commit `925f935`,03:30)

- `FalInpaintingService`(fal-ai/wan-vace-14b/inpainting):3 档分辨率
  economy=480p / standard=580p / premium=720p,mask_image_url +
  reference_image_urls + prompt + 熔断。
- `FalLipsyncService`:economy=veed/lipsync / standard=fal-ai/latentsync /
  premium=fal-ai/sync-lipsync/v2,统一 sync(video, audio, tier) 接口。
- `/edit` 同时 create_task TTS + Inpainting 真并行(规划 §2 Step 3/4 并行)。
- 经济档完整后端就绪,等 P4 前端 + 真实视频 PoC。
- ✅ deploy(green → blue,03:31)。

### P4a — /video/oral-broadcast 5 步 UI 基础版(commit `0e7787c`,03:40)

- 列表页 + 工作台(`[id]/page.tsx`),5 步:档位/确认 → ASR 编辑 → 进度 → 预览 →
  失败/退款。
- 4s 轮询 /api/oral/status,localStorage token + credentials:include 双轨。
- mask 用占位方案("用户用 Photoshop 画 PNG 上传"),canvas 编辑器留 P4b。
- i18n zh/en `oral.*` namespace 60+ 双语 key + Sidebar 🎤 入口。
- frontend build 0 error(40 静态 + 8 动态路由)。
- ✅ 蓝绿 deploy(blue → green,03:40,frontend rebuild)。

### P4b — canvas mask 编辑器(原生 HTML5 自实现)(commit `8a6757e`,03:47)

- 不引第三方(react-konva / fabric.js),原生 HTML5 + React 自实现避免
  依赖膨胀 + SSR 兼容问题。
- `MaskEditor.tsx`:首帧抽取 + 双画布(背景首帧 / 前景 mask 半透叠加) +
  brush/erase/rect 三工具 + 大小滑动条 + clear + pointerCapture +
  CSS 缩放坐标换算 + toBlob 上传。
- 后端 `/api/oral/status` products 加 `original_video_url`(同源资源,无 CORS)
  + `mask_uploaded` boolean(真值来自后端,前端不靠本地 state)。
- 删除原 maskUploading/maskUploaded 本地 state,改读 sess.products.mask_uploaded。
- ✅ 蓝绿 deploy(green → blue,03:42,frontend rebuild)。
- **至此 P4 完整,经济档全链路可端到端跑。**

### P5 fix — 上传慢 → 5MB 分片 + 进度条 + 3 次重试(commit `030254a`,04:00)

诊断:
- 服务器实测 upload 27 Mbps 出口,用户上行典型 5-20 Mbps;60s 视频 50-100MB
  单次 multipart 30-300s,没进度条 → 用户感觉灾难。
- P4a 跳过了 video_studio 早就有的 /upload-chunk 模式,图省事用单次 multipart,
  低带宽用户上完全不可用。

修(仿 video_studio,5 文件):
- `POST /api/oral/upload-chunk`:upload_id 16hex 防路径穿越 / 单 chunk ≤ 10MB /
  累计 ≤ 200MB / 同 user 并行 ≤ 5 / 流式落盘 1MB chunks 不占内存 / 最后一片
  自动合并 + ffprobe 时长校验 + 创建 oral_session。
- 临时目录 `/opt/ssp/uploads/oral/_uploading/<uid>_<id>`。
- 前端 5MB 分片循环 + 单片失败 3 次重试(1s 间隔)+ 实时显示速度 MB/s + ETA。
- 进度条 80px 高度,#0d0d0d 黑色填充,transition 0.2s。
- backend test_oral.py 55 全过 / frontend build 0 error。
- ✅ 蓝绿 deploy(blue → green,04:00 切换)。

### P5 续 — 浏览器侧 MediaRecorder 视频压缩(commit `d58e2fc`,13:30)

诊断:P5 fix 解了"分片不会失败",但低带宽用户传 60s 1080p 原始 50-100MB 仍要
1-3 分钟;再加一层浏览器压缩可以砍 80% 流量,不依赖后端 / 第三方 SDK / wasm。

`frontend/src/lib/utils/videoCompress.ts`(新文件 202 行):
- canvas drawImage 跟 video.currentTime 重绘,降到 1280px 宽。
- canvas.captureStream(30) + video.captureStream() 取 audio track(后端 ASR 要从
  音轨提音频文本,不能丢)。
- MediaRecorder vp9+opus / 1.5Mbps 视频 + 128kbps 音频 → webm(后端
  LONG_VIDEO_MIMES 已含)。
- 兼容性 fallback(MediaRecorder / captureStream / 编码器 不支持)→ 返原文件
  透明继续走分片上传。

`oral-broadcast/page.tsx`:createNew 先压缩再分片;ratio < 0.9 才用压缩版本
(< 10% 收益不值得换 webm);双阶段 UI phase ∈ {idle, compress, upload}。

i18n:zh + en 各加 oral.compressing / oral.compressDone。
frontend npm run build 0 error 0 warning,后端无改动。

**⚠️ 本 commit 上次会话结束时未 deploy 也未 push,本续(2026-04-29)统一收尾。**

### 总账(七十七续)

- 7 个 commit,后端 +800/前端 +900 行(含测试 +40)
- 后端测试 422 → 462(+40 全过)
- frontend 路由 +2:`/video/oral-broadcast` 静态 + `[id]` 动态
- 已完成:经济档(MiniMax + wan-vace + veed/lipsync)端到端
- 待用户:
  - **真实视频 PoC** — 跑一段验证 wan-vace salient tracking 鲁棒性(规划 §14.4)
  - **ElevenLabs API key** — 解锁标准/顶级档(P6)
  - **L2 AIGC 水印**(ffmpeg drawtext burn-in)留 Phase 4 合规批次
- 下一步默认:等 PoC 反馈 → 调档位/分辨率/价格,再 P6 接 EL。

---

## 2026-04-29 七十六续(长视频模型可切换架构 + admin 灰度 kling/reference)

### 诊断
用户长视频工作台 video/replace/element 出现"空气穿衣"(衣服飘人物外不贴合)。
要架构层可切换 + 灰度通道,但**默认行为零变化**(不能偷偷动现有付费用户 mode=o3)。

### Step 2 — 架构(commit `ec7444f`)

`config.py`:加 3 个 env(默认全空 → 走代码默认值)
- `STUDIO_VIDEO_MODEL_EDIT`       覆盖 mode=edit
- `STUDIO_VIDEO_MODEL_EDIT_O3`    覆盖 mode=o3(中文口播)
- `STUDIO_VIDEO_MODEL_OVERRIDE`   非空时无视 mode 全用它(灰度开关)

`services/fal_service.py`:`FalVideoService` 重构
- `MODELS` class-dict → `DEFAULT_ENDPOINTS` + `LABELS`(`MODELS @property` 保留兼容)
- `_resolve_endpoint(model_key)` → `(endpoint, source)`;优先级 `OVERRIDE > 单 mode env > DEFAULT`
- `_generate_video` 加 fallback:override 路径用独立 circuit_breaker key
  (`override:{endpoint}`),失败 3 次自动熔断 + 回退 default。每步 stderr 打日志
  (`FAL_SUBMIT[source]` / `FAL_OVERRIDE_FAIL` / `FAL_OVERRIDE_CIRCUIT_OPEN`)给 Sentry 抓
- 返回新增 `model_source` 字段给前端/admin 看实际跑哪个

`api/admin.py`:`GET /api/admin/studio-model-status`
- 当前 3 个 env 值
- 每个 mode 解析后的 endpoint + source
- `STUDIO_TASKS` 内 batch_results 聚合(GC 24h 自然就是近 24h)
- 失败原因 top 3(给灰度判断)

测试 +12(test_studio_model_switch.py)— 4 种优先级组合 + fallback 三路径 + admin 端点鉴权与聚合。

### Step 3 — admin 灰度(commit `1179e91`)

`video_studio.py:476` 选 `model_key`:
- `mode=o3` → `kling/edit-o3`(中文口播,不动)
- **admin role + mode=edit → `kling/reference`**(空间引导,改善空气穿衣)
- 普通用户 → `kling/edit`(零影响)

stderr 打 `STUDIO_GRAYSCALE` 日志便于 Sentry / 巡检看灰度命中。
新增 4 例覆盖 admin/普通 × edit/o3 四种组合,**全套 422 passed**。

### 已 deploy 进生产 ✅(2026-04-29 02:06)
蓝绿 green → blue。验证:
- `GET /api/admin/studio-model-status` 401(端点存在,鉴权 OK)
- `GET /api/jobs/list` 401(七十五续 jobs 合并代码生效)
- `https://ailixiao.com/` 200

### 灰度阶段(用户主导)
admin 账号自测 3-5 段真实视频,对比新老模型,肉眼标注"贴合 / 不贴合 / 部分贴合",
积累比例数据再决定 Step 4 全量切换。

---

## 2026-04-29 七十五续(My Tasks 合并 long-video sessions — 入口找不到 fix)

### 用户反馈
"长视频翻拍跑完后 My Tasks 找不到入口" — 因为 `STUDIO_TASKS` 是独立 in-memory dict,
`/api/jobs/list` 只读 `JOBS`,长视频 session 完全不进 My Tasks。

### 修(虚拟 job 视图,不动 `STUDIO_TASKS` 真实结构)

`backend/app/api/jobs.py`:
- 新增 `_studio_sessions_as_virtual_jobs(user_id)`:从 `STUDIO_TASKS` 把当前用户的、
  有 `batch_results` 的 session 转成虚拟 job
- id 形如 `studio_{sid}`,`type=long_video`
- status 推导:`final_url` → completed / 全 failed → failed / 其他 → running
- 标题动态:`X/Y 完成,Z 生成中` / `等待合并` / `全部完成`
- `/list` 接口在排序前 `extend` 进 `mine[]`

`frontend/src/components/JobPanel.tsx`:
- Job interface 加 `_long_video` / `_session_id` / `_route`
- card 整条点击 `router.push(_route)`,关弹层
- 删除按钮换成 `↗ 打开`(防误删长任务,且 `studio_xxx` 没 DELETE)
- typeLabel 加 `long_video` → "长视频翻拍"

`test_jobs.py` 全套 20 passed,frontend build 通过。

### 已 deploy 进生产 ✅(2026-04-29 同 Step 3 一起 deploy)

---

## 2026-04-28 七十四续(batch-status + split fal-upload 并发化 — 4-5s → 1s)

### 诊断
用户反馈"上传/使用很慢",backend log 真因清晰:
- `/api/studio/batch-status` 每次 4.3-4.9s(用户每 3s 轮询,体验灾难)
- `/api/studio/split` 45s(N 段 ffmpeg 完后串行 fal upload 跨境)

### 修(`asyncio.gather` 并发,commit `466165c`)

1. **batch-status**:之前 4 段 fal `get_task_status` 串行 4×1.5s=6s,并发后 max ≈ 1.5s。
   已完成 / 已失败的段不查 fal,直接走内存计数。`gather return_exceptions=True`
   防 fal 抖动单段挂全请求。
2. **split**:ffmpeg 切片仍 `Semaphore` 串行(CPU bound),但 fal upload 抽出来
   `asyncio.gather` 并发。N 段视频(典型 4-8 段)总上传时间从 N×5s → max(单段)≈ 5s。

测试 +23,全套 400 passed。

---

## 2026-04-28 七十三续(前端图片压缩 — 5MB → 500KB,上传 30s → 3s)

### 诊断真因(用户给定)
- A 方案中转(用户→服务器→Pillow→fal)走两段网络 + 一次重压缩
- 单次上传 30-50s,占满 nginx worker,拖慢全站
- B 方案 OSS 直传是终极解,但用户开 COS 账号需时间

### 解(commit `5b1c9a2`)
`frontend` 引 `browser-image-compression` 浏览器侧压缩:
- maxSizeMB=0.5 / maxWidthOrHeight=1920 / use webworker
- 上传前压缩,5MB JPG → ~500KB,30-50s → 3-5s
- 透明降级:压缩失败 / 浏览器不支持 → 走原图(不阻塞流程)

挂在所有 image upload 入口(image / video / studio / quick-ad)。

---

## 2026-04-28 七十二续(B 方案 OSS 直传 STS 凭证签发 — 解出口带宽 32Mbps 瓶颈)

### 诊断
- 服务器轻量套餐出口 32 Mbps + 跨境 fal CDN(美国)= 100MB 视频 1-3 分钟
- 入口 206 Mbps 不慢,慢的是服务器→fal 出口段(A 方案中转放大瓶颈)

### B 方案(commit `fb56877`)
- 用户前端 → COS 直传(走自己上行 50-200 Mbps + COS 国内 GB/s 入口)
- 服务器只签 STS 临时凭证(15 分钟有效),无文件流量
- 上传完 public_url 喂 fal API,fal 自己拉
- **预期 3-10 倍提升**

代码:
- `services/storage_sts.py`:腾讯云 STS SDK 调 `GetFederationToken`,policy 限定只
  `PutObject` 到 `uploads/<user_id>/<timestamp>_<filename>`
- `api/storage.py POST /api/storage/sts`:鉴权 + 签发凭证
- `config.py`:7 个 `STORAGE_*` 字段(默认空,`STORAGE_DIRECT_UPLOAD_ENABLED=false` 时 503)
- `requirements.txt`:`tencentcloud-sdk-python-sts`(只 STS 子包)

### 安全
- STS 凭证 15 分钟过期 + 路径隔离
- 文件名清洗(`/`、`..`、特殊字符 → `_`)
- 子账号最小权限(只 STS 签发,不用 root key)
- bucket 格式校验(必须 name-appid 形式)

测试 +6(394 → 400):未启用 503 / 鉴权 401 / 启用后 mock STS 返凭证。

**待用户**:开 COS 账号 + 配 STS 子账号 + 填 env 启用。

---

六十九 ~ 七十一续(快速带货 prompt 工具 + 视频处理 5 项优化 + 独立 GC)
已归档到 [`docs/PROGRESS-archive/2026-04.md`](docs/PROGRESS-archive/2026-04.md);
更早的三十四 ~ 六十八续也在该文件,本文件仅留最近 5 续。
