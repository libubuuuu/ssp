# P221 视频复刻 V2 — API & 工程设计 (v4)

> 阶段 A 第 1 步设计文档,**法务 + 本设计两边都审过才进入第 2 步(写代码)**。
>
> 配套:
> - SQL:`docs/P221-MIGRATION.sql`
> - 法务:`docs/legal/terms-of-service.md`(修订) + `docs/legal/video-clone-v2-upload-disclaimer.md`(新建) + `docs/legal/P221-LEGAL-DELTA.md`(diff 说明)
>
> 创建:2026-05-09 / v2(段位独立选择)/ v3(全局两档)/ **v4 2026-05-09**:加 4 个产品质量功能 + 失败段不补原视频
>
> 价格基线(P220 实测):¥6.66 / ¥7.5(对应 input 2s/4s,output 8s,resolution 480p)

---

## 0. 跟历次方案的差异(便于审)

| # | 上一版设计 | 用户最终决议 v4 | 备注 |
|---|---|---|---|
| 1 | 全能档每段恒定 15 积分 | 段位独立选择,每段经济或标准,总价=各段累加 | DB schema 跟着变 |
| 2 | 单一 estimate 入口 | 加 `/preview-segments` + estimate 双模(single / ultimate) | 用户拿到分段缩略图后才好选档 |
| 3 | 切片仅返回 plan | 切片同时为每段抽缩略图(ffmpeg 中间帧) | 前端 UI 选档时展示 |
| 4 | 单段任务 type 不存在 | 显式 `type=single\|ultimate` | 区分计费模型 + DB 查询效率 |
| 5 | 段长 < input_seconds 无策略 | ~~前端 UI 灰掉该 tier~~ → **2026-05-10 砍单档:不再有 tier 概念,无此问题** | 历史:防"用户付贵价但段不够长" |
| 6 | 三档(economy/standard/premium) | ~~全局两档:economy + standard~~ → **2026-05-10 进一步砍单档:全局单档,fal 行为同质** | 实测 fal 端 economy/standard 资源参数完全等价,分档只是 UI 噱头 |
| 6.5 | 双档(economy + standard) | **全局单档:每段 ¥19.9 / 20 积分,无档位选择** | 2026-05-10 commit 3 实施 |
| 7 | 一刀切 ai 替换全段 | ⭐ **功能 1+2:replacement_mode(partial/full)+ 每段 AI / 原片段切换** | 部分段选"原视频片段"则跳 fal 不计费,直接拼接 |
| 8 | 单一 image_urls 数组 | ⭐ **功能 3:每张图标 role(product/person/scene/reference),后端拼 prompt 用 @ 语法** | 提示模型识别参考素材身份 |
| 9 | 用户写 prompt | ⭐ **功能 4:5 个 prompt 模板按钮**(婴儿用品带货 / 服装试穿 / 美食制作 / 数码开箱 / 美妆护肤) | 模板填入后用户可改 |
| 10 | 失败段从原视频抠秒数补 | ⭐ **失败段直接跳过**(不补原视频) | 简化拼接 + 用户决议 |

---

## 1. 跟项目现有体系的对接

| 项 | 复用对象 | 说明 |
|---|---|---|
| 用户认证 | `app.api.auth.get_current_user` | 同 v1 video_clone |
| 扣费 / 退款 | `services.billing.deduct_credits` / `add_credits` | 1 积分 = ¥1(沿用 v1) |
| 积分流水 | `credits_ledger`(billing.py 自动写) | reason: `task_charge` / `task_refund` |
| 异步任务追踪 | **不复用 jobs.json**,新表 `video_clone_v2_jobs` | v2 多段状态 + segments_plan 装不下 jobs.json |
| 退款幂等 | **复用** `pending_refunds` + `refund_tracker.py` | 进程崩了不丢退款 |
| 内容审核 | **prompt** 走 `content_filter.py`(P2 黑名单);**视频/图**不审 | 法务+弹窗承诺托底 |
| fal 调用 | `fal_client` 直用(同 v1)+ `fal_upload_with_retry` | 不另建包装 |
| 媒体归档 | `media_archiver.py`(防 fal.media 30 天过期) | 成片归档到 `/opt/ssp/uploads/video_clone_v2/{job_id}/` |
| Feature flag | 新增 `ENABLE_VIDEO_CLONE_V2`(默认 false) | 旧 `ENABLE_SEEDANCE_VIDEO_CLONE` 不动 |
| 日志 / 告警 | `logger.log_info/log_error` + `alert.py` | 三道保险触发时走 alert |
| 任务孤儿清理 | `cleanup_orphan_jobs_on_startup` 加 v2 表分支 | 服务重启把 status=processing 标 failed + 退积分 |
| 上传声明留痕 | **新增** `video_clone_v2_disclaimer_log` | 每次用户勾选 disclaimer 留痕,事后举证 |

---

