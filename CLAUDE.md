# SSP / AI Lixiao 项目档案

> 这是一份给未来 Claude 会话用的"上车指南"。不替代代码,只补充代码看不出来的 **意图、决策、限制、路线**。

## 一句话定位

`ailixiao.com` 是一个面向中文用户的 AI 创意平台 SaaS:图片/视频/数字人/虚拟形象/语音克隆/长视频工作台 + 用户系统 + 额度计费 + 商家产品 + 独立管理后台 (`admin.ailixiao.com`)。

**目标定级:企业级**——对标的是真正运营 To B / 接付费用户的产品,不是个人 demo。当前现状离这个目标还有距离,见下方"已知差距"。

## 技术栈快照

| 层 | 选型 | 备注 |
|---|---|---|
| 前端 | Next.js 16.2 + React 19.2 + Tailwind 4 + Zustand + 自研 i18n | 省心但版本偏新,踩坑风险需关注 |
| 后端 | FastAPI 0.109 + 直接 sqlite3(no SQLAlchemy)+ Redis(部分使用) | 详见下文"数据库"差距 |
| 反代 | nginx + Let's Encrypt(Certbot 管的) | 三个 vhost:`ailixiao.com` / `admin.ailixiao.com` / `monitor.ailixiao.com` |
| 服务管控 | systemd:`ssp-backend.service` / `ssp-frontend.service` | **目前以 root 运行,待降权(Phase 1)** |
| 备份 | `/root/backup_daily.sh` 每天 03:00,本机 7 天 | **未异地备份(Phase 1)** |
| 监控 | `monitor.ailixiao.com:3001` 反代(应是 Uptime Kuma 或类似) | 没有 APM/Sentry/Prometheus |
| AI 服务 | FAL AI(图片+视频),Resend(邮件) | `services/fal_service.py` |
| 邮件 | Resend(`/api/auth/send-code` 等) | 验证码用进程内 dict,多 worker 失效 |
| 短信 | 阿里云 SMS(`alibabacloud-dysmsapi20170525`) | `services/alert.py` 用于告警 |

## 关键路径(服务器上)

```
/root/ssp/                      仓库根
  backend/
    app/main.py                 FastAPI 入口
    app/config.py               Settings(读 backend/.env)
    app/database.py             SQLite schema(手撸 CREATE TABLE)
    app/api/                    auth/admin/image/video/avatar/digital_human/jobs/payment/...
    app/services/               auth/billing/rate_limiter/circuit_breaker/alert/task_queue/health_check/logger
    .env.enc                    加密 env,**纯文本 .env 永远不入库**
    dev.db                      SQLite,单点
    venv/                       py 虚拟环境
  frontend/
    src/app/                    Next App Router 页面
    src/components/             共享组件
    src/lib/i18n/{zh,en}.ts     自研 i18n 字典(已覆盖 80%+)
  jobs_data/                    任务队列文件型存储(.gitignore 已忽略)
  studio_workspace/             长视频 session(.gitignore 已忽略)
  CLAUDE.md                     本文件
  README.md / SPECIFICATION.md  早期文档

/etc/nginx/sites-enabled/default        三个 vhost 都在这里
/etc/systemd/system/ssp-backend.service  以 root 跑,待降权
/etc/systemd/system/ssp-frontend.service 以 root 跑,待降权
/root/backup_daily.sh                    本机 7 天备份脚本(cron 03:00)
/root/backups/                           备份目录
/root/bluegreen-backup/                  上一次蓝绿备份残留(可清理)
```

## 已知差距(对标企业级)

按严重度排序的清单——这是**当前到"企业级"还差的工程项**,不是修补建议。Phase 落地路线见下一节。

### 🔴 一级(不修不能叫企业级)

1. **数据库是 SQLite,无迁移管理** — `app/database.py` 手撸 CREATE TABLE,无 Alembic,无回滚。需迁 PostgreSQL。
2. **后端/前端服务以 root 运行** — `User=root` 在两个 systemd unit。RCE = 整机沦陷。
3. **零自动化测试** — 无 pytest / Jest / 集成测试。每次改 auth/支付都靠手点。
4. **无 CI/CD** — 部署 = ssh + git pull + systemctl restart。无依赖审计 / 无 SAST / 无构建产物校验。
5. **任务队列是文件 JSON** — `jobs_data/jobs.json`,崩溃即坏。Redis 已装但未充分用。
6. **备份等于没有** — 同机本地 + 7 天保留。机器挂 = 备份和数据库一起完蛋。

### 🟠 二级(运营会受限)

7. **单进程,无水平扩展** — uvicorn 无 workers,Next 单实例。
8. **文件存储在本地磁盘** — S3 配置项有但代码未用。
9. **可观测性接近零** — 自研 logger / 无结构化 / 无聚合 / 无 Sentry / 无 Prometheus。`feishu.py` 已实现告警但未充分接入。
10. **无审计日志** — 管理员加额度 / 改角色无不可变记录。
11. **JWT 体系不完整** — 无 refresh / 无吊销 / 无强制下线。
12. **支付是手动入账** — 截图 + 管理员确认。无对账,无回滚轨迹。

### 🟡 三级(工程素质)

13. **CORS / RateLimit 内存版** — 多 worker 后内存计数器穿透。
14. **运营文档缺** — 没有部署 runbook / 故障 SOP / 密钥轮换流程。
15. **合规未做** — ICP 备案、隐私政策、用户协议、内容审核(网信办深度合成规定)、AIGC 水印——全空。
16. **依赖版本激进** — Next 16 / React 19 / Prisma 7 都是刚 GA。

