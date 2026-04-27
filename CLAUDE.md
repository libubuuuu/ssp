# SSP / AI Lixiao 项目档案

> 这是一份给未来 Claude 会话用的"上车指南"。不替代代码,只补充代码看不出来的 **意图、决策、限制、路线**。
>
> **最后大更:2026-04-27** — 历经多轮(降权 / Phase 1 完成 / P0-P9 + BUG-1/2 + 隐藏雷 1-3 + P8 阶段 1+2)后重写。
> 详细历史在 `PROGRESS.md`(1500+ 行,按"X 续"日记格式归档)。

## 一句话定位

`ailixiao.com` 是一个面向中文用户的 AI 创意平台 SaaS:图片/视频/数字人/虚拟形象/语音克隆/长视频工作台 + 用户系统 + 额度计费 + 商家产品 + 独立管理后台 (`admin.ailixiao.com`)。

**目标定级:企业级**——对标的是真正运营 To B / 接付费用户的产品,不是个人 demo。

## 技术栈快照(2026-04-27 现实)

| 层 | 选型 | 备注 |
|---|---|---|
| 前端 | Next.js 16.2.4 + React 19.2 + Tailwind 4 + Zustand + 自研 i18n | 0 high CVE,5 moderate 卡 upstream |
| 后端 | FastAPI 0.122 + sqlite3(WAL)+ 可选 Redis | Postgres 待迁(Phase 2) |
| 反代 | nginx + Let's Encrypt(Certbot 管的) | 三个 vhost:`ailixiao.com` / `admin.ailixiao.com` / `monitor.ailixiao.com` |
| 服务管控 | **supervisor**:`ssp-{backend,frontend}-{blue,green}` 蓝绿四个 program | systemd unit 已 disable,**别再 systemctl restart 业务** |
| 用户 | **`ssp-app`**(UID 998,降权完成 2026-04-27) | 业务进程不再以 root 跑 |
| 备份 | `/root/backup_daily.sh` 每天 03:00,本机 7 天 | **未异地备份**(待用户开 COS) |
| 监控 | `monitor.ailixiao.com:3001` 反代 + watchdog cron 5 分钟巡检 + 微信(Server 酱)推送 | Sentry 框架就绪等 DSN |
| AI 服务 | FAL AI(图片+视频),Resend(邮件) | `services/fal_service.py` |
| 短信 | 阿里云 SMS | `services/alert.py` 用于告警 |

## 关键路径(服务器上,2026-04-27 现实)

```
/opt/ssp/                       生产 working tree(ssp-app 拥有)
  backend/
    app/main.py                 FastAPI 入口(含 Sentry 初始化)
    app/config.py               Settings(读 backend/.env)— 加 SENTRY_DSN / COOKIE_DOMAIN / REDIS_URL 可选
    app/database.py             SQLite + WAL(P1)+ register_ip_log/failure 表
    app/api/                    auth/admin/image/video/avatar/digital_human/jobs/payment/...
    app/services/               auth/billing/rate_limiter(可选 Redis 后端)/
                                 content_filter(P2 黑名单)/media_archiver(BUG-2 fal→本地)/
                                 sentry_filter(隐藏雷 #3 4xx 过滤)/uploads_gc(隐藏雷 #1 GC)/
                                 task_ownership/audit/circuit_breaker
    .env.enc                    加密 env,**纯文本 .env 永远不入库**
    dev.db                      SQLite WAL,生产真库
    venv/                       py 虚拟环境(ssp-app 拥有)
  frontend/
    src/app/                    Next App Router 页面(35+)
    src/components/AuthFetchInterceptor.tsx   全局 fetch 401 拦截 + cookie credentials:include
    src/lib/i18n/{zh,en}.ts     自研 i18n
  uploads/                      用户生成媒体本地归档(BUG-2 阶段 A)
  deploy/                       部署/运维脚本(进 git)
  jobs_data/                    任务队列文件型存储(.gitignore 已忽略,fcntl 锁)
  studio_workspace/             长视频 session(.gitignore 已忽略)

/root/ssp/                      Git working tree(root 拥有,git ops 在这里)
  → deploy.sh rsync 到 /opt/ssp;详见 RUNBOOK.md

/etc/nginx/sites-enabled/default                三个 vhost + /uploads/ alias
/etc/supervisor/conf.d/ssp.conf                 4 个 program(stopasgroup/killasgroup)
/etc/ssp/master.key                             主密钥(640 root:ssp-app)
/root/backup_daily.sh                           本机 7 天备份(cron 03:00)
/root/{deploy,rollback}.sh                      symlink → /root/ssp/deploy/
```

## 已知差距(对标企业级,2026-04-27 现实)

### 🔴 一级(剩 4 项,从原来 6 项)

