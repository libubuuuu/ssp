项目进度日志,每次收工前更新

## 2026-04-26 凌晨(通宵交付)

### ✅ 安全加固(不可逆,生产关键)
- RESEND_API_KEY 轮换 + 在线测试通过
- FAL_KEY 轮换 + 在线测试通过
- JWT_SECRET 轮换,所有历史 token 失效(包括之前泄露过的 admin token)
- 销毁 jsonl 缓存(防本会话 key 明文残留)
- 销毁 .env.enc.preroll-jwt 旧加密备份

### ✅ 工程基建
- 启动咒语固化:/root/start-claude.txt
- 项目记忆体系:CLAUDE.md + PROGRESS.md(本文件)
- .gitignore 加固:.tar.gz / .ssp_master_key / .bak.* / jobs.json
- GitHub 账号安全:2FA 已开 + 旧 PAT 清 + OAuth 清
- 35 个本地 commit 全部 push 到 origin/main(b16ce0e → 6c89907)
- 4 个 ref 对齐:main = origin/main = feat = origin/feat = 6c89907

### ✅ 灾备体系(从 0 → 1)
- deploy/ 目录:nginx.conf / supervisor.conf / fail2ban.local / deploy.sh / rollback.sh / README.md
- deploy/setup-fresh-server.sh:全新 Ubuntu 22.04 一键恢复(247 行,12 阶段,幂等)
- docs/DISASTER-RECOVERY.md:完整灾备手册(268 行,7 章节,故障排查 7 类)
- 设计目标:**新服务器 git clone + bash setup-fresh-server.sh + 3 步手动 = 30-60 分钟恢复**

### ✅ 运维清理(Claude Code 主动顺手做的)
- 杀僵尸 next-server 进程(回收 96% CPU)
- journal 清理 3.7G
- 改名禁用废 systemd unit
- 密钥泄漏面清零(明文 .env、老 dev.db、误命名快照)

### ⏸ 今晚不做(明天/本周/下周做)
- 接腾讯云 COS 异地备份 + restore-from-backup.sh(需要先开 COS 账户)
- 灾备真演练(在测试服务器上跑一次完整流程)
- 接微信支付官方 API + 验签 + 回调
- JOBS 从 JSON 文件迁数据库(SQLite WAL 或 Postgres)
- Token 改 httpOnly Cookie
- 修扣费竞态(原子 SQL UPDATE)
- 注册加邮箱验证 + 图形验证码 + IP 限流

### 决策记录
- 2026-04-26:服务器现状作为新 main,fast-forward 而非 force(零风险)
- 2026-04-26:secret123 在 conftest.py 是测试 fixture,不脱敏
- 2026-04-26:scripts/ 合并到 deploy/(目录统一,git mv 保留历史)
- 2026-04-26:.preroll-jwt 直接 shred 销毁(旧 key 已作废,无回滚价值)
- 2026-04-26:暂不接微信支付 / 不改 JOBS / 不改 Token 存储 → 性质上需要工作时间窗口,通宵期不做不可逆改动

## 2026-04-25

- JWT_SECRET 轮换 + 旧 token 全失效验证通过 + i18n 重复 key 清理

## 待办

- 未来要做:rotate-key.sh 脚本化 key 轮换流程
