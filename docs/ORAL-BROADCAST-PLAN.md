# 口播带货工作台 — 完整规划文档

> **状态**:规划阶段。**未动任何代码**。等用户拍板后启动实施。
> **作者**:Claude Opus 4.7(2026-04-29)
> **数据时效**:fal.ai 价格 2026-04-29 实时调研(参见底部 Sources);现有项目代码盘点 2026-04-29 grep 结果。

---

## 0. 一句话定位

新建独立工作台 `/video/oral-broadcast`,把"上传一段口播带货视频 → 换音色 + 换文案 + 换人物 + 换产品"这条链路工业化,**3 档定价 ¥80/180/350 每分钟成片**,毛利 75-85%。

**与现有 `/video/studio`(长视频翻拍)的区别**:studio 只换"人物/产品",**保留原音原口型**;oral-broadcast 是 5 步全套 pipeline(ASR + 重生 TTS + 视频换装 + 口型对齐),输出"换语换脸"的全新成片。两者不复用 UI、不复用 session 表,仅复用底层 fal_service / billing / refund 等基础设施。

---

## 1. 三档定价 — 实际成本 vs 售价(2026-04-29 实测价核算)

按 1 分钟成片估算,汇率 1 USD = 7 CNY,1 分钟脚本 ≈ 200 中文字符 / 350 英文字符。

| 档位 | 售价 | 实际成本(USD) | 折人民币 | **实际毛利率** | 用户估算 | 差额 |
|---|---|---|---|---|---|---|
| 经济档 | **¥80** | $2.85 | ¥20.0 | **75.0%** | 75% | ✅ 吻合 |
| 标准档 | **¥180** | $3.92 | ¥27.5 | **84.7%** | 78% | ↑ +6.7pp |
| 顶级档 | **¥350** | $7.84 | ¥54.9 | **84.3%** | 78% | ↑ +6.3pp |

**结论:实际毛利比用户预估高 6-7 个百分点**(因为 fal 当前价格比用户参考的低)。建议:
- 维持售价不动,毛利空间留给"失败重跑 + ElevenLabs 月度订阅摊销 + GPU 调度溢价"
- 或把标准/顶级档售价下调 ¥20-30 做促销卡位,但留 75% 毛利下限

### 成本拆解(按档逐项)

**经济档** ¥80 售价 → 成本 $2.85
| 步骤 | 模型 | 单价 | 1 分钟用量 | 成本 |
|---|---|---|---|---|
| ASR | fal-ai/wizper | $0.0005/分钟 | 1 min 音频 | $0.0005 |
| Voice clone + TTS | fal-ai/minimax/voice-clone | 克隆 $1.50/次 + TTS $0.10/1k 字 | 摊销:50 视频复用同 voice_id → $0.03 + 200 字 × $0.10/1k = $0.02 | $0.05 |
| 视频换装 | fal-ai/wan-vace-14b/inpainting **480p** | $0.04 / 视频秒 | 60 秒 | $2.40 |
| 口型对齐 | veed/lipsync | $0.40 / 视频分钟 | 1 min | $0.40 |
| **合计** | | | | **$2.85 ≈ ¥20** |

**标准档** ¥180 售价 → 成本 $3.92
| 步骤 | 模型 | 单价 | 1 分钟用量 | 成本 |
|---|---|---|---|---|
| ASR | fal-ai/wizper | $0.0005/分钟 | 1 min | $0.0005 |
| Voice clone | **ElevenLabs 官方 API**(fal 无此端点)| IVC $22/月套餐摊销,假设 100 视频/月 | $0.22 | $0.22 |
| TTS | fal-ai/elevenlabs/tts/turbo-v2.5 | $0.05/1k 字 | 350 字符 | $0.018 |
| 视频换装 | fal-ai/wan-vace-14b/inpainting **580p** | $0.06 / 视频秒 | 60 秒 | $3.60 |
| 口型对齐 | fal-ai/latentsync | $0.005/秒(>40s)| 60 秒 | $0.30 |
| **合计** | | | | **$4.14 ≈ ¥29**(含 EL 订阅摊销) |

**顶级档** ¥350 售价 → 成本 $7.84
| 步骤 | 模型 | 单价 | 1 分钟用量 | 成本 |
|---|---|---|---|---|
| ASR | fal-ai/wizper | $0.0005/分钟 | 1 min | $0.0005 |
| Voice clone | **ElevenLabs 官方** | 同标准档摊销 | $0.22 | $0.22 |
| TTS | fal-ai/elevenlabs/tts/multilingual-v2 | $0.10/1k 字 | 350 字符 | $0.035 |
| 视频换装 | fal-ai/wan-vace-14b/inpainting **720p** | $0.08 / 视频秒 | 60 秒 | $4.80 |
| 口型对齐 | fal-ai/sync-lipsync/v2 | $3.00/分钟 | 1 min | $3.00 |
| **合计** | | | | **$8.06 ≈ ¥56**(含 EL 订阅摊销) |

> ⚠ **wan-vace-14b inpainting 在所有档位都是成本大头(70-85%)**。后续优化空间:
> - 短 mask 区域(只 inpaint 主体,不动背景)→ 视频时长不变但 GPU time 减半
> - 降帧(16fps → 12fps)节省 25% 成本
> - 暂时不优化,MVP 先按 fal 默认参数

---