1. **数据库是 SQLite,无迁移管理** — `app/database.py` 手撸 CREATE TABLE,无 Alembic。WAL 已开但 schema 漂移仍靠手维护。需迁 PostgreSQL(Phase 2)。
2. ~~**后端/前端服务以 root 运行**~~ ✅ **2026-04-27 完成**(切到 ssp-app + /opt/ssp + /etc/ssp/master.key)
3. ~~**零自动化测试**~~ ✅ **205 测试覆盖**(auth/billing/jobs/admin/audit/2FA/邮箱码/refresh/吊销/WS/IP 限流/失败配额/媒体归档/uploads GC/Sentry filter/Cookie 双轨/RateLimiter 双后端)
4. ~~**无 CI/CD**~~ ✅ **GitHub Actions** 后端 pytest + frontend lint + pip-audit 强制阻塞 + npm audit high 阻塞
5. **任务队列是文件 JSON** — `jobs_data/jobs.json`,fcntl 锁防并发损坏(P1)但仍单点。需迁 Celery+Redis(Phase 2)。
6. **备份等于没有(异地)** — 同机本地 + 7 天保留,机器挂 = 备份和数据库一起完蛋。等用户开 COS。

### 🟠 二级

7. **单 worker uvicorn,无水平扩展** — Redis 后端已就绪可启用,扩多 worker 再开。
8. **文件存储 — BUG-2 阶段 A**:本地 /opt/ssp/uploads(归档防 fal.media 30 天过期)+ uploads_gc cron 90 天清理。**阶段 B 待迁 OSS**(Phase 2)。
9. ~~**可观测性接近零**~~ 部分:trace_id middleware ✅ + Sentry 框架就绪等 DSN ✅ + watchdog cron + Server 酱微信推 ✅。仍缺 Prometheus / 业务指标。
10. ~~**无审计日志**~~ ✅ `audit_log` 表 + 7 个动作覆盖(adjust_credits / confirm_order / change_password / reset_password / logout_all_devices / reset_model / force_logout)
11. ~~**JWT 体系不完整**~~ ✅ access(1h)+ refresh(30d)+ 用户级吊销(`tokens_invalid_before`)+ /api/auth/logout(P8)+ /logout-all-devices
12. **支付是手动入账** — 截图 + 管理员确认。等用户开微信支付商户号 + ICP 备案。

### 🟡 三级

13. ~~**RateLimit 内存版**~~ ✅ Redis 后端可选(REDIS_URL 配置开关),fail-open 故障降级
14. ~~**运营文档缺**~~ ✅ RUNBOOK 重写 / SENTRY-SETUP / CLOUDFLARE-SETUP / REDIS-SETUP / P8-COOKIE-MIGRATION
15. **合规未做** — ICP 备案、隐私政策、用户协议、内容审核(P2 是简版,Phase 4 必须接阿里云内容安全 / 腾讯云 CMS)、AIGC 水印——**用户主导**
16. **依赖版本激进** — Next 16.2.4 / React 19.2 / Prisma 7 都是刚 GA;npm audit 5 moderate(全卡 upstream)

### 🟣 P8 httpOnly Cookie 进度(2026-04-27)

- ✅ 阶段 1:后端双轨(set/clear cookie + get_current_user 优先 cookie)
- ✅ 阶段 2:前端 `AuthFetchInterceptor` 加 `credentials:include`(中心 patch 71 处自动获益)
- ⏸ 阶段 3:30 天后清理 header 路径(localStorage 写仍开,过渡期)— 详见 `docs/P8-COOKIE-MIGRATION.md`

## 路线图(2026-04-27 重整)

### Phase 1 — 工程根基 ✅ ~85%

完成项:pytest + CI + 服务降权 + 蓝绿部署 + audit log + JWT refresh/吊销 + IP 限流(双层)+ 内容审核简版 + 注册要求邮箱码 + 媒体归档 + uploads GC + Sentry/Redis/CF 框架

剩余:
- [ ] 前端 e2e(Playwright)0→1
- [ ] 备份异地化(等用户 COS)
- [ ] 测试覆盖 jobs.py / payment.py 到 70%(当前 48% / 50%)
- [ ] 修 58 个 lint errors(setState in effect 等)

### Phase 2 — 数据/状态正规化(没启动)

- [ ] PostgreSQL 迁移 + SQLAlchemy + Alembic
- [ ] 任务队列改 Celery + Redis(jobs.json 退役)
- [ ] 媒体存储迁 OSS(BUG-2 阶段 B)
- [ ] 邮箱码 / 全局 RateLimit 启 Redis 后端(代码已就绪)

### Phase 3 — 可观测 ✅ ~70%

完成:audit log / refresh+吊销 / Sentry filter 框架 / watchdog 微信推 / trace_id

剩余:
- [ ] Sentry 真启用(等用户贴 DSN)
- [ ] Prometheus + Grafana 业务指标

### Phase 4 — 合规商业(用户主导)

- [ ] ICP / 公安备案
- [ ] 隐私政策 + 用户协议 + Cookie 同意页
- [ ] 内容审核云服务(阿里云 / 腾讯 CMS)替换 P2 简版
- [ ] AIGC 水印(满足深度合成规定)
- [ ] 真实支付:微信支付正式接入 + 对账

