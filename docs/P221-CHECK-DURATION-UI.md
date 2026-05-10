# P221 智能切片提示 UI 设计

> 创建:2026-05-09 / A2 阶段新增
> 配套:`POST /api/video/clone-v2/check-duration` 端点
> 数据库:`video_clone_v2_jobs.{trimmed_seconds, trim_start, trim_end}`

---

## 触发流程

```
用户上传视频
    │
    ▼
upload/video 返回 { video_url, duration_sec=18.5, sha256, sha256_short }
    │
    ▼
前端立即 POST /api/video/clone-v2/check-duration { video_duration_sec: 18.5 }
    │
    ├─ needs_trim=false → 直接进 preview-segments → 选档生成 (现有流程)
    │
    └─ needs_trim=true  → 弹窗 ↓
```

---

## 弹窗布局(18 秒视频要丢 2 秒为例)

```
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║  ⚠️  视频时长 18 秒,需要丢弃 2 秒以匹配生成时长              ║
║                                                              ║
║  说明:每段固定生成 8 秒。最接近的完美时长是 16 秒(2 段),    ║
║       多余的 2 秒由你选择丢弃方式。                          ║
║                                                              ║
║  ─────────────────────────────────────────────────────────  ║
║                                                              ║
║  请选择丢弃方式:                                            ║
║                                                              ║
║  ┌──────────────────────────────────────────────────┐       ║
║  │ 🔘  ⭐ 推荐:丢末尾 2 秒                            │       ║
║  │     时间段:16.0s ~ 18.0s                         │       ║
║  │     运动量:0.4(几乎静止,影响最小)              │       ║
║  │                                                  │       ║
║  │     [缩略图 16-18s 末段]                         │       ║
║  └──────────────────────────────────────────────────┘       ║
║                                                              ║
║  ┌──────────────────────────────────────────────────┐       ║
║  │ ⚪  丢开头 2 秒                                    │       ║
║  │     时间段:0.0s ~ 2.0s                           │       ║
║  │     运动量:0.5(中等)                            │       ║
║  │                                                  │       ║
║  │     [缩略图 0-2s 开头]                           │       ║
║  └──────────────────────────────────────────────────┘       ║
║                                                              ║
║  ┌──────────────────────────────────────────────────┐       ║
║  │ ⚪  丢中间 2 秒                                    │       ║
║  │     时间段:8.0s ~ 10.0s                          │       ║
║  │     运动量:0.6(略高,可能丢掉关键动作)          │       ║
║  │                                                  │       ║
║  │     [缩略图 8-10s 中段]                          │       ║
║  └──────────────────────────────────────────────────┘       ║
║                                                              ║
║  ┌──────────────────────────────────────────────────┐       ║
║  │ ⚪  我自己剪辑后重新上传                            │       ║
║  │     建议:用剪映 / Final Cut 把视频精确剪到        │       ║
║  │     8 / 16 / 24 / 32 / 40 / 48 / 56 / 64 秒       │       ║
║  └──────────────────────────────────────────────────┘       ║
║                                                              ║
║  ─────────────────────────────────────────────────────────  ║
║                                                              ║
║  关于"运动量":数字越小代表那段画面越静止,丢掉影响越小。     ║
║  系统已自动测算并推荐最适合丢弃的段。                        ║
║                                                              ║
║  [ 取消 ]                       [ 确认丢弃,继续 ]            ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

---

## 响应数据结构(后端 → 前端)

**情况 1 — 无需 trim**(8 / 16 / 24 / ... 整倍数,或 ≤ 8s 单段)
```json
{
  "needs_trim": false,
  "current_duration": 16.0,
  "target_duration": 16.0
}
```

**情况 2 — 需要 trim**(18.5s 例)
```json
{
  "needs_trim": true,
  "current_duration": 18.5,
  "target_duration": 16.0,
  "drop_seconds": 2.5,
  "suggestions": [
    {
      "label": "丢末尾 2.5 秒",
      "position": "tail",
      "start": 16.0,
      "end": 18.5,
      "motion_score": 0.4,
      "recommended": true
    },
    {
      "label": "丢开头 2.5 秒",
      "position": "head",
      "start": 0.0,
      "end": 2.5,
      "motion_score": 0.5
    },
    {
      "label": "丢中间 2.5 秒",
      "position": "middle",
      "start": 8.0,
      "end": 10.5,
      "motion_score": 0.6
    }
  ]
}
```

`suggestions` 已按 `motion_score` 升序排,第一个标 `recommended=true`(运动量最低,丢掉影响最小)。

---

## 用户确认后

前端把用户选的候选 `start/end` 传到 `/create`(以及之后的 `/preview-segments`)。

`POST /api/video/clone-v2/create` body 加:
```json
{
  ...
  "trim_start": 16.0,
  "trim_end": 18.5,
  ...
}
```

后端在 `processor.split_input_video` 里跳过 `[trim_start, trim_end]` 区间:
```python
# 把 plan 里的段时间映射到原视频(避开丢弃区间)
def map_plan_to_original(plan, trim_start, trim_end):
    drop = trim_end - trim_start
    for seg in plan:
        if seg["start"] >= trim_start:
            seg["start"] += drop      # 整段后移
        # 若 seg.start < trim_start < seg.end → split.py 不应产出这种 plan(target 已规整)
    return plan
```

**注意**:因为 `target_duration = floor(duration/8)*8`,plan 都是基于 target 算的整 8 秒段,跳过的 trim 区间不会跨段(算法保证)。

---

## ffmpeg 运动量算法(motion_score)

用 `signalstats` 滤镜的 YDIF(亮度通道相邻帧差):
```
ffmpeg -ss <start> -i input.mp4 -t <duration> \
       -vf "signalstats,metadata=print:file=/tmp/sig.txt" \
       -an -f null /dev/null
# 然后 parse /tmp/sig.txt 拿 YDIF=xxx 行平均
```

**实测对照**(P220 婴儿视频 8 秒,3 段):

| 段 | 时间区间 | motion_score(YDIF 均值) |
|---|---|---|
| 开头 | 0-2s | **0.4** ⭐ 最静 |
| 中间 | 3-5s | 0.63 (动作最大) |
| 末尾 | 6-8s | 0.48 |

算法**能分辨**段间运动差异,排序合理(开头婴儿刚出现还没动作,中段抬头动作最猛,末段略缓)。

---

## "我自己剪辑后重新上传" 选项

引导用户用第三方工具精确裁剪到 8/16/24/...64 秒。前端不展示"剪辑工具",只是给文字建议:
- 微信用户:剪映(免费)
- iOS:照片 App 自带剪辑
- Web:Clipchamp / Canva 视频编辑

完美 8 倍数视频回到 `check-duration` → `needs_trim=false` → 进入正常生成流程。

---

## 边界 case

| 输入时长 | 行为 |
|---|---|
| < 2.0s | 拒绝(`HTTPException 400 视频太短`)|
| [2, 4) | 单段,仅 economy(无 trim)|
| [4, 8] | 单段,两档可选(无 trim)|
| 9-15s | 弹窗,target=8,drop=1-7s |
| 16s 整(±0.05s 容差) | 完美,无 trim |
| 17-23s | 弹窗,target=16 |
| 24s 整 | 完美 |
| 25-63s | 弹窗,target = floor/8 × 8 |
| 64s 整 | 完美(段数 8 上限)|
| > 64s | 拒绝(`HTTPException 400 视频太长`)|
