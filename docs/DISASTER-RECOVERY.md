# SSP 灾备恢复手册

> 面向场景:服务器整机不可用,需要在新机上从零重建生产环境。
> 面向读者:**未来失忆的我**,或接手运维的工程师。
> 假设读者:具备基本 Linux 运维能力,但**不熟悉本项目内部细节**。

**最近演练:未演练。** 文档可能已腐烂,首次演练前认真过一遍每个命令。

---

## 1. 触发场景

走完整重建流程的典型情况:

- 云服务器整机失联(机房事故、被关停、被入侵后无法干净接管)
- 域名 / IP 必须迁移
- 重大配置错误,蓝绿都坏,回滚不动,决定推倒重来
- **季度演练**(强制,见第 6 节)

**不适用** 以下场景(走更轻流程,不要用这套):

| 情况 | 用什么 |
|---|---|
| 单次部署崩溃,蓝绿其中一边坏 | `bash /root/rollback.sh` |
| 单个 supervisor 服务跑飞 | `supervisorctl restart <name>` |
| dev.db 损坏但机器健康 | 单独从备份恢复 dev.db,不动其他东西 |
| 配置改坏(nginx/supervisor) | `cp ${path}.bak.* ${path}` 还原最近备份,reload |

---

## 2. 恢复前提清单

恢复**必须**有这些,任何一项缺就先停下补全,别开始动手:

| 必备 | 来源 | 校验方式 |
|---|---|---|
| GitHub 仓库访问 | SSH key 加到 GitHub Settings | `ssh -T git@github.com` 返回 `Hi libubuuuu!` |
| **主密码**(`.ssp_master_key` 内容) | 个人保管(密码管理器 / 纸质保险柜) | 用它能解 `backend/.env.enc` |
| 数据库备份(`dev.db`) | 对象存储(**TODO:接入**) / 临时:手头最近一次本地备份 | 文件可读 + `sqlite3 .schema users` 有输出 |
| 域名管理后台 | DNS 提供商账号 | 能改 A 记录到新服务器 IP |
| 云服务器购买能力 | 腾讯云 / 阿里云账号 + 余额 | 能开 1 台 Ubuntu 22.04 实例 |
| 邮箱(SSL 证书申请) | 任意能收信的 | certbot 注册要 |

**可选(没有也能恢复,但损失功能):**

- Resend / FAL / 阿里云 SMS API key — 都在 .env.enc 里,主密码解开后就有
- 旧 Let's Encrypt 账号 — certbot 会自动重新注册新账号

---

## 3. 完整恢复步骤

### 阶段 1:开新服务器(预计 10 分钟)

1. 云控制台开 1 台 **Ubuntu 22.04 LTS** 实例
   - 推荐配置:2C / 4G / 50G SSD(跟生产一致)
   - 区域:跟原服务器同区域,DNS 切换 TTL 短
2. 安全组开 22 / 80 / 443
3. SSH 上去:改 root 密码,关 SSH 密码登录改 key

```bash
# 临时密码登录后立刻
passwd                                    # 改 root 密码
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "你的公钥内容" > ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
sed -i 's/^#*PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
```

### 阶段 2:git clone + setup-fresh-server.sh(预计 15-20 分钟)

```bash
# 配 SSH key 让能 clone(若新机还没 key)
ssh-keygen -t ed25519 -C "ssp-recovery"
cat ~/.ssh/id_ed25519.pub
# 把公钥贴到 GitHub Settings → SSH and GPG keys

# clone
git clone git@github.com:libubuuuu/ssp.git /root/ssp
cd /root/ssp

# 一键搭基础设施(装 Node/Python/Nginx/Supervisor/fail2ban/UFW/certbot,创 swap,装依赖)
sudo bash deploy/setup-fresh-server.sh
```

`setup-fresh-server.sh` 跑完会停在"4 步手动清单",按下面阶段 3-5 继续。
**它不会启动后端 / 前端服务**(因为还没解 env)。

### 阶段 3:写主密码 + 数据库恢复(预计 5-30 分钟)

