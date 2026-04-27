# SSP / AI Lixiao 运营手册 (Runbook)

> 应急、例行运维、关键流程的"打开就能用"操作手册。**遇事先看这里,不要靠记忆。**
> 与 `CLAUDE.md`(架构 + 路线)互补:CLAUDE.md 写"是什么 / 应该怎样",RUNBOOK 写"现在崩了我该敲什么"。

## 目录

- [服务进程 / 端口](#服务进程--端口)
- [蓝绿部署](#蓝绿部署)
- [回滚](#回滚)
- [数据库与备份](#数据库与备份)
- [证书 (Let's Encrypt)](#证书-lets-encrypt)
- [密钥 / 凭据轮换](#密钥--凭据轮换)
- [常见故障处置](#常见故障处置)
- [健康巡检 / 监控](#健康巡检--监控)
- [审计 / 安全应急](#审计--安全应急)

---

## 服务进程 / 端口

**当前架构**:supervisor 管 4 个 program(蓝绿 × 前后端),全部以 `ssp-app` 用户跑(2026-04-27 降权完成)。

| program | 端口 | autostart | 备注 |
|---|---|---|---|
| ssp-backend-blue   | 8000 | false | 备用 |
| ssp-backend-green  | 8001 | true  | **当前 active** |
| ssp-frontend-blue  | 3000 | false | 备用 |
| ssp-frontend-green | 3002 | true  | **当前 active** |

```bash
# 看状态
supervisorctl status

# 查谁是当前 active(看 nginx upstream)
grep -E "proxy_pass.*localhost:(800|300)" /etc/nginx/sites-enabled/default

# 重启 active 一边(green)
supervisorctl restart ssp-backend-green
supervisorctl restart ssp-frontend-green

# 看日志
tail -200 /var/log/supervisor/ssp-backend-green-stdout*.log
tail -200 /var/log/supervisor/ssp-backend-green-stderr*.log
```

**老的 systemd 单元已 disable**(`ai-frontend.service` / `ssp-backend.service` / `ssp-frontend.service`)。重启服务永远走 supervisorctl,不要碰 systemctl。

**降权后路径**:
- 工作目录:`/opt/ssp/{backend,frontend}`(ssp-app 拥有)
- 主密钥:`/etc/ssp/master.key`(640,owner root,group ssp-app)
- git working tree:**仍在 `/root/ssp`**,deploy.sh 同步到 /opt/ssp(暂未迁移 git)

---

## 蓝绿部署

> 30 秒停机蓝绿切换。脚本:`/root/deploy.sh` → `/opt/ssp/deploy/deploy.sh`(symlink)

```bash
# 0. 本地必须先绿
cd /root/ssp/backend && venv/bin/pytest -v       # 100 例必须全过
cd /root/ssp/frontend && npm run build           # 必须成功

# 1. commit + push(deploy.sh 从 git 拉)
cd /root/ssp && git status && git push

# 2. 一键蓝绿部署(切到 idle 那一边)
bash /root/deploy.sh

# 3. 健康检查
bash /root/post-deploy-check.sh
cat /root/HEALTH_AT_08.md   # 看 8 项报告
```

**deploy.sh 干了啥**(简化):rsync /root/ssp → /opt/ssp,pip/npm install,build,启动 idle 边,改 nginx upstream 切流量,停老边。

**避免**:中国时区高峰(10:00-22:00)发布。低峰 03:00-07:00。

**特殊场景:supervisor 配置变更**
deploy.sh 不会动 supervisor 配置。改了 `deploy/supervisor.conf` 要手工应用:
```bash
diff /root/ssp/deploy/supervisor.conf /etc/supervisor/conf.d/ssp.conf  # 看变化
cp /etc/supervisor/conf.d/ssp.conf /etc/supervisor/conf.d/ssp.conf.before-$(date +%Y%m%d-%H%M%S)
cp /root/ssp/deploy/supervisor.conf /etc/supervisor/conf.d/ssp.conf
supervisorctl reread       # 应该列出变化的 program
supervisorctl update       # 重启变化的 program(~30 秒停机)
```
**已知坑**:旧版本 supervisor 配置启的进程组不规范,新配置 `stopasgroup` 杀不掉,会变 PPID=1 孤儿占端口。新进程会 FATAL。处理:`kill <orphan_pid>` 释放端口,`supervisorctl start <program>`。从此以后不会再撞。

---

## 回滚

> 一键回滚:`/root/rollback.sh` → `/opt/ssp/deploy/rollback.sh`

```bash
# 立即切回上一个 active(快)
bash /root/rollback.sh

# 或手动:active=green 时切回 blue
supervisorctl start ssp-backend-blue ssp-frontend-blue
sleep 8
# 改 nginx upstream 指 :8000 + :3000
# nginx -t && systemctl reload nginx
supervisorctl stop ssp-backend-green ssp-frontend-green
```

**DB 不回滚**:SQLite 没迁移版本,如果新 deploy 改了 schema,回滚代码后 schema 仍是新版,可能不兼容老代码。Phase 2 上 Postgres + Alembic 解。

**Hard rollback(去年式)**:服务器还在 2026-04-27 降权前老路径 `/root/ssp` 留 24 小时作 hard rollback。如果 /opt/ssp 整个崩,把 supervisor 配置 revert 到 `/etc/supervisor/conf.d/ssp.conf.preopt-backup`,30 秒回到 root 跑 /root/ssp 旧版。

---

## 数据库与备份

**位置**:
- `/opt/ssp/backend/dev.db`(生产真库,SQLite)
- `/root/backups/dev_<ts>.db` + `data_<ts>.tar.gz`(每日 03:00 自动,保留 7 天)
- 脚本:`/root/backup_daily.sh`,`SSP_ROOT` 环境变量(默认 `/opt/ssp`)

**手动触发一次备份**:
```bash
bash /root/backup_daily.sh
ls -la /root/backups/ | tail -5
```

**从备份恢复**(选最近一份):
```bash
# 1. 停后端(active 一边)
supervisorctl stop ssp-backend-green

# 2. 备份当前可疑库
cp /opt/ssp/backend/dev.db /root/dev_corrupt_$(date +%s).db

# 3. 用备份替换(注意 owner)
cp /root/backups/dev_20260427_125454.db /opt/ssp/backend/dev.db
chown ssp-app:ssp-app /opt/ssp/backend/dev.db
chmod 600 /opt/ssp/backend/dev.db

# 4. 起服务,验证
supervisorctl start ssp-backend-green
sleep 5
curl -s https://ailixiao.com/api/payment/packages | head
```

**⚠️ 已知风险**:备份只在本机,机器挂 = 备份 + 数据库一起完蛋。Phase 1 待办:rclone 推到对象存储 + 加密 + 月度恢复演练。

**直接查 DB(只读)**:
```bash
sqlite3 /opt/ssp/backend/dev.db ".schema users"
sqlite3 /opt/ssp/backend/dev.db "SELECT email, credits, role FROM users WHERE role='admin';"
sqlite3 /opt/ssp/backend/dev.db "SELECT COUNT(*) FROM users;"
```

---

## 证书 (Let's Encrypt)

`certbot` 管理,自动续期。

```bash
certbot certificates                                    # 看证书状态
certbot renew --post-hook "systemctl reload nginx"      # 续期 + reload
```

证书文件:`/etc/letsencrypt/live/{ailixiao.com,admin.ailixiao.com,monitor.ailixiao.com}/`

---

## 密钥 / 凭据轮换

> **重要**:.env 是加密的(`backend/.env.enc`)。主密钥在 `/etc/ssp/master.key`(2026-04-27 降权后从 /root/.ssp_master_key 迁来)。

**通用流程**(改任何 .env 项)
```bash
# 1. 解密看当前内容
cd /opt/ssp/backend
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d -in .env.enc -pass file:/etc/ssp/master.key

# 2. 改后再加密
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt -in /tmp/.env.new -out .env.enc -pass file:/etc/ssp/master.key

# 3. 重启 active 后端(新 env 生效)
supervisorctl restart ssp-backend-green
```

**JWT_SECRET** — 轮换后**所有用户被强制重退**(用户级吊销 + 新 secret 双层失效):
1. `openssl rand -base64 64` 生成新 secret
2. 改 .env 重加密(上面通用流程)
3. `supervisorctl restart ssp-backend-green`
4. 用户友好提示:登出所有人会跳 /auth?expired=1,有友好文案(不是技术错误)

**FAL_KEY / RESEND_API_KEY** — 直接在对应控制台 revoke 旧 + 生成新,改 .env 重加密 + restart。

**主密钥(/etc/ssp/master.key)轮换** — 复杂,等专项做:需用旧密钥解 .env.enc,再用新密钥重加密,最后替换文件。

---

## 常见故障处置

### 1. 用户登录返回 500
```bash
# 看后端日志(active green 端)
tail -200 /var/log/supervisor/ssp-backend-green-stderr*.log
```
常见原因:
- DB schema 缺列 → `cd /opt/ssp/backend && venv/bin/pytest -v` 暴露
- .env.enc 解密失败 → 检查 /etc/ssp/master.key 权限(640 root:ssp-app)
- DB 锁 → 看是否有长事务/备份在跑

### 2. 接口 502 / 504
```bash
# 后端是否还活着(green active)
curl -s http://127.0.0.1:8001/api/payment/packages
ss -tlnp | grep 8001

# 前端
curl -s http://127.0.0.1:3002/
ss -tlnp | grep 3002

# nginx 自身
systemctl status nginx && nginx -t
```

### 3. 任务一直 pending
`jobs_data/jobs.json` 是文件型队列(Phase 2 迁 Redis 退役)。
```bash
cat /opt/ssp/jobs_data/jobs.json | jq '. | length'
cat /opt/ssp/jobs_data/jobs.json | jq 'to_entries | map(.value.status) | group_by(.) | map({(.[0]): length}) | add'

# 卡死任务 → 退还扣费(谨慎,先确认 task 真的没完成)
sqlite3 /opt/ssp/backend/dev.db "UPDATE users SET credits = credits + <amount> WHERE id = '<user_id>';"
```

### 4. fail2ban 误封
```bash
fail2ban-client status sshd
fail2ban-client status nginx-limit-req
fail2ban-client unban <IP>          # 单独解封
fail2ban-client unban --all         # 全解
```

### 5. 磁盘满
```bash
du -sh /root/* /opt/* | sort -h | tail
du -sh /var/log/* | sort -h | tail
du -sh /opt/ssp/frontend/.next        # Next 构建缓存
du -sh /opt/ssp/studio_workspace      # 长视频 session
du -sh /root/backups                   # 7 天备份滚动
journalctl --disk-usage
journalctl --vacuum-time=7d            # 收割旧日志
```

### 6. supervisor 程序反复 FATAL "Exited too quickly"
99% 是端口已被占。先确认:
```bash
ss -tlnp | grep -E ':(8000|8001|3000|3002)'
ps -ef | grep -E "next-server|uvicorn" | grep -v grep
```
- PPID=1 的孤儿(降权切换时出现过):`kill <pid>` 释放端口
- 不属于 ssp-app 的:可能是其他服务(systemd ai-frontend 之类),`systemctl disable --now <unit>`
- 释放后 `supervisorctl start <program>`

### 7. WS(WebSocket)鉴权 4401 / 4403
- **4401**:token 无效/过期 — 前端走 refresh 应自动恢复;持续就是用户该重登
- **4403**:task 不属于这个用户 — 越权订阅,正确的拒绝;若是合法用户却被拒,看 `task_ownership.py` 注册点是否漏了某个新端点

### 8. 用户报"老被踢登录页"
检查多层:
- 后端 .env JWT_SECRET 是不是被轮换过(所有 token 必失效)
- /api/auth/refresh 是否 200(用户有 refresh_token 且未过期)
- 前端 AuthFetchInterceptor 是否在 layout.tsx 挂上(rebuild 后偶发)

---

## 健康巡检 / 监控

### 自动巡检
- `/root/watchdog.sh`:cron 每 5 分钟跑,绿/黄/红 写 `/var/log/ssp-watchdog.log`,异常推微信(Server 酱)
- `/root/post-deploy-check.sh`:8 项快速体检,生成 `/root/HEALTH_AT_08.md`(蓝绿切换后跑)

### 手动一键诊断
admin 后台 顶部 banner 有 🩺 按钮 → 浏览器自动复制完整 JSON 快照 → 粘贴给 Claude 精准定位。

后端直接调:
```bash
curl -s -H "Authorization: Bearer <admin_token>" \
  https://admin.ailixiao.com/api/admin/diagnose | jq | head -100
```

诊断历史(watchdog 告警时自动冻结):
- 文件:`/var/log/ssp-diagnose/<TS>-<LEVEL>.json`
- UI:`https://admin.ailixiao.com/admin/diagnose`

### 关键端点
- 公网首页:https://ailixiao.com/
- 公开 api:https://ailixiao.com/api/payment/packages(无需鉴权,200 = 后端活)
- 监控站:https://monitor.ailixiao.com(反代 :3001)

### 日志分布
```
/var/log/supervisor/ssp-{backend,frontend}-{blue,green}-{stdout,stderr}*.log
/var/log/nginx/{access,error}.log
/var/log/ssp-backup.log
/var/log/ssp-watchdog.log
/var/log/ssp-diagnose/*.json
```

---

## 审计 / 安全应急

### 查谁做了什么(审计日志)
所有敏感操作(改积分/确认订单/强制下线/改密码/重置密码/重置模型/登出所有设备)写不可变 audit_log 表。
```bash
sqlite3 /opt/ssp/backend/dev.db \
  "SELECT created_at, actor_email, action, target_id, ip FROM audit_log ORDER BY created_at DESC LIMIT 30;"

# 按 action 过滤(前端 UI:https://admin.ailixiao.com/admin/audit)
sqlite3 /opt/ssp/backend/dev.db \
  "SELECT * FROM audit_log WHERE action='adjust_credits' ORDER BY created_at DESC LIMIT 20;"
```

### 强制踢人(管理员侧)
- UI:`https://admin.ailixiao.com/admin/users` → 找用户 → "强制下线"
- 机制:`tokens_invalid_before` 列推到当前时间,后续 access/refresh 全失效
- 自动写 audit

### 用户自救("登出所有设备")
- UI:profile 页红色边框按钮
- 用户怀疑账号被盗时主动触发
- 当前浏览器 token 也失效 → 跳 `/auth?expired=1` 友好提示

### 2FA / TOTP 重置
用户失去 TOTP 设备,需管理员重置:
```bash
# 关掉用户的 2FA(必须确认身份后做)
sqlite3 /opt/ssp/backend/dev.db \
  "UPDATE users SET totp_secret=NULL, totp_enabled=0, recovery_codes=NULL WHERE email='<email>';"
```
之后用户重新走 `/profile/2fa` 启用流程。**记得手动写一条 audit**(无 UI 入口,Phase 3 加)。

### 凭据泄漏应急(假设 master key 或 JWT_SECRET 泄漏)
1. **立即**:轮换 JWT_SECRET(所有用户掉线,但比泄漏强)
2. **立即**:轮换 FAL_KEY / RESEND_API_KEY(防资源被薅)
3. 4 小时内:轮换主密钥(/etc/ssp/master.key)— 复杂,见上面"密钥轮换"章节
4. 24 小时内:审计 audit_log 看是否有可疑的 admin 操作
5. 通知用户(邮件)+ 公告

---

## 修订记录

- 2026-04-25 初版
- 2026-04-27 大重写:适配蓝绿 + supervisor + ssp-app 降权 + /opt/ssp + AIOps 闭环 + 审计/2FA/WS 应急