## 2. 价格 + 常量定义(代码常量,改要走 PR)

```python
# services/video_clone_v2_pricing.py(新建)

# ⭐ 全局两档(用户最终决议):premium 已砍掉
# 2026-05-10 commit 3 砍单档:三个 dict 统一成单档常量
SEGMENT_CREDITS:           Final[int] = 20      # 每个 ai 段固定扣 20 积分
SEGMENT_DISPLAY_RMB:       Final[str] = "19.9"  # 前端营销展示价
SEGMENT_LABEL:             Final[str] = "AI 替换"
SEGMENT_INPUT_SECONDS_MAX: Final[int] = 8       # worst-case 估算上限,实际段长 4-8s

# fal 端固定参数(仅 v2 用)
FAL_ENDPOINT       = "bytedance/seedance-2.0/fast/reference-to-video"
FAL_RESOLUTION     = "480p"
FAL_OUTPUT_DURATION = 8
FAL_GENERATE_AUDIO = False           # 用原视频音轨拼回
FAL_SAFETY_CHECKER = True

# 全能档限制
MAX_ULTIMATE_SECONDS = 64
MAX_ULTIMATE_SEGMENTS = 8

# 三道工程保险
MAX_SEGMENT_COST_USD   = 1.50    # 单段 fal 实扣超 → 全额退 + 报警
MAX_ORDER_COST_USD     = 15.0    # 单订单估算超 → 拒收
DAILY_FAL_BUDGET_USD   = 100.0   # 每日累计超 → 自动 disable v2

# ⭐ 功能 4:5 个 prompt 模板(可在 admin 改,先写死)
PROMPT_TEMPLATES = [
    {"id": "baby_goods",  "label": "婴儿用品带货",
     "template": "婴儿安静地玩耍/睡觉/学习抬头,展示婴儿用品的安全和舒适,柔光卧室或客厅"},
    {"id": "clothing_try",  "label": "服装试穿",
     "template": "模特展示服装的合身度和质感,镜头自然过渡,光线明亮简洁"},
    {"id": "food_making",   "label": "美食制作",
     "template": "食材新鲜陈列,烹饪过程清晰展示,光泽诱人,配文火慢炖的氛围"},
    {"id": "digital_unbox", "label": "数码开箱",
     "template": "产品开箱展示,细节特写,质感金属/玻璃反光,简约工业风背景"},
    {"id": "beauty_skincare", "label": "美妆护肤",
     "template": "产品近景,质地细腻,模特肤感清透,柔光梳妆台或大理石背景"},
]

# ⭐ 功能 3:image role 枚举(prompt @ 语法用)
IMAGE_ROLES = ("product", "person", "scene", "reference")
ROLE_TO_AT_LABEL = {
    "product": "产品",   # @产品1
    "person":  "人物",   # @人物1
    "scene":   "场景",   # @场景1
    "reference": "图",   # @图1(默认/兜底)
}

# ⭐ 功能 1:替换模式
REPLACEMENT_MODES = ("partial", "full")
```

---

## 3. 路由 API

**前缀**:`/api/video/clone-v2`(挂在 main.py 紧跟 `clone`)

**Feature flag**:每个端点首行 `if not settings.ENABLE_VIDEO_CLONE_V2: raise 503`

### 3.1 上传视频
```
POST /api/video/clone-v2/upload/video
```
Body: multipart/form-data,跟 v1 同(`upload_guard.read_bounded` 50MB / `fal_upload_with_retry`)。

**Response**: `{ "video_url": "https://v3.fal.media/...", "duration_sec": 32.5 }`

### 3.2 上传参考图(⭐ 功能 3:加 role)
```
POST /api/video/clone-v2/upload/image
```
**Body**(multipart/form-data):
- `file` 图片文件
- `role` 字符串,值 ∈ {`product`, `person`, `scene`, `reference`}

**Response**: `{ "image_url": "...", "role": "product" }`

**校验**:`role` 必传,不在枚举 → 400。前端按需调用 1-3 次,后端只管单图。

### 3.3 切片预览(不扣费)
```
POST /api/video/clone-v2/preview-segments
```
**Body**: `{ "video_url": "...", "video_duration_sec": 32.5 }`

**Response**:
```json
{ "type": "ultimate",
  "segments": [
    { "idx": 0, "start": 0,    "duration": 8.0,
      "thumbnail_url": "https://ailixiao.com/uploads/video_clone_v2/preview/{token}_0.jpg" },
    { "idx": 1, "start": 8.0,  "duration": 8.0, "thumbnail_url": "..." },
    { "idx": 2, "start": 16.0, "duration": 8.0, "thumbnail_url": "..." },
    { "idx": 3, "start": 24.0, "duration": 8.5, "thumbnail_url": "..." }
  ],
  "preview_token": "uuid-for-cache-cleanup"
}
```

