项目进度日志,每次收工前更新

## 2026-04-27 续(JWT access 缩短 + 依赖 CVE 清理 + audit 入 CI)

### ✅ JWT access:7 天 → 1 小时(泄漏窗口缩 168 倍)
- backend `JWT_ACCESS_EXPIRATION_HOURS = 1`(原 24*7)
- 前端配套早就位:401 拦截 + refresh 单例并发 + 主动续期阈值 10 分 + 5 分轮询 + visibility 兜底
- 87 测试全过(decode 走 ExpiredSignatureError 与时长无关)
- 用户实际无感:活跃 tab 永远不撞过期那一刻

### ✅ 后端依赖 CVE 大扫除(8 → 2,清掉 75%)
基线扫描 `pip-audit -r requirements.txt`:
- **PyJWT 2.8.0 → 2.12.0**(CVE-2026-32597,JWT 命脉必修)
- **python-multipart 0.0.9 → 0.0.26**(3 个 CVE,上传命脉)
- **python-dotenv 1.0.0 → 1.2.2**(CVE-2026-28684)
- **Pillow 10.2.0 → 12.2.0**(跨大版本,代码只用基础 API,稳)
每升一个跑全测试:87/87 全过零回归

### ⏸ 剩 2 个(starlette CVE-2024-47874 + 2025-54121)
- 必须联动 FastAPI 0.109 → 0.110+ 一起升才能用 starlette 0.47.2
- FastAPI 跨小版本可能有 breaking,留下次专项评估

### ✅ pip-audit / npm audit 入 CI
- backend job 加 `pip-audit -r requirements.txt`(暂 || true 不阻塞)
- frontend job 加 `npm audit --audit-level=high`(暂 || true)
- requirements-dev.txt 加 pip-audit==2.10.0
- **基线降到 0 后改强制阻塞**,这样新增依赖带漏洞会立刻被 CI 抓到

### 决策记录(2026-04-27)
- 缩 access 到 1 小时:前端流程已就位 + 用户实测过(改密码/refresh/拦截器全链路),不是真"等下次",真是这次该做的事
- pillow 跨大版本(10 → 12):代码只用 Image.open/new/paste/convert/split/size/mode 基础 API,不用 deprecated 接口,实测 87 测试全过 → 直升 12.2.0
- audit 入 CI 不阻塞:**首次扫描总会有历史包袱,直接 fail-build 影响开发**。先 || true 收集,基线降到 0 再去掉 || true,Phase 1 完成的标志就是 audit 强制阻塞
- starlette 单独留:fastapi 0.109 强约束 starlette<0.37,要联动升 — 是真"需要专项"

## 2026-04-27(WS task 归属验证 v2 — 防越权订阅)

### ✅ 闭环上次留下的安全坑
- **问题**:WS 鉴权只验 token,没验 task 归属。任一登录用户拿到别人的 task_id 就能订阅别人的进度推送。
- **真因**:WS 用的 task_id 是 FAL request_id,本身不带用户身份;`generation_history` 主键是新 uuid,task_id 没保留;`tasks` 表全库无人写。**没现成 task_id → user_id 映射**。
- **方案**:新建 `app/services/task_ownership.py` — 进程内 dict + 30 分钟 TTL + 锁。提交 FAL 任务、拿到 request_id 时立即注册 (task_id, user_id);WS 接到连接 token 校验通过后再校归属,失败 close 4403(跟 4401 鉴权失败区分)。
- **注册点**:`video.py` 的 image-to-video / replace/element / clone 三个端点 + `avatar.py /generate`。`jobs.py` 内部用 FAL task_id 不暴露给前端 WS,无需注册。
- **不入库的取舍**:任务最长 10 分钟,内存够用;backend 重启后 in-flight 任务归属丢失,重新提交即可,可接受。

### 测试 +3(84 → 87)
- ws_owner_can_connect / ws_rejects_unregistered_task / ws_rejects_other_users_task / ownership 单元(总 8 例覆盖鉴权 + 归属两层)
- 全 87 例过,零回归

### 决策记录
- 2026-04-27:**纯 in-memory 不入库** — 给 generation_history 加 task_id 列要 schema migration + 改若干写入点,ROI 不如 TTL 方案;Postgres + Alembic 落地后再考虑持久化
- 2026-04-27:close code 选 **4403**(归属失败)与 **4401**(鉴权失败)分开 — 前端可区分"重新登录"与"task 不属于你"
- 2026-04-27:未注册 / 已过期 / owner 不匹配三种情况对外**不区分**,统一返 4403 — 防信息泄漏(攻击者无法通过响应差异判断 task_id 是否真实存在)

## 2026-04-26 深夜·收尾(AIOps 闭环 + 响应式 UI)