```bash
# 1. 写主密码(用密码管理器里那串)
echo "你保管的主密码原文" > /root/.ssp_master_key
chmod 400 /root/.ssp_master_key

# 2. 验证密码对(只看前几行,确认能解就行)
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d \
  -in /root/ssp/backend/.env.enc \
  -pass file:/root/.ssp_master_key | head -3
# 看到 RESEND_API_KEY=re_xxx 之类输出 = 密码正确
# 看到 "bad decrypt" 之类报错 = 密码错,停下别继续

# 3. 拉 dev.db
# TODO(对象存储接入后):bash /root/ssp/deploy/restore-from-backup.sh
# 当前手动方案:从最近一次本地备份 / 异地备份 scp 到 backend/
scp <旧机或备份位置>/dev.db /root/ssp/backend/dev.db
chmod 600 /root/ssp/backend/dev.db

# 验证库可读
sqlite3 /root/ssp/backend/dev.db ".tables"
# 应看到 users / jobs / billing_records 等表
```

### 阶段 4:DNS + SSL + 起服务(预计 15 分钟,卡 DNS 生效)

```bash
# 1. 在 DNS 管理后台改 A 记录到新机 IP
#    ailixiao.com / www.ailixiao.com / admin.ailixiao.com / monitor.ailixiao.com
#    TTL 改短(60-300s)等生效

# 2. 验证 DNS 已生效(在新机上自查)
dig +short ailixiao.com         # 应该是新机 IP
dig +short admin.ailixiao.com   # 同
dig +short monitor.ailixiao.com # 同

# DNS 没生效就别继续,certbot 会失败

# 3. 申请 SSL(同时 4 个子域)
certbot --nginx \
  -d ailixiao.com -d www.ailixiao.com \
  -d admin.ailixiao.com -d monitor.ailixiao.com \
  --agree-tos -m your@email.com --no-eff-email --redirect

# 4. 启动应用
supervisorctl start ssp-backend-blue ssp-frontend-blue
supervisorctl status

# 5. 重载 nginx(certbot 改了 ssl_certificate 路径)
systemctl reload nginx
```

### 阶段 5:验证(预计 5 分钟)

走第 4 节"验证清单",每项都过才算完成。

---

## 4. 验证清单

| 检查项 | 命令 | 预期 |
|---|---|---|
| supervisor 4 服务 | `supervisorctl status` | blue 两个 RUNNING,green 两个 STOPPED |
| 后端健康 | `curl https://ailixiao.com/health` | 200 + JSON `{"status":"ok"}` |
| 前端首页 | `curl -I https://ailixiao.com/` | 200 |
| 管理后台 | `curl -I https://admin.ailixiao.com/admin/` | 200 |
| HTTPS 强制 | `curl -I http://ailixiao.com/` | 301 → https |
| 数据库可读 | `sqlite3 backend/dev.db ".tables"` | 显示 users/jobs 等表 |
| 邮件能发 | 前端触发一次注册 → 收验证码 | 邮箱收到 |
| AI 任务能跑 | 提交一次图生图 | 任务状态走完 |
| fail2ban | `fail2ban-client status` | sshd / nginx-http-auth jail 列出 |
| 防火墙 | `ufw status` | 22/80/443 ALLOW,默认 deny |
| swap | `swapon --show` | /swapfile 2G |
| 后端日志无 ERROR | `tail -200 /var/log/ssp-backend-blue.err.log` | 干净 / 仅启动信息 |

任何一项失败 → 第 5 节排查。

---

## 5. 常见故障排查

### 5.1 supervisor 起不来后端 / 不停重启

```bash
tail -200 /var/log/ssp-backend-blue.err.log
```