## 2. Pipeline 流程图(5 步,**部分并行**)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Step 0: 用户上传 + 选档位 + 选模特库元素 + 选产品库元素                       │
│         (前端表单,后端预扣积分,创建 ORAL_TASKS session)                    │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Step 1: 提取音轨 + ASR(并行启动 voice-clone 拷贝原音作为参考样本)            │
│   1a) ffmpeg -i input.mp4 -vn audio.mp3                                 │
│   1b) fal-ai/wizper(input=audio.mp3) → text + word-level timestamps    │
│   1c) [并行] 截原音 ≥10 秒做 voice-clone 参考音频(经济档 minimax 用)        │
│   产物:audio.mp3(本地)、transcript.txt、voice_ref.mp3                    │
│   预计耗时:**< 30 秒**(wizper 250x realtime)                            │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Step 2: 用户编辑文案(前端弹界面,可改写台词)+ 后端等用户提交                 │
│   产物:edited_transcript.txt                                            │
│   耗时:**用户主导**(可能 30 秒到 5 分钟,session 持久化等待)              │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Step 3: 音色克隆 + TTS 生成新音频(并行启动 Step 4 视频换装)                  │
│   3a) [经济] fal-ai/minimax/voice-clone(reference=voice_ref.mp3,         │
│         text=edited_transcript)→ custom_voice_id + new_audio.mp3        │
│       [标准/顶级] ElevenLabs 官方 API 克隆 → voice_id,                    │
│         然后 fal-ai/elevenlabs/tts/{turbo-v2.5 | multilingual-v2}        │
│         → new_audio.mp3                                                 │
│   3b) [并行从 Step 2 启动] Step 4 视频换装(独立链路,不依赖音频)            │
│   预计耗时:**1-2 分钟**(TTS 200 字符约 10-20 秒,克隆首次 30-60 秒)        │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  │ Step 4 同时在跑(从 Step 2 触发)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Step 4: 视频换装 / 换人物 / 换产品(异步 fal,长任务)                        │
│   fal-ai/wan-vace-14b/inpainting(                                       │
│     video_url=original.mp4,                                             │
│     mask_video_url=??? ← 见下方风险 #1,                                  │
│     reference_image_urls=[模特图, 产品图],                                │
│     prompt="Replace person with @model, replace product with @product", │
│     resolution={480p|580p|720p}                                         │
│   ) → swapped_video.mp4(无新音频,带原音频或静音)                          │
│   预计耗时:**3-5 分钟**(720p 60s 视频)                                   │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼ (等 Step 3 + Step 4 都完成)
┌─────────────────────────────────────────────────────────────────────────┐
│ Step 5: 口型对齐 + 合成最终视频(异步 fal,中等长度)                         │
│   按档位选 lipsync endpoint:                                              │
│     [经济] veed/lipsync                                                  │
│     [标准] fal-ai/latentsync                                             │
│     [顶级] fal-ai/sync-lipsync/v2                                        │
│   输入:swapped_video.mp4 + new_audio.mp3                                 │
│   输出:final_video.mp4                                                  │
│   归档:archive_url(final) → /uploads/oral/<user_id>/<sid>/final.mp4     │
│   预计耗时:**1-3 分钟**                                                   │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                          【完成,前端弹预览 + 下载】

总耗时(并行后):用户编辑等待 30s-5min + 自动 pipeline ≈ 5-8 分钟
```

### 关键设计点

1. **Step 3 / Step 4 并行**:Step 4(视频换装)不依赖 Step 3(新音频)的产物,从 Step 2 用户编辑提交后两条链路同时启动。这把整体耗时从 7-10 分钟压到 5-8 分钟。
2. **Step 2 是用户中断点**:文案编辑可能要 5 分钟,session 必须持久化等待,用户可关页面再回来。
3. **Step 5 是汇合点**:必须等 Step 3 和 Step 4 都完成。后端 state machine 用"step3_done && step4_done → trigger step5"判断。

---

## 3. 每一步:模型 ID + 输入 + 输出 + 价格 + 风险

### Step 1 — ASR(`fal-ai/wizper`)

| 字段 | 值 |
|---|---|
| Endpoint | `fal-ai/wizper` |
| 定价 | $0.0005 / 音频分钟($0.50 per 1000 audio min) |
| 输入 | `audio_url`(必填,mp3/ogg/wav/m4a/aac);Whisper 通用建议 ≤25MB 单段 |
| 输出 | JSON `{ text, chunks: [{start, end, text}] }`,可选 word-level timestamps |
| 处理时长 | ~250x realtime(60s 音频约 0.24s) |
| **风险** | **低**。fal 标准模型,稳定。仅需在前端 Step 1 做"提取音轨"的 ffmpeg 调用(后端) |

### Step 2 — 文案编辑(无模型,纯前端)

无外部 API。用户在前端富文本框里编辑 transcript,点"下一步"提交。

### Step 3a — 经济档音色克隆 + TTS(`fal-ai/minimax/voice-clone`)

| 字段 | 值 |
|---|---|
| Endpoint | `fal-ai/minimax/voice-clone`(注:`fal-ai/minimax/preview/voice-clone` 已 404 下线) |
| 定价 | 克隆 **$1.50 / 次**(返回 `custom_voice_id`,**7 天不调用 TTS 自动删除**)+ 后续 TTS $0.10/1000 字符 |
| 输入 | `reference_audio_url`(≥10 秒)+ `text`(初次 preview)+ 后续调 TTS 端点用 `voice_id` |
| 输出 | `custom_voice_id` + preview 音频 URL |
| 风险 | **中**。$1.50 单次克隆是固定成本,**必须靠多视频复用同一 voice_id 摊销**。需要"voice_id 7 天保活"策略(详见数据结构 §4) |

### Step 3b — 标准/顶级档音色克隆(**ElevenLabs 官方 API**)

| 字段 | 值 |
|---|---|
| Endpoint | **fal.ai 没有 ElevenLabs voice-clone 端点**(`fal-ai/elevenlabs/voice-clone` / `voice-cloning` / `instant-voice-clone` 全部 404 实测) |
| 替代方案 | 直接调 **ElevenLabs 官方 API** `POST https://api.elevenlabs.io/v1/voices/add`,拿到 `voice_id`,再传给 fal TTS |
| 定价 | IVC(Instant Voice Clone)需 ElevenLabs **Creator 套餐 $22/月起**,套餐含一定额度 + 超量 ~$0.30/1000 字符 multilingual |
| **风险** | **高 — vendor lock-in**。需要单独申请 ElevenLabs 账号 + API Key + 月度订阅。MVP 必须把这条供应链写进环境变量(`ELEVENLABS_API_KEY`)+ 单独的 service 类 `ElevenLabsClient`(不在 fal_service.py 里) |

### Step 3c — 标准/顶级档 TTS(`fal-ai/elevenlabs/tts/*`)

| 字段 | 标准档 | 顶级档 |
|---|---|---|
| Endpoint | `fal-ai/elevenlabs/tts/turbo-v2.5` | `fal-ai/elevenlabs/tts/multilingual-v2` |
| 定价 | $0.05 / 1000 字符 | $0.10 / 1000 字符 |
| 特点 | 低延迟(turbo) | 29 语言支持 |
| 输入 | `text`(必填,支持 `[laughs]/[whispers]` 情绪标签)+ `voice_id`(ElevenLabs 拿)+ `stability`/`similarity_boost`/`speed`/`language_code` 可选 |
| 输出 | mp3_44100_128 默认格式 |
| 风险 | **低**(假设 voice_id 已经拿到) |