**业务**:跟 v3 一样(详见 v3 的 §3.3),preview-segments **不知道 replacement_mode**。前端拿到 segments 后:
- 若用户选 `replacement_mode=partial` → 默认所有段 source_type='original',用户勾要 AI 的段
- 若用户选 `replacement_mode=full` → 默认所有段 source_type='ai',用户可勾掉单段切回原视频

**short-circuit**:`type=single` 时返一段,前端 UI 一张缩略图 + 单段功能 1+2 的 UI(局部/全方位 + AI/原片段)。

### 3.4 价格预估(不扣费)
```
POST /api/video/clone-v2/estimate
```
**Body**(⭐ 功能 1+2:每段含 source_type,2026-05-10 砍单档后无 tier 字段):
```json
{ "type": "ultimate",
  "replacement_mode": "partial",
  "segments": [
    { "idx": 0, "source_type": "ai" },
    { "idx": 1, "source_type": "original" },
    { "idx": 2, "source_type": "ai" },
    { "idx": 3, "source_type": "original" }
  ]
}
```
**Response**:
```json
{ "type": "ultimate",
  "replacement_mode": "partial",
  "ai_segments_count": 2,
  "original_segments_count": 2,
  "total_segments": 4,
  "total_credits": 35,
  "total_rmb_display": "34.8",
  "estimated_minutes": 4
}
```

**校验**:
- `type=single`:segments 长度 = 1
- `type=ultimate`:`len(segments) ∈ [1, 8]`,**至少 1 段 source_type='ai'**(否则没 AI 工作可做,纯原视频拼接 → 拒;前端 UI 也禁掉这种状态)
- 每段 `source_type ∈ {ai, original}`(2026-05-10 砍单档,tier 字段删除)
- Pydantic SegmentPlanItem 加 `extra="forbid"`,传 tier 字段会被拒 422
- `replacement_mode ∈ {partial, full}`(不强校验跟 segments 默认值的对应,纯前端语义)
- `MAX_ORDER_COST_USD` 估算超 → 400

### 3.5 创建任务(扣费 + 异步推 worker)
```
POST /api/video/clone-v2/create
```
**Body**(⭐ 功能 1+2+3:加 replacement_mode / segments 数组 / image_urls 对象数组):
```json
{ "type": "ultimate",
  "replacement_mode": "partial",
  "segments": [
    { "idx": 0, "source_type": "ai" },
    { "idx": 1, "source_type": "original" },
    { "idx": 2, "source_type": "ai" },
    { "idx": 3, "source_type": "original" }
  ],
  "video_url": "https://v3.fal.media/.../input.mp4",
  "video_duration_sec": 32.5,
  "image_urls": [
    {"url": "https://v3.fal.media/.../prod1.png", "role": "product"},
    {"url": "https://v3.fal.media/.../person.jpg", "role": "person"}
  ],
  "prompt": "婴儿在白色鸭子形状睡袋上练习抬头",
  "disclaimer_acknowledged": true }
```

**Body — single 模式**(replacement_mode 含义弱化为前端 UI 状态记录):
```json
{ "type": "single",
  "replacement_mode": "full",
  "segments": [{ "idx": 0, "source_type": "ai" }],
  "video_url": "...", "video_duration_sec": 6.5,
  "image_urls": [{"url": "...", "role": "product"}],
  "prompt": "...", "disclaimer_acknowledged": true }
```

**Response**:
```json
{ "job_id": "uuid", "status": "processing",
  "type": "ultimate", "replacement_mode": "partial",
  "ai_segments_count": 2, "original_segments_count": 2,
  "total_credits_charged": 35, "estimated_completion_minutes": 4 }
```

**业务流(create 入口逻辑顺序)**:
1. **disclaimer 检查**:`disclaimer_acknowledged != true` → 400
2. **disclaimer 留痕**:`INSERT INTO video_clone_v2_disclaimer_log (...)` 写 user_id / ip / video_sha256 / job_id
3. **prompt 内容审核**:`content_filter.check_text(prompt)` 命中 → 400
4. **切片重算**:后端用 `plan_segments_v2(video_duration_sec)` 重算 plan,跟前端传的 `len(segments)` 不一致 → 400(防前端篡改)
5. **每段 source_type 验证**(2026-05-10 砍单档,tier / allowed_tiers 字段删除):
   - 至少 1 段 source_type='ai'(全 original 没工作 → 400)
   - Pydantic extra='forbid' 拒废弃字段(传 tier → 422)
6. **价格 + 保险 2**:算总积分 + 估算 fal 成本(只算 ai 段),超 `MAX_ORDER_COST_USD` → 400
7. **额度检查 + 扣费**:`deduct_credits(user_id, total_credits)` 失败 → 402
8. **prompt 拼接(⭐ 功能 3)**:`build_prompt(prompt, image_urls)` 加 @ 语法(详见 §5.6)
9. **DB 写入**:`INSERT INTO video_clone_v2_jobs (...)` status='processing',segments_plan 包含每段 source_type / start / duration / input_seconds / thumbnail_url(2026-05-10 砍单档,tier 字段已删)
10. **异步推**:`asyncio.create_task(_process_v2_job(job_id))`
11. **立即返回** job_id