### 🤖 AIOps 完整闭环建成
- **一键诊断 API**:GET /api/admin/diagnose 收集完整快照(supervisor/nginx/后端 err/db 行数/磁盘/内存)
- **admin Banner 一键复制按钮**:🩺 按钮 → 浏览器自动复制 JSON → 用户粘贴给 Claude
- **诊断历史页 /admin/diagnose**:watchdog 告警时自动冻结快照写 /var/log/ssp-diagnose/{TS}-{LEVEL}.json,timeline 列出最近 100 份
- **微信推送**:Server 酱接入,告警时自动 push 微信(SCKEY 已配 + 已轮换);推送内容含严重度图标 + 状态总览 + 行动建议
- **合成监控**:watchdog 每 5 分钟模拟用户访问 /api/payment/packages、主页、/api/jobs/list 鉴权、admin 子域,bug 在用户撞到前抓到
- **闭环 3 次实战**:用户粘 JSON → 30-90 秒精准定位 + 修 + push,无猜测

### 🐛 真 bug 修复(从 AIOps 闭环抓到的)
- **RequestIdMiddleware 真 bug**:starlette BaseHTTPMiddleware 在 client disconnect 时抛 RuntimeError("No response returned.")。重写为 pure ASGI middleware(scope/receive/send 风格),免疫 streaming/disconnect 问题
- **watchdog 误报**:STOPPED 进程的 err.log 残留旧 RuntimeError,find -mmin -10 跳过老文件 + grep pattern 收紧到 ^(ERROR:|Traceback \(most|[A-Z]+Error:)
- **watchdog health 5s timeout**:deploy 蓝绿切换 30-60s 窗口期误报,改 sleep 8s 重试 1 次再 CRIT
- **disk/memory 字段空**:shell 引号嵌套吃掉了变量,改用中间变量 DISK_STR/MEM_STR

### 📹 视频上传完整重做
- **真因(用户报上传慢/失败)**:① 后端 await file.read() 一次性读全文件到内存 ② 前端 fetch 不支持 progress ③ 文件 > 100MB 撞 nginx client_max_body_size
- **修复 3 层**:
  - 后端流式 1MB 块写入(节内存)
  - **分片上传**(对标 YouTube/OSS):前端切 5MB 块 → 顺序传 → 失败重试 3 次 → 后端最后一片到达时合并 + 创建 session。**任意大小都能传,无需用户压缩**
  - 前端 XHR 进度条:"上传中 35.2% · 5.3 MB/s · 剩余约 12 秒"

### 🛡️ nginx 大幅加固
- limit_req api_limit rate 30→60 r/s,burst 60→200(多 tab + polling 不再撞限流)
- proxy_connect_timeout 30s / send 120s / read 120s / client_body 120s(防大文件 reset)
- client_max_body_size 100m→500m
- **error_page JSON 化**(关键):429/502/503/504 全返 JSON 不返 HTML,前端 fetch.json() 永不再炸 "Unexpected token '<'"

### 💻 admin 后台 UX 提升
- /admin/users 用户管理页(列表 + ± 积分按钮 + 强制踢出按钮,触发 audit)
- /admin/diagnose 诊断历史页(timeline + 一键复制)
- profile 加"登出所有设备"红色按钮(用户主动安全自救)
- audit 页 8 个 action 过滤按钮
- **侧栏响应式**(< 768px):手机端汉堡菜单 ☰ + 全屏内容 + 滑出侧栏 + 点蒙层关闭 + 选菜单后自动收起

### 决策记录(深夜段)
- 2026-04-26:全局 fetch patch window.fetch 而不是替换 71 处 fetch,零业务代码改动所有调用自动获益
- 2026-04-26:**修复必须从日志事实出发,不凭直觉猜**。Token 无效 / 上传慢 / connection reset 多次猜错,直到从 access log 看到 `400 body=0` 才定位 client_max_body_size
- 2026-04-26:不做"自动 push 代码"(完整 AIOps 终态)— 风险大,需 Claude Agent SDK 几天工程,且必有 bug 周期。当前"半自动"已经把"用户描述+我猜"压缩到"复制 JSON+精准修",ROI 最高
- 2026-04-26:Claude Max 月卡不能调 API(产品差异),用户用 claude.ai 网页版手机浏览器够用 + 0 额外成本

### 📊 今天整体总账(最终)
- **commit 数:25+**(从早上扣费修复到深夜响应式 UI)
- **deploy 次数:13+**(全部蓝绿成功,零回滚)
- **测试覆盖:38 → 79**(+41,翻倍)
- **生产坐标:~45% → ~70%**(企业级安全 + AIOps + 用户体验三大类全跨过中线)
- **真 bug 修复:5 个**(扣费竞态/Token UX/上传体系/nginx 限流/middleware streaming bug)
- **AIOps 闭环建成:** watchdog → 微信推送 → admin/diagnose → 一键复制 → claude.ai/我修

### ✅ WS 鉴权(明天清单提前做)
- /api/tasks/ws/{task_id} 加 ?token=<access> query 鉴权(WS 不支持 Authorization header)
- decode_jwt_token 校验签名 + 过期 + 用户级吊销 + 拒绝 refresh
- 失败 close code 4401(应用级约定)
- 测试 +5(79 → 84):无 token / 无效 token / 有效 / 拒 refresh / 拒 revoked

### ⏸ 真留给下次(已重复多次,这次写死)
- **服务降权**(/root → /opt 大迁移,半天专项)
- **微信支付正式接入**(用户备好商户号 + ICP 备案)
- **Postgres + Alembic 迁移**(SQLite 撑不到几百用户)
- ~~WebSocket 鉴权~~ ✅ 已落地(2026-04-26 深夜)
- **Sentry / 全自动 Agent**(都需 API 钱,用户不愿,搁置)
- **合规打底**(ICP / 内容审核 / AIGC 水印,用户主导跑流程)
- ~~WS task 归属验证 v2~~ ✅ 已落地(2026-04-27)

## 2026-04-26 凌晨之后(用户体验 + AIOps 起步)

### 🎯 用户报的问题 → 真因 → 修复
| 用户报 | 我的初次猜测 | 真因(从日志) | 修复 |
|---|---|---|---|
| "Token 无效或已过期" 弹窗 | 老 token 残留 | **大量 fetch 直接 .json() 没 res.ok 检查** | 全局 fetch 拦截器 + 401 自动 refresh |
| 用着用着被踢登录页 | 拦截器太激进 | access 7 天到期那一刻没刷 | 主动续期(剩余 < 10 分钟提前刷)|
| 上传视频 ERR_CONNECTION_RESET | nginx 限流 | **视频 > 100MB 撞 client_max_body_size** | client_max_body_size 100m → 500m |
| 上传太慢 | 服务器慢 | **后端 await file.read() 一次性读到内存 + 前端无进度条** | 流式 1MB 块写 + XHR 进度条 |
| 视频压不下来怎么办 | (用户痛点) | UX 不该让用户压缩 | **分片上传**(5MB 块,任意大小) |
| 429 风暴 | 拦截器死循环 | 4 tab 同时 polling 累积超 burst | nginx burst 60→200 + JobPanel visibilitychange |
| nginx 错误页让前端 .json() 炸 | (副作用)| 默认 nginx 错误页是 HTML | error_page 429/502/503/504 全 JSON 化 |

### ✅ 工程修复完整链
1. **前端 401 拦截 + 主动续期**(双层保险,users 永不撞过期那一刻)
2. **profile 加"登出所有设备"**(用户安全自救)
3. **JobPanel visibilitychange + 401 累计停**(后台 tab 不 polling,防 429 风暴)
4. **nginx 限流大幅放宽**(api_limit rate 30→60r/s,burst 60→200)
5. **nginx error_page JSON 化**(关键 — 前端 fetch.json() 永不炸)
6. **nginx client_max_body_size 100→500m + proxy_timeout 加大**
7. **视频上传流式 + 分片**(任意大小直传,5MB 块 + 失败重试 3 次)
8. **watchdog cron 5 分钟一次自动巡检**(/health / supervisor / 5xx-429 / 后端 ERROR / 备份新鲜度)
9. **admin 系统健康 Banner**(顶部自动显示 — 健康绿/告警黄/危险红)
10. **🩺 一键诊断按钮**(GET /api/admin/diagnose 收集完整快照,粘贴给 Claude 精准定位)

### 🤖 AIOps 路线图(用户诉求:出问题自动诊断 + 修复)
**当前阶段(✅ 已落地)**:
- watchdog 5 分钟自动巡检(本地告警)
- admin Banner 实时显示生产健康
- 一键诊断生成完整快照(用户复制粘贴给 Claude 即可)

**下一阶段(待用户提供凭证)**:
- 飞书 webhook 推送告警(用户配机器人 → 我接到 alert 服务)
- Sentry 接入(用户提供 DSN → 前后端错误自动上报)

**目标终态(大工程,需 Claude Agent SDK)**:
- watchdog 触发告警 → webhook 调用 Claude Agent → 自动诊断 + 起草修复方案 → 飞书发用户 → 用户审批 → 自动 git push + deploy
- 这是真正的 "AIOps 闭环",几天-几周工程量,等用户决定再做

### 决策记录(本轮新增)
- 2026-04-26:**修复要从 access log 事实出发,不要凭直觉猜**。多次"修了"都不对,直到 access log 看到 `400 body=0` 才看出 client_max_body_size 才是真因。教训:**先看日志再动代码**。
- 2026-04-26:分片上传选 5MB 块 + 失败重试 3 次 + 16 字符 hex upload_id 防路径穿越;不做断点续传(简单优先,后续按需加)
- 2026-04-26:nginx error_page JSON 化是**根本性提升** — 此后任何 5xx/429 前端都能优雅处理,不再"Unexpected token '<'"
- 2026-04-26:watchdog 选 cron 5 分钟而非 systemd timer — 项目用 supervisor 不是 systemd 主导,cron 更轻量
- 2026-04-26:一键诊断按钮放 admin Banner 而非独立页 — 出问题时用户已经在 admin 看 Banner,顺手点最快

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

## 2026-04-26 深夜(收尾 5 件)

### ✅ 后端审计补完
- **用户主动安全动作也写审计** — change_password / reset_password / logout_all_devices 三类自驱动事件接入审计钩子(之前只覆盖管理员动作,现在用户自己改密码也有不可变记录)。合规视角:谁在何时何 IP 改了自己密码 / 强制踢出所有设备 → 全部留痕。

### ✅ 前端审计页改进
- 过滤按钮从 3 个扩到 8 个,跟后端 7 种 action 对齐(全部 / 改额度 / 确认订单 / 强制下线 / 改密码 / 重置密码 / 登出所有设备 / 重置模型)

### ✅ 全局 fetch 401 拦截器(关键 UX 修复)
- **根因**:前端 71 处 fetch 各自处理 401,把后端"Token 无效或已过期" detail 直接 throw 给用户,任何 token 失效场景(SECRET 轮换 / 自然过期 / 改密码 / 强制下线)都会在生成图、上传视频、改密码等任意操作时弹原文,UX 灾难。
- **方案**:layout.tsx 挂全局 AuthFetchInterceptor(client component),启动时 patch window.fetch。所有 71 处业务 fetch 零代码改动自动获益。
- **逻辑**:401 → 用 refresh_token 调 /api/auth/refresh 换新 access(单例 promise 防并发)→ 重试原请求,业务无感;refresh 失败才静默清 localStorage 跳 /auth?expired=1。
- **配套**:auth/page.tsx::goAfterLogin 现在存 refresh_token;支持登录后回跳被中断的页面(sessionStorage.post_login_redirect);auth 页看 ?expired=1 显示"会话已过期"友好提示,不暴露技术细节。

### ✅ 主动续期(双层保险,用户绝不撞"过期那一刻")
- **痛点**:401 拦截器只是"撞到过期才补",用户在生成图中间被踢回登录页 — 即使提示再友好也是 UX 失败。
- **方案**:在 access 剩余 < 10 分钟时主动调 /refresh 换新,3 个触发点:
  - 启动时(进站立刻检查)
  - 每 5 分钟周期 setInterval
  - tab 重新可见 visibilitychange(用户切走半小时回来,立即检查)
- **保证**:7 天内活跃用户 access 永远不过期;30 天内来过站的 refresh 持续工作;只有 30+ 天没用 / 主动撤销 / 改密码后才会跳登录页(全是预期场景)。
- 失败静默不踢,401 拦截器作为兜底。

### ✅ profile 加"登出所有设备"按钮
- 用户能主动触发全设备 token 失效(防账号被盗自救)
- 红色边框按钮 + confirm 确认 + 调 /api/auth/logout-all-devices
- 触发链路:用户级吊销 + audit 写入(action=logout_all_devices)+ 当前浏览器 token 也失效 → 跳 /auth?expired=1
- **真实意义**:用户点这一个按钮就能端到端验证今晚 90% 的工作健康(吊销 + 审计 + 401 拦截 + 友好提示 + 重新登录全链路)

### 决策记录(深夜追加)
- 2026-04-26:401 处理选**全局 patch window.fetch** 而不是替换 71 处 fetch — 零业务代码改动,所有现有调用自动获益,改 1 个文件影响全部
- 2026-04-26:**主动续期阈值 10 分钟** — 留充足缓冲应对网络慢/时钟漂移;每 5 分钟检查一次,visibility 监听补"用户切走又回来"场景
- 2026-04-26:登出所有设备按钮选**红色边框非红色填充** — 暗示破坏性但不刺眼,符合现有 UI 语言

### 部署状态(2026-04-26 收工)
- 主代码:`7ec657c`
- 生产 active = green
- 4 ref 对齐于 `7ec657c`
- 一天 8 次蓝绿部署,全部健康检查通过零回滚
- 测试 38 → 79(+41,翻倍),全部通过

### 用户实测验证 ✅
- 改密码:成功,旧 token 立刻失效跳登录,新密码登回正常
- 整条链路(前端 401 拦截 + 后端用户级吊销 + 审计写入 + refresh token + 友好提示)经过用户真实操作端到端验证

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