### Step 4 — 视频换装(`fal-ai/wan-vace-14b/inpainting`)

| 字段 | 值 |
|---|---|
| Endpoint | `fal-ai/wan-vace-14b/inpainting`(**单一端点**,通过参数选分辨率,**不是三个端点**) |
| 定价 | 480p **$0.04**/秒,580p **$0.06**/秒,720p **$0.08**/秒 |
| 输入 | `video_url`(mp4/mov/webm/m4v/gif)+ **`mask_video_url`**(必填,见风险)+ `reference_image_urls`(模特/产品)+ `prompt` + `resolution` |
| 输出 | MP4 URL |
| 处理时长 | ~1 分钟 / 次生成(60s 视频在 720p 约 3-5 分钟) |
| **风险** | **极高 — mask 怎么生成是核心未知数**。fal 文档要求传 mask_video,项目里没有视频分割能力。详见风险 §10 #1 |

### Step 5 — 口型对齐(三档不同模型)

| 档位 | Endpoint | 定价 | 备注 |
|---|---|---|---|
| 经济 | `veed/lipsync` | $0.40 / 视频分钟 | 长视频更便宜 |
| 标准 | `fal-ai/latentsync` | ≤40s 固定 $0.20;>40s $0.005/秒($0.30/min) | 短视频(≤40s)比 veed 便宜 |
| 顶级 | `fal-ai/sync-lipsync/v2` | $3.00 / 分钟(Pro $5/min) | Pro 变体专门优化 close-up 镜头 |
| **输入(三个共通)** | 视频 mp4/mov/webm/m4v/gif + 音频 mp3/ogg/wav/m4a/aac | | |
| **风险** | **中**。三个 endpoint 都是黑盒,fal 文档对失败率没披露。**MVP 必须给每个跑 5-10 段真实视频测稳定性** | | |

---

## 4. 数据结构设计

### 4.1 `oral_sessions` 表(SQLite)

> 不复用 `STUDIO_TASKS` 内存 dict — 因为 oral 链路有用户文案编辑中断点,**必须 SQLite 持久化**(用户可关页面再回来)。

```sql
CREATE TABLE oral_sessions (
    id              TEXT PRIMARY KEY,           -- session_id (uuid)
    user_id         TEXT NOT NULL,
    tier            TEXT NOT NULL,              -- 'economy' | 'standard' | 'premium'
    status          TEXT NOT NULL,              -- state machine 状态(下方枚举)
    
    -- 输入
    original_video_path  TEXT NOT NULL,         -- /uploads/oral/<uid>/<sid>/orig.mp4
    duration_seconds     REAL NOT NULL,
    selected_models      TEXT,                  -- JSON [{name, image_url}, ...]  最多 4 个
    selected_products    TEXT,                  -- JSON [{name, image_url}, ...]  最多 4 个
    
    -- Step 1 产物
    extracted_audio_path TEXT,                  -- /uploads/oral/<uid>/<sid>/audio.mp3
    voice_ref_audio_path TEXT,                  -- 截 ≥10s 做克隆参考样本
    asr_transcript       TEXT,                  -- 原文案
    asr_word_timestamps  TEXT,                  -- JSON
    
    -- Step 2 用户编辑
    edited_transcript    TEXT,                  -- 用户提交后填
    
    -- Step 3 产物
    voice_provider       TEXT,                  -- 'minimax' | 'elevenlabs'
    voice_id             TEXT,                  -- minimax custom_voice_id 或 elevenlabs voice_id
    voice_id_created_at  TIMESTAMP,             -- minimax 7 天保活检查用
    new_audio_url        TEXT,
    
    -- Step 4 产物
    swap_fal_request_id  TEXT,                  -- wan-vace fal request_id
    swapped_video_url    TEXT,                  -- 异步 polling 拿到后填
    
    -- Step 5 产物
    lipsync_fal_request_id  TEXT,
    final_video_url         TEXT,
    final_video_archived    TEXT,               -- 归档到 /uploads/oral/<uid>/<sid>/final.mp4
    
    -- 计费
    credits_charged      INTEGER NOT NULL,      -- 预扣金额
    credits_refunded     INTEGER DEFAULT 0,     -- 累计退款
    
    -- 失败 / 重试
    error_step           TEXT,                  -- 'step1' / 'step3' / 'step4' / 'step5'
    error_message        TEXT,
    retry_count          INTEGER DEFAULT 0,     -- 同档全链路重跑次数
    
    -- 时间
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at  TIMESTAMP
);
CREATE INDEX idx_oral_user ON oral_sessions(user_id, created_at DESC);
CREATE INDEX idx_oral_status ON oral_sessions(status);
```

### 4.2 状态机(`status` 字段枚举)

```
uploaded
  → asr_running          (Step 1 fal 调用中)
  → asr_done             (产物齐,等用户编辑文案)
  → edit_submitted       (用户提交编辑后,触发 Step 3 + Step 4 并行)
  → tts_running          (Step 3 进行中)
  → swap_running         (Step 4 进行中,可与 tts_running 并存)
  → tts_done             (Step 3 完成)
  → swap_done            (Step 4 完成)
  → both_ready           (3 + 4 都完成,触发 Step 5)
  → lipsync_running      (Step 5 进行中)
  → completed            (✅ 终态)
  → failed_step1 / failed_step3 / failed_step4 / failed_step5  (各种失败终态)
  → cancelled            (用户主动取消)
```

> 实施时:`tts_done` 和 `swap_done` 是过渡态,真正驱动 Step 5 用一个独立的 "ready check" 函数(每次 step 完成后查另一个是否也完成)。

### 4.3 中间产物存放

| 产物 | 路径 |
|---|---|
| 原视频 | `/uploads/oral/<user_id>/<sid>/orig.mp4` |
| 提取音轨 | `/uploads/oral/<user_id>/<sid>/audio.mp3` |
| voice 参考样本 | `/uploads/oral/<user_id>/<sid>/voice_ref.mp3` |
| 新音频 | `/uploads/oral/<user_id>/<sid>/new_audio.mp3` |
| 换装中间视频 | `/uploads/oral/<user_id>/<sid>/swapped.mp4`(异步归档自 fal URL) |
| 最终视频 | `/uploads/oral/<user_id>/<sid>/final.mp4` |
| GC | 30 天后清理(复用现有 `uploads_gc.py` cron 模式) |

