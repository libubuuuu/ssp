项目进度日志,每次收工前更新

## 2026-04-26 下午(企业级安全增强)

### ✅ 后端安全 4 大件
- **扣费竞态修复**:`deduct_credits` 用 `UPDATE WHERE credits >= ?` 原子化,杜绝并发把余额扣到负数。删除调用方 `check_user_credits` 预检(预检反而引入竞态窗口),失败统一返 402
- **审计日志体系**:新建 `audit_log` 表(不可变,只增不改) + `services/audit.py` 钩子。`adjust-credits` / `force-logout` 已接入,记录 actor/target/details(JSON)/IP
- **JWT 用户级吊销**:`users.tokens_invalid_before` 列 + `decode_jwt_token` 校验。覆盖:用户主动登出所有设备 / 改密码 / 重置密码 / 管理员强制踢人 4 个入口
- **审计查询接口**:`GET /api/admin/audit-log?action=...&limit=...` 给前端用(前端待开发),limit cap 500

### 测试覆盖增长
- 38 例(昨晚)→ 58 例(今天),新增 20 例
- 新增分类:扣费原子性 / 余额边界 / 审计持久化 / 审计 e2e / token 吊销 7 场景 / 审计接口权限 + cap

### conftest 顺手 fix
- `reset_database` autouse fixture 改为依赖 `app` fixture,纯函数测试(不用 client 的)也能拿到 schema(之前会 "no such table: users" 直接挂)

### 决策记录(今天追加)
- 2026-04-26:JWT 吊销选**用户级**(`tokens_invalid_before`)而非 jti 黑名单 — 简单 + decode 只多 1 次小查询 + 无 Redis 依赖。代价:登出一台 = 登出所有设备(UX 折中,可接受)
- 2026-04-26:扣费竞态修复同时把 `check_user_credits` 预检删掉 — 预检不仅多余还增加竞态窗口,SQL `WHERE` 自带原子保证
- 2026-04-26:审计日志 `details` 字段用 JSON TEXT 不做强 schema — 不同 action 字段不一致,JSON 自由结构最适合扩展;严格 schema 等 Phase 2 迁 PostgreSQL 时再考虑(JSON 列原生支持)
- 2026-04-26:**服务降权暂缓** — 不是改 `User=ssp-app` 就完,要把项目从 `/root/ssp/` 移到 `/opt/ssp/`(因为 `/root/` 默认 700 ssp-app cd 不进),涉及改一堆引用 + 多次生产 reload + 4+ 小时,作为独立"专项工作日"不混进"快速增量"

### ⏸ 留给下次
- 服务降权(专项工作日,/root/ssp → /opt/ssp 大迁移)
- jti 黑名单 + 单设备 logout(本次只做用户级)
- ~~JWT refresh token + 短期 access token~~ ✅ 已落地基础设施(2026-04-26 晚)
- Sentry / Prometheus 接入(trace_id 基础已就位 ✅)
- 微信支付正式接入(替换"截图人工入账")

## 2026-04-26 晚(继续干)

### ✅ 后端 3 件
- **扩大审计覆盖**:`payment.py::confirm_order`(管理员手动入账 — 合规重点)+ `admin.py::reset_model`(熔断器重置)接入审计钩子
- **trace_id middleware**:每 HTTP 请求生成 12 位短 UUID 写 `X-Request-ID` 响应头 + 请求级日志(method/path/status/duration/trace_id);上游 X-Request-ID 自动复用,串成链路。**为接 Sentry / ELK 做基础设施**(可按 trace_id 串调用链)
- **JWT access/refresh 分离**:`create_access_token`(7 天)+ `create_refresh_token`(30 天)+ `type` 字段 + 双向 decode 拒绝(refresh 不能调业务、access 不能换 access)+ 用户级吊销同样杀 refresh + `/api/auth/refresh` 重写。前端切 refresh 流程后可把 access 缩到 1 小时(泄漏窗口缩短 168 倍)

### 测试覆盖再升一轮
- 60 例(午)→ 76 例(晚),今天总增 38 例
- 新增:audit confirm_order / reset_model 端到端 / RequestId 5 场景 / refresh access/refresh 双向 11 场景

### 决策记录(再追加)
- 2026-04-26:trace_id 选 12 位 UUID 短形式 + 优先复用上游 X-Request-ID — 短足够 + 跟网关/前端能串
- 2026-04-26:refresh 实现选"不轮换"(refresh 用到过期为止)— 比"每次 refresh 都换新 refresh"简单,降低实现复杂度;后续可加轮换 + 黑名单
- 2026-04-26:access 暂保持 7 天**不缩短** — 缩到 1 小时需要前端配合做 refresh 流程,本次只做后端基础设施,前端切换留下次

### 部署状态(2026-04-26 19:20)
- 主代码:`85d5419`
- 生产 active = blue(蓝绿一天内来回切了 2 次,验证 deploy.sh 健康)
- 4 ref 对齐于 `85d5419`(本地 + 远端 main + feat 双分支)

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
- 2026-04-26:阶段 3 验证 dev.db 时手敲路径漏 `backend/`,sqlite3 在错路径上默默建空文件,误判"备份是坏的",还 shred 销毁了证据。教训:**验证脚本输出时严格复制脚本打印的路径,不凭脑子重写**。

## 2026-04-25

- JWT_SECRET 轮换 + 旧 token 全失效验证通过 + i18n 重复 key 清理

## 待办

- 未来要做:rotate-key.sh 脚本化 key 轮换流程
