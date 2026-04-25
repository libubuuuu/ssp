# SSP / AI Lixiao 运营手册 (Runbook)

> 应急、例行运维、关键流程的"打开就能用"操作手册。**遇事先看这里,不要靠记忆。**
> 与 `CLAUDE.md`(架构 + 路线)互补:CLAUDE.md 写"是什么 / 应该怎样",RUNBOOK 写"现在崩了我该敲什么"。

## 目录

- [服务进程](#服务进程)
- [部署 / 发布](#部署--发布)
- [回滚](#回滚)
- [数据库与备份](#数据库与备份)
- [证书 (Let's Encrypt)](#证书-lets-encrypt)
- [密钥/凭据轮换](#密钥凭据轮换)
- [常见故障处置](#常见故障处置)
- [监控访问](#监控访问)

---

## 服务进程

```
ssp-backend  → /etc/systemd/system/ssp-backend.service   uvicorn :8000  (User=root,待降权)
ssp-frontend → /etc/systemd/system/ssp-frontend.service  npm start :3000 (User=root,待降权)
```

```bash
# 查看状态
systemctl status ssp-backend ssp-frontend

# 重启
systemctl restart ssp-backend
systemctl restart ssp-frontend

# 看日志
journalctl -u ssp-backend -f --since "10 min ago"
journalctl -u ssp-frontend -f --since "10 min ago"
```

**已知例外(2026-04-25):** `is-active` 可能显示 `inactive`,但 :8000 / :3000 上有手动启动的进程在跑。重启前先 `ss -ltnp | grep -E ':(8000|3000)\b'` 看清楚是不是 systemd 自己起的。

## 部署 / 发布

> 当前是 git pull + systemctl restart 的"瑟瑟发抖式"部署,Phase 5 升级到蓝绿之前先按下面流程做:

```bash
# 0. 先在本地跑测试(必须绿)
cd /root/ssp/backend && venv/bin/pytest -v

# 1. 拉代码
cd /root/ssp && git pull

# 2. 后端依赖变化时重装(看 backend/requirements.txt 有没有改)
cd /root/ssp/backend && venv/bin/pip install -r requirements.txt

# 3. 前端依赖变化时重装 + 重新 build
cd /root/ssp/frontend && npm ci && npm run build

# 4. 重启
systemctl restart ssp-backend
systemctl restart ssp-frontend

# 5. 健康检查
curl -s https://ailixiao.com/health | jq
```

**避免:** 在中国时区白天高峰(10:00-22:00)做发布。低峰窗口:凌晨 3:00-7:00。

## 回滚

```bash
# 看上一个稳定 commit
cd /root/ssp && git log --oneline -10

# 切回去
git checkout <commit-sha>

# 后端立即 restart;前端要 rebuild
systemctl restart ssp-backend
cd frontend && npm run build && cd .. && systemctl restart ssp-frontend

# 验证回滚成功
curl -s https://ailixiao.com/health | jq
```

如果 DB schema 也回滚了:**先停止接受写入(把 nginx 改 503),再人工核对当时的 schema 状态**——SQLite 没有迁移版本,容易翻车。Phase 2 有 Alembic 之后这一步会简单。

## 数据库与备份

**位置:**
- `/root/ssp/backend/dev.db` (SQLite,生产真库)
- `/root/backups/dev_<timestamp>.db` (每日 03:00 自动备份,保留 7 天)
- 备份脚本:`/root/backup_daily.sh`

**手动触发一次备份:**
```bash
bash /root/backup_daily.sh
ls -la /root/backups/ | tail -5
```

**从备份恢复:**
```bash
# 1. 停服务
systemctl stop ssp-backend

# 2. 备份当前坏库
cp /root/ssp/backend/dev.db /root/dev_corrupt_$(date +%s).db

# 3. 用备份替换
cp /root/backups/dev_20260425_030001.db /root/ssp/backend/dev.db

# 4. 起服务,验证
systemctl start ssp-backend
curl -s https://ailixiao.com/health | jq
```

**⚠️ 已知风险:** 备份只在本机,机器挂了等于备份和数据库一起完蛋。Phase 1 待办:rclone 推到对象存储 + 加密。

**直接查 DB(只读):**
```bash
sqlite3 /root/ssp/backend/dev.db ".schema users"
sqlite3 /root/ssp/backend/dev.db "SELECT email, credits, role FROM users WHERE role='admin';"
```

## 证书 (Let's Encrypt)

`certbot` 管理,自动续期。

```bash
# 看证书状态
certbot certificates

# 强制续期(过期前 30 天才会真续)
certbot renew

# 续期 + reload nginx
certbot renew --post-hook "systemctl reload nginx"
```

证书文件:`/etc/letsencrypt/live/{ailixiao.com,admin.ailixiao.com,monitor.ailixiao.com}/`

## 密钥/凭据轮换

**JWT_SECRET** — `backend/.env.enc` 里。轮换后所有用户被强制重新登录:
1. 生成新 secret:`openssl rand -base64 64`
2. 解密 `.env.enc`,改 `JWT_SECRET=<new>`,重新加密
3. `systemctl restart ssp-backend`
4. 通知用户"系统更新需重新登录"

**FAL_KEY** — 直接在 fal.ai 控制台 revoke 旧的 + 生成新的,改 `.env.enc` + restart。

**RESEND_API_KEY** — Resend 控制台同上。

**GitHub PAT(已知风险,2026-04-25 待处理):**
当前 `.git/config` 有明文 PAT。轮换步骤:
1. https://github.com/settings/tokens 撤销旧 token
2. 生成 SSH key:`ssh-keygen -t ed25519 -C "ailixiao-server" -f ~/.ssh/id_ed25519_github`
3. 把 `~/.ssh/id_ed25519_github.pub` 内容贴到 https://github.com/settings/keys
4. 改 remote:`git -C /root/ssp remote set-url origin git@github.com:libubuuuu/ssp.git`
5. 测试:`ssh -T git@github.com`
6. 清理 `/root/ssp.bak.*/.git/config` 这些历史快照里残留的 token

## 常见故障处置

### 1. 用户登录返回 500

先看 backend 日志:`journalctl -u ssp-backend -f`

常见原因:
- DB schema 缺列(看上次会话是不是手动 ALTER 过没同步代码)→ 跑 `pytest -v`,本地 init_db 会暴露
- JWT_SECRET 没设 → 检查 `.env.enc` 是否解密成功
- DB 锁(SQLite 单写)→ 看是不是有备份/导出进程在长事务

### 2. 接口 502 / 504

```bash
# 后端是否还活着
curl -s http://127.0.0.1:8000/health
ss -ltnp | grep 8000

# 前端
curl -s http://127.0.0.1:3000
ss -ltnp | grep 3000

# nginx 自身
systemctl status nginx
nginx -t  # 配置语法检查
```

### 3. 任务一直 pending

`jobs_data/jobs.json` 是文件型队列,进程崩溃可能让任务卡死。

```bash
# 看队列
cat /root/ssp/jobs_data/jobs.json | jq '. | length'
cat /root/ssp/jobs_data/jobs.json | jq 'to_entries | map(.value.status) | group_by(.) | map({(.[0]): length}) | add'

# 强制退还某 user 卡死的扣费(谨慎)
sqlite3 /root/ssp/backend/dev.db "UPDATE users SET credits = credits + <amount> WHERE id = '<user_id>';"
```

Phase 2 迁 Redis 之后这块会有正经的 stuck-job 死信处理。

### 4. fail2ban 误封

```bash
# 看封禁列表
fail2ban-client status sshd
fail2ban-client status nginx-limit-req

# 解封
fail2ban-client unban <IP>
fail2ban-client unban --all   # 一次性放掉
```

### 5. 磁盘满

```bash
# 谁在占空间
du -sh /root/* | sort -h | tail
du -sh /var/log/* | sort -h | tail

# 常见嫌疑
du -sh /root/ssp/frontend/.next   # Next 构建缓存
du -sh /root/ssp/studio_workspace # 长视频 session
du -sh /root/backups              # 备份(7 天滚动)
journalctl --disk-usage           # systemd 日志
journalctl --vacuum-time=7d       # 收割超过 7 天的日志
```

## 监控访问

- **应用健康:** https://ailixiao.com/health
- **监控站:** https://monitor.ailixiao.com (反代 :3001,Uptime Kuma 之类)
- **日志:** `journalctl -u ssp-backend -f`,`journalctl -u ssp-frontend -f`,`/var/log/nginx/*.log`,`/var/log/ssp-backup.log`

---

## 修订记录

- 2026-04-25 初版,涵盖服务/部署/回滚/备份/证书/密钥/常见故障/监控