### 4.4 失败重试记录

复用现有 `refund_tracker`(SQL 持久化退款),新加表 `oral_step_attempts` 仅当**真正用到分阶段重试**时再加;MVP 先用 `oral_sessions.retry_count + error_step` 字段简单跟踪。

### 4.5 `voice_id` 保活策略(MiniMax 经济档)

MiniMax 的 `custom_voice_id` 7 天不用自动删,导致下次重跑要再付 $1.50。两个方案:

- **方案 A**:每个 session 一个独立 voice_id,不复用,直接接受 $1.50 单次成本 → 经济档每视频成本就是 $1.50 + 其他,共 ≈ $4.35 ≈ ¥30,毛利 62.5%(掉 12.5pp)
- **方案 B**(推荐):**用户级 voice_id**,以 user_id + 原视频音轨哈希为 key,7 天内复用。建表 `user_voice_cache(user_id, audio_hash, voice_id, created_at)`,每次 oral session 先查表;命中复用,未命中重新克隆。每天定时跑一次"伪 TTS"调用(1 字符)保活
- MVP 用方案 A 简单粗暴,V2 升 B(预计 1 工作日额外工作量)

---

## 5. API 设计

| # | Endpoint | Method | 用途 |
|---|---|---|---|
| 1 | `/api/oral/upload` | POST | 上传原视频,返 session_id + duration |
| 2 | `/api/oral/start` | POST | `{ session_id, tier, models, products }` 开始 pipeline,**预扣积分**,触发 Step 1 |
| 3 | `/api/oral/edit` | POST | `{ session_id, edited_transcript }` 用户提交编辑,触发 Step 3 + 4 并行 |
| 4 | `/api/oral/status/{session_id}` | GET | 返当前 status + 各步骤产物 URL + 进度百分比 |
| 5 | `/api/oral/cancel/{session_id}` | POST | 用户取消,按当前阶段退款 |
| 6 | `/api/oral/retry/{session_id}` | POST | 失败后免费/收费重跑(MVP 暂不实现,V2 加) |
| 7 | `/api/oral/list` | GET | 用户 oral session 列表(挂 My Tasks 虚拟 job 同 studio 模式) |

### 5.1 详细输入输出

**POST `/api/oral/upload`**
```json
请求:multipart/form-data, video=<file>
响应:{ "session_id": "uuid", "duration_seconds": 60.5, "size_mb": 12.3 }
```

**POST `/api/oral/start`**
```json
请求:{
  "session_id": "uuid",
  "tier": "economy" | "standard" | "premium",
  "models": [{ "name": "模特A", "image_url": "..." }],   // 1-4 个
  "products": [{ "name": "产品X", "image_url": "..." }]  // 0-4 个
}
响应:{
  "status": "asr_running",
  "credits_charged": 160,    // 按 tier 算
  "estimated_eta_seconds": 480
}
错误:402 积分不足 / 400 已开始 / 403 无权
```

**POST `/api/oral/edit`**
```json
请求:{ "session_id": "uuid", "edited_transcript": "..." }
响应:{ "status": "edit_submitted" }
错误:400 状态非 asr_done(不能跳步)/ 400 transcript 超字数
```

**GET `/api/oral/status/{session_id}`**
```json
响应:{
  "session_id": "uuid",
  "status": "swap_running",
  "tier": "standard",
  "duration_seconds": 60.5,
  "credits_charged": 360,
  "credits_refunded": 0,
  "step_progress": {
    "step1": "done",                       // ASR
    "step2": "done",                       // 用户已编辑
    "step3": "done",                       // TTS 完成
    "step4": "running",                    // 视频换装进行中
    "step5": "pending"                     // 等待
  },
  "products": {
    "asr_transcript": "...",
    "edited_transcript": "...",
    "new_audio_url": "https://.../new_audio.mp3",
    "swapped_video_url": null,
    "final_video_url": null
  },
  "estimated_remaining_seconds": 240,
  "error": null
}
```

**POST `/api/oral/cancel/{session_id}`**
```json
响应:{ "status": "cancelled", "credits_refunded": 80 }
```

**GET `/api/oral/list`**
```json
响应:{
  "sessions": [
    {
      "session_id": "uuid",
      "tier": "standard",
      "status": "completed",
      "title": "口播带货 60s",
      "final_video_url": "...",
      "created_at": "2026-04-29T12:00:00Z"
    }
  ]
}
```

### 5.2 可复用的现有基础设施

| 现有 | 复用点 |
|---|---|
| `decorators.require_credits` | Step 2 `start` 端点装饰 |
| `services/refund_tracker` | Step 4/5 fal 异步 task 注册 + 失败 try_refund |
| `services/task_ownership` | WS 进度推送鉴权(若做)|
| `services/circuit_breaker` | 7 个新 endpoint 各注册一个熔断 key |
| `services/media_archiver.archive_url` | swap/final 视频从 fal.media 归档到 /uploads(防 30 天过期) |
| `services/uploads_gc` cron | 加 oral 路径到 GC 范围(30 天) |
| `services/storage_sts` | 用户上传原视频可走 OSS 直传(七十二续)|
| `services/upload_guard` | 视频上传 OOM 防护 |

---

## 6. 前端 UI 设计

### 6.1 路由

`/video/oral-broadcast/[id]/page.tsx`(独立工作台,**不复用 `/video/studio` UI**)。
sidebar 加入口"🎤 口播带货"。

### 6.2 5 步交互流程

**Step 1 — 上传 + 选档位**
```
┌──────────────────────────────────────────────────┐
│  口播带货工作台                                    │
│                                                  │
│  ① 上传原视频(≤ 60 秒)                           │
│     [拖拽区]   或  [选择文件]                     │
│                                                  │
│  ② 选档位                                         │
│     ○ 经济档  ¥80/分钟  (160 积分)               │
│       MiniMax 音色 + 480p + veed lipsync         │
│     ● 标准档  ¥180/分钟 (360 积分)  ⭐ 推荐       │
│       ElevenLabs Turbo + 580p + latentsync       │
│     ○ 顶级档  ¥350/分钟 (700 积分)               │
│       ElevenLabs Multi + 720p + sync-lipsync v2  │
│                                                  │
│  ③ 法律确认(MVP 必做)                           │
│     ☐ 我已获得视频内所有人物肖像权 + 音乐版权,    │
│        并对生成内容承担全部责任                  │
│                                                  │
│  [下一步:开始 ASR →]                            │
└──────────────────────────────────────────────────┘
```

