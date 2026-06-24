---
name: feedback-ssp-cron-point-to-root-deploy
description: 监控/运维 cron 必须指向 /root/ssp/deploy（git 源码），/opt/ssp/deploy 是无人同步的孤儿拷贝
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 002dd539-34cd-4761-9fe4-a4e63bbc515f
---

deploy.sh 只 rsync `backend/app`、`frontend/src`、`frontend/public` 到 slot，**`deploy/` 目录从不同步**。`/opt/ssp/deploy/` 是 4 月底的孤儿拷贝：synthetic-user-test.sh 缺 `-sS`（2026-06-12 告警详情为空的真因）、watchdog.sh 缺磁盘水位检查（瞎了一个半月，恢复后立刻逮到磁盘 82%）。

**Why:** 源码修了 bug 但 cron 跑的是旧拷贝 = 修复永远不生效，且无人察觉。

**How to apply:** 2026-06-12 已把 synthetic-user-test.sh 和 watchdog.sh 的 cron 改指 `/root/ssp/deploy/`（与 autorepair.sh、backup_cos.sh 同模式）。新增监控/运维 cron 一律指 `/root/ssp/deploy/`。仍指 /opt 的遗留条目（backup.sh、studio-uploading-gc.sh、/opt/ssp/scripts/*.py）当前内容一致但有同样腐化风险，动它们前先 diff。watchdog WARN 推送无冷却机制——任何持续性 WARN（如磁盘水位）会每 5 分钟推一条，处理要快。相关：[[feedback-ssp-deploy-via-script]]
