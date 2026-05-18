---
name: SSP 项目结构与栈
description: SSP 项目的技术栈、关键路径与部署/回滚命令(蓝绿部署 + supervisor + nginx)
type: project
originSessionId: c9d691af-d261-494c-b8cb-27c9d6288ade
---
SSP 项目位于 `/root/ssp`,蓝绿部署。

**技术栈:**
- 后端:FastAPI + Python 3.11 + SQLite
- 前端:Next.js 14 + TypeScript + Tailwind
- 部署:supervisor + nginx + Blue-Green

**关键路径:**
- 后端代码:`/root/ssp/backend/app/`
- 前端代码:`/root/ssp/frontend/src/`
- 数据库:`/root/ssp/backend/dev.db`
- 加密 env:`/root/ssp/backend/.env.enc`

**常用命令:**
- 部署:`bash /root/deploy.sh`
- 回滚:`bash /root/rollback.sh`
- 重启后端:`supervisorctl restart ssp-backend-blue`

**Why:** 用户在 2026-04-25 显式提供了这份说明作为项目基线,后续工作以此为准。
**How to apply:** 修改代码先定位到对应路径;部署/回滚走脚本,不要手动操作 supervisor 之外的进程;改后端配置注意 `.env.enc` 是加密的。