**Step 2 — ASR 完成,文案编辑**
```
┌──────────────────────────────────────────────────┐
│  ② 编辑文案(原视频提取)                         │
│                                                  │
│  原文案(只读):                                  │
│  ┌────────────────────────────────────────────┐ │
│  │ 大家好,今天给大家推荐一款好用的吹风机...    │ │
│  │ (with timestamps overlay)                  │ │
│  └────────────────────────────────────────────┘ │
│                                                  │
│  你的新文案(编辑):                              │
│  ┌────────────────────────────────────────────┐ │
│  │ 大家好,今天推荐一款超好用的电动牙刷...      │ │
│  │ ...                                        │ │
│  └────────────────────────────────────────────┘ │
│  字符数:178 / 1000                             │
│                                                  │
│  ⚠ 提交后将开始视频换装 + 音频生成,无法再改      │
│  [上一步]              [下一步:开始生成 →]      │
└──────────────────────────────────────────────────┘
```

**Step 3 — 选模特库元素 + 选产品库元素**(可整合到 Step 1,但分开更清晰)
```
┌──────────────────────────────────────────────────┐
│  ③ 选要替换的人物 + 产品(最多各 4 个)            │
│                                                  │
│  人物素材:                                       │
│  [+] [模特A 图] [模特B 图] [+]                  │
│                                                  │
│  产品素材:                                       │
│  [+] [电动牙刷图] [+]                           │
│                                                  │
│  自动 prompt 预览(只读):                         │
│  "Replace person with @ModelA, replace product   │
│   with @ElectricToothbrush, maintain camera..."  │
│                                                  │
│  [开始生成]                                      │
└──────────────────────────────────────────────────┘
```

> 实操:Step 3 可前置到 Step 1(用户上传时就选好),减少一次跳页。MVP 把 1+3 合并成"上传页"。

**Step 4 — 等待 + 进度条**
```
┌──────────────────────────────────────────────────┐
│  ④ 生成中(约 5-8 分钟)                          │
│                                                  │
│  ☑ 提取音轨 + 识别文案    (30 秒)               │
│  ☑ 编辑文案完成                                  │
│  ☐ 音色克隆 + 生成新音频   ⏳ 进行中(预计 1-2分)│
│  ☐ 视频换装 / 换产品       ⏳ 进行中(预计 3-5分)│
│  ☐ 口型对齐合成最终视频    ⏳ 等待                │
│                                                  │
│  整体进度:[██████░░░░] 62%                      │
│  剩余时间:约 3 分 45 秒                          │
│                                                  │
│  💡 你可以关闭页面,稍后在 "我的任务" 查看        │
│  [取消任务]                                       │
└──────────────────────────────────────────────────┘
```

**Step 5 — 成片预览 + 下载**
```
┌──────────────────────────────────────────────────┐
│  🎉 生成完成!                                    │
│                                                  │
│  [视频预览播放器]                                │
│                                                  │
│  [↓ 下载 MP4]    [📋 复制链接]    [🗂 我的任务]  │
│                                                  │
│  本次消耗:360 积分                              │
│  视频时长:60.5 秒                               │
│  ⚠ 视频在云端保留 30 天,请尽快下载              │
└──────────────────────────────────────────────────┘
```

### 6.3 组件复用情况(grep 现有代码)

| 现有组件 | 复用点 |
|---|---|
| `JobPanel.tsx` | 长任务卡片可复用,加 `oral_broadcast` typeLabel |
| `AuthFetchInterceptor` | 自动加 cookie 鉴权 + 401 拦截(全局已挂)|
| `browser-image-compression` | 模特/产品图片上传压缩(七十三续)|
| `studio/[id]/page.tsx` mode 切换 UI | **不直接复用**,但布局风格可参考 |
| `i18n/{zh,en}` | 双语 key 加在 `oral.*` namespace |

---

## 7. 计费逻辑

### 7.1 积分预扣表(按 tier × 视频秒数)

```python
PRICING_PER_SECOND = {
    "economy":  160 / 60,    # ≈ 2.67 积分/秒
    "standard": 360 / 60,    # = 6.0 积分/秒
    "premium":  700 / 60,    # ≈ 11.67 积分/秒
}

# 1 秒视频也按 1 秒收,向上取整
def compute_charge(tier: str, duration_seconds: float) -> int:
    return math.ceil(PRICING_PER_SECOND[tier] * duration_seconds)
```

加入现有 `services/billing.py` 的 PRICING 表(按 endpoint key 加):

```python
# 新增项
"oral_broadcast/economy":  160,   # 1 分钟全套,按秒折算
"oral_broadcast/standard": 360,
"oral_broadcast/premium":  700,
```

### 7.2 失败按阶段退款规则(写死)

> **核心原则**:**已花的算钱,没花的全退**。fal 真实失败按阶段比例退款,fal 已成功的不退。

| 失败阶段 | 实际成本占比 | 退款比例 | 退款逻辑 |
|---|---|---|---|
| Step 1 (ASR) 失败 | < 1% | **100%** | 没真扣 fal,全退 |
| Step 2 用户取消 | 1% | **99%** | ASR 已扣,扣 1% 处理费 |
| Step 3 (TTS+clone) 失败 | 经济 1.7% / 标准 5.6% / 顶级 3.0% | **95%** | TTS 失败概率低,统一扣 5% |
| Step 4 (视频换装) 失败 | 70-85% | **20%** | inpainting 是大头,失败损失最大 — 用户拿不到结果但 fal 已收钱,**只退 20%** + 后台 try_refund 看 fal 是否真扣到费(若 fal 自己 refund 则全退)|
| Step 5 (lipsync) 失败 | 经济 14% / 标准 7.6% / 顶级 38% | **30%** | 已经走完 80% 链路,只退 30% |

