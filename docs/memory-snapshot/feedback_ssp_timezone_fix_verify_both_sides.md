---
name: feedback-ssp-timezone-fix-verify-both-sides
description: "修时区 bug 前必须先实证比较两边的真实时区——43111a6 凭假设把 V2 watchdog 修反，活任务 3 分钟被判\"超时30分钟\"误退积分"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 734a2e74-6025-4262-ae76-40c807d9e22a
---

2026-06-11 事件：43111a6 修 V2 watchdog "30分钟阈值永不触发"，commit message 声称
created_at 是 CST、now 是 UTC，于是把截止线改成 `time.localtime`。
实际 `video_clone_v2_jobs` 的时间列是 DB `CURRENT_TIMESTAMP`（**UTC**），原 julianday 比较本来语义正确。
"修复"后截止线快了 8 小时 → 任何 processing 任务在 watchdog 下一轮（≤5 分钟）必中超时：
job feb1e765 昨 22:03 创建 → 22:06 被误杀+退 825 积分 → 22:14 自己跑完 → completed+refunded 双花。

**Why:** 时区 bug 的修法只有一个正确起点：先 SELECT 一行真实数据，对照本地挂钟时间，实证每一列的时区，再改代码。凭 commit message 或猜测定时区方向，50% 概率修反，而修反的后果（全量误杀/永不触发）比不修更糟。

**How to apply:**
- 改任何涉及 `created_at/updated_at` 与 now 比较的代码前：`sqlite3 ... "SELECT created_at FROM x ORDER BY rowid DESC LIMIT 1"` + `date`，亲眼对差值。
- 本项目铁律：**SQLite `CURRENT_TIMESTAMP`/`datetime('now')` 全是 UTC；日志时间戳是本地 CST(+8)**。DB 时间列与 Python 比较一律用 `time.gmtime`。
- 卡死判定用 `COALESCE(updated_at, created_at)`（最后进展）而非 created_at，活的长任务不会被杀（1e88b50 已落地）。
- 又一例 commit message 与代码事实不符（前例见 [[project-ssp-2026-06-11-harvester-regression]] 的 c6513cc semaphore）：验证 bug 是否已修，只认代码+数据，不认 commit message。
