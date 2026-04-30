# Phase 2 — 长视频拆段并行实施方案

> **状态:评估稿(2026-04-30)** — 不做任何代码改动,等用户读完再决定是否启动。
> **预计工程量:15-20 小时** | **优先级:P0(根因 bug,不是优化)**

---

## TL;DR

**当前实现是 bug,不是质量瓶颈**。最近一个成功 session `14c390bb-1ea` 实测证据:

| 产物 | 实际时长 | 期望时长 |
|---|---|---|
| 原视频 `original_video_path` | 75.371 s | — |
| `swap1_video_url` (wan-vace round-1) | **5.063 s** | 75 s |
| `swapped_video_url` (wan-vace round-2) | **5.063 s** | 75 s |
| `final_video_url` (lipsync 后) | 75.392 s | 75 s |

中间 wan-vace 输出固定 **5.063 s**(= 81 frames @ 16 fps,fal 默认值)。lipsync 把 5s 画面"拉/重复"匹配 75s 音频,所以表面上 final 时长对得上,但**画面只有头 5 秒被换装**,后 70 秒等同于失败。流程"跑绿"是假象。

`docs/ORAL-BROADCAST-PLAN.md` §820 已预告"3 分钟成片需要拆段,先 MVP 不做"。Phase 2 是把这个 TODO 还掉。

---

## 1. 当前 `_run_inpainting_step` 实现

**位置:** `backend/app/api/oral.py:439-577`

**结构:**
```
_run_inpainting_step(session_id):
    person_mask = session["person_mask_image_path"]              # 必需
    product_mask = session["product_mask_image_path"]            # 可选
    do_swap2 = bool(product_mask and products)

    # ───── round-1 (必跑):换人 ─────
    result1 = inp_svc.inpaint(
        video_url=video_fal_url,            # 输入原视频
        mask_image_url=person_mask_fal_url,
        prompt="Replace the person ...",
        reference_image_urls=model_refs,
        resolution=_resolution_for_tier(tier),
        # ⚠️ 没传 num_frames,用 default=81 → 输出 5s
    )
    swap1_url = result1["video_url"]
    _update_session(swap1_video_url=swap1_url)

    # ───── round-2 (可选):换产品 ─────
    if do_swap2:
        result2 = inp_svc.inpaint(
            video_url=swap1_url,            # 串行依赖 round-1 输出
            mask_image_url=product_mask_fal_url,
            prompt="Replace the product ...",
            reference_image_urls=product_refs,
            resolution=...,
            # ⚠️ 同样没传 num_frames
        )
        _update_session(swapped_video_url=result2["video_url"])
    else:
        _update_session(swapped_video_url=swap1_url)

    if _try_advance_to_lipsync(session_id):
        asyncio.create_task(_run_lipsync_step(session_id))
```

**关键观察:**

1. **round-1 / round-2 是"按 mask 类型分轮"(person vs product),不是"按时长拆段"。** 这两个维度正交。Phase 2 拆段并不替换 round-1/2,而是叠加在每一轮内部。
2. round-2 严格依赖 round-1 输出(必须串行),无法并行。
3. swap1/swap2 命名容易让人误以为"已经分段",**实际不是**。
4. `inp_svc.inpaint(...)` 调用**没传 `num_frames`**,落到 fal 默认 81。

---

## 2. fal-ai/wan-vace-14b/inpainting 参数边界

来源:`docs/ORAL-BROADCAST-PLAN.md` §202 / §928(从 fal 官方文档摘录)

| 参数 | 范围 | 默认 |
|---|---|---|
| `num_frames` | 81 – 241 | **81** ⚠️ |
| `frames_per_second` | 5 – 30 | 16 |
| `resolution` | auto / 240p-720p | auto |
| `aspect_ratio` | auto / 16:9 / 1:1 / 9:16 | auto |

**单次调用最大输出:** 241 帧 ÷ 16 fps = **15.0625 s**

**所以无论怎么调参数,wan-vace 单次都做不了 75s。必须拆段。**

> 把 fps 降到 5 理论可塞 48s,但低 fps 画面卡顿,口播带货不可用,不在方案里。

---

## 3. swap1 / swap2 / swapped 字段含义(澄清)

```
oral_sessions.swap1_video_url        = round-1 输出(person 换装后)
oral_sessions.swap2_video_url        = round-2 输出(product 换装后)— 实际没用,见下
oral_sessions.swapped_video_url      = 进 lipsync 的最终 swap 产物
                                       = swap2 if 双 mask, else swap1
```

`grep "swap2"` 显示数据库 schema 留了 `swap2_video_url` 列,但代码里**只在 round-2 分支写**(oral.py:546-549),且写的是 `swapped_video_url` 而非 `swap2_video_url` —— **命名有点冗余**,Phase 2 重构时可顺手清理(也可不动,字段闲置无害)。

**Phase 2 扩展时,新字段建议命名:**
- `swap1_segments_json`:round-1 各分段 URL 列表 + 段时长元数据
- `swap2_segments_json`:round-2 各分段 URL 列表
- `swap1_video_url` 保留语义:= round-1 拼接后整片(向后兼容前端展示)
- `swapped_video_url` 保留语义:= 进 lipsync 的最终拼接整片