### 3.6 查询任务
```
GET /api/video/clone-v2/jobs/{job_id}
```
**鉴权**:仅本人(403),admin role 全可见。

**Response**:
```json
{ "job_id": "uuid", "type": "ultimate",
  "replacement_mode": "partial", "status": "processing",
  "progress": { "completed": 1, "total_ai": 2, "total_original": 2 },
  "segments": [
    {"idx": 0, "source_type": "ai", "status": "completed", "output_url": "..."},
    {"idx": 1, "source_type": "original", "status": "ready"},
    {"idx": 2, "source_type": "ai", "status": "processing"},
    {"idx": 3, "source_type": "original", "status": "ready"}
  ],
  "final_video_url": null,
  "total_credits_charged": 40, "total_credits_refunded": 0,
  "error": null }
```

### 3.7 取消任务
```
POST /api/video/clone-v2/jobs/{job_id}/cancel
```
仅 status `pending`/`processing` 且 segments_results 全空时可取消。已开扣 fal → 拒。
成功 → `status='cancelled'` + `add_credits` 全额退。

### 3.8 历史列表
```
GET /api/video/clone-v2/jobs?limit=20&offset=0
```
返当前用户的最近 N 条(按 created_at DESC)。

### 3.9 ⭐ 功能 4:Prompt 模板(GET,公开)
```
GET /api/video/clone-v2/prompt-templates
```
**Response**:
```json
{ "templates": [
  {"id":"baby_goods","label":"婴儿用品带货","template":"婴儿安静地玩耍..."},
  {"id":"clothing_try","label":"服装试穿","template":"..."},
  {"id":"food_making","label":"美食制作","template":"..."},
  {"id":"digital_unbox","label":"数码开箱","template":"..."},
  {"id":"beauty_skincare","label":"美妆护肤","template":"..."}
] }
```

**业务**:返回 `services/video_clone_v2_pricing.py` 里的 `PROMPT_TEMPLATES` 常量。前端展示成 5 个按钮,点击 → 填入 prompt 输入框 → 用户可调整。

**校验**:无(GET 公开,要不要鉴权未来加 — 不影响隐私)。

---

## 4. 数据库表

详见 `docs/P221-MIGRATION.sql` v3(已对齐 v4 架构)。

**关键字段速览**:
- `id` / `user_id`
- 计费模型:`type`(single/ultimate)/ `replacement_mode`(partial/full)/ `tier`(已废弃留空)/ `segment_tiers`(已废弃留空)
- 输入:`input_video_url` / `input_video_duration_sec` / `input_video_sha256` / **`image_urls`** (JSON: `[{url, role}]`,⭐ 功能 3)/ `prompt` / `prompt_compiled`(⭐ 功能 3:后端拼好的带 @ 语法的最终 prompt)
- 计划:`segments_plan` JSON: `[{idx, start, duration, source_type, input_seconds, thumbnail_url}]`(2026-05-10 砍单档,tier 字段已删,input_seconds 保留作 fallback,后续 cleanup commit 决定是否删)
- 结果:`segments_results` JSON: `[{idx, source_type, fal_request_id, status, output_url, retry_count, actual_cost_usd, error}]`
- 成片:`final_video_url` / `final_video_local_path`
- 计费:`total_credits_charged` / `total_credits_refunded` / `fal_cost_total_usd`
- 状态:`status`(pending/processing/concatenating/completed/failed/refunded/cancelled)
- 错误:`error_step` / `error_message`
- 时间:`created_at` / `updated_at` / `completed_at` / `archived_at`

辅助表:
- `video_clone_v2_daily_budget`(保险 3 看板)
- `video_clone_v2_disclaimer_log`(声明书勾选留痕)

---

## 5. 状态机 + 调度

```
pending          ─(扣费成功)──> processing
processing       ─(全 ai 段成功 + type=single)──> completed
processing       ─(全 ai 段成功 + type=ultimate)──> concatenating ─> completed
processing       ─(部分 ai 段失败,重试 1 次仍失败)──> 跳过失败段 + 部分退款 → completed
processing       ─(全 ai 段失败)──> refunded
processing       ─(用户主动取消)──> cancelled (全额退)
processing       ─(MAX_SEGMENT_COST 触发)──> failed (全额退 + 报警)
任意非终态       ─(服务重启孤儿扫描)──> failed (全额退)
```

**注**:`source_type=original` 段不调 fal,所以"段失败"只对 ai 段。

终态:`completed` / `failed` / `refunded` / `cancelled`(不可再变)

### 5.1 调度伪代码(`services/video_clone_v2_processor.py`)

