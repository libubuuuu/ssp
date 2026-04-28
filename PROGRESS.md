项目进度日志,每次收工前更新

## 2026-04-28 五十九续(e2e 鉴权矩阵守门 +8 — 防回滚无意打开洞)

### 发现
扩 e2e 时确认前端基础已就位:`@playwright/test` 装好,`playwright.config.ts`
已配,`e2e/smoke.spec.ts` 9 条 baseline 在跑(打生产 https://ailixiao.com)。
之前 PROGRESS 标 "前端 e2e 0→1" 其实是"扩 0→**多**"。

### 扩 +8 条鉴权矩阵守门
直接守住五十+续做的 6 个真生产洞,401/200 断言:
| Test | 守的 bug |
|---|---|
| POST /api/products → 401 | 五十七续 OWASP 越权 |
| PUT /api/products/{id} → 401 | 五十七续 |
| DELETE /api/products/{id} → 401 | 五十七续 |
| GET /api/products → 200 | 反向:别一刀切关掉 public 列表 |
| GET /api/video/status/{id} → 401 | 五十四续 归属窥探 |
| POST /api/content/upload → 401 | 五十六续 OOM 守卫 |
| POST /api/content/enhance → 401 | 五十八续 |
| POST /api/image/inpaint → 401 | 五十八续 stub 防扫 |

### 18 全过
打生产环境 read-only(无 side effect)直接 4.6s 跑完。回滚或重 deploy
任何一条洞被无意打开 → 这个矩阵立刻 fail。

### 用法
```
cd frontend
PLAYWRIGHT_BASE_URL=https://ailixiao.com npm run e2e
# 或 npm run e2e:prod 同义
```

### 不本地跑
playwright 浏览器引擎已装(node_modules),但本地后端 dev mode 跑 form / auth flow
e2e 留下次专项(需要 backend mock email + sqlite 隔离)。本次只扩 read-only smoke,
零脏数据写到生产。

## 2026-04-28 五十八续(content/enhance + image/inpaint 加鉴权 — sweep 收尾)

### 收尾
五十七续做完 sweep 剩两条低优先级 endpoint(无外部 API 调用所以风险低,
但仍是 attack surface):
- `/api/content/enhance`:纯模板字符串拼接,匿名扫返大量营销文案 → 脏 log
- `/api/image/inpaint`:501 stub,匿名扫无功能但仍可用作探测

各 1 行加 `Depends(get_current_user)`。

### 测试 +4(363 → 367)
- enhance 401 / 200(模板返回校 title/scenes)
- inpaint 401 / 501(鉴权放行后仍是 stub)
- /api/image/models 保持 public 注释化(实现耦合 fal init 不验)

### 已 deploy 进生产 ✅
蓝绿 green → blue。生产验:
- `curl POST /api/content/enhance`(无 token)→ **401**(之前 200)
- `curl POST /api/image/inpaint`(无 token)→ **401**(之前直接 501)

### sweep 完整 audit 清单(全部已修)
| Endpoint | 修法 |
|---|---|
| /api/products POST/PUT/DELETE | 五十七续:鉴权 + 商家归属 |
| /api/video/status/{id} | 五十四续:鉴权 + task_ownership |
| /api/content/upload | 五十六续:upload_guard |
| /api/ad-video/upload/image | 五十三续:upload_guard |
| /api/content/enhance | **五十八续:鉴权** |
| /api/image/inpaint | **五十八续:鉴权** |

剩余 public endpoints 已 audit 确认应该 public:auth/* 所有入口、
payment 价格列表、products GET、image/models、avatar/voice/presets。

## 2026-04-28 五十七续(products CUD 加鉴权 — 🚨 OWASP 越权大洞)

### 系统性 sweep 挖到的真严重 bug
做完五十六续后扫所有 endpoint 鉴权矩阵,挖出 **products CUD 完全裸跑**:
- `POST /api/products`:任何匿名脚本给任意 merchant 注入产品(merchant_id 自填)
- `PUT /api/products/{id}`:任意改价格 / 上下架 / 图片
- `DELETE /api/products/{id}`:任意删

OWASP Top 10 - Broken Access Control。前端 merchant 页面是 UI shell 没接通,
但裸跑后端就是被扫攻击面 → 库内任意写。

### 修
- `_assert_owns_merchant(merchant_id, user)` helper:admin 跨商家 OK,
  普通 user 必须是 `merchants.user_id` 对应 owner
- POST 校 `product.merchant_id ↔ user`
- PUT/DELETE 先 SELECT product 拿 merchant_id 再校
- merchant / product 不存在统一 404,不区分"非 owner"vs"不存在"防泄漏

### 测试 +13(350 → 363)
矩阵覆盖 401/403/404/200,含:owner / 非 owner / admin 跨商家 / 不存在 merchant /
删后 GET 404 / list 仍 public。

### 已 deploy 进生产 ✅
- 蓝绿 blue → green
- 验证生产 `curl POST /api/products`(无 token)→ 401(之前 200 注入)
- list 仍 public 200(电商展示场景不变)

### sweep 剩余低优先级
- `/api/content/enhance` 无鉴权但纯模板 string,DoS 风险低
- `/api/image/inpaint` 501 stub
- 一致都加 require_login 是 cleanup,不紧急

## 2026-04-28 五十六续(/api/content/upload 接 upload_guard — 自审 sweep 收 OOM 同源)

### 自审 sweep 发现
做完五十三续 ad-video upload guard 后,扫了一遍 `app/api/` 看还有没有同源:
- ✅ video.py / studio / ad-video — 已接 upload_guard
- 🔴 **content.py /upload** — 有 `Depends(get_current_user)` 但无 size guard,
  `await file.read()` 直接读到内存。**登录用户**传 500MB(nginx 上限)→ OOM
- 🟡 content.py /enhance:无鉴权但纯模板 string replacement 不调外部 API,优先级低留下次
- 🟡 image.py /inpaint:501 stub(P0 紧扫已 503 化空壳),优先级低

### 修
接 `upload_guard.read_bounded(10MB IMAGE_MIMES)`,模式跟其他 upload 端点完全一致。

### 测试 +4(346 → 350)
- 401 匿名拒
- 413 oversize 拒
- 415 错 MIME 拒
- 合规通过(fal_client mock)

### 已 deploy 进生产 ✅
蓝绿 green → blue。生产 `curl POST /api/content/upload`(无 token)→ 401,符合预期。

## 2026-04-28 五十五续(jobs.py / payment.py 测试覆盖率补漏 +11)

Phase 1 明确 TODO:覆盖率到 70%。挑了既存测试文件里漏掉的明显分支补。

**jobs.py +7:**
- get_job owner happy(含 type/cost/status/title 字段断言)+ 不存在 404
- delete_job owner happy(删后 GET 404 验证)+ 不存在 404
- list_jobs 空数组 + 按 created_at 倒序
- submit cost==0 路径(monkeypatch 免费 type)

**payment.py +4:**
- admin_list_orders status=all(paid + pending 都返)
- admin_list_orders 空数组
- admin_list_orders LEFT JOIN user_email + user_name 字段断言
- confirm_order 写 audit_log(合规重点端到端验证)

**测试统计:** 335 → **346**(+11)

### 不 deploy
纯测试改动,业务代码零改动。commit + push + main fast-forward 收尾。

### Git 4-ref 对齐
HEAD = origin/feat = main = origin/main = `d5543b3`

## 2026-04-28 五十四续(/api/video/status 加鉴权 — 收匿名归属窥探洞)

### 真隐私洞
`/api/video/status/{task_id}` 之前 `def get_task_status(task_id: str)` 完全匿名,
任意人猜对 fal task_id(UUID 难猜但不绝对)就能拿到归档视频 URL。
归档地址 `/uploads/<user>/...` 暴露 → 用户视频隐私泄漏。

### 修
- `Depends(get_current_user)` + `task_ownership.verify(task_id, user_id)`:
  - 401:未登录 / token 失效
  - 403:登录但非 owner(包括"瞎猜 task_id 没注册过"也走这条,不区分泄信息)
- 失败退款逻辑(refund_tracker)保持不变

### 前端零改动
AuthFetchInterceptor(P8 阶段 2)已 patch 所有 `/api/*` 自动 `credentials:include`,
登录用户 fetch 走 cookie 鉴权 → 后端 get_current_user 拿 cookie token → 通过。
旧用户没 cookie 时 401 → AuthFetchInterceptor 走 /refresh → 新 cookie → 重试。

### 测试 +5(330 → 335)
- 未登录 401
- 登录但非 owner 403
- 瞎猜 task_id 没注册 403
- owner + processing 透传
- owner + failed 触发 refund(余额涨)

### 已 deploy 进生产 ✅
蓝绿 blue → green。生产 `curl /api/video/status/random` 现在返 401,之前是 200 泄漏。

## 2026-04-28 五十三续(ad-video upload 接 upload_guard — 收 OOM 攻击面 + 模式统一)

### 47 续遗留
`/api/ad-video/upload/image` 完全无 size/MIME 守卫,`await file.read()` 直接读
500MB(nginx 上限)到内存 → backend OOM。另一处 /analyze 已有 inline 10MB + MIME
检查但模式跟其他端点不一致(48 续 upload_guard 没扫到这边)。

### 修
两处统一接 `upload_guard.read_bounded`:
- /analyze:删 inline `if len > 10MB raise 400`,换 read_bounded(超 → 413,错 MIME → 415)
- /upload/image:加守卫,从此跟 video.py / studio 一致

### 测试 +3(327 → 330)
既存 oversize/MIME 测试改 status_code 期望(400 → 413/415);+3 新覆盖
/upload/image(oversize/wrong-mime/valid-pass)。

### 已 deploy 进生产 ✅
蓝绿 green → blue ~30s。这次 chown 提前(SOP 已固化),无 spawn error。

## 2026-04-28 五十二续(refund_tracker 改 SQLite 持久化 — 收 v1 进程内存 limitation)

### 为什么
五十一续遗留 limitation:
- 进程内存 dict → backend 重启丢退款记录 → 异步失败任务永远不退
- multi-worker 时各 worker 一份,扩 worker 后 register/refund 可能不在同一 worker

alembic 脚手架 47 续刚就位,顺手用上。

### v2 设计
**新表 `pending_refunds`(PK=task_id):**
- `INSERT OR IGNORE` 防重复 register
- `try_refund` = `UPDATE WHERE refunded=0 AND registered_at>=cutoff` — SQL 层原子,
  rowcount==1 才真退,**多 tab / HTTP+WS / multi-worker 并发都靠 SQL 锁幂等**
- 惰性 GC(1/50 写入概率),删 30min 过期 entries
- 双轨:init_db() CREATE TABLE IF NOT EXISTS(测试 + 重启自愈)+ alembic migration `76b4501342c9`(schema 漂移管理)

### API 兼容
register / try_refund / peek / _clear_for_test 签名不变,五十一续既存 9 测试**零改动**通过。
+1 新测试 `test_register_persists_to_db` 直接 SQL 查询验证落表。

### 已 deploy 进生产 ✅
- rsync app/ + alembic/ + chown(踩坑后已成 SOP)
- 蓝绿 blue → green ~30s
- `alembic stamp 76b4501342c9`:生产 dev.db schema_version 24bf7cbb36fb → 76b4501342c9
- `sqlite3 dev.db ".schema pending_refunds"` 验证表 + 索引就位
- 生产 health 200

### 测试统计
326(五十一续) → **327**(+1)

## 2026-04-28 五十一续(异步退款必落地 — refund_tracker 接通 4 类异步任务)

### 自审挖出的真生产 bug
做完五十续(batch-status 双退竞态)后审"还有没有同源退款洞",找到一处更狠的:
**require_credits 装饰器扣费 + fal 异步任务失败 → 用户钱不退**。

具体死的退款逻辑:
- `tasks.py:50` `SELECT generation_history WHERE id = fal_task_id` —
  但 generation_history 主键 = 装饰器自生成的 uuid4 ≠ fal task_id → 永远查不到 → 永远不退
- `_poll_fal_task` WS 后台 polling 检测到 failed 直接关连接,**根本没退款逻辑**
- `video.py /status/{task_id}` 完全无退款

后果:用户做 image-to-video(10) / video/replace(15) / video/clone(20) /
avatar/generate(10),fal 异步失败 = 白丢钱。

### 修
**新增 `services/refund_tracker.py`** — 内存级 register/try_refund + 30min TTL
- `pop` 是 dict 原子操作,确保只退一次,多 tab / HTTP+WS 双轨触发都幂等
- 与既存 `task_ownership.py` 同 pattern(threading.Lock + TTL)

**接通 4 处:**
| 文件 | 改动 |
|---|---|
| decorators.py | require_credits 扣费成功 + result 含 task_id → 自动 register |
| tasks.py /status/{id} | 删死 SELECT,接 try_refund |
| tasks.py _poll_fal_task | failed 时调 try_refund + 推 refunded 给前端 |
| video.py /status/{id} | 接 try_refund(前端 video/replace、clone 在用) |
| avatar.py /generate | fal async 任务 register |

### 测试 +9(317 → **326**)
- 基本流程:register → try_refund 成功 / 未注册返 0 / 二次返 0
- 参数无效 noop(空 task_id / 空 user / cost ≤ 0)
- peek 不消费
- **并发幂等**:10 线程 Barrier 同时 try_refund,断言**恰好一次** = cost
- TTL 过期返 0
- 装饰器集成:async 任务 register / 同步任务不 register

### 限制(代码注释化)
- 进程级 dict,backend 重启 → 退款记录丢。失败任务不退,需人工补
- multi-worker 时各 worker 一份。当前 uvicorn 单 worker,acceptable
- 后续切 SQLite 表持久化(可走 alembic 迁移,五十续刚就位)

### 已 deploy 进生产 ✅
五十 + 五十一两 commit 一起切,蓝绿 green → blue,~30s。
**踩坑:** rsync 用 root 跑导致 /opt/ssp/backend/app 文件 owner 变 root,
backend 跑 ssp-app 拿不到 logs/ 写权限(EXITED + spawn error)。
**修法:** chown -R ssp-app:ssp-app /opt/ssp/backend/app 后重跑 deploy → 通过。

**Lesson learned**:rsync 后必跟 chown,或用 `--chown=ssp-app:ssp-app`(rsync 3.1+)。
下次 deploy.sh 加这一步。

## 2026-04-28 五十续(/batch-status 加 asyncio.Lock — 防多 tab 双退)

### 真竞态
`video_studio.py /batch-status` 失败段退款的 `_refund_if_needed`:
1. 检 `seg.get("refunded")` 为 False
2. add_credits(user_id, cost)
3. 标 `seg["refunded"] = True`

**check-then-set 非原子**。同 user 多 tab 各自 startPolling 调 batch-status,
两个协程同时进入 1.,各自看到 False → 都 add_credits → **双退**。

### 修
- 新增 `_SESSION_LOCKS: dict[session_id, asyncio.Lock]`
- `_refund_if_needed` + `_save_tasks` 全部搬进 `async with lock`
- 注释说明:重启后 refunded 标记已持久化(sessions.json),锁丢不会双退过去已退的

### 测试 +1
- `test_batch_status_concurrent_polls_no_double_refund`:asyncio.gather + httpx.ASGITransport 启 2 并发,断言总退款 = 30 而非 60

## 2026-04-28 四十九续(管理员强制 2FA — scaffolding 就位,默认关)

### 为什么
- 管理员密码失守 = 全平台沦陷(改余额 / 踢人 / 看 audit / 全设备登出)
- 行业标准:GitHub / AWS root / Google Workspace 都强制管理员 2FA
- 现状:基础设施已有(`/2fa/setup`/`enable`),admin 可选不启 = 等于没用
- 合规要求:Phase 4 ICP 网安审查通常要管理后台必须 2FA,提前备好

### 设计(与 Sentry/CF/Redis 同 scaffolding pattern)
- 代码就位,环境开关 `ADMIN_2FA_REQUIRED` **默认 false**(用户 enroll 后再翻开)
- 顺手简化:17 处 inline `if role != admin` 收口到 `_check_admin_role`

### 后端
- `services/auth.py:get_user_by_id` 加 `totp_enabled` 字段
- `api/admin.py`:`_check_admin_role` 校验 role + (可选)2FA;`require_admin` Dep 走它;17 处 inline 替换
- `api/auth.py`:login 响应 user dict 加 totp_enabled(前端可读)
- 强制开时:无 2FA admin 拿 **403 + 结构化 detail** `{code, message, redirect}`

### 前端 admin/layout.tsx
- 顺手切 useLocalStorageItem + useIsMobile(消残存 set-state-in-effect)
- 加**琥珀色 2FA 引导横幅**:admin && !totp_enabled 时显示,"去启用 →" 直达 `/profile/2fa`
- 文案告知"未来 env 启用时会硬墙"

### 测试 +4(307 → 311)
| 用例 | 验证 |
|---|---|
| enforce_off_admin_without_2fa_passes | 默认关,无 2FA admin 通行 |
| enforce_on_admin_without_2fa_blocked | 开关,403 + 结构化 detail |
| enforce_on_admin_with_2fa_passes | 开关,已 enroll 通行 |
| doesnt_affect_non_admin | 普通用户仍普通 403(detail 是 str 不是 2FA dict) |

### 文档 `docs/ADMIN-2FA.md`
- 启用 SOP(enroll → 改 env → 重启 → 验证)
- **紧急救援**:锁外时 env 临时关 / SQL 直清 totp_secret
- 不要做的事:不存仓库 / 不强制全用户 / TOTP secret 必入密码管理器

### 已 deploy 进生产
(待执行 — 默认关,不影响现有 admin 访问)

## 2026-04-28 四十八续(/upload 端点加 size + MIME 守卫 — 防 OOM 攻击)

### 自审挖出的真生产风险
讨论"还差什么"时,我列了一份待补,用户点继续。我先核实发现"异地备份"已经做了(GitHub 私有仓库 + cron 03:15 加密推),那条不算 gap。

换一件真存在的 🔴 — `/upload/*` 端点 OOM 攻击隐患:
- nginx `client_max_body_size = 500MB`,后端无二次校验
- `/api/video/upload/image` 和 `/upload/video`:`await file.read()` 一次读到内存 → Pillow 加载 → 后端 OOM
- `/api/studio/upload`:流式落盘但**无 size 上限**,可写满磁盘
- 三个端点都没 MIME 校验,允许任意伪装

### 新增 `app/services/upload_guard.py`
- `read_bounded()` — 边读边累加字节,超限 raise 413
- `stream_bounded_to_path()` — 大文件流式落盘,超限**立刻终止 + 清部分文件**
- `_check_mime()` — 415 拦截非白名单 Content-Type
- IMAGE_MIMES / SHORT_VIDEO_MIMES / LONG_VIDEO_MIMES 集合(后者含 octet-stream 兼容 iOS Safari)

### 应用到 3 个端点
| 端点 | 限制 | MIME |
|---|---|---|
| /api/video/upload/image | 10MB | image/jpeg/png/webp/gif |
| /api/video/upload/video | 100MB | video/mp4/quicktime/webm/x-matroska |
| /api/studio/upload | 2GB | + octet-stream |

### 测试 +9(298 → 307)
read_bounded: 小 / 超限 413 / 错 MIME 415 / 空 / 边界等于
stream_bounded_to_path: 小 / 超限 413 + 清文件 / 错 MIME 415 / octet-stream OK

### 未覆盖
- `/api/studio/upload-chunk` 分片上传(已有 upload_id 格式校验 + 50GB 总上限,单 chunk 不紧急)
- `/api/ad-video/upload/image`(已有手动 10MB + MIME 但模式不一致,下次统一)

### 已 deploy 进生产
(待执行)

## 2026-04-28 四十七续(Phase 2 alembic 脚手架就位)

### 用户拍板
四十六续后我列了剩余真值得做的几件:lint 改 ROI 触底,Postgres 迁移最大,但需要拍板。用户选 A(alembic 脚手架,不切 Postgres)。

### 为什么这样做
- CLAUDE.md 一级差距 #1:数据库 SQLite 无迁移管理
- 切 Postgres 是 1-2 天体力活,先搭好 schema 管理基础设施 → 未来切的时候只剩"装驱动 + alembic upgrade + 数据迁移"
- 现在不切,**业务运行时 0 改动**,纯增量

### 脚手架内容
1. **`requirements.txt`** — alembic 1.18 + SQLAlchemy 2.0
2. **`backend/alembic/env.py`** — `DATABASE_URL` 优先(切 Postgres)/ `DATABASE_PATH` fallback(继续 SQLite)/ `render_as_batch=True`(SQLite ALTER 限制)
3. **第一份 migration `24bf7cbb36fb_initial_schema_mirror.py`** — 14 表 + 13 索引,完全镜像 `app/database.py:init_db()`,用 `sa.text("0")` 而非 `"0"` 防 Postgres 字符串化
4. **现有 dev.db**(本地 + 生产)`alembic stamp head` 标已迁
5. **`docs/POSTGRES-MIGRATION.md`** — 完整路径文档(现状 / 日常加列 / 切 Postgres 步骤 / 回滚 / TODO)
6. **`init_db()`** docstring 加双轨说明

### 验证
fresh DB `alembic upgrade head` 与 `init_db()` schema **功能等价**(FLOAT/REAL 同 affinity / 默认值引号 cosmetic / PK 隐含 NOT NULL — type behavior 完全一致)。

backend pytest 298/298 全过。

### 不在 scope(留切 Postgres 那天)
- 不引入 SQLAlchemy ORM,业务继续 sqlite3 直连
- 不写 SQLite→Postgres 数据迁移脚本
- tests 仍用 init_db()(快 + 隔离)
- `init_db()` 不改成 `alembic upgrade head`(避免 init/alembic 死循环 + 测试开销)

### 已 deploy 进生产
(脚手架是后端代码 + alembic_version 表多 1 行,无运行时影响。stamp 已对生产 dev.db 跑过)

## 2026-04-28 四十六续(exhaustive-deps 6→0 + alt-text 8→0)

### 收尾扫
四十五续后 lint 残留 79 个问题,挑高 ROI 的两类:
- **exhaustive-deps(6)** — stale closure 风险藏身处。函数定义在组件内每渲染重生,effect deps 不带它意味着 effect 永远拿首次 render 的 closure。当前没观察到真 stale bug,但 useCallback 包是定式
- **alt-text(8)** — a11y 真 win,屏幕阅读器无 alt 时读 src URL 给视障用户

### exhaustive-deps 6 → 0
| 文件 | 修法 |
|---|---|
| admin/audit | `load` → useCallback([actionFilter, isEn, router]) |
| admin/diagnose | `load` → useCallback([isEn, router]) |
| admin/orders | `loadOrders` → useCallback([filter, lang, router]) |
| admin/users | `load` → useCallback([isEn, router]) + me state 改 useLocalStorageItem |
| video/studio | `loadSessions` → useCallback([]) |
| video/studio/[id] | startPolling 引用早于声明,显式 eslint-disable + 注释说明(1 处) |

### alt-text 8 → 0(给 user-uploaded preview img 加描述性 alt)
ad-video ×3 / image ×1 / video ×1 / studio/[id] ×2 / JobPanel ×1(用任务 title 作 alt 最精准)

### 数字
- lint: 79 → **65** problems(-14;session 起 117 → 65,-44%)
- exhaustive-deps: 6 → **0**
- alt-text: 8 → **0**
- warnings: 35 → **21**
- 剩余: 44 个 explicit-any error(纯类型噪音)+ 21 个 no-img-element warning(需迁 Next/Image,scope 大,留独立 PR)

### 已 deploy 进生产
(待执行)

## 2026-04-28 四十五续(死代码扫除 + Sidebar 收口 + image loading 真接)

### 顺手扫 lint 残留挖出 2 个真 bug
四十四续做完 set-state-in-effect 重构,扫 21 个 unused-vars warning 时发现:

1. **image/page.tsx setLoading 从未被调用** — `[loading,setLoading]=useState(false)`,提交时不更新,用户看不到 loading 反馈,UI 一直显示空 gallery 占位 → 接通 generate() 的 try/finally
2. **voice-clone resultAudioUrl + clonedVoiceId 死状态** — 设了从不读,UI 实际用 saveGallery 显示成品,早期版本残留 → 删

### Sidebar.tsx 收口
原 commit 623ceac 用 useEffect + 手挂 user-updated/storage listener;这一版统一改 `useLocalStorageItem` 走 useSyncExternalStore,逻辑减半 + 加 `SidebarUser` 类型替 `any`(顺手再修 1 个 explicit-any error)。

### 死代码扫除清单(13 文件)
- ad-video: 删 unused `t` / `jobId` state + 配套 reset 调用
- admin/orders: useLang 改只取 lang
- dashboard: useLang 改只取 t
- admin/dashboard: `catch (err)` → `catch`(err 没用)
- canvas: 删 unused `THREE` import
- image/multi-reference: 删 handleDragOver 的 index 参数
- products/[id]: 删 unused `useParams` + import
- try-on(stub 页): 整理 unused imports + 函数 props
- video/clone: `catch (err)` → `catch`
- voice-clone: 删两个死 state
- SystemHealthBanner: 删 unused `router`

### 数字
- lint: 101 → **79** problems(session 起 117 → 79,-32%)
- error: 54 → **44**(-10)
- warning: 56 → **35**(-21)
- unused-vars: **21 → 0**

### 已 deploy 进生产
(待执行)

## 2026-04-28 四十四续(React 19 set-state-in-effect 5 → 0 收口)

### 自审发现
四十一续修 lint 时跳过的 5 个 set-state-in-effect 错(localStorage hydration 模式)。React 19.2 收紧了这个 anti-pattern,5 个挂载流程都中招:
- LanguageContext / JobPanel / AdminSidebar / homepage / dashboard

正确写法:`useSyncExternalStore` 订阅外部 store,SSR snapshot 与 client snapshot 分离 → 无 hydration mismatch + 无渲染期级联

### 实现
**新 2 个 hook:**
- `src/lib/hooks/useLocalStorageItem.ts` — 订阅 localStorage 单 key,SSR 安全,跨 tab + 同 tab 双通信
- `src/lib/hooks/useIsMobile.ts` — 订阅 resize,< 768px 判定

**userState.ts 加 2 个 helper(token 写入闭环):**
- `setAuthToken(t)` — 写 localStorage + dispatch user-updated
- `clearAuthSession()` — 删 token/user/refresh_token + dispatch

**9 个 token 写入点全部事件化:**
- login(auth/page) 1 个 → setAuthToken
- refresh(AuthFetchInterceptor ×2) → setAuthToken  
- logout(profile ×2 / homepage / admin/users / AdminSidebar / AuthFetchInterceptor redirectToLogin)→ clearAuthSession

**5 个站点重构:**
1. LanguageContext: `useLocalStorageItem("lang", "zh")`
2. JobPanel: `useLocalStorageItem("token")` 替原 2s 轮询黑科技 + 简化 401 cascade(AuthFetchInterceptor 已统一处理)
3. AdminSidebar: `useLocalStorageItem("user")` + `useIsMobile()`
4. homepage: `useLocalStorageItem` token + user
5. dashboard: 同上

**1 处显式 disable:** `admin/dashboard` 的 `loadData()` 是 async,setState 在微任务,实际不算 sync-in-effect,但 lint 规则不分辨;eslint-disable 加注释说明

### 行为改进(用户能感知)
- 登录后 sidebar/JobPanel **立刻**显示登录态,不再 2s 滞后
- 充值/扣费 → 所有订阅组件实时刷新(原已支持 user 缓存,这次扩到 token)
- 跨 tab:tab A 登出 → tab B 立刻反应(storage 事件原生触发)
- SSR/CSR 首渲一致,消除 hydration mismatch 警告

### 数字
- lint:54 errors → 45(-9)
- set-state-in-effect:5 → 0
- 测试无变化(298 全过)

### 已 deploy 进生产
(待执行)

## 2026-04-28 四十三续(studio /batch-status async 退款 — 收漏第二刀)

### 上轮(四十二续)留下的 TODO
> 只 cover fal submit 失败的退款。async 任务后续失败的返还(fal 接了任务但跑挂)留下次。

四十三续就是这个跟进。

### 修
**后端 `app/api/video_studio.py`**:
- `batch_results` 每段加 `refunded` 标记
  - submit 失败的段:`refunded: True`(/batch-generate 时已 add_credits)
  - submit 成功的段:`refunded: False`
- `/batch-status` 检测段 status 翻 `failed` 时:
  - `refunded == True` → 跳过(防双退)
  - `refunded == False` → `add_credits(seg["cost"])` + 置 True
- 返 `refunded_this_call` 给前端

**前端 `studio/[id]/page.tsx`** poll 里:
- `if (data.refunded_this_call > 0) adjustLocalUserCredits(+data.refunded_this_call)`
- 用户看 sidebar credits 涨回去,知道退款已到账

### 幂等性(关键)
- `refunded` 标记保证同一段只退一次,无论 poll 多少轮 / 进程是否重启
- `batch_results` 持久化(`_save_tasks` 写 sessions.json)

### 测试 +3(8 → 11)
| 用例 | 验证 |
|---|---|
| async_failure_refunds | poll 检测到 1 段 async failed → 退 15 + sidebar 涨 |
| no_double_refund_on_repoll | 重复 poll 同失败段,第二次 refunded_this_call=0 |
| submit_failed_segments_not_double_refunded | submit 已退的段,batch-status 不重复退 |

总测试 295 → **298**

### 不在 scope(留下次)
- /merge 阶段的失败处理 — merge 是 ffmpeg + fal 上传,不涉及计费,但 merge 失败用户 retry 时要确保不重复扣费(待审视)
- circuit breaker 触发的批量失败告警 — 监控层的事,不在 studio 内

### 已 deploy 进生产
(待执行)

## 2026-04-28 四十二续(studio /batch-generate 收漏 — 真扣费上线)

### 自审发现
四十一续接 sidebar credits 时调研到 `/api/studio/batch-generate`,挖到底层:
**这个端点既无 `@require_credits` 也无手动 `deduct_credits`,完全免费**。

- `video/replace/element` 定价 15 积分(已在 PRICING 里)
- 一个 session 5-20 段
- 单 session **75-300 积分**免费 FAL 视频生成
- FAL 那边按调用收钱,我们 0 入账 → 直接漏成本

### 修
**后端 `app/api/video_studio.py`** 镜像 `ad_video.py /generate` + `jobs.py` 的 fail-refund pattern:
- 进入业务前 `check_user_credits(N × 15)` 不够 → 402
- `deduct_credits` 上预扣全额(SQL WHERE credits >= 原子)
- 批量循环跟踪 `submit_failed`(status != pending)
- 循环后 `add_credits(refund)` 把失败段退回
- `create_consumption_record` 写实扣到 `generation_history`(用户 /tasks/history 看得到)
- 返 `{cost: actual_cost, submit_failed}` 给前端

**前端 `studio/[id]/page.tsx`:**
- `adjustLocalUserCredits(-data.cost)` 实时反映 sidebar
- 失败段数量提示透明告知用户

### 测试 (新 `test_video_studio.py` 8 例)
| 用例 | 验证 |
|---|---|
| 401 | 未登录 |
| 403 | 别人的 session |
| 404 | session 不存在 |
| 402 | 余额不足不扣费 |
| happy path | 3 段全 ok → 扣 45 |
| 部分失败 | 1/3 失败 → 实扣 30 退 15 |
| 全失败 | 实扣 0 全退 |
| generation_history | 写消费记录 |

总测试 287 → **295**(+8)

### 决策记录
- **预扣 + 失败退** 而非 **per-call 扣费** — 一次性 SQL 扣减比 N 次 SQL 写竞态少;失败比例通常很低,补退一次更便宜
- **只 cover fal submit 失败** — async 任务后续失败的返还(circuit breaker / fal 内部错)留下次。当前如果 fal 接了任务但跑挂,用户被多扣 ¯\_(ツ)_/¯;改这层要动 batch-status 阶段,scope 较大
- **/merge 不再扣费** — 算力费已在 batch-generate 阶段付完;merge 只是 ffmpeg + fal 上传,免费给用户

### 已 deploy 进生产
(待执行)

## 2026-04-28 四十一续(自审清理 + sidebar credits 扣费路径接通)

### 自审发现
跑 `npm run lint` 看到 117 problems(61 errors,56 warnings),里面夹着真 bug:
- `useTaskPolling.ts`(0 消费者孤儿,8 天前一次性 commit 进来从没人引用)
- `admin/dashboard/page.tsx` immutability error:`loadData` 在 useEffect 之后声明,deps `[]` 把首次实例锁住
- `detail/page.tsx` 真显示 bug:JSX text 写了 `\"复制文案\"`,backslash 不会被吃,用户实际看到字面量

### 修(commit 1: 自审清理)
1. 删 useTaskPolling.ts(-2 refs error -1 any error)
2. dashboard loadData 用 useCallback 包 + 进 deps
3. detail 引号 → 中文「」+ a → Link + AuthFetchInterceptor `let response` → const

lint: 61 errors → 54 errors(-7)

### 接 sidebar credits(commit 2: 完成上次 commit 留的 TODO)
上一个 commit `623ceac` 明确说"扣费路径不在 ad_video deploy 范围内,留独立 commit"。现在补完闭环。

后端所有扣费端点都已返 cost(@require_credits 装饰器 + jobs.py submit + ad_video/generate),前端拿来用即可。

5 文件 / 7 个扣费触点统一:
| 页面 | 触点 |
|---|---|
| /image | jobs/submit |
| /video | jobs/submit (video_i2v) |
| /ad-video | analyze + preview + generate + scene/regenerate(共 4) |
| /avatar | avatar/generate |
| /voice-clone | voice/clone + voice/tts(共 2) |

模式:`if (typeof data.cost === "number" && data.cost > 0) adjustLocalUserCredits(-data.cost);`

效果:扣费成功 → sidebar 立刻减(无需刷页);跨 tab 也同步(storage 事件 + 自定义 user-updated)

### 决策记录
- **不修 5 个 set-state-in-effect** — 都是 localStorage 同步初始化,正确写法需 `useSyncExternalStore` + SSR snapshot,改 5 个挂载流程(auth/lang/mobile/job/sidebar)风险大于收益。lint 非阻塞,留下次专项重构
- **不收紧 48 个 explicit-any** — 纯类型噪音,不是 bug,改了不影响行为,ROI 低
- **不修 21 个 unused-vars 警告** — 同上,纯清理,留专项
- **digital-human 不接 adjust** — 当前 503 stub 不扣费;接通时同步加

### 未覆盖(留下次)
- 5 个 set-state-in-effect 重构(localStorage hydration 用 useSyncExternalStore)
- 48 个 explicit-any 类型收紧
- studio (长视频) 扣费接口未明确返 cost,留独立调研后再接 adjust

### 已 deploy 进生产
- frontend rsync + restart blue/green
- 实测:充值后 sidebar 立刻刷新 ✓ / 生成图片后 sidebar credits 减少 ✓

## 2026-04-27 四十续(`/forgot-password` dead code 410 化 — P0 同 pattern)

### 自审发现
`/api/auth/forgot-password` 是个 dead code:
- 接受任意 email
- TODO: 没真发邮件
- 假装"重置链接已发送" — **跟 P0 数字人同 UX 欺诈 pattern**

**前端不调它**(`/auth/forgot-password` 页早走 `send-code` + `reset-password-by-code` 真流程)。

### 修
- `/api/auth/forgot-password` → **410 Gone**(永久废弃,语义比 503 更准)
- detail 引导用新流程:`send-code (purpose=reset) + reset-password-by-code`
- 测试 +1(269 通过)

### 决策记录
- **410 而非 503** — 503 = 暂时不可用;410 = 永久废弃。这个端点不会回归
- **保留端点不删** — 兼容老客户端(若有);返 410 + 引导文案让他们看到怎么迁

### 已 deploy 进生产
- backend rsync + restart blue
- 实测:`POST /api/auth/forgot-password` → 410 + 引导文案 ✓

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