---

## 4. 实施方案(草案)

### 4.1 拆段策略

```
段长上限 SEG_MAX = 12s         # 留 3s 余量(241 帧上限 15s)
段长下限 SEG_MIN = 6s          # 太短换装不稳定
重叠 OVERLAP = 0.5s            # 段间 0.5s 重叠用于拼接平滑

N = ceil(duration / SEG_MAX)
seg_len = duration / N         # 均分,避免最后一段过短
```

例:75s → N=7,每段 ~10.7s。

### 4.2 拆 → 跑 → 拼 流程

```
async def _run_inpainting_step_v2(session_id):
    duration = session["duration_seconds"]
    N = ceil(duration / SEG_MAX)
    segments = ffmpeg_split(original_video, N, OVERLAP)
                # → [seg_0.mp4, seg_1.mp4, ..., seg_{N-1}.mp4]

    # ───── round-1 person:N 段并发 ─────
    seg_urls_1 = await asyncio.gather(*[
        inp_svc.inpaint(
            video_url=fal_upload(seg),
            mask_image_url=person_mask_fal_url,    # 全段共用同一张 mask
            prompt=prompt1,
            reference_image_urls=model_refs,       # 全段共用参考图
            resolution=resolution,
            num_frames=frames_for(seg_duration),   # 显式传
        )
        for seg in segments
    ])
    swap1_full = ffmpeg_concat(seg_urls_1, OVERLAP)
    _update_session(swap1_video_url=swap1_full,
                    swap1_segments_json=json.dumps(seg_urls_1))

    # ───── round-2 product:基于 round-1 拼接结果再拆 N 段并发 ─────
    if do_swap2:
        segments_2 = ffmpeg_split(swap1_full, N, OVERLAP)
        seg_urls_2 = await asyncio.gather(*[...])
        swap2_full = ffmpeg_concat(seg_urls_2, OVERLAP)
        _update_session(swapped_video_url=swap2_full,
                        swap2_segments_json=json.dumps(seg_urls_2))
    else:
        _update_session(swapped_video_url=swap1_full)
```

### 4.3 段间拼接

两条路线选一:

**A) 简单 concat(推荐先做)**
```
ffmpeg -f concat -safe 0 -i list.txt -c copy out.mp4
```
段间会有 0.5s 重叠,直接丢掉一段的开头 0.25s 和另一段的结尾 0.25s,硬切。**成本 0,可能在切点有跳变**。

**B) 帧融合(优化项,先不做)**
0.5s 重叠区域用 alpha blending 渐变。需要 ffmpeg `xfade` 滤镜,工程量 +2h,但段间过渡更自然。

### 4.4 并发数节流

`asyncio.gather` 同时打 N=7 个 wan-vace 请求,fal.ai 账户级 QPS 限制需要确认。**保守做法:`asyncio.Semaphore(3)` 限并发 3**,7 段总耗时 ≈ 单段 × ⌈7/3⌉ = 单段 × 3,而不是 × 7。

---

## 5. 成本估算

价格(`fal_service.py:455-457` + `:401-403`):

| Tier | wan-vace 分辨率 | wan-vace 单价 | lipsync 端点 | lipsync 单价 |
|---|---|---|---|---|
| economy | 480p | $0.04/s | veed/lipsync | $0.40/min |
| standard | 580p | $0.06/s | latentsync | $0.005/s (>40s) |
| premium | 720p | $0.08/s | sync-lipsync/v2 | $3.00/min |

### 75s 视频 economy 档(双 mask 双轮)

```
当前实现(实际产出 5s 假成品):
  wan-vace round-1 (5s @ 480p):  $0.04 × 5 = $0.20
  wan-vace round-2 (5s @ 480p):  $0.04 × 5 = $0.20
  lipsync veed (75s 音频):       $0.40 × 1.25 min = $0.50
  ─────────────────────────────────────────────
  小计:                           $0.90  ← 但产物垃圾,等于白花

Phase 2 拆段(N=7,真正 75s 成品):
  wan-vace round-1 (75s 总,7 段):$0.04 × 75 = $3.00
  wan-vace round-2 (75s 总,7 段):$0.04 × 75 = $3.00
  lipsync veed (75s):             $0.40 × 1.25 = $0.50
  ─────────────────────────────────────────────
  小计:                           $6.50

成本上升:$0.90 → $6.50(7.2x)
但当前的 $0.90 是"白扔",所以实际是 0 → $6.50
```

### 计费侧检查

`compute_charge` 现状(`oral.py:86-98`):
```
经济:  ¥80/min  → 2.67 积分/秒    × 75s = 200 积分(¥100)
标准:  ¥180/min → 6.00 积分/秒    × 75s = 450 积分(¥225)
顶级:  ¥350/min → 11.67 积分/秒   × 75s = 875 积分(¥437)
```