```python
async def process_v2_job(job_id: str):
    job = load_job(job_id)
    plan = job.segments_plan        # 已含 source_type / tier
    seed = stable_seed(job.id)

    # 1. 切原视频成各段(ai + original 都要切,后续路径不同)
    seg_files = await split_input_video(job)

    # 2. ai 段并发调 fal,original 段从原视频本地切(不调 fal)
    sem = asyncio.Semaphore(2)
    async def gen_one(idx):
        if plan[idx]["source_type"] == "original":
            # ⭐ 功能 1+2:跳过 fal,直接用原视频段
            return SegmentResult(
                idx=idx, source_type="original", status="ready",
                local_path=seg_files[idx], actual_cost_usd=0.0
            )
        # ai 段:截 input_seconds + 调 fal
        async with sem:
            input_url = await prepare_segment_input(seg_files[idx], plan[idx])
            return await call_fal_seedance(input_url, plan[idx], job, seed)

    results = await asyncio.gather(*[gen_one(i) for i in range(len(plan))],
                                    return_exceptions=True)

    # 3. ai 段失败重试(同 seed,1 次)
    for i, r in enumerate(results):
        if plan[i]["source_type"] == "ai" and is_failed(r):
            results[i] = await retry_once(i, seed, seg_files[i], plan[i])

    # 4. 保险 1 触发?(只检查 ai 段)
    for i, r in enumerate(results):
        if plan[i]["source_type"] == "ai" and not is_failed(r):
            if r.actual_cost_usd > MAX_SEGMENT_COST_USD:
                await alert("单段 fal 扣费超限", job.id, i, r.actual_cost_usd)
                await refund_full(job, "single_segment_cost_overflow")
                return

    # 5. 全 ai 段失败 → 全额退(original 段没扣钱所以无需退)
    ai_results = [r for i, r in enumerate(results) if plan[i]["source_type"] == "ai"]
    failed_ai = [i for i, r in enumerate(results)
                 if plan[i]["source_type"] == "ai" and is_failed(r)]
    if len(failed_ai) == len(ai_results) and len(ai_results) > 0:
        await refund_full(job, "all_ai_segments_failed")
        return

    # 6. ⭐ 失败段直接跳过(不补原视频),拼接时只用成功的段
    success_segments = [(i, r) for i, r in enumerate(results)
                        if not (plan[i]["source_type"] == "ai" and is_failed(r))]

    if len(success_segments) == 1:
        final_url = await archive_to_local(success_segments[0][1].output_url, job)
    else:
        merged = await concat_segments(success_segments, plan, job)  # 详见 §7
        final_url = await archive_to_local(merged, job)

    # 7. 部分失败按段退款(只退失败 ai 段的钱,original 段本就不计费)
    if failed_ai:
        refund_credits = sum(TIER_CREDITS[plan[i]["tier"]] for i in failed_ai)
        await add_credits(job.user_id, refund_credits, reason="task_refund",
                         ref_id=job.id, module="video/clone-v2")

    # 8. 写入每日预算(只计 ai 段实扣)
    await record_daily_spend(sum(r.actual_cost_usd for i, r in enumerate(results)
                                  if plan[i]["source_type"] == "ai" and not is_failed(r)))

    # 9. 完成
    update_job(job_id, status='completed', final_video_url=final_url)
```

### 5.2 每段 ai 输入准备(`prepare_segment_input`)

```python
async def prepare_segment_input(seg_file: str, plan_item: dict) -> str:
    """seg_file 已经是切出的某段视频(可能 8s 或末段合并 8.5s)
       plan_item.tier 决定输入秒数:economy=2 / standard=4(全局两档)
       仅 ai 段才调用本函数(original 段在调度第 2 步直接返 local_path)"""
    assert plan_item["source_type"] == "ai", "original 段不应进 prepare_segment_input"
    tier_input = TIER_INPUT_SECONDS[plan_item["tier"]]
    seg_duration = plan_item["duration"]

    if seg_duration <= tier_input:
        out = seg_file
    else:
        out = f"{seg_file}.{plan_item['tier']}.mp4"
        await ffmpeg(["-i", seg_file, "-ss", "0", "-t", str(tier_input),
                     "-c", "copy", out])

    actual_dur = await ffprobe_duration(out)
    if actual_dur < 2.0:
        out2 = f"{out}.padded.mp4"
        await ffmpeg(["-i", out, "-ss", "0", "-t", "2.1",
                     "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                     "-c:a", "aac", out2])
        out = out2

    return await fal_upload_with_retry(out)
```

### 5.3 stable_seed
```python
def stable_seed(job_id: str) -> int:
    return int(hashlib.sha256(job_id.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF
```

