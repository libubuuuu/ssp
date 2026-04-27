项目进度日志,每次收工前更新

## 2026-04-27 三十九续(Playwright e2e 0→10 + PROGRESS.md 拆分归档)

### Playwright e2e MVP(P1 路线图最后一项)
- `npm install -D @playwright/test@^1.59.1`
- `playwright.config.ts`:`baseURL` 默认 localhost,可 `PLAYWRIGHT_BASE_URL=https://ailixiao.com` 切生产 / staging
- `e2e/smoke.spec.ts`:10 个金路径冒烟(浏览器 + API 双层)
  - 首页 / /auth / /digital-human "敬请期待" / /video/editor "敬请期待"(浏览器渲染)
  - /api/payment/packages 200 / /api/jobs/list 401
  - /uploads/no-ext 403(BUG-2 + 自审 #1 验证)
  - /api/auth/register 缺 code 422(P3-2 验证)
  - /api/digital-human/generate 401/503(P0 验证)
  - /api/auth/login-by-code 端点存在
- npm scripts:`npm run e2e`(本地)/ `npm run e2e:prod`(打生产)
- 装 chromium-headless-shell + system deps

### 实测 against https://ailixiao.com
**10/10 全过**(4.5s 总耗时,API 测试 ~10ms,页面测试 ~600ms)

### PROGRESS.md 拆分归档
1500 → 182 行(只留最近 5 续)+ `docs/PROGRESS-archive/2026-04.md` 归档 1440 行历史

### 决策记录
- **e2e 跑生产而非 staging** — 没 staging 环境;e2e 只读 + 公开端点不破坏数据
- **冒烟测试不写注册 → 登录 → 改密码全链路** — 那需要 email code 注入或邮件接收,工程量大;留下次 + 那时再加 staging
- **Playwright 装 chromium 不装 firefox/webkit** — 三套占空间 + 跑慢,先 1 浏览器够用
- **PLAYWRIGHT_BASE_URL 留 env 切换** — 不写死 localhost,生产/dev 切换零代码改

### 文档
- `docs/INDEX.md` 加 PROGRESS-archive 入口

## 2026-04-27 三十八续(admin.py 覆盖 30→41 + 顺手修 force-logout 返 success bool)

### 改动
新增 `test_admin.py` 测试 11 个(原 5 个 → 16 个),覆盖:
- `users-list`:管理员列出全部用户(数据库查询全路径)
- `adjust-credits`:边界(超过余额 floor 0)+ 不存在用户 404
- `force-logout`:成功踢人 + token 立即失效 + 不存在用户 404 + 非管理员 403
- `audit-log`:管理员限制 + 列表 + action 过滤 + limit 500 截断
- `diagnose-history`:权限 + 文件系统目录不存在仍 200 不抛

### 顺手修 force-logout 返 success 字段
P8 阶段 1 改 `invalidate_user_tokens` 返 int(原 bool)后,`admin.py force_logout` 把 int 当 bool 返了 `success: <ts>`(int)。前端 if 仍 truthy 但语义错。修:`success: True`,把 ts 移到 audit details 里方便事后追溯。

### 覆盖率
- admin.py:30% → **41%**(+11%)
- 整体:54% → **55%**
- 测试 257 → **268**(+11 此轮)
- 累计本会话:100 → 268(+168,2.68x)

### 不再继续补 admin
剩下未覆盖的 admin 路径(stats/queue-status/diagnose-snapshot/watchdog/upload-qr/tasks-recent)依赖外部状态(supervisor 命令 / 文件系统 / circuit_breaker 内存)— 测试 ROI 低,留下次专项。

## 2026-04-27 三十七续(jobs.py 覆盖率 48→91 / 核心 4 路径全达标)

### 完成 P7 最后一项
- `jobs.py`:48% → **91%**(+43%)
- 核心路径 4 个全部达 70% 标线:auth 91% / billing 91% / payment 98% / **jobs 91%** / decorators 98%(虽然不在原 4 但补了)

### 测试 +22(235 → **257**)
新文件 `test_jobs_internals.py`,直接调内部函数 + mock fal:
- `_module_from_type` 4 个变种 + unknown fallback
- `_save_jobs / _load_jobs` 持久化 + 损坏 JSON / 空文件 / 不存在 — 全 fallback 不抛
- `_run_image_job` simple + multi-reference + error 传播 + empty images raise
- `_run_video_job` 三个 type(i2v/edit/clone) + unknown raise + no task_id raise + completed/failed
- `_execute_job` happy path 写 history + failure 退还积分 + unknown type fail + missing job 静默 + 归档失败不影响主流程

### 修过程发现
**conftest 把 `_execute_job` 全局 noop'd 了**,我的内部测试直接调它得到 noop。修法:conftest 加 `jobs_module._execute_job_original = 原 fn` 保存,我的测试用 `_execute_job_original`。两者并存,端点测试继续走 noop,内部测试走真路径。

### 累计本会话(从 100 测试起)
- 测试 100 → **257**(+157,2.57x)
- 整体覆盖率 46% → **54%**(+8%)
- **核心 4 路径全达标**,P7 承诺 100% 兑现

### 决策记录
- **AsyncMock(asyncio.sleep)** — _run_video_job 内部 polling 5s × 120 次,测试不能等;monkeypatch sleep 让 polling 立即跑完
- **conftest 保留双轨** — `_execute_job_original` 给内部测试,`_execute_job` 给端点测试;不破坏老测试
- **不测真 FAL** — fal_service 单独 mock,不引入测试时网络依赖

## 2026-04-27 三十六续(覆盖率补齐 P7 承诺:decorators 27→98 / payment 50→98)

### 上轮 P7 承诺
> "decorators.py 27% → 70%(0.5h)— 扣费命脉单测"
> "payment.py 50% → 70%(1.5h)— confirm_order + 退款"

### 实际成绩(超额)
- `decorators.py`:27% → **98%**(+71%)
- `payment.py`:50% → **98%**(+48%)

### 测试 +28(207 → **235**)
**decorators.py 12 测试**(新文件 `test_decorators.py`):
- 未登录 401 / current_user 通过 kwargs 或 args dict 注入
- 余额不足 402 不扣
- 成功扣费 + 写 generation_history(含 description 提取 + module fallback)
- 非 dict 结果不报错不附 cost
- HTTPException 路径返还积分 + re-raise(400 透传)
- 普通 Exception 路径返还 + 转 500
- ValueError 大额(20)也返还
- get_user_credits 不存在用户返 0 / 真值

**payment.py 16 测试**(新文件 `test_payment.py`):
- /packages /credit-packs 公开列表
- 创建订单:套餐 / 充值包 / 无效 id 400 / 无效 type 400
- 查询订单:owner 200 / 别人 403 / 不存在 404
- /orders 列表用户严格隔离(A 看不到 B 的)
- /orders/{id}/confirm:非管理员 403 / 不存在 404 / 已确认 400 / 成功加积分
- /admin/orders:非管理员 403 / status 过滤

### 修过程发现
**循环导入:** `decorators.py` 第 8 行 `from ..api.auth import get_current_user` 实际未使用,但触发 `decorators → auth → api/__init__ → image → decorators` 循环。删了 import 注释说明"由 FastAPI Depends() 注入,不需要 import"。

### 整体覆盖率 46% → 52%
- 核心路径 4 个里 3 个达标(auth 91%, billing 91%, payment 98%, decorators 98%);剩 jobs.py 48%(异步路径,留下次)
- 总测试 159 → 235(本会话累计 +135 测试,从 100 起)

### 决策记录
- **decorators 单测直接 import** 而不是端点间接测 — 隔离逻辑,跑得快(0.04s/test)
- **delete dead import 而非加 lazy import** — 该 import 本来就没用,直接删干净
- **payment 测试不 mock 数据库** — `_register` + `set_role` 走真 DB,集成度更高发现真问题

## 2026-04-27 三十五续(再自审三个真问题 + JWT timing race 隐性 bug 修)

### 自审发现
1. 🟠 **nginx `/uploads/` 白名单漏 no-extension**:实测 `secret_no_ext` 文件返 200 而非 403
2. 🟡 **change_password 改完密码不 set 新 cookies** → 用户被踢回登录
3. 🟡 **docs 7 份散乱无索引**

### 修 #1:nginx 白名单严格化
- 原配置嵌套 location `~* \.(白名单)` + `~ /uploads/.*\.`,无扩展名漏过
- 改成扁平正则 + catch-all 403:
  ```
  location ~* ^/uploads/(?<asset>[^?]+\.(jpg|jpeg|png|webp|gif|mp4|webm|mov|mp3|wav|m4a))$ {
      alias /opt/ssp/uploads/$asset; expires 30d;
  }
  location /uploads/ { return 403; }
  ```
- 实测:no-ext → 403 ✓ / .sh → 403 ✓ / .bin → 403 ✓ / .jpg → 200 ✓ / 路径穿越 → 404

### 修 #2:change_password set 新 cookies + 修 timing race
**新功能**:改密成功后立即签新 access+refresh + set 新 cookies,**本设备无缝续登**(其他设备由 invalidate 踢)。

**修过程发现 JWT timing race**:测试合跑挂,旧 token 被 invalidate 后仍能 decode。原因:JWT `iat` 和 `tokens_invalid_before` 都是整秒,同秒发的 token decode 检查 `tokens_invalid_before > iat` 是 False(同值不大于),误判为有效。

**修法**:
- `invalidate_user_tokens` 改返 int 时间戳(原 bool),设 `tokens_invalid_before = int(time.time()) + 1`(严格大于现存 token iat)
- `create_access_token` / `create_refresh_token` 加可选参数 `iat_unix`,change_password 用 `iat_unix=invalidate_ts` 让新 token iat == tokens_invalid_before(decode `> iat` False → 通过)
- 同步改 `test_token_revoke` 适配新返回类型

### 测试 +2(205 → **207**)
- `test_change_password_set_new_cookies_seamless_login`:改密后 cookie 已 set,旧 token 401
- `test_change_password_invalidates_old_tokens`:关键安全断言 — 旧 token 立即失效

### #3:docs/INDEX.md
- 顶级文档(CLAUDE / RUNBOOK / PROGRESS)
- 用户操作 SOP 表(Sentry / CF / Redis / DR)
- 工程参考(P8 / COVERAGE)
- 场景导航("查 bug" / "做新功能" / "服务器出问题" / "用户报 bug" / "新接外部服务")

### 决策记录
- **invalidate ts 用 +1 不是当前秒** — 否则同秒 token 误判为有效;返回 ts 让 caller 协调新 token
- **create_access_token 加 iat_unix 不是 sleep** — 等 1 秒太蠢 + 阻塞;改 iat 直接绕过同秒碰撞
- **nginx 白名单改扁平正则** — 嵌套 location 难推理 + 漏 no-ext;扁平 + catch-all 403 心智简单
- **deploy 用 in-place restart 不走蓝绿** — 这次只改后端代码,5 秒重启 vs 30 秒蓝绿,影响小;蓝绿留给 schema 改动 / nginx 改动场景

### 已 deploy 进生产
- backend rsync + restart blue:200 OK
- nginx /uploads/ 严格白名单已 reload(同时清掉旧 nested location)

## 2026-04-27 三十四续(deploy 进生产 + CLAUDE.md 重写)

### Deploy 进生产(blue-green 30 秒)
17:18 → 19:46 累积 5 个 commit 上线:
- BUG-1 注册 IP 失败软配额 (`425f613`)
- BUG-2 媒体归档 (`7235071`)
- P5 Sentry 框架 (`ebb403d`)
- P6 CF-Connecting-IP 优先 (`8642f59`)
- P9 限流 Redis 后端 (`a8f34e4`)
- 隐藏雷 #1/#2/#3 (`d313b62` / `3437855` / `f96d951`)
- P8 阶段 1+2 (`d59aab3` / `3c09405`)
- P8 阶段 3 doc (`04acca6`)
- /login-by-code 修复 (`b4d8a7c`)
- media_archiver client 共享 (`9519265`)

实际操作:
- 19:46 备份 dev.db_20260427_194620.db
- rsync /root/ssp/backend → /opt/ssp/backend(排除 venv/db/logs)
- chown ssp-app
- bash /root/deploy.sh:active green → blue,30 秒切换
- 验证:公网 200 / supervisor blue RUNNING / register_ip_failure_log 表已建 / Sentry 跳过日志正常

### CLAUDE.md 重写(202 行,从 169 行)
旧版严重过时:User=root / systemd / /root/ssp/backend/dev.db / "Phase 1 服务降权 todo"。新版反映:
- 服务 supervisor + ssp-app 已降权
- /opt/ssp 是生产路径
- 一级差距从 6 项剩 4 项(降权 + 测试 + CI 完成)
- 新增 P8/P9/BUG-1/2/隐藏雷 1-3 路线
- "用户操作待办"清单 7 项

### 最终状态
- 4-ref 对齐 `9519265`(待 CLAUDE.md commit 后再齐)
- 测试 205 全过
- 生产已上线本轮所有修复
- 仅余 P8 阶段 3(30 天后)+ Phase 2 大工程 + 用户决定项


---

## 更早历史

三十三续及更老条目已归档到 [`docs/PROGRESS-archive/2026-04.md`](docs/PROGRESS-archive/2026-04.md)(1440 行,含 P0-P9 + BUG-1/2 + 隐藏雷 1-3 + P8 阶段 1-2 + 通宵交付 + 灾备 + AIOps 等)。
