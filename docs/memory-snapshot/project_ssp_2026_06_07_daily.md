---
name: project-ssp-2026-06-07-daily
description: 2026-06-07 今日修复汇总：deploy drain V2漏计/stopwaitsecs/退款字段，最新 commit 791eb59
metadata: 
  node_type: memory
  type: project
  originSessionId: 972c4844-f1d7-4f98-9683-4ae0221e9d96
---

今日主要修复（全部已推 origin，最新 commit 791eb59）：

1. **deploy drain V2漏计 bug（核心）** commit 92b536e / 84f5e1a
   - 旧 deploy.sh drain 只查旧槽 API，旧代码不统计 V2 任务 → 误判0 → 立刻停进程 → SIGKILL V2协程
   - 修复：deploy.sh `_get_active()` 新增 SQLite 直查兜底，取 API 和 SQLite 最大值
   - Why: 今天下午连续部署20次期间，用户任务两次被杀死

2. **supervisor stopwaitsecs 15→660**（/etc/supervisor/conf.d/ssp.conf）
   - 原15秒太短，V2任务需2-5分钟，即使drain正确等到0仍有被SIGKILL风险
   - 修复：改为660秒（600s app timeout + 60s缓冲）
   - supervisorctl reread + update 已生效，green backend pid 633070 运行中

3. **V2退款路径漏写 total_credits_refunded** commit 791eb59
   - cleanup_stale_v2_jobs 和 v2_watchdog_loop 退积分后未更新 jobs 表字段
   - 修复：两处 add_credits 后补 UPDATE total_credits_refunded
   - 历史脏数据（a8dce7c3/ba2bc02d）已直接在DB修正

今日失败的2个V2任务：
- a8dce7c3（user a358096b，550积分）：服务重启清理退款 ✅
- ba2bc02d（user 64402546，825积分）：手动退款 ✅

**Why:** 今天集中调试 deploy.sh，连续约20次部署，期间用户恰好提交 → 踩中漏计bug。

**How to apply:** 下次修 deploy.sh 后确认 `_get_active()` 仍包含 SQLite 兜底；stopwaitsecs 不能改回15。
