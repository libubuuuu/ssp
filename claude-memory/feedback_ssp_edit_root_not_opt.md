---
name: SSP Edit 必须改 /root/ssp 源码不是 /opt
description: 用 Edit 工具改后端代码必须用 /root/ssp 路径,改 /opt 的 deploy 副本 git 不追踪导致漏 commit 灾难
type: feedback
originSessionId: f98e6643-4495-4bf6-96fa-44085151a76c
---
用 Edit / Write 修改 SSP 后端代码时,文件路径**必须是 /root/ssp/...**,不能是 /opt/ssp/...。

**Why**: 2026-05-04 P70 漏 commit 事故 — 我先 Read /opt/ssp/backend/app/services/fal_service.py(因为是 deploy 后路径),Edit 后写到 /opt 副本。git working tree 在 /root/ssp,git diff 看不到 /opt 改动,commit 没带 fal_service.py。然后 deploy.sh 用 /root → /opt rsync,**反向覆盖了 /opt 上我的改动**,新加的 service 类被抹掉。用户跑新引擎立刻 ImportError 失败。

**How to apply**:
- Read 时:可以读 /opt/ssp/...(看 deploy 后真实运行版本)
- Edit / Write 时:**必须**改 /root/ssp/...
- deploy 之前:`md5sum /root/ssp/<file> /opt/ssp/<file>` 验两边一致
- 如果不一致 + 我有意改了 /opt → 立刻 sync 回 /root 再 commit

适用范围:任何后端 / 前端源码文件。/opt 是 deploy 副本,/root 是源码 + git working tree。
