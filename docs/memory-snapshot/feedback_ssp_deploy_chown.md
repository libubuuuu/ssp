---
name: SSP rsync 后必须 chown 给 ssp-app
description: 部署时 rsync /root/ssp → /opt/ssp 用 root 跑会让文件 owner 变 root,backend 跑 ssp-app 起不来
type: feedback
originSessionId: ea8e179f-9ae8-4332-8e34-2c4e10a28ff0
---
**rsync /root/ssp → /opt/ssp 后必跟 `chown -R ssp-app:ssp-app /opt/ssp/backend/app/`,否则 supervisor 启 backend 时 spawn error。**

**Why:** 2026-04-28 部署 refund_tracker 时踩坑:rsync -av 默认保留 owner(从 source = root),target /opt/ssp/backend/app 文件全变 root:root。backend 跑 ssp-app(UID 998),RotatingFileHandler 打 `app/logs/ai_platform.log` 拿不到写权限 → ImportError → spawn error。第一次 deploy 失败,chown 后重跑通过。

**How to apply:**
- 完整 deploy SOP:`rsync ...` → **`chown -R ssp-app:ssp-app /opt/ssp/backend/app /opt/ssp/backend/logs`** → `bash /root/deploy.sh`
- 同样适用 frontend(若改 frontend):`chown -R ssp-app:ssp-app /opt/ssp/frontend`
- 终极修:rsync 加 `--chown=ssp-app:ssp-app`(rsync 3.1+),或者改 deploy.sh 内嵌 chown
- 蓝绿一台失败时:active 还在跑无影响,只是 standby 不健康。fix ownership 后 supervisorctl start <stopped> 验证健康再走 deploy 流量切换