经济档 75s 收 ¥100,fal 成本 $6.50 ≈ ¥47,**毛利约 53%**(未含其他成本)。
**fal 成本上升不需要前端调价。** 预扣公式已经按秒×倍率,价格表本身就是按 75s 全做计算的。

---

## 6. 风险点

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 段间面部跳变(seam artifact) | 🔴 高 | 段内每段都传同一组 `reference_image_urls`,模型用同一参考图收敛;拼接处加 0.25s 硬切丢帧 |
| 同一人在不同段长得不一样 | 🟠 中 | 同上,固定参考图;若仍漂,加 `seed` 固定参数 |
| 退款逻辑要重写 | 🟠 中 | 当前 `failed_step4` 整段退,拆段后部分段成功部分失败 → 全段失败 + 全退,简化语义 |
| fal 账户并发限速 | 🟡 低 | Semaphore(3) 节流 |
| 拼接时音轨对不齐 | 🟡 低 | 拆段时音轨剥离,inpaint 后只拼视频,音轨在 lipsync 步骤重生成 |
| ffmpeg 拆/拼 IO 慢 | 🟡 低 | 75s 视频本机 ffmpeg 拆/拼 < 5s,可忽略 |
| 段长不均匀(75/7=10.71)| 🟢 极低 | 用浮点 seg_len,ffmpeg `-ss/-t` 精准 |
| 进度推送字段变化 | 🟠 中 | WS 推送 `step4_progress` 由 `round-1/round-2` 改成 `round-1/N + round-2/N`,前端要同步 |

---

## 7. 工时拆分(15-20h)

| # | 任务 | 时长 |
|---|---|---|
| 1 | 写 `ffmpeg_split` / `ffmpeg_concat` helper(可复用 `video_studio` 已有的 ffmpeg 工具) | 1.5 h |
| 2 | 改 `_run_inpainting_step` 为分段并发版本 | 3.0 h |
| 3 | 加 `swap1_segments_json` / `swap2_segments_json` 列(database.py 加列 + 老库迁移) | 1.0 h |
| 4 | 加 `asyncio.Semaphore` 并发节流 + `frames_for(seg_duration)` 帧数计算 | 0.5 h |
| 5 | 改 `_build_status_payload` step4 进度由 `2 round` 改成 `2 round × N seg` | 1.5 h |
| 6 | 前端 `oral-broadcast/[id]/page.tsx` step4 进度条按 round + seg 二维显示 | 2.0 h |
| 7 | 改退款语义(任意段失败 → 全段失败 + 全退) | 1.0 h |
| 8 | 单测:mock fal 返回 → 验证 N 段拆/拼流程 + 部分段失败行为 | 2.5 h |
| 9 | 端到端实测:75s / 120s / 180s 三档 each tier 共 9 例 | 2.5 h |
| 10 | 文档更新(ORAL-BROADCAST-PLAN.md §3 Step 4 / 本文档) | 1.0 h |
| 11 | Buffer(段间 seam 调试 + fal 限速实测调整) | 1.5 - 3.5 h |
| **合计** | | **17.5 - 19.5 h** |

---

## 8. 不在本方案内(明确剔除)

- ❌ 帧融合 / xfade 优化(先做硬切,seam 真不行再加)
- ❌ wan-vace 替换成其他模型(现在不评估替换,只解决拆段)
- ❌ Studio 长视频管线复用(那个是 60s 切片 image-to-video,与口播 video-to-video 不同维度)
- ❌ lipsync 拆段(lipsync 三档都已支持长音频,不需要拆;只 step4 需要拆)
- ❌ 修 `swap2_video_url` 闲置字段(顺手不优先)

---

## 9. 决策点(等用户拍板)

1. **是否启动?** 当前是 P0 bug 还是 P1 优化由用户判断 — 实测证据看是 P0。
2. **段长 / 重叠参数:** 推荐 `SEG_MAX=12s, OVERLAP=0.5s`。是否接受?
3. **并发数:** 推荐 `Semaphore(3)`。要不要更激进 / 保守?
4. **顺序:** 先做这个,还是先把当前部署的 13e05f0 bypass 验证完全跑稳再做?
5. **要不要顺手做帧融合(+2h)** 还是先硬切 ship 看 seam 实际严重程度?

---

## 10. 附:实测证据

```bash
# 数据库
$ sqlite3 /opt/ssp/backend/dev.db "SELECT id, duration_seconds, status FROM oral_sessions WHERE id='14c390bb-1ea';"
14c390bb-1ea|75.371|completed

# 各阶段产物时长
$ ffprobe -show_entries format=duration video_c66315ac497e4f17bcc682d318a85c0c.mp4   # swap1
duration=5.063000

$ ffprobe -show_entries format=duration video_26df2c475b254315b336c30d26207051.mp4   # swap2
duration=5.063000

$ ffprobe -show_entries format=duration /opt/ssp/uploads/oral/.../14c390bb-1ea/final.mp4
duration=75.392000

# 解释
81 frames ÷ 16 fps = 5.0625 s   ← 完美对应 wan-vace 默认 num_frames=81 输出
```
