---
name: SSP V2 fal cost 对账机制(公开前必修)
description: fal seedance r2v API 不返 cost / request_id,db 里 actual_cost_usd 是估算非真实,长期是慢性对账 bug
type: project
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---
# 现状(2026-05-10 实测)

`bytedance/seedance-2.0/fast/reference-to-video` 端点 `subscribe_async` 返回:

```json
{
  "video": {"url": "...", "content_type": "video/mp4", "file_name": "video.mp4", "file_size": ...},
  "seed": 42
}
```

**没有 cost 字段。没有 request_id 字段**。

`processor.py:265` 注释证实:fal seedance 不直接返 cost,我们暂时填 None,processor 用 fallback 估算(`tier_input_seconds × 0.0925 × 1.3`)。

# Why this matters

- db 里 `video_clone_v2_jobs.segments_results.actual_cost_usd` **不是 fal 真实扣费**,是猜测值
- 财务对账时:fal dashboard 总扣费 ≠ db 总 actual_cost_usd 之和,差额无法解释
- 单段成本超限保护(`VC2_MAX_SEGMENT_COST_USD`)用估算值判断,**保护可能失效**
- 给用户 invoice / 财务报表时,数字假
- 已踩(2026-05-10):用户问"4 段 probe 实际花多少钱",我答不出,只能让用户去 fal dashboard 手查

# Three-tier 修复路径

**短期(今天就该做)**:每周/每月人工对账
- 用户在 fal dashboard 导出当月扣费记录
- 跟 db 里 `SUM(actual_cost_usd)` 对比
- 差额 > 10% 就报警查原因(可能 fal 改了 fallback 倍率不准、可能漏计了某段)

**中期(公开 1-3 个月内)**:看 fal 是否提供 billing API / webhook
- 调查 fal 平台 docs,确认有无 `GET /billing/usage` 之类
- 有 → 写定时任务每天拉,反向更新 db `actual_cost_usd`
- 无 → fal community 提工单要求加这个 feature

**长期(公开后持续)**:每次任务存 fal 返回的所有对账标识
- 当前只存了 `seed` 和 `output_url`,没存 fal storage 上的 input video URL hash、调用时间精确到 ms
- 加字段:`fal_endpoint`(端点版本)、`fal_call_started_at`(精确时间)、`fal_storage_input_url`(上传后 fal 给的 URL)
- fal 平台后续如果暴露 billing API 按时间区间查询时,这些标识能让我们对到具体调用

# How to apply

- 任何人(包括我)看 db `actual_cost_usd` 时,**默认当估算值,不当真实账单**
- 给用户/财务的报表标注"基于估算,精确数字以 fal dashboard 为准"
- 公开前 checklist 必修项之一(其它公开前必修:假蓝绿 / wall time 体验)
- 不是今天做,但**产品公开前必修**