### 5.4 fal 调用核心
```python
async def call_fal_seedance(video_url: str, plan_item: dict, job: dict, seed: int):
    return await fal_client.subscribe_async(
        FAL_ENDPOINT,
        arguments={
            "video_urls": [video_url],
            "image_urls": [img["url"] for img in job.image_urls],   # ⭐ 功能 3:取 url
            "audio_urls": [],
            "prompt": job.prompt_compiled,                           # ⭐ 功能 3:用拼好的带 @ 语法 prompt
            "resolution": FAL_RESOLUTION,
            "duration": FAL_OUTPUT_DURATION,
            "aspect_ratio": detect_aspect_ratio(video_url) or "9:16",
            "generate_audio": FAL_GENERATE_AUDIO,
            "enable_safety_checker": FAL_SAFETY_CHECKER,
            "seed": seed,
        },
    )
```

### 5.5 ~~段长 vs tier UI 约束~~(已废弃)

**2026-05-10 commit 3 砍单档,本节失效**。原 allowed_tiers 规则随 _allowed_tiers 函数一并删除。所有段统一定价,无段长档位约束。

### 5.6 ⭐ 功能 3:Prompt 拼接(`build_prompt`)

```python
def build_prompt(user_prompt: str, image_urls: list[dict]) -> str:
    """把 image_urls 的 role 拼成 @ 语法附加到用户 prompt 末尾。

    输入:
        user_prompt:用户自填或 prompt 模板填入的文字
        image_urls:[{"url": "...", "role": "product"}, ...]
    输出:
        "{user_prompt}(参考素材:@产品1, @人物1, @场景1)"
    """
    if not image_urls:
        return user_prompt

    refs = []
    counters = {"product": 0, "person": 0, "scene": 0, "reference": 0}
    for img in image_urls:
        role = img.get("role", "reference")
        if role not in IMAGE_ROLES:
            role = "reference"
        counters[role] += 1
        label = ROLE_TO_AT_LABEL[role]
        refs.append(f"@{label}{counters[role]}")

    return f"{user_prompt}(参考素材:{', '.join(refs)})"
```

**示例**:
- user_prompt="婴儿在睡袋上抬头",images=[{role:"product"}, {role:"product"}, {role:"person"}]
- 输出:`婴儿在睡袋上抬头(参考素材:@产品1, @产品2, @人物1)`

写入 DB `prompt_compiled` 字段供调 fal 时用,**`prompt` 字段保留用户原始输入**(便于审计 / 重新生成)。

---

## 6. 切片算法(`services/video_clone_v2_split.py`)

```python
TIER_INPUT_SECONDS = {"economy": 2, "standard": 4}
MAX_ULTIMATE_SECONDS = 64
MAX_ULTIMATE_SEGMENTS = 8

def plan_segments_v2(total_sec: float) -> list[dict]:
    """切片(不带 source_type / tier — 那是前端用户选完才知道)。
    返 [{idx, start, duration, allowed_tiers}]"""
    if total_sec < 4:
        raise ValueError("视频太短,最少 4 秒")
    if total_sec > MAX_ULTIMATE_SECONDS:
        raise ValueError(f"视频太长,最多 {MAX_ULTIMATE_SECONDS} 秒(请截取 60 秒以内)")

    if total_sec <= 8:
        return [{"idx": 0, "start": 0.0, "duration": total_sec,
                "allowed_tiers": _allowed_tiers(total_sec)}]

    segments = []
    idx = 0
    cur = 0.0
    while cur < total_sec:
        seg_dur = min(8.0, total_sec - cur)
        segments.append({"idx": idx, "start": cur, "duration": seg_dur})
        cur += seg_dur
        idx += 1

    if len(segments) > 1 and segments[-1]["duration"] < 4.0:
        last = segments.pop()
        segments[-1]["duration"] += last["duration"]

    if len(segments) > MAX_ULTIMATE_SEGMENTS:
        raise ValueError(f"段数超限({len(segments)} > {MAX_ULTIMATE_SEGMENTS})")

    for seg in segments:
        seg["allowed_tiers"] = _allowed_tiers(seg["duration"])

    return segments


def _allowed_tiers(seg_duration: float) -> list[str]:
    """根据段长决定可选 tier 列表(全局两档,详见 §5.5)"""
    if seg_duration < 2.0:
        return []
    if seg_duration < 4.0:
        return ["economy"]
    return ["economy", "standard"]
```

**ffmpeg 切片**:每段先尝试 `-c copy`(无损快),时长不足 2s → 重编码补到 2.1s(P220 实测铁律)。

---

## 7. 拼接(`services/video_clone_v2_concat.py`,多段才走)

V1 实现(本期):
1. **每个成功段视频源**:
   - source_type=ai 且成功:用 fal 输出视频
   - source_type=original:用 ffmpeg 从原视频抠对应秒数 (`-ss start -t duration -c copy`)
   - source_type=ai 且失败重试也失败:**直接跳过该段不入拼接**(用户最终决议改方案,不补原视频)
