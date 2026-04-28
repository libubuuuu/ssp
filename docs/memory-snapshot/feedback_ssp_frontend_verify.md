---
name: SSP 前端改动必须独立复核(grep + build)
description: 改 i18n / TS 文件后 Edit 工具返"成功"不算证明,必须 grep 看真值 + npm run build 通过才能 commit/deploy
type: feedback
originSessionId: 5deffd10-8627-41d0-81b9-23bb319a09a5
---
改前端文件(尤其 i18n / TypeScript / page.tsx)后,**不能只信 Edit 工具的返回**,必须独立复核两步:

1. `grep -A1 "<新增的 key>" frontend/src/lib/i18n/{zh,en}.ts` — 确认两份 i18n 都真写进去了(后端 pytest 不覆盖前端 i18n,改漏不会被自动发现)
2. `cd frontend && npm run build` 真跑一次 — TS 类型错 / 漏逗号 / i18n 引用不存在,prod build 才会挂

**Why**:用户 2026-04-28 部署 Aurora / Omnihuman 时挑出来的 SOP — Edit 工具偶发返 "Error writing file" 但有时表象成功。如果只看 Edit 返回不复核,prod 蓝绿切换时 build 失败 = 在最难收拾的时刻挂。后端 pytest 不覆盖前端 i18n / TS,这两类错误必须靠 build 兜底。

**How to apply**:
- 改 i18n 至少 2 个文件(zh.ts + en.ts)→ 改完 grep 一次,确认两份都有
- 改 page.tsx / TS 文件 → npm run build 必须过(不要只看 lint,build 才会暴露真错)
- 这两步**都过了**才能进 commit + push + deploy 流程
- 时间紧也不省这两步 — deploy 失败回滚比预防贵 10 倍