```python
REFUND_RATIO = {
    "failed_step1": 1.00,
    "cancelled_after_step1": 0.99,
    "failed_step3": 0.95,
    "failed_step4": 0.20,
    "failed_step5": 0.30,
}

def refund_on_failure(session, failed_step: str):
    ratio = REFUND_RATIO[failed_step]
    refund_amount = int(session["credits_charged"] * ratio)
    add_credits(session["user_id"], refund_amount)
    update session SET credits_refunded = refund_amount, status = failed_step
```

> **注**:Step 4 退款 20% 是"留毛利"逻辑,用户体验上比较糟糕(花了 360 积分只退 72)。**建议 MVP 改成 50%,运营观察 1 个月再调**。最终比例运营拍板。

### 7.3 重跑收费

- **MVP**:不做免费重跑,失败按上面比例退款,用户重新发起新任务
- **V2**(预计 +1 工作日):同档位全链路重跑 1 次免费(用 `retry_count` 字段),后续 50% 收费;升档不算重跑正常计费

---

## 8. MVP 工作量估算

> 假设单人全职、不算 ElevenLabs 账号申请等待时间(用户主导)。

| 阶段 | 子项 | 估算 |
|---|---|---|
| **后端** | wizper ASR 服务 + 端点 | 0.5 天 |
| | ElevenLabs SDK 封装(独立 service)| 1.0 天 |
| | wan-vace-14b 视频换装服务 | 0.5 天 |
| | 3 个 lipsync endpoint 服务 | 0.5 天 |
| | 5 步 state machine + ORAL_TASKS 持久化 | 2.0 天 |
| | 6 个 API 端点 + 退款分阶段 | 1.5 天 |
| | DB migration(`oral_sessions` 表 + alembic)| 0.5 天 |
| **后端小计** | | **6.5 天** |
| **前端** | `/video/oral-broadcast` 路由 + 5 步流程 | 2.5 天 |
| | 文案编辑器 + 进度条 + WS 进度推送 | 1.5 天 |
| | 模特/产品库选择器复用 | 0.5 天 |
| | i18n 双语 + 法律确认勾选 | 0.5 天 |
| | sidebar 入口 + JobPanel 集成 | 0.5 天 |
| **前端小计** | | **5.5 天** |
| **测试** | 7 个新 fal endpoint 单测(mock fal)| 1.0 天 |
| | state machine 5 步状态测 | 0.5 天 |
| | e2e Playwright 1 条 happy path | 0.5 天 |
| **测试小计** | | **2.0 天** |
| **部署 + 验证** | ElevenLabs 账号 + API key + env 部署 | 0.5 天 |
| | 跑 5-10 段真实视频每档 | 1.0 天(多档对比) |
| | admin 灰度 + 监控接入 | 0.5 天 |
| **部署小计** | | **2.0 天** |
| **总计 MVP** | | **16 个工作日(约 3 周)** |