### Phase 5 — 高可用 / 扩展(没启动)

- [ ] 多 worker uvicorn + 多实例 Next(Redis 后端已就绪)
- [ ] CF CDN(snippet 已写,等用户 DNS 切)
- [ ] 数据库主从 + WAL 归档 / PITR
- [ ] 必要时 K8s 化

## 协作约定(给未来的 Claude)

- **语言:中文回复**。用户偏好直接、严格、不 sugar-coat。
- **commit 风格**:`<type>: <中文描述>`。type ∈ {feat, fix, chore, i18n, security, complete, refactor, docs, ops}。重要 commit 在 message 里写"为什么",不只是"做了什么"。
- **破坏性操作前先对齐**:动 DB / systemd / nginx / 任何 push 远端 / 任何会让用户掉线的操作,先告诉用户范围、风险、rollback,得到确认再做。
- **不要替代用户决策**:给两条路 + 你的推荐 + 取舍,让用户拍板。
- **不擅自装系统服务**:Redis / Sentry / CF 等,代码就绪 + 文档化让用户自己装。
- **memory**:`/root/.claude/projects/-root/memory/` 已存项目目标、偏好、运营路径,新会话务必读。
- **deploy 流程**:不要 git pull(那是老脚本)。改完代码 commit + push,然后 rsync `/root/ssp/{backend,frontend}` → `/opt/ssp/...` + `bash /root/deploy.sh` 蓝绿切换 30 秒。详见 RUNBOOK。

## 当前状态(2026-04-27)

**生产环境**:ailixiao.com,腾讯云轻量服务器,supervisor 蓝绿部署。
- 业务进程跑 `ssp-app` 用户(UID 998),不再 root
- 工作目录 `/opt/ssp/`(/root/ssp 仅 git 工作树)
- 数据库 SQLite WAL,新表 register_ip_log + register_ip_failure_log + audit_log 全在 prod
- nginx /uploads/ alias 已就位(BUG-2 媒体本地服务)
- supervisor stopasgroup/killasgroup 已生效

**Git 状态**:见 PROGRESS.md 末尾的"4-ref 对齐"。每轮重要 commit 后必同步 main + feat。

**安全状态**(2026-04-27):
- RESEND_API_KEY / FAL_KEY / JWT_SECRET 已轮换(2026-04-25 夜)
- access 1h + refresh 30d + 用户级吊销
- 注册要邮箱码 + IP 双层限流(成功 3/24h + 失败 10/24h)
- 内容审核简版(政治 / 色情 / 暴力 ~200 词)接 4 个 FAL 端点
- 媒体归档防 fal.media 30 天过期
- httpOnly Cookie 双轨(P8 阶段 1+2,前端 credentials:include 自动)

**记忆体系**:
- `/root/.claude/projects/-root/memory/MEMORY.md` 索引
- `CLAUDE.md`(本文件)— 长期项目档案
- `PROGRESS.md` — 进度日记(1500+ 行,按"X 续"日记格式)
- `RUNBOOK.md` — 故障应急手册
- `docs/`:`SENTRY-SETUP.md` `CLOUDFLARE-SETUP.md` `REDIS-SETUP.md` `P8-COOKIE-MIGRATION.md` `DISASTER-RECOVERY.md` `COVERAGE-2026-04-27.md`

**部署体系**:
- `/root/ssp/deploy/` — 系统配置 + 脚本(已入 git)
- `/root/{deploy,rollback}.sh` — symlink → deploy/ 下真实文件
- supervisor 4 个 program:`ssp-{backend,frontend}-{blue,green}`
- nginx `/uploads/ alias` + `cloudflare-real-ip.conf` snippet(等 CF DNS 切再装)

## 怎么跑测试

```bash
cd /opt/ssp/backend                     # 注意:生产路径 /opt,不是 /root
venv/bin/pytest -v                      # 全跑(205 例)
venv/bin/pytest tests/test_auth.py -v   # 单个文件
venv/bin/pytest -k cookie -v            # 按关键字
venv/bin/pytest --cov=app               # 带覆盖率(整体 46%)
```

测试用 `/tmp/ssp_test_*.db` 临时库 + `COOKIE_SECURE=false` env override,**不会碰 `dev.db`**。

## 用户操作待办(等用户决定)

| 项 | 文档 | 工作量 |
|---|---|---|
| Sentry 启用 | docs/SENTRY-SETUP.md | 5 分钟(注册 + DSN) |
| Cloudflare CDN | docs/CLOUDFLARE-SETUP.md | 15 分钟(DNS 切 + 24h 等) |
| Redis 启用 | docs/REDIS-SETUP.md | 15 分钟(apt + config) |
| 备份异地化 | RUNBOOK.md | 30 分钟(开 COS + rclone 配) |
| 微信支付 | — | 等商户号 + ICP 备案 |
| ICP 备案 | — | 用户主导,1-2 周走流程 |
| 内容审核云服务 | — | 等阿里云 / 腾讯 CMS 账号 |
