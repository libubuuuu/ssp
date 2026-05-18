---
name: SSP 假蓝绿架构 bug(待修)
description: blue/green 共用 /opt/ssp 同份代码,只是双进程冗余,不是双版本可切换 — 真蓝绿是下一阶段必修
type: project
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---
# 现状(2026-05-10 实测)

`ssp-backend-blue`(8000)/ `ssp-backend-green`(8001)和 `ssp-frontend-blue`(3000)/ `ssp-frontend-green`(3002)四个 supervisor program,用同一份 `/opt/ssp/backend` 和 `/opt/ssp/frontend` 代码。

deploy 流程:rsync /root → /opt → restart green。**blue 启动时跑的是新代码,不是旧版本快照。**

# Why this is broken

- 标榜"蓝绿"但**没法切回老版本**:出问题想切 blue,blue 一启动也是新代码
- 部署期间 nginx upstream 切换没意义(双方代码一样)
- 唯一价值剩下"双进程冗余"(green 重启时 blue 顶) — 但 blue 长期 STOPPED,这价值也没兑现
- p221-a2-deploy.sh 配套的 rollback 脚本依赖手动 archive,本次手动 rsync 没生成 archive → rollback 脚本用不了

# 正解(下一阶段任务)

两条路径,任选一:

**路径 A:双独立目录**
- `/opt/ssp/backend-blue` + `/opt/ssp/backend-green`,各跑各的代码
- deploy:rsync 到非 active 那边 → start → wait healthy → nginx upstream 切 → stop 老 active
- 优点:真蓝绿,可切回老版本。缺点:磁盘 ×2

**路径 B:archive 自动备份**
- deploy 脚本强制 `cp -a /opt/ssp /root/.ssp-archive-{timestamp}` 再 rsync 新代码
- rollback 走 `rsync -a /root/.ssp-archive-{ts}/ /opt/ssp/` + restart
- 优点:不改架构,只加 archive 钩子。缺点:回滚比路径 A 慢(rsync 整个目录)

# How to apply

- 任何人(包括我)用"蓝绿"这词时,提醒一下当前是假蓝绿,慎当真蓝绿用
- 产品公开前必修。优先级仅次于功能上线
- 实现路径推荐 B(改动最小,deploy 脚本加 archive 钩子即可)