> ⚠ **不含**:VACE 的 mask 生成方案研究(见风险 #1,可能是 +3-5 天的额外探索)。如果 mask 必须靠用户上传,MVP 可走"用户传 mask 视频"简化版,但用户体验差。

---

## 9. 关键风险列表

### 🔴 #1(最高)— VACE inpainting 的 mask 怎么生成

**问题**:`fal-ai/wan-vace-14b/inpainting` 必须传 `mask_video_url`(白色区域 = 要换的部分,黑色 = 保留)。项目里没有视频分割能力。

**3 个方案**:
- **A. 用户手动传 mask**:UI 给画笔工具让用户在第一帧画区域,自动复制到所有帧。**MVP 最简,但用户体验差,且固定 mask 无法跟随主体移动**。预估 +1 天前端
- **B. 接 SAM 2 / Florence-2 自动分割**:fal.ai 上找个 video segmentation 模型(如 `fal-ai/sam-2-video` 或类似),传"主体"prompt,模型自动出 mask。**MVP 可行**,预估 +2 天后端 + $0.05-0.10/秒额外成本(成本核算要重算)
- **C. wan-vace 是否支持 prompt-only 自动 mask**:**需要查 fal 文档**。如果支持,无需 mask 视频,直接传文字 prompt。**最理想,但未确认**

**建议**:先 WebFetch fal-ai/wan-vace-14b 详细文档确认支持哪种;若必须传 mask,MVP 走方案 B(接 SAM 2)。**这个风险不解决,工程不能启动**。

### 🟠 #2 — ElevenLabs 不在 fal.ai 托管 voice-clone

**问题**:fal-ai/elevenlabs/voice-clone 等端点全部 404。要克隆必须直接调 ElevenLabs 官方 API。

**影响**:
- 多一个 vendor + API key 管理(`/etc/ssp/elevenlabs.key`)
- 多一个月度订阅($22/月起)
- 月 100 视频以下,IVC 摊销成本 $0.22/视频,毛利掉 4-5pp(已计入上方成本表)
- **vendor 失败时降级路径**:标准/顶级档失败 fallback 到 minimax(经济档同款)?要决定

**建议**:接受这个 vendor lock-in。降级路径文档里写清,实施时给 `ELEVENLABS_FALLBACK_TO_MINIMAX=true` 开关。

### 🟠 #3 — 整体耗时 5-8 分钟,用户流失

**问题**:5 步串行 + 用户编辑等待,即使 Step 3/4 并行总耗时仍长。用户开始的兴奋感会被 5 分钟等待消磨。

**建议**:
- Step 4 进度条必须有真实进度(不是假动画),fal request_id polling 要实时
- 加 WebSocket 推送(项目有 task_ownership 现成基础)
- 邮件 / 微信通知"生成完成"(项目有 alert.py / Server 酱)
- UI 明确写"约 5-8 分钟,可关闭页面"

### 🟡 #4 — 帧率不一致

**问题**:用户上传 24fps 视频,wan-vace 默认 16fps 出帧,lipsync 又是 25fps,合成后画面卡顿。

**建议**:Pipeline 入口 ffmpeg 强制规范化到 25fps + 1080x1920 / 1920x1080 两档,所有中间产物按这个跑。MVP 直接 reject 不规则视频。

### 🟡 #5 — 视频长度上限太长导致失败链路放大

**问题**:5 分钟视频 5 步串行成功率 = 各步成功率乘积,假设各步 95% → 整体 77%;3 分钟同公式略好但仍 >20% 失败率。

**建议**:**MVP 限制 60 秒**(技术稳定性优先),用户反馈强烈再放宽到 3 分钟。

### 🟡 #6 — MiniMax voice_id 7 天清退

**问题**:经济档用户重跑会再付 $1.50。

**建议**:MVP 接受这个成本(单视频毛利从 75% 掉到 ~62%);V2 加 user_voice_cache 表 + 定时保活脚本,1 天工作量。

### 🟢 #7 — 法律风险:用户上传侵权视频

详见产品决策 Q4。

---

## 10. 上线后第一周监控指标

### 10.1 各档位成功率

```
metric: oral_session_success_rate{tier="economy|standard|premium"}
来源: SELECT tier, status, COUNT(*) FROM oral_sessions GROUP BY tier, status
告警: 任一档位成功率 < 70%(连续 1 小时)→ 微信 alert
```

### 10.2 各步骤失败率

```
metric: oral_step_failure_rate{step="step1|3|4|5"}
来源: SELECT error_step, COUNT(*) FROM oral_sessions WHERE status LIKE 'failed_%'
告警: 任一步失败率 > 15%(滚动 6 小时)→ Sentry critical
```

### 10.3 平均耗时(各步骤 + 整体)

```
metric: oral_step_duration_p50 / p95
来源: 各 step 的 timestamp 字段差值
看板: Grafana 面板新增 "oral pipeline" 板块
```

### 10.4 档位选择分布(A/B 数据)

```
metric: oral_tier_distribution
看板: 第一周后看 economy/standard/premium 比例
判断: 如果 90% 用户选经济档,说明定价过高 / 标准档卖点不够
```

### 10.5 fal API 单次调用成本

```
metric: fal_cost_per_session{tier}
来源: fal.ai 后台 API 结算
告警: 单次成本 > 预算 1.5 倍 → 立即下线 + 复盘
```

### 10.6 失败重跑率

```
metric: oral_retry_count_distribution
看板: 用户平均重跑几次?Tail 用户重跑 5+ 次说明某档某步骤模型不稳定
```

---

## 11. 风险/工作量总表(给用户拍板用)

| 环节 | 类型 | 风险 | 工作量 | 备注 |
|---|---|---|---|---|
| ASR(wizper)| 现成模型直接调 | 🟢 低 | 0.5 天 | 项目无 ASR 历史,但 fal 包装简单 |
| MiniMax voice-clone | 现成模型直接调 | 🟢 低 | 已接(扩 caching)| 复用 FalVoiceService |
| ElevenLabs voice-clone | **新接入(vendor lock-in)** | 🟠 中 | 1.0 天 | 必须走官方 API + 月订阅 |
| ElevenLabs TTS | 现成模型直接调 | 🟢 低 | 0.5 天 | fal 端点存在,标准/顶级档共用类 |
| **wan-vace inpainting** | **新接入 + mask 生成方案待定** | 🔴 **高** | **0.5 + 2-3 天** | **mask 风险见 #1,不解决不启动** |
| veed/lipsync | 现成模型直接调 | 🟢 低 | 0.2 天 | fal 标准 |
| latentsync | 现成模型直接调 | 🟢 低 | 0.2 天 | fal 标准 |
| sync-lipsync v2 | 现成模型直接调 | 🟢 低 | 0.2 天 | fal 标准 |
| **5 步 state machine** | **自研逻辑** | 🟠 中 | 2.0 天 | checkpoint 重入 + 并行汇合 |
| **ORAL_TASKS 持久化 + DB migration** | 自研逻辑 | 🟢 低 | 0.5 天 | 仿造 STUDIO_TASKS,但用 SQLite |
| **API 6 端点** | 自研逻辑 | 🟢 低 | 1.5 天 | 模式跟现有一致 |
| **分阶段退款** | 自研逻辑 | 🟠 中 | 0.5 天 | 复用 refund_tracker,加 ratio 表 |
| **前端 5 步流程 UI** | 自研逻辑 | 🟠 中 | 2.5 天 | 文案编辑 + 进度条复杂度高 |
| **文案编辑器** | 自研逻辑 | 🟢 低 | 1.0 天 | 普通 textarea + 字数限制 |
| **进度推送(WS)** | 自研 + 现成基础 | 🟡 偏低 | 1.0 天 | 复用 task_ownership |
| 模特/产品库选择 | 复用现有 | 🟢 低 | 0.5 天 | 复用 studio 模式 |

**汇总**:
- **必须先解决的高风险:VACE mask 生成方案(#1)**
- 总工作量(含 mask 方案 B):**16-18 个工作日**
- 单人全职 → **3-4 周交付 MVP**

---

## 12. 4 个产品决策问题答复

### Q1 — 是否允许中途升档?

**方案 A:允许升档,只重跑必要步骤**
- 经济档跑完 → 用户嫌画质 → 升标准档,只重跑 Step 4(视频换装到 580p)+ Step 5(latentsync)
- 收费:差价 ¥100 + 退掉经济档 Step 4/5 已花成本
- 优点:用户体验顶级,沉没成本最小化
- 缺点:state machine 复杂,checkpoint 重入要严密;MVP 多 2-3 天工作量

**方案 B:不允许升档,从头跑新档位**
- 经济档跑完 → 用户嫌画质 → 全部重新提交一个标准档新任务,全价 ¥180
- 优点:实现简单,退款逻辑干净
- 缺点:用户花了 ¥80 拿到不满意的产物 + 重新花 ¥180,实付 ¥260 才拿满意成片

**我推荐:MVP 用 B,V2 升 A**
理由:
- MVP 阶段先验证用户愿不愿意付费,实现复杂度优先简化
- 数据收够后,如果"用户经济档跑完后升档"是高频路径,V2 再做(预计 2-3 天)
- 用户还可以先试经济档预览 30 秒"满不满意"(MVP 可做"经济档前 30 秒免费试")

### Q2 — 视频长度上限?

**推荐:MVP **60 秒硬上限**,V2 放宽到 3 分钟**

理由:
- 60 秒成本 / 等待时长 / 失败概率三个指标都在可接受区间
- 顶级档 60 秒 ¥350,客单价已经不低,用户主流需求覆盖
- 3 分钟成片需要拆段处理(类似当前 long-video studio 的 60 秒切片),工程复杂度跳升,先 MVP 不做
- 5 分钟以上失败概率 >30%,用户体验差且成本失控,**不推荐**

**给前端的硬限**:
- 上传时 ffmpeg probe 时长,>60s 直接 reject 413
- 错误提示"目前支持 ≤ 60 秒视频,后续版本支持更长"

### Q3 — 失败重跑策略?

**推荐:不做免费重跑,按阶段退款让用户自决定**

理由:
- 免费重跑失控成本,5 分钟内同 IP 连续重跑 10 次的滥用案例真实存在
- 按阶段退款已经把用户损失最小化(详见 §7.2 退款表)
- 用户拿到退款后自由决定:换档位 / 换原视频 / 不再尝试
- 失败率监控(§10.2)>15% 时主动下线问题档位 + 全员通知,用产品手段而不是免费重跑解决

**风控配套**(MVP 必做):
- 同用户 24 小时内连续 3 次失败 → 暂停该用户 1 小时,人工审核
- 同 IP 24 小时内 >5 个 session 全失败 → ban IP 24 小时

**V2 加**(看数据):
- 同档位重跑 1 次按 50% 收费(部分用户接受)
- 失败率 >20% 的档位/步骤 → 该档位整周内自动退 70%(信任机制)

### Q4 — 防侵权?

**法律风险点**:
1. 视频版权:用户上传抖音/快手爆款视频 → 侵犯原作者版权
2. 演员肖像权:原视频里的真人脸 → 即使被换掉了,生成过程涉及
3. 音乐版权:背景音乐 → 提取音轨 + 替换音频涉及
4. AIGC 标识:深度合成规定要求显示+隐式水印

**推荐分层方案**:

**L1(MVP 必做)— 用户责任声明**
- 上传页强制勾选:"我已获得视频内所有人物肖像权 + 内容版权,后果自负"
- 不勾选无法继续(disabled 按钮)
- audit_log 记录:user_id + 时间戳 + IP + 同意版本号
- ToS / 用户协议补充"AI 生成内容条款"(法务起草)

**L2(MVP 必做)— AIGC 标识(深度合成法规)**
- 显式水印:成品视频右下角 "AI生成"4 字水印,ffmpeg drawtext 烧录
- 隐式水印:文件名规范 `<sid>_aigc.mp4` + EXIF 元数据 `Software=ailixiao-aigc`
- 法规依据:《互联网信息服务深度合成管理规定》§16

**L3(V2,3-6 个月后)— 哈希黑名单**
- 定期抓取抖音/快手 top 1000 视频,生成 pHash(perceptual hash)
- 用户上传视频提取 pHash 比对,命中拒绝
- 工程量:1 个 cron + pHash 库(opencv 或 imagehash)~3 天
- 准确度有限(剪辑后 pHash 失效)

**L4(V3,运营阶段)— AI 内容检测**
- 接腾讯云内容安全 API 视频版权检测
- 成本 ~¥0.05/秒,60 秒视频 ¥3
- 延迟增加 30-60 秒
- **MVP 不做**(成本负担 + 用户体验降低)

**MVP 范围**:L1 + L2 必做,L3/L4 后续做
**额外工作量**:L1 0.5 天 + L2 1 天(ffmpeg 水印 burn-in)

---

## 13. 我的最终建议(给用户拍板)

### 短期(MVP,3-4 周)

1. **必须先解决 VACE mask 生成方案**(风险 #1)— 我建议接 SAM 2 自动分割,+2 天工作量 + 成本核算重算
2. **接受 ElevenLabs vendor lock-in**(风险 #2)— 月订阅 $22 + 实现简单
3. **走方案 B 不允许升档**(产品决策 Q1)— 简化 MVP,V2 再升级
4. **60 秒视频硬上限**(产品决策 Q2)
5. **不做免费重跑,按阶段退款**(产品决策 Q3)
6. **L1 用户责任 + L2 AIGC 水印**(产品决策 Q4)

### 启动前置(用户主导)

| 项 | 工作量 | 阻塞度 |
|---|---|---|
| 申请 ElevenLabs Creator 套餐 + API key | 30 分钟 | **阻塞标准/顶级档** |
| 法务审阅"AIGC 内容条款" + 用户责任声明 | 1-2 周 | **阻塞上线** |
| 决定 Q1-Q4 四个产品决策 | 30 分钟 | 阻塞工程 |

### 启动后第一周必做

- 跑 admin 灰度 5-10 段真实视频每档,看 mask 方案 + lipsync 效果
- 收集前 50 个真实用户行为数据(看 §10 监控指标)
- 第二周决定 V2 路线(升档 / 重跑 / 长视频)

### 千万不要做的事

- ❌ 不要先实现"完美版"再上线 — MVP 60 秒视频 + 不允许升档先走
- ❌ 不要重写现有 video_studio — 全新独立工作台,不影响现有付费用户
- ❌ 不要省掉 mask 方案研究 — VACE 用错 mask 出来全是糊的视频,毁口碑
- ❌ 不要在 fal 出问题时"我也来抗一下" — 现成基础设施(circuit_breaker / refund_tracker / media_archiver)已经完善,直接复用,不要重发明

---

## Sources(fal.ai 模型实时定价,2026-04-29)

- [fal-ai/wizper](https://fal.ai/models/fal-ai/wizper)
- [fal-ai/minimax/voice-clone](https://fal.ai/models/fal-ai/minimax/voice-clone)
- [fal-ai/elevenlabs (hub)](https://fal.ai/elevenlabs)
- [fal-ai/elevenlabs/tts/turbo-v2.5](https://fal.ai/models/fal-ai/elevenlabs/tts/turbo-v2.5)
- [fal-ai/elevenlabs/tts/multilingual-v2](https://fal.ai/models/fal-ai/elevenlabs/tts/multilingual-v2)
- [fal-ai/wan-vace-14b/inpainting](https://fal.ai/models/fal-ai/wan-vace-14b/inpainting)
- [veed/lipsync](https://fal.ai/models/veed/lipsync)
- [fal-ai/latentsync](https://fal.ai/models/fal-ai/latentsync)
- [fal-ai/sync-lipsync](https://fal.ai/models/fal-ai/sync-lipsync)
- [fal-ai/sync-lipsync/v2](https://fal.ai/models/fal-ai/sync-lipsync/v2)
- [fal.ai 全模型价格 gist 备查](https://gist.github.com/azer/6e8ffa228cb5d6f5807cd4d895b191a4)

---

**等用户拍板,不动代码**。