2. 各成功段加 0.3s `fade=in` + `fade=out`(过渡)
3. ffmpeg `concat` demuxer 合并
4. 提取**原视频音轨**(用户上传那条):`-vn -c copy original_audio.aac`
5. concat 视频 + 原音轨混合:`-map 0:v -map 1:a -c:v copy -c:a aac`
   - **注**:跳过失败段后视频时长会比原视频短,音轨需要按比例截短 / 或直接 concat 各 success 段对应的原音轨片段(更精确)

**不做**:colorbalance / 跨段 LUT 归一化(留 P221b)。

**末段长 8.5 秒 / 12 秒(并段后)的 fal 成本**:2026-05-10 砍单档后段输入 = 段实际秒数(4-12s),fal duration 字段对齐到 [4,15] 区间,worst-case 按 SEGMENT_INPUT_SECONDS_MAX × $0.0925 × 1.3 估算。

---

## 8. 三道工程保险

```python
# config.py
class Settings(BaseSettings):
    ENABLE_VIDEO_CLONE_V2: bool = False
    MAX_SEGMENT_COST_USD: float = 1.50
    MAX_ORDER_COST_USD: float = 15.0
    DAILY_FAL_BUDGET_USD: float = 100.0
```

**保险 1 — 单段 ai fal 实扣超限**:
- 触发点:`call_fal_seedance` 完成后查 `actual_cost_usd`
- 超 → `add_credits` 全额退 + alert
- fallback(fal 不返 cost):用 `SEGMENT_INPUT_SECONDS_MAX × $0.0925/s × 1.3` 估,超阈值也触发

**保险 2 — 单订单总额上限**:
- 触发点:`/estimate` 和 `/create` 入口
- 估算只算 ai 段(original 段免费)
- 超 → 直接 400

**保险 3 — 每日总预算**:
- 表 `video_clone_v2_daily_budget`(date PK + spent_usd + locked)
- 每段 ai 成功后 INSERT/UPDATE 累加
- 超 → `locked=1` + 改 `settings.ENABLE_VIDEO_CLONE_V2 = False` + alert
- 次日 00:05 cron INSERT OR IGNORE 当日新行 + 检查前一天 locked 触发短信
- 服务重启 startup hook 读当日 `locked`,locked → 强制 disable(防进程内 flag 重启失效)

---

## 9. 内容审核(对齐用户最终决议)

**用户决议**:不做技术审核 → 强力法务协议托底。

**实际做的**:
- ✅ **prompt** 走 `content_filter.check_text(...)` P2 简版黑名单(政治/色情/暴力 ~200 词)
- ❌ **视频 / 图片**:**不做**任何技术审核(明星识别 / 未成年人识别 / NSFW / 品牌侵权)
- ✅ **fal 端 safety_checker**:`enable_safety_checker: true` 必开(fal 自带兜底)
- ✅ **法务 + 弹窗双层托底**:
  - 用户协议 §4.4(`docs/legal/terms-of-service.md`)
  - 上传弹窗 `docs/legal/video-clone-v2-upload-disclaimer.md`(C1-C6 + A1-A5 + 总勾)
  - 后端 create 入口 `disclaimer_acknowledged != true` → 400
  - 后端落库 `video_clone_v2_disclaimer_log`(法务事后举证)

---

## 10. 退款逻辑总览

| 场景 | 退款金额 | 触发位置 |
|---|---|---|
| 全 ai 段失败 | 全额(total_credits_charged) | processor 第 5 步 |
| 部分 ai 段失败 | 单档:每失败段统一退 SEGMENT_CREDITS(=20 积分),`refund = len(failed_ai) × SEGMENT_CREDITS`,失败段在拼接时跳过 | processor 第 7 步 |
| 用户主动取消(未扣 fal) | 全额 | `/cancel` 端点 |
| 保险 1 触发 | 全额 | processor 第 4 步 |
| fal NSFW 拦 | 该段 tier 价格(走 ai 段失败路径) | call_fal_seedance |
| 服务重启孤儿 | 全额 | startup cleanup |

**注**:`source_type=original` 段本就不计费,跳过 / 失败 / 没失败概念都不适用。

所有退款走 `add_credits(reason="task_refund", ref_id=job_id, module="video/clone-v2")` → 自动写 credits_ledger。

幂等:**所有 add_credits 调用前先 `pending_refunds` 表 INSERT(task_id PK)**,`UPDATE refunded=1 WHERE refunded=0` 原子保证只退一次。

---

## 11. 阶段范围(对齐用户最终决议)

> ⚠ 用户最终决议:**不预测上线时间,做完才算**。下面只列任务,不写天数。

