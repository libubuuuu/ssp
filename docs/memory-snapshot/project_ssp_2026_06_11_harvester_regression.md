---
name: project-ssp-2026-06-11-harvester-regression
description: 图片变慢 3-5 分钟事件：真因是 aiview 上游慢，叠加蓝绿重叠期 harvester/孤儿清理三个放大器，修复 commit 见本文
metadata: 
  node_type: memory
  type: project
  originSessionId: 6fc01be5-6d5a-48b4-8fe6-dff14162762d
---

2026-06-11 用户报"拿图 3 分半，第三方只要 60-80s"。排查结论：

**"先显示上游 URL + 异步归档"约定没有被改掉**（commit 6895ef6 还把视频统一成同模式，[IMG-ARCHIVE] attempt=1 正常工作）。

**真因**：aiview 上游当天就是慢——aiview 侧 created_at → 完成实测 188-323s（gpt-image-2 双参考图）。用户看到的 60-80s 是 aiview 的"纯处理时长"，不含 aiview 侧排队。检测无延迟的证据：blue/green 两个独立 harvester 同一秒检测到完成。

**我们叠加的三个放大器**（当天修复）：
1. aiview 偶发 "Invalid image" 假失败（同图重试就过）→ 整单自动重试 → 时间翻倍（350s = 73s 失败 + 280s 重试）
2. `_execute_job` 的 semaphore（MAX_CONCURRENT=5，全任务类型共享）在等第三方期间不释放 → 图片变慢后排队雪崩（实测 T1=455s 其中排队~280s）。注意 c6513cc commit message 说"提交后释放槽位"但代码从未实现，4b22415 改注释承认现状——**commit message 与代码不符的先例**
3. 蓝绿重叠期三连环：新槽启动无条件把老槽 drain 中的活任务标孤儿+标 failed+退积分（钱双花+误报"服务异常重启"）；两个 harvester 互抢 polling_queue 行（先到的标 completed，对端等待协程挂到 900s 超时）；jobs.json 两进程各持全量内存副本互相覆盖写（last-writer-wins，未修，Phase 2 迁 DB 才能根治）

**修复**（jobs.py / main.py / aiview_service.py）：
- aiview query/query_video：10011 认证缓存繁忙 → transient processing 而非 failed（submit 路径早有重试，query 路径漏了）
- harvester 只轮询本进程 `_poll_events` 里有等待方的行 + 每 ~5 分钟 GC 超 2h 死行
- cleanup_orphan_polling_queue 加 60s staleness 守卫（活 harvester 每 3s 摸一次 last_polled_at）
- 孤儿 job 清理：对端槽 /health 活着（[[project-ssp-fake-bluegreen]] 真蓝绿 8000/8001 互探）→ 延迟到对端退出后，重读 jobs.json 以对端最终状态为准，本进程 `_session_jids` 创建的任务豁免

**未修待决**：semaphore 等待期不释放（当天有意决策 53d9828/4b22415，但当时不知 aiview 会 300s）；jobs.json 双写覆盖；jobs.json 已 5MB/3117 条每次全量 dumps+fsync。
