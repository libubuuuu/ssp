---
name: SSP deploy 前必须查 running session
description: 蓝绿切换会杀进程导致 task 协程死,deploy 前必须查 oral_sessions running 状态
type: feedback
originSessionId: f98e6643-4495-4bf6-96fa-44085151a76c
---
deploy.sh 蓝绿切换时,supervisor `stopasgroup/killasgroup` 强杀旧进程 → 内存里的 asyncio task 协程被杀 → 数据库状态卡死(`tts_running` / `inpainting_running` 等),fal 已 submit 的任务在云端继续跑(钱已花)但本地永远收不到结果。

**Why**: 2026-05-03 P58 部署事故 — session 9655e87c-839 用户在 21:55 点了生成跑到 Step B,22:00 我第二次 deploy 杀 green,task 协程死 + db 状态卡 → 退 53 credit(430→483)给用户。

**How to apply**: deploy 之前必须先跑:
```sql
SELECT id, status, step_b_engine FROM oral_sessions
WHERE status NOT IN ('completed','failed','failed_step1','failed_step4','cancelled','refunded','uploaded')
ORDER BY updated_at DESC;
```
如果有 running 的 session:
- 短时间能完(< 5 min):等完再切
- 长时间(Step B fal 调用 5-30 min):告诉用户当前有 X 个 session 在跑,deploy 会杀掉 → 让用户决定等还是杀
- 紧急 hot fix 必须立刻切:cancel + 退 credit 给受影响用户

适用范围:任何 oral / studio / digital_human 这种长任务异步 pipeline 的部署。