### 阶段 A — 数据库 + API 骨架 + 单段两档
- A1 基建
  - [ ] `database.py init_db()` 加 3 张表(`video_clone_v2_jobs` / `video_clone_v2_daily_budget` / `video_clone_v2_disclaimer_log`)
  - [ ] alembic mirror migration
  - [ ] `config.py` 加 `ENABLE_VIDEO_CLONE_V2` + 三个保险阈值
  - [ ] `services/video_clone_v2_pricing.py`(常量 + PROMPT_TEMPLATES + IMAGE_ROLES + REPLACEMENT_MODES + 价格计算)
  - [ ] `app/api/video_clone_v2.py` 骨架(9 个端点 + 503 占位)
  - [ ] `main.py` `include_router`
- A2 单段两档跑通
  - [ ] `services/video_clone_v2_split.py`(切片 + allowed_tiers,unit test ≥ 10 case)
  - [ ] `services/video_clone_v2_processor.py`(单段路径,⭐ 含 source_type='original' 跳过逻辑)
  - [ ] `services/video_clone_v2_archive.py`(本地归档)
  - [ ] **⭐ 功能 3 prompt 拼接 `build_prompt`**(单段也用)
  - [ ] **⭐ 功能 4 prompt 模板 GET 端点**(返 PROMPT_TEMPLATES)
  - [ ] 真实 fal 跑通 economy / standard 两档
- A3 验收
  - [ ] 两档单段实测视频 URL × 2 + 实测扣费数据(对照 P220 基线 ¥6.66 / ¥7.5)
  - [ ] OpenAPI `/docs` 自动生成截图
  - [ ] 单元测试 pass(切片 + 计费 + 鉴权 + prompt 拼接 + image role 校验)
  - **阶段 A 不上线**(`ENABLE_VIDEO_CLONE_V2=False`)

### 阶段 B — 全能档分段 + 段位选择 + ⭐ 功能 1+2(部分 / 全方位 / 段切换)
- [ ] `/preview-segments` 端点 + ffmpeg 缩略图
- [ ] `processor.py` 加 multi-segment 调度(并发 2 + 串行)
- [ ] **⭐ source_type='original' 段从原视频抠 ffmpeg `-ss/-t -c copy`**(本地切,不调 fal)
- [ ] `services/video_clone_v2_concat.py`(fade-only + 原音轨,**失败段跳过不补**)
- [ ] 失败段重试 1 次(同 seed)
- [ ] preview_token GC 规则加进 uploads_gc.py
- [ ] 简单前端 UI:上传 → 局部/全方位 radio → 段位选择卡片(每段:AI/原片段 + 档位) → 价格汇总 → 生成
- [ ] 验收:全能档 4 段 + 含 original 段实测;部分 ai 失败跳过实测

### 阶段 C — 任务进度 + 历史 + 内容审核 + ⭐ 功能 4 模板 UI
- [ ] 任务进度页(实时段状态轮询)
- [ ] 历史记录页
- [ ] prompt 内容审核接入 `content_filter.check_text`
- [ ] **⭐ 功能 4 前端**:5 个 prompt 模板按钮 → 填入 prompt 框 → 用户可改
- [ ] 上传弹窗渲染 disclaimer + 勾选状态上报后端
- [ ] frontend mirror legal docs 同步
- [ ] 任务孤儿清理 hook 加 v2 表分支

### 阶段 D — 联调 + 灰度
- [ ] 5 个种子用户内测(覆盖 4 个新功能各场景)
- [ ] 真实场景压测(并发 / 边缘段长 / fal 失败注入 / 全 ai 段失败 / 部分失败跳过)
- [ ] 上线灰度(10% — 通过用户级 feature flag,等用户决定开关方式)

---

## 12. 待用户决议(本版剩 1 个,前 6 个已敲定)

| # | 问题 | 我的推荐 |
|---|---|---|
| 1 | 是否新增 `video_clone_v2_disclaimer_log` 表(每次勾选弹窗留痕,法务事后举证 §4.4.4 用) | **加**。表很轻(user_id + ip + video_sha256 + ts),A1 一起做。 |

其他原 OPEN QUESTIONS 已全部由用户决议敲定。

---

## 13. 已确定不做的事(给未来 Claude 看,避免再问)

- ❌ **不做** 视频 / 图片技术审核(明星脸、未成年人、品牌侵权)— 用户最终决议
- ❌ **不做** premium 档(全局只两档:economy + standard)— 用户最简化决议
- ❌ **不做** colorbalance 跨段色调归一化 — 留 P221b
- ❌ **不做** 阶梯折扣(全能段 N → ¥0.1×(N-1) 折让)— 整数积分不可表达
- ❌ **失败段不补原视频**(直接跳过,拼接出来视频会比原视频短)— 用户最终决议改方案
- ❌ **不动** `ENABLE_SEEDANCE_VIDEO_CLONE`(旧版 v1 保持下线)— 用户明令
- ❌ **不写** API key 进 git
- ❌ **不擅自** 上线 V2,`ENABLE_VIDEO_CLONE_V2=False` 默认值不能在 PR 里改
- ❌ **不预测** 上线时间(阶段 A/B/C/D 不带天数,做完才算)— 用户最终决议
