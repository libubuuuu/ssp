---
name: blue 重启会杀掉 in-flight job(没有持久化恢复)
description: 部署 ssp-backend-blue 时 in-memory worker map 不持久化,任何 status=running 的 job 都会变孤儿(JSON 显示 running 但实际无人跑)
type: feedback
originSessionId: bf5e5bd4-8e8a-4d86-89e7-4176ad18cde7
---
部署窗口期间提交的 replicate/ad_video job,如果 worker 重启时正在运行,**job 会变孤儿**:
- `jobs.json` 里 `status=running` 永远卡着
- worker 内存里没这个 task,前端轮询永远拿不到结果

**实证(2026-05-07):** job 3a860737 (kling-3-pro-i2v) 在 05:35:42 提交,我恰好同时在 rsync + supervisorctl restart blue。20 分钟后日志里 0 worker 处理痕迹,只有前端 GET 轮询。手动改 jobs.json status=failed 才解。

**Why:** worker task 用 asyncio.create_task 注册到内存 dict,blue 重启时丢失。jobs.json 是结果存档,不是 task queue。

**How to apply:**
- 部署前先 `cat /opt/ssp/jobs_data/jobs.json | jq '[.[] | select(.status=="running")] | length'`,确认 0 个 running 再重启
- 如果有用户正在跑,等它完(或告知用户)
- 用户报"卡住了",优先怀疑这个,grep `created_at` 时间 vs blue uptime

**已修复(2026-05-07,commit cac6036 / P163):** main.py lifespan startup 调用 `cleanup_orphan_jobs_on_startup()`,自动把 status=running 的标 failed + 退积分 + 写 ledger。下次撞上不再人工干预,但部署前查 running 仍是好习惯(失败的 UX 提示用户重提)。
