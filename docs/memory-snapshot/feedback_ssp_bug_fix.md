---
name: SSP 生产 bug 立即修复约定
description: 网站不可用 / 有 bug 时立刻在代码里修,不要只诊断或给指引,用户期望见到立即修复行动
type: feedback
originSessionId: 547cfbae-7bc2-40b3-908f-77fc0c885ec5
---
**网站不可用 / 用户报 bug 时:立刻在代码里修,不要只是诊断、解释、或让用户清缓存。**

**Why:** 2026-04-26 晚生产触发 nginx 429(多 tab + JobPanel polling 叠加),用户报 "Unexpected token '<'" 错误。我的第一反应是诊断 + 让用户关 tab 清缓存,用户回:"网站有 bug 需要你在这里面做修复"。意思明确:**修代码是默认动作,不是可选项。**

**How to apply:**
- 用户报"网站坏了 / 不能用 / 有 bug" → 立刻 read 相关代码 → 找 root cause → 修 → deploy。诊断必要但不是终点。
- 即使 root cause 看起来是用户端(残留 token / 缓存 / 多 tab),也要从工程侧加防御层:visibilitychange / 防抖 / 401 优雅降级 / 重试限制。
- 让用户清缓存只能作为**临时缓解**给在 → 同时立刻动手做工程修复。
- 修完才报告"已修复 + 已 deploy",不要只报告"找到 root cause 了"就停。