| 报错 | 原因 | 修法 |
|---|---|---|
| `bad decrypt` | 主密码错 | 检查 `/root/.ssp_master_key` 内容,有没有多余换行/空格 |
| `ModuleNotFoundError` | venv 缺包 | `cd backend && venv/bin/pip install -r requirements.txt` |
| `Address already in use` | 端口冲突 | `lsof -i:8000` 找到占用进程杀掉 |
| `database is locked` | dev.db 权限 / 多进程 | `chmod 600 dev.db`;确认 backup_daily 没在跑 |
| `no such table` | dev.db 是空的或漂移 | 检查恢复的 dev.db 是不是对的版本 |

### 5.2 certbot 申请失败

```bash
certbot --nginx -d ailixiao.com -v
```

| 报错关键字 | 原因 | 修法 |
|---|---|---|
| `DNS problem: NXDOMAIN` / `does not match` | DNS 还没生效 | `dig +short` 确认是新 IP,等 5-30 分钟 |
| `Connection refused` 80 端口 | nginx 没起 / 防火墙没开 | `systemctl status nginx`、`ufw status` |
| `too many failed authorizations` | rate limit | Let's Encrypt 同域名 1 周 5 次失败,等;调试时用 `--staging` |
| `Could not bind to IPv6` | 实例没 IPv6 | nginx.conf 里 `listen [::]:443` 删掉或保留(certbot 不会卡) |

### 5.3 nginx -t 报 `limit_req_zone "api_limit" not found`

`deploy/nginx.conf` 引用 `api_limit` / `login_limit` 两个 zone,但定义必须在 http {} 块,server {} 里引用不到。

修法:在 `/etc/nginx/nginx.conf` 的 http {} 块里加:

```nginx
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=20r/s;
limit_req_zone $binary_remote_addr zone=login_limit:10m rate=2r/s;
```

`setup-fresh-server.sh` 阶段 6 会检测并打印警告。

### 5.4 /root/{deploy,rollback}.sh symlink 失效

```bash
readlink -f /root/deploy.sh   # 应输出 /root/ssp/deploy/deploy.sh
# 失效就重建:
ln -sf /root/ssp/deploy/deploy.sh /root/deploy.sh
ln -sf /root/ssp/deploy/rollback.sh /root/rollback.sh
```

### 5.5 admin.ailixiao.com 502

admin 子域反代到同一组后端 8000 / 前端 3000:

```bash
curl -I http://127.0.0.1:3000               # 前端是否在
curl -I http://127.0.0.1:8000/health        # 后端是否在
supervisorctl status
```

### 5.6 npm run build 失败

可能原因:
- node_modules 半装 → `cd frontend && rm -rf node_modules .next && npm ci`
- 缺 `NEXT_PUBLIC_*` env → 检查 `frontend/.env.local`(不在 git 里,需手工补);或暂时空跑(部分 build-time env 缺会警告但不 fail)

### 5.7 主密码丢失

**.env.enc 永久无法解密**。后端起不来。处理:

1. 生成新主密码,新建 `.env.enc`(把 RESEND/FAL/JWT 等 key 重新申请一遍)
2. 旧 `.env.enc` 留作纪念
3. 把所有 API key 全部轮换(因为不知道旧密码会不会泄露)

**主密码备份要求:** 至少 2 处异地存放(密码管理器 + 纸质 / 或 2 个不同账号的密码管理器)。

---

## 6. 演练记录

**强制要求:每季度演练一次完整恢复。** 在测试服务器上跑通阶段 1-5,所有验证清单通过。

文档会腐烂(端口变、API 变、依赖升级、域名加减),只有真跑过才知道哪里失效。每次演练后在下表加一行:

| 日期 | 演练人 | 在哪台机演 | 总耗时 | 卡点 / 修正 |
|---|---|---|---|---|
| _(未演练)_ | | | | |

---

## 7. 相关文档与脚本

- `deploy/setup-fresh-server.sh` — 阶段 2 自动化执行
- `deploy/deploy.sh` — 蓝绿部署(已上线后用)
- `deploy/rollback.sh` — 单次部署回滚
- `deploy/{nginx,supervisor,fail2ban}.conf` — 系统配置真源
- `CLAUDE.md` — 项目档案 + 路线图 + 协作约定
- `PROGRESS.md` — 进度日志 + 决策记录