## 路线图(分阶段,每阶段都是 1-2 周可交付)

> **执行原则:** 任何一项动手前对齐范围、风险、回滚方案。涉及生产改动(DB / systemd / nginx)分阶段灰度,留 rollback 路径。

### Phase 1 — 工程根基(没有这一步别谈"企业级")

- [x] **pytest 后端测试基础设施**(2026-04-25 落地,DB + jobs 文件双隔离)
  - 跑测试:`cd backend && venv/bin/pytest -v`
  - 第一轮就抓到 users 表 schema 漂移 bug(见 commit `da34c57`)
- [x] **GitHub Actions CI**(backend pytest + frontend lint,push/PR 触发)
- [x] **后端测试覆盖到位**:auth(18) + billing(7) + jobs(7) + admin(5) = **38 例**
  - 关键安全断言:用户严格隔离(A 看不到 B 的 jobs)、普通用户不能 adjust-credits
- [ ] 加前端 e2e(Playwright)覆盖关键金路径
- [ ] Dependabot / pip-audit / npm audit 自动化
- [ ] 服务降权:`ssp-app` 用户 + `NoNewPrivileges` 等 sandbox 选项
- [ ] 备份异地化:rclone 推到对象存储 + 加密 + 月度恢复演练
- [ ] runbook / 故障 SOP 文档化

### Phase 2 — 数据/状态正规化

- [ ] PostgreSQL 迁移 + SQLAlchemy + Alembic 迁移版本
- [ ] 任务队列改为 RQ/Celery + Redis(jobs_data/jobs.json 退役)
- [ ] 验证码 / RateLimiter 改 Redis 后端(支持多 worker)
- [ ] 媒体存储迁阿里云 OSS / S3 + CDN

### Phase 3 — 可观测 + 风险控制

- [ ] Sentry 接入(后端 + 前端)
- [ ] 业务指标 → Prometheus + Grafana(dashboard 改 OAM 标准)
- [ ] 飞书告警接入到关键事件(扣费失败、生成失败激增、5xx 超阈值)
- [ ] `audit_log` 表 + 管理员操作强制写入
- [ ] JWT refresh token + Redis 黑名单

### Phase 4 — 合规与商业闭环

- [ ] ICP / 公安备案
- [ ] 隐私政策 / 用户协议 / cookie 同意页 + 注册时勾选
- [ ] 内容审核(阿里云内容安全 / 腾讯云 CMS)接到所有上传 + 生成入口
- [ ] 生成图像 / 视频隐式水印(满足深度合成规定)
- [ ] 真实支付:支付宝当面付 / 微信 Native + 对账接口 + 财务导出

### Phase 5 — 高可用 / 扩展

- [ ] 多 worker uvicorn + 多实例 Next + 内网负载均衡
- [ ] 蓝绿/金丝雀部署脚本
- [ ] 数据库主从 + WAL 归档 / PITR
- [ ] 必要时 K8s 化(到这一步说明已经有量了)

## 协作约定(给未来的 Claude)

- **语言:中文回复。** 用户偏好直接、严格、不 sugar-coat。
- **commit 风格:** `<type>: <中文描述>`。type ∈ {feat, fix, chore, i18n, security, complete, refactor}。重要 commit 在 message 里写"为什么",不只是"做了什么"。
- **破坏性操作前先对齐:** 动 DB / systemd / nginx / 任何 push 远端 / 任何会让用户掉线的操作,都先告诉用户范围、风险、rollback,得到确认再做。
- **不要替代用户决策:** 给两条路 + 你的推荐 + 取舍,让用户拍板。
- **memory:** `/root/.claude/projects/-root-ssp/memory/` 已存项目目标、偏好、运营路径,新会话务必读。
- **服务进程当前在 systemd 之外:** ssp-backend systemd 是 inactive 但 :8000 上有 uvicorn 在跑,ssp-frontend 同理。是上一会话遗留状态。**重启服务前先理清,别盲目 systemctl restart。**

## 当前状态(2026-04-26)

**生产环境**:ailixiao.com 跑在腾讯云轻量服务器(43.134.71.189),Blue-Green 部署,Blue 当前激活。

**Git 状态**:本地 + origin/main + origin/feat/auth-email-code-ui 三方对齐于 b16ce0e。SSH 协议(git@github.com:libubuuuu/ssp.git)+ /root/.ssh/id_ed25519_github。GitHub 账号 2FA 已开,旧 PAT 已清。

**安全状态**:RESEND_API_KEY、FAL_KEY、JWT_SECRET 均已轮换(2026-04-25 夜)。所有历史 token 已失效。.env.enc + 主密码分离,主密码在 /root/.ssp_master_key(权限 400)。

**记忆体系**:
- /root/start-claude.txt - 启动咒语
- /root/ssp/CLAUDE.md - 项目长期记忆(本文件)
- /root/ssp/PROGRESS.md - 进度日志 + 决策记录 + 待办

**部署体系**:
- /root/ssp/deploy/ - 系统配置 + 脚本(已入 git)
- /root/{deploy,rollback}.sh - symlink → deploy/ 下真实文件
- supervisor 4 个服务:ssp-{backend,frontend}-{blue,green}

## 怎么跑测试

```bash
cd /root/ssp/backend
venv/bin/pytest -v               # 全跑
venv/bin/pytest tests/test_auth.py -v   # 单个文件
venv/bin/pytest -k email_code -v        # 按关键字
```

测试用 `/tmp/ssp_test_*.db` 临时库,**不会碰 `backend/dev.db`**。
