---
name: SSP V2 fal wall time 体验雷(公开前必修)
description: fal r2v 单段 wall time 116-132s,临近 2 分钟用户耐心阈值,缺进度反馈/超时保护/监控会引发投诉
type: project
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---
# 现状(2026-05-10 probe 实测)

`bytedance/seedance-2.0/fast/reference-to-video` 端点 wall time:

| input 段长 | wall time |
|------------|-----------|
| 4s | 118s |
| 5s | 116s |
| 6s | 132s |
| 7s | 129s |
| 8s | ~120s(昨晚 prod 实测) |

**关键数据点**:
- wall time 跟 input 长度**不强相关**(116-132s 范围内随机分布,主要是排队 + 模型推理)
- 单段 ~2 分钟,**临近用户耐心阈值**(行业经验:90s 后退出率显著上升)
- ultimate 多段并发(`asyncio.Semaphore(3)`)→ 总 wall time ≈ 较慢段 + 排队损耗,可能突破 5 分钟

# Why this matters

公开前要做完整产品体验,光出片成功不够。当前实现裸出片,可能暴露三个问题:

1. **没有进度反馈** → 用户看到"处理中"3 分钟以为卡死、刷页面、重新下单 → 重复扣费投诉
2. **没有超时保护** → fal 一次卡 10 分钟(高峰期偶发),用户钱锁住,客服压力
3. **没有 wall time 监控** → fal 平台 SLA 漂移自己不知道,用户先察觉

# Three-tier 公开前必修

**1. 进度反馈 UI(必修)**
- 段卡片显示"处理中(预计剩余 X 秒)"
- 后端 polling endpoint 返回每段 status:queued / fal_submitted / fal_processing / completed / failed
- 前端按真实 status 更新而非"轮询查 status,有就显示"
- 整片进度 = 已完成段数 / 总段数

**2. fal 超时保护(必修)**
- 单段 fal 调用超过 5 分钟自动取消重试 1 次
- 单段超过 10 分钟自动 failed → 走 partial refund 链路
- 整 job 超过 30 分钟全部 failed → 全额退款
- 退款链路已有(`_refund_full` / `_refund_partial`),只需挂超时触发

**3. wall time 监控(应做)**
- 每段 fal 调用记录 `fal_started_at` / `fal_returned_at`,p50/p95/p99 wall time
- 暴露给 watchdog cron / Sentry / 内部 dashboard
- p95 > 5 分钟 → 微信告警(Server 酱已就位)
- 不是公开前阻塞项,但首月内必上

# How to apply

- 任何 V2 相关功能改动,如果影响 wall time(比如改并发数、改端点),必须提到这个 memory 评估影响
- 公开前 checklist 必修项之一(其它必修:假蓝绿 / fal cost 对账)
- 不是今天做,但**产品公开前必修**
- 数据基线:本 memory 的 wall time 表是 2026-05-10 单点采样,正式监控上线后用 p95 替代
