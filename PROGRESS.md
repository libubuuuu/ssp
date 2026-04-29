项目进度日志,每次收工前更新

## 2026-04-29 七十八(rate-limit 二修 — CF-Connecting-IP + polling 独立桶)

线上现象:用户反映 oral mask 编辑器卡死无限"加载中"。前面 6 轮全在前端
追 video/Blob/effect deps,真根因在后端:`/api/oral/status` 与
`/api/jobs/list` 反复 429。

两个独立 bug 叠加:

1. **CF 边缘 IP 共桶**:`RateLimitMiddleware._get_client_ip` 自己写了一份,
   只看 `X-Forwarded-For / X-Real-IP / client.host`,漏 `CF-Connecting-IP`。
   日志里限流 key 是 `172.71.110.x`(CF 边缘节点),意味着所有走同一 CF 边缘
   的用户共用同一个 60/min 桶 — 高峰必崩。
   同模块上面 L36-57 已经有正确的 `get_client_ip()`,中间件没复用。
2. **轮询接口跟常规接口共桶**:状态查询 4s/次轮询 + dashboard + 普通点击,
   单用户单页就能吃到 60/min 上限。

### 修法(commit `dc34692`)

- `_get_client_ip` 改为一行委托给模块级 `get_client_ip`
- 新增 `polling_ip_limit = 300` + `POLLING_PATH_PREFIXES`(4 条:
  `/api/oral/status/`、`/api/jobs/list`、`/api/studio/batch-status/`、
  `/api/health`)
- `InMemoryRateLimiter` / `RedisRateLimiter` 两后端都加 `check_ip_polling_limit`
- `dispatch` 按路径前缀分桶,response header `X-RateLimit-Limit` 动态

### 验证 ✅

- `curl -sI /api/auth/me` → `X-RateLimit-Limit: 60`
- `curl -sI /api/jobs/list` → `X-RateLimit-Limit: 300`
- 连刷 100 次 `/api/oral/status/dummy` → 100 × 401(走完限流没被打 429)

### 已 deploy 进生产 ✅(蓝绿 blue → green)

注意:本次踩了一脚 — `deploy.sh` 不动代码,只切 supervisor/nginx,
必须先 `rsync /root/ssp/backend → /opt/ssp/backend` + `chown ssp-app:ssp-app`
才能让新代码到 prod。第一次直接 `bash deploy.sh` 验证仍 `limit=60`,
rsync + chown 后再 deploy 才生效。

### 遗留 backlog

- **Redis 持久化限流(Phase 2)**:当前仍是 in-memory,单 worker 安全,
  多 worker 时每个 worker 独立桶 → 限流失准。代码已支持(`RedisRateLimiter`),
  配 `REDIS_URL` 即启用。
- **POLLING_PATH_PREFIXES 用 `startswith` 前缀匹配**:目前 OK(4 条都没冲突),
  如果以后出现 `/api/jobs/list-archived` 之类与现有前缀冲突的路径,改 `==`
  精确匹配。

---

## 2026-04-29 七十七续 P12(oral_sessions 60 天 GC)

防磁盘满。uploads_gc 是按 mtime 90 天扫整 /opt/ssp/uploads/,但 oral
session 政策更紧(60 天)且要 DB 同步标记 archived_at,所以单独跑。

### Schema(commit `8567361`,向后兼容)

- `oral_sessions.archived_at` TIMESTAMP nullable
- `database.py _patch_oral_columns` 幂等 ALTER + alembic migration
  `c5d2e8f1a703_add_oral_archived_at.py`(链 `8a3f1c2d9b04`)
- 生产 schema 已自动 patch(进程启动调 init_db)

### services/oral_gc.py(新)

`clean_old_oral_sessions(days=60, dry_run=False)`:
- 选择条件:`created_at < now-N` AND `(status='completed' OR 'cancelled' OR
  LIKE 'failed_%')` AND `archived_at IS NULL`
- in-flight session(asr_running / tts_running / inpainting_running /
  lipsync_running / edit_submitted / uploaded)绝不动 — 用户可能还在排队
- 每个 session 整目录 `/opt/ssp/uploads/oral/<uid>/<sid>/` rmtree
  (orig.mp4 / mask / swap1 / swap2 / final.mp4 等全清)
- DB row 保留(账单/审计/admin drill-down)只 UPDATE archived_at
- 路径穿越守卫:rmtree 前 _is_within_oral_root 验证

返回 `{scanned, archived, freed_bytes, errors}`。

### deploy/oral-gc.sh + oral-gc.cron.example

- shell wrapper sudo -u ssp-app 跑(uploads/oral 是 ssp-app 拥有)
- cron 模板每天 05:00(避开 03:00 备份 + 04:00 uploads-gc)
- 留给用户装(`/opt/ssp/deploy/oral-gc.cron.example` 头部有安装步骤)

### 测试 +4(505 → 509 全过)

- 60 天前 completed → 目录删 + archived_at 标记 + freed_bytes 正确
- in-flight asr_running 70 天前也不动(数据完整性)
- 30 天前 completed < 60 天阈值不动
- 重跑幂等:archived_at 非 NULL 的 row 不再处理

### 已 deploy 进生产 ✅(2026-04-29 蓝绿 blue → green)

- ailixiao.com / 200 / /api/payment/packages 200 / /api/oral/list 401
- `PRAGMA table_info(oral_sessions)` 含 archived_at(prod schema 已 patch)

### 决策记录

- **DB row 保留只标记 archived_at**:整 row 删会让 admin drill-down 看
  历史断、账单审计也丢上下文;只标一列让 UI 可显示"产物已归档(60 天)"
- **政策 60 天比 uploads_gc 90 天紧**:口播是大文件(60s 视频 + 5 个产物
  轻松 200MB+ / session),用户回看率比图片低,60 天磁盘节省更显著
- **单独跑不复用 uploads_gc**:uploads_gc 是文件 mtime 维度,oral 是 DB
  row 维度(终态判断 + archived_at 双向同步),逻辑不一样
- **路径穿越守卫不省**:user_id / sid 来自 DB,但 defense-in-depth

### 用户操作待办

装 cron(代码已就位):
```bash
crontab -l > /tmp/cron.bak 2>/dev/null || true
cat /tmp/cron.bak /opt/ssp/deploy/oral-gc.cron.example | sort -u | crontab -
crontab -l                                  # 验证
tail -f /var/log/ssp-oral-gc.log            # 看输出(05:00 后)
```

### 下一步候选

- (等用户)真实视频 PoC + ElevenLabs key + 装 oral-gc cron
- 我能继续干的:
  - lint errors 58 个清理(setState in effect 等)
  - profile 页"邮件通知开关"

---

## 2026-04-29 七十七续 P11(终态邮件通知 — 用户离开页面也能跟进)

P10 解了"在页面看进度",本续解"离开页面也能跟进":任务完成或失败时
Resend 发邮件给用户,告知结果 / 失败原因 / 已退积分。

### services/notify_email.py(新文件,commit `a8fb0c8`)

- `_send_resend(to, subject, html)` 通用 wrapper(无 RESEND_API_KEY
  print warning 跳过 — 开发模式不破任务流程)
- `send_oral_completion(email, sid, tier, duration, final_url)`:中文
  模板 + 工作台直链 + 档位 zh 映射(经济 / 标准 / 顶级)
- `send_oral_failure(email, sid, error_step, error_message, refunded_credits)`:
  失败步骤 zh 映射(Step 1 提取/转写音频 / ... / Step 5 口型对齐 + 水印)
  + 已退积分(0 时不渲染该行)+ 错误消息 truncate 300

### oral.py 加 hook

`_update_session` commit 后 fire-and-forget 第二个 hook(跟 P10 broadcast
同位置同模式,5 步状态机所有完成 / 失败路径自动覆盖):

- `status == "completed"` → `send_oral_completion`
- `status.startswith("failed_")` → `send_oral_failure`(refunded 来自字段)
- `cancelled` 不发(用户主动取消不打扰)
- in-memory `_oral_notified_terminal` set 去重防重发
- 不在 event loop(sync 测试路径)静默跳过

### 测试 +4(501 → 505 全过)

- send_oral_completion 调 _send_resend(subject 含"已完成" / "顶级档" / "42 秒")
- send_oral_failure 含失败步骤 zh + 已退积分渲染
- _send_oral_terminal_email completed → send_oral_completion(email 来自 DB)
- _send_oral_terminal_email failed_step3 + refunded=60 → send_oral_failure
  含正确 refunded_credits

### 已 deploy 进生产 ✅(2026-04-29 蓝绿 green → blue)

- ailixiao.com / 200 / /video/oral-broadcast 200
- /api/payment/packages 200 / /api/oral/list 401
- WS 升级(HTTP/1.1)/api/oral/ws/test → backend 4403(鉴权生效)

### 决策记录

- **抽 services/notify_email.py 不写进 auth.py**:auth 验证码邮件跟任务
  通知不同语义,新模块也能给以后其他长任务通知复用(image-studio batch /
  digital_human / video-studio 等)
- **hook 进 _update_session 不改 5 处**:跟 P10 broadcast hook 同位置同
  模式;cancelled 在条件里直接排除;in-memory 去重防重发(backend 重启
  set 清空但 _run_*_step 是 orphan task 重启后不会重跑,无重发风险)
- **没 RESEND_API_KEY 跳过不报错**:开发 / 临时停 Resend 时任务流程不
  受影响,打 [WARN] 留 log
- **失败邮件含 refunded_credits**:用户最关心"花的钱退了吗",直接告诉;
  0 时不渲染该行避免噪音

### 下一步候选

- (等用户)真实视频 PoC + ElevenLabs key
- 我能继续干的:
  - oral_sessions GC(60 天清 final.mp4)
  - lint errors 58 个清理(setState in effect 等)
  - profile 页加"邮件通知开关"(可选 — 当前默认所有人开)

---

## 2026-04-29 七十七续 P10(WS 实时进度替 4s 轮询 + nginx WS 头修)

PoC 阶段用户起任务后 5 步进度条卡 4s 才动一下,体验最差的工程拦路虎。
改 WS 推送后状态切换实时跳。复用 _update_session 统一出口 + tasks.py 已
就绪的 WS 鉴权架构,改动局限在 oral 范围。

### 后端(commit `6fa359f`)

- 新增 `_build_status_payload(session)` 共用 — status 端点 + WS 推送同源,
  保证前端只看一种数据格式
- `_update_session` commit 后 fire-and-forget `asyncio.create_task` 调
  `_broadcast_session_status` — 5 步状态机所有切换点统一出口,自动覆盖。
  sync 路径 `RuntimeError` 静默跳过(测试不破)
- `/api/oral/ws/{sid}` WebSocket:
  - 鉴权 4401(无/坏 token)/ 4403(跨用户 / session 不存在)
  - accept 后立即推一条当前 status,前端不需先 fetch
  - 终态(completed / cancelled / failed_*)推完关连接 + 清订阅集
- 测试 +5(496 → 501 全过):无 token 4401 / 错 token 4401 / 跨用户 4403 /
  happy path 收到 initial payload / 终态 session 推完关连接

### 前端(commit `6fa359f`)

- 引 `WS_BASE = NEXT_PUBLIC_WS_URL || ws://localhost:8000`(同 tasks 页)
- `loadStatus` 抽出"自动灌 editedText"逻辑到独立 effect — 防止 editedText
  进 loadStatus 闭包导致 WS effect 频繁重连
- useEffect:首次 loadStatus + 然后 `new WebSocket` 订阅;`onmessage` 直接
  `setSess(JSON.parse(e.data))` (payload 跟 status 端点 schema 完全一致)
- WS `onerror` / `onclose`(非 1000)自动 fallback 到 4s 轮询(老 token /
  网络抖动 / 反代 WS 失败都不影响 UX)

### nginx 修生产配置(commit `b0f9f89`)

部署后第一次测发现 WS 升级请求 404 — 根因:`/api/` location block 缺
`Upgrade` / `Connection "upgrade"` 头(tasks `/ws` 历史也是同样问题,只是
没真用户撞)。两处 `location /api/` block 各加两行,nginx -t + reload。
git 模板 `deploy/nginx.conf` 同步,下次 deploy 不丢。

`$http_upgrade` 在普通请求里为空,nginx 不会真触发 upgrade,对现有 71+
普通 API 路径无副作用。

### 已 deploy 进生产 ✅(2026-04-29 蓝绿 blue → green)

- ailixiao.com / 200 / /video/oral-broadcast 200
- /api/payment/packages 200 / admin /api/products 200
- /api/oral/list 401(端点 OK)
- WS 升级 /api/oral/ws/test:nginx → backend → 4403(后端鉴权生效)

### 决策记录

- **WS 端点放在 oral.py 末尾不另开文件**:_broadcast 跟 _update_session 同
  模块,避免循环 import + 复用 router 不用 main.py 改
- **fire-and-forget broadcast**:_update_session 是同步,所有 5 步都在
  event loop,`asyncio.get_running_loop().create_task` 即可;RuntimeError
  兜底是给 sync 测试路径
- **WS payload = status payload**:推送跟 GET /status 完全同源,前端一行
  setSess 不需要建第二种 schema
- **fallback 到轮询不放弃**:WS 在反代 / 老 token / 网络坏环境下偶尔挂,
  完全砍轮询会让小部分用户看不到进度;onclose code !== 1000 自动退回 4s
  轮询,UX 不掉级
- **nginx WS 头加在 /api/ 顶层不开新 location**:加专门 location ~ /ws/
  会跟 limit_req / 大文件 timeout 配置打架,Upgrade 头普通请求无副作用,
  最小改动最稳

### 下一步候选

- (等用户)真实视频 PoC + ElevenLabs key
- 我能继续干的:
  - oral_sessions GC(60 天清 final.mp4)
  - 长任务用户邮件通知(完成/失败)
  - lint errors 58 个清理(setState in effect 等)

---

## 2026-04-29 七十七续 P9a(模特/产品本地图片上传)

P6 加了 MediaPicker(从商家产品库 / 历史任务挑图)+ URL 输入框,但用户
**电脑里的本地图片**没有入口 — 真实 PoC 时模特图很可能是用户自己临时
拍的或网上随便存的,逼用户先传到别处再回来贴 URL,体验差。

### 改动(commit `32c99c4`)

- **零新后端代码**:复用现成 `/api/video/upload/image`(已带 Pillow
  宽高比修正 + 最小 300px + JPEG 压缩 + fal_client.upload 返回 https
  URL,IMAGE_MIMES 校验也现成)
- **前端 `page.tsx`**:
  - `uploadingKind` state(null | "model" | "product")
  - `handleImageUpload(kind, file)` 异步:multipart POST → 返回 url 自动
    填 modelUrl/productUrl;name 为空时取文件名(去后缀,32 字符截断)
  - 模特 / 产品两块右上角各加"📁 上传图片" label-style 按钮(和原
    "📂 从生成历史选 / 🛒 从产品库选"按钮并列)
  - 上传中按钮显示"上传中...",**另一侧按钮 opacity 0.5 防误触**
  - input.value="" 重置 → 同一文件可重传
- **i18n**:`oral.picker.uploadBtn` / `uploading` / `uploadFail`(zh/en)

### 已 deploy 进生产 ✅(2026-04-29 蓝绿 green → blue)

- ailixiao.com / 200 / /video/oral-broadcast 200
- /api/video/upload/image 401(端点 OK 要 token)

### 决策记录

- **复用 `/api/video/upload/image` 不新开端点**:image-studio 早就有这套
  逻辑(Pillow 处理 + fal upload),长一样的代码再写一份只会增加维护面
- **上传完自动填 URL,name 留空时取文件名**:用户最常见路径"传图片就走"
  少一步手填名字;name 已填则不覆盖
- **不放在 MediaPicker 里加 tab**:MediaPicker 是浏览历史/产品库的语义
  ("挑一个已有的"),上传是新增语义,放外面按钮更清楚 + 改动小
- **复用 video/upload 而非 oral 命名空间**:wan-vace inpainting 的 ref
  只要个 https URL,与 oral 业务无强绑定;沉淀通用 image upload 端点
  比按业务复制一份更健康

### 下一步候选

- **P9b — 人物/产品拆分**(等用户拍板架构方案):
  - A. 双 mask + 双轮 wan-vace inpaint(2× 调用费 + 时间)
  - B. 单 mask + 智能 prompt(改动小,效果不可控)
  - C. 只换人不动产品(最稳,失去"换产品"卖点)
- (等用户)真实视频 PoC + ElevenLabs key
- 我能继续干的:
  - oral_sessions GC(60 天后清 final.mp4)
  - WS 实时进度替 4s 轮询
  - 长任务用户邮件通知(完成/失败)
  - lint errors 58 个清理

---

## 2026-04-29 七十七续 P8(oral admin drill-down — 点行展开看完整字段)

P7 表格只能看任务概要,真实视频 PoC 时如果某条结果不对,需要看完整字段
(ASR 听对没 / 编辑文案 / 中间产物 / fal request_id)定位问题。

### 后端 GET /api/admin/oral-tasks/{sid}(commit `a821f23`)

- 鉴权 require_admin,LEFT JOIN users 拿 user_email
- selected_models / selected_products / asr_word_timestamps 后端 json.loads
  解析为对象,失败保留原 string(前端能看到坏数据)
- 派生 credits_net = charged - refunded
- 派生 original_video_url(从 `/opt/ssp/uploads/...` 转 public `/uploads/...`)
- 404 不存在

### 前端 OralDetailModal

`frontend/src/components/OralDetailModal.tsx`(~210 行):
- 表格行 onClick → setDetailSid → modal,hover 高亮 + cursor pointer
- 视频 ▶ 链接 e.stopPropagation() 避免跟 row click 冲突
- modal 8 段(失败排查最关注的"错误"放最上面):
  ① **Meta grid**(ID / 用户 / tier / 状态 / 时长 / 净扣 / 创建 / 完成)
  ② **错误**(红框高亮,有 error_message 时优先显示,error_step 标注)
  ③ **模特 / 产品**(parsed JSON 渲染图 + 名,JSON 失败 raw fallback)
  ④ **ASR + 编辑文案**(pre + whitespace:pre-wrap,看清是否换行/编辑差异)
  ⑤ **5 个产物 inline 播放**(原视频 / mask / 新音频 / 换装视频 / 最终)+ ↗ 新窗
  ⑥ **FAL request_id**(voice_provider / swap / lipsync)— 去 fal dashboard 查

### 测试 +4(487 → 491 全过)
- 401 / 403 / 404 / 200 含完整字段
- JSON 解析正确(selected_models[0].name == "Alice")
- credits_net 派生 + 各 fal request_id 字段返回

frontend npm run build 0 error 0 warning。

### 已 deploy 进生产 ✅(2026-04-29 14:13 蓝绿 blue → green)
- ailixiao.com / 200 / /admin/oral 200 / /api/admin/oral-tasks/anysid 401(端点 OK)

### 决策记录
- **错误段放最上面**:用户来查 drill-down 90% 是因为"这条失败了想看为啥",
  meta info 和模特产品列表是次要 — 红框 + 醒目位置 = 第一眼就看到原因
- **JSON parse 后端做 + 失败 fallback raw**:前端不需要 catch 解析,坏数据
  也能展示(red 警告)— 双方各退一步,坏数据不让 UI 整个挂掉
- **inline 播放器**:5 个产物每个都给 video/audio 标签直接看,不用全部跳新窗 —
  排查"音频对得上换装视频吗 / 换装视频对得上 mask 区域吗"靠肉眼比对最快
- **FAL request_id 留外链 hint**:不在产品里链 fal dashboard(单点账号绑定),
  把 ID copyable 给运营人手贴 fal 后台查

### 下一步候选
- (等用户)真实视频 PoC + ElevenLabs key
- 我能继续干的:
  - oral_sessions GC(60 天后清 final.mp4 — uploads_gc 已有框架可扩)
  - WS 实时进度替 4s 轮询
  - 长任务用户邮件通知(完成/失败)
  - lint errors 58 个清理(setState in effect 等)

---

## 2026-04-29 七十七续 P7(口播任务运营后台 — admin 看任务卡哪一步)

P6 完成水印 + picker 后,真实视频 PoC 阶段最后一个工程拦路虎:**用户测试出
问题时,只能 ssh 进服务器 grep 日志才知道卡哪一步**。补 admin 后台一站式看清。

### 后端 GET /api/admin/oral-tasks(commit `1086324`)

- 鉴权:require_admin(沿用现有依赖)
- 三段返回:
  - **summary**:total / status_counts dict / avg_duration_seconds /
    avg_net_credits / total_net_credits(净扣 = charged - refunded)
  - **failure_top**:`status LIKE 'failed_%' AND error_message IS NOT NULL`
    GROUP BY (error_step, message[:120]) → top 5 by count
  - **items**:LEFT JOIN users 拿 email,过滤 ?status= / ?tier=,默认 limit=50
    最大 200。每条 step_progress 字段 5 步 bool 从对应 column IS NOT NULL 派生

### 前端 /admin/oral

`frontend/src/app/admin/oral/page.tsx`(~270 行):
- 7 张汇总 Card(总 / 完成 / 失败 / 进行中 / 平均时长 / 平均净扣 / 总净扣)
- 失败 top 5 红框列表(`×N step5 — lipsync timeout` 这种格式)
- status / tier 下拉过滤 + 刷新按钮
- 表格 9 列:创建时间 / 用户 / tier / 状态(中文映射 + 颜色)/ 5 步圆点 (●○○○○) /
  时长 / 净扣(退款独立标注)/ 错误(hover 看全文,truncated) / 产物(▶ 视频链接)

`AdminSidebar.tsx`:加 🎤 口播任务 入口(在 系统监控 与 审计日志 之间)。

### 测试 +6(481 → 487 全过)
- unauthenticated 401 / 普通用户 403
- admin summary + failure_top 聚合正确(GROUP BY error_step+message,count 正确)
- items 含 user_email / credits_net = charged - refunded
- ?status= / ?tier= 过滤
- step_progress 字段从 NULL/非 NULL 派生(UPDATE 三个字段后,前 3 步 true / 后 2 false)

### 已 deploy 进生产 ✅(2026-04-29 14:00 蓝绿 green → blue)
- ailixiao.com / 200 / /admin/oral 200 / /api/admin/oral-tasks 401(端点 OK)
- /video/oral-broadcast 200(用户工作台不变)

### 决策记录
- **complete drill-down 不做**:点行展开看 ASR / 编辑文案 / 各阶段 fal request_id
  也有用,但不是 PoC 阶段最缺的 — 失败原因 + 卡哪一步 + 净扣已经覆盖 80% 排查需求,
  drill-down 等真有用户报"任务跑出来不对"再加
- **status_counts 用 dict 不固定 keys**:数据库 status 文本在演化(P1 + 后续可能加),
  前端聚合也是按 startsWith("failed_") / endsWith("_running") 模糊匹配,前后端都不强耦合
- **5 步 bool 派生而非加列**:数据 source-of-truth 是各产物字段(asr_transcript /
  new_audio_url 等),派生 bool 永远跟数据真实状态一致;加 step1_done 列就要维护 +
  容易跟产物字段脱节

### 下一步候选
- (等用户)真实视频 PoC + ElevenLabs key
- 我能继续干的:
  - admin 单任务 drill-down(看完整字段 + ASR 转写 + 中间产物 URL)
  - oral_sessions GC(60 天后清 final.mp4 — 现在归档但没 GC 策略)
  - WS 实时进度替 4s 轮询

---

## 2026-04-29 七十七续 P6(L2 AIGC 水印 + 模特/产品选择器)

P5 续解了上传慢,本续补两块"用户上线测试前最后的拦路虎":
- L2 合规:AIGC 水印必须烧录(深度合成规定 §16),P1 只做了 L1 用户责任声明
- UX:模特/产品 URL 手输不可用 — 用户哪有现成 https URL,得能从平台库选

### L2 AIGC 水印(commit `f681619`,后端)

`oral.py` 加 `_apply_aigc_watermark(fal_video_url, user_id, sid)`:
- httpx 流式下载 fal final → `ORAL_UPLOAD_ROOT/<uid>/<sid>/_lipsync_raw.mp4`
- ffmpeg `drawtext` 烧录 "AI 生成内容"(WenQuanYi Zen Hei 中文字体,
  右下角白字 @0.85 + 黑底 @0.55 半透明,字号 h*0.04 即 1080p ~43px) → `final.mp4`
- 一举两得:水印 + 替代原 archive_url 落本地防 fal.media 30 天过期
- **失败 raise**(深度合成规定要求显著标识 + 不可移除,无水印不算合格产物)

`_run_lipsync_step` 替换原 archive_url 调用为 _apply_aigc_watermark。水印失败
走原 except,按 lipsync 失败逻辑退 30%。

服务器字体已确认:`/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`(文泉驿正黑)。

### 模特/产品选择器(commit `f681619`,前端)

`frontend/src/components/MediaPicker.tsx`(新文件,~140 行):
- `source="products"` → GET /api/products?limit=24 取 thumbnail_url + name
- `source="history"`  → GET /api/tasks/history 展开 images 数组,每张独立 item
- modal grid(auto-fill 140px)+ 点中 onPick → 自动 close
- 标准 React + fetch,无第三方依赖

`/video/oral-broadcast/[id]`:
- modelTitle / productTitle 旁加按钮 "📂 从生成历史选" / "🛒 从产品库选"
- URL 输入框下方加 60px 缩略图实时预览(贴 URL 立即看到预览)
- pickerOpen state 单值,复用同一 MediaPicker 实例

i18n zh + en 各加 oral.picker namespace(7 双语 key)。

### 测试 +4(462 → 466 oral / 481 全套全过)
- _apply_aigc_watermark happy path:落 final.mp4 + 返 public URL + raw 删除
- download 404 / ffmpeg 失败 → raise
- _run_lipsync_step 水印失败 → status=failed_step5 + 退 30% + 错误信息含 watermark

frontend npm run build 0 error 0 warning(40 静态 + 8 动态路由不变)。

### 已 deploy 进生产 ✅(2026-04-29 13:51)
蓝绿 blue → green。验证:
- ailixiao.com / 200 / /video/oral-broadcast 200
- /api/products 200(picker products 数据源)
- /api/tasks/history 401(picker history 数据源,鉴权 OK)

### 决策记录
- **水印失败 raise(严格)而非 fallback 无水印**:合规硬性,无水印=废产物,30% 退款
  比"用户拿到无水印视频出去用"风险低
- **复用 products + history 不新建模特库**:products 公开已上架(全平台),history
  是用户自己生成历史 — 两个数据源覆盖大多数场景,新建一个"模特库"模型 + 表 +
  CRUD 是过度设计,先看真实使用反馈再说
- **MediaPicker 不引第三方**:Material UI / Ant 类大库装包慢且 SSR 兼容麻烦,
  140 行原生 JSX 够用
- **缩略图实时预览**:URL 输入对错只能贴完才知,加 60px <img> 预览让"贴错"立刻
  暴露,不用等 start 后第 4 步换装才发现

### 下一步
- 真实视频 PoC(等用户) — 现在水印 + picker 都有了,用户可以贴一段真视频跑端到端
- ElevenLabs key(等用户)— 解锁 standard/premium 档

---

## 2026-04-29 七十七续(口播带货工作台 P1~P5 续 — 经济档完整端到端)

完整规划见 `docs/ORAL-BROADCAST-PLAN.md`(1002 行,3 档定价 + 5 步 pipeline +
mask 方案研究)。本续从骨架到端到端可跑,共 7 个 commit。

### P1 — 数据层 + 6 端点骨架(commit `186e8d5`,03:09)

- `database.py` 新表 `oral_sessions`(32 字段)+ 2 索引;init_db 启动自动建。
- `billing.PRICING` 加 3 档:economy 160 / standard 360 / premium 700 积分/分钟,
  按秒 ceil(`oral.compute_charge`)。
- `/api/oral/{upload, start, edit, status, cancel, list}` 6 端点骨架。
- L1 用户责任声明强制勾选 + audit_log 写入(action=oral_legal_consent)。
- 测试 +33(422 → 455 全过)。
- ✅ 蓝绿 deploy(green → blue,03:08)。

### P2 — ASR + 经济档 voice-clone + 异步状态机(commit `5e4b4bb`,03:19)

- `FalASRService`(fal-ai/wizper,$0.0005/min)+ 注册到 init_fal_services。
- `FalVoiceService.clone_voice` 修返回结构(读 result.custom_voice_id,旧版 hash
  假造已废弃);audio.url 兼容 dict/str。
- `_extract_audio_track`:ffmpeg 完整音轨 → audio.mp3(送 wizper),前 10 秒 →
  voice_ref.mp3(送 minimax voice-clone)。
- `_run_asr_step` / `_run_tts_step` 异步驱动,asyncio.create_task 在 /start /
  /edit 末尾触发;失败按规划 §7.2 退款(ASR 100% / TTS 95%);状态被改(cancel)
  时不覆盖。
- 测试 +7(455 → 462 全过),fal 链路全 monkeypatch。
- ✅ 蓝绿 deploy(blue → green,03:19)。

### P3 — wan-vace inpainting + lipsync + Step 3/4 真并行(commit `925f935`,03:30)

- `FalInpaintingService`(fal-ai/wan-vace-14b/inpainting):3 档分辨率
  economy=480p / standard=580p / premium=720p,mask_image_url +
  reference_image_urls + prompt + 熔断。
- `FalLipsyncService`:economy=veed/lipsync / standard=fal-ai/latentsync /
  premium=fal-ai/sync-lipsync/v2,统一 sync(video, audio, tier) 接口。
- `/edit` 同时 create_task TTS + Inpainting 真并行(规划 §2 Step 3/4 并行)。
- 经济档完整后端就绪,等 P4 前端 + 真实视频 PoC。
- ✅ deploy(green → blue,03:31)。

### P4a — /video/oral-broadcast 5 步 UI 基础版(commit `0e7787c`,03:40)

- 列表页 + 工作台(`[id]/page.tsx`),5 步:档位/确认 → ASR 编辑 → 进度 → 预览 →
  失败/退款。
- 4s 轮询 /api/oral/status,localStorage token + credentials:include 双轨。
- mask 用占位方案("用户用 Photoshop 画 PNG 上传"),canvas 编辑器留 P4b。
- i18n zh/en `oral.*` namespace 60+ 双语 key + Sidebar 🎤 入口。
- frontend build 0 error(40 静态 + 8 动态路由)。
- ✅ 蓝绿 deploy(blue → green,03:40,frontend rebuild)。

### P4b — canvas mask 编辑器(原生 HTML5 自实现)(commit `8a6757e`,03:47)

- 不引第三方(react-konva / fabric.js),原生 HTML5 + React 自实现避免
  依赖膨胀 + SSR 兼容问题。
- `MaskEditor.tsx`:首帧抽取 + 双画布(背景首帧 / 前景 mask 半透叠加) +
  brush/erase/rect 三工具 + 大小滑动条 + clear + pointerCapture +
  CSS 缩放坐标换算 + toBlob 上传。
- 后端 `/api/oral/status` products 加 `original_video_url`(同源资源,无 CORS)
  + `mask_uploaded` boolean(真值来自后端,前端不靠本地 state)。
- 删除原 maskUploading/maskUploaded 本地 state,改读 sess.products.mask_uploaded。
- ✅ 蓝绿 deploy(green → blue,03:42,frontend rebuild)。
- **至此 P4 完整,经济档全链路可端到端跑。**

### P5 fix — 上传慢 → 5MB 分片 + 进度条 + 3 次重试(commit `030254a`,04:00)

诊断:
- 服务器实测 upload 27 Mbps 出口,用户上行典型 5-20 Mbps;60s 视频 50-100MB
  单次 multipart 30-300s,没进度条 → 用户感觉灾难。
- P4a 跳过了 video_studio 早就有的 /upload-chunk 模式,图省事用单次 multipart,
  低带宽用户上完全不可用。

修(仿 video_studio,5 文件):
- `POST /api/oral/upload-chunk`:upload_id 16hex 防路径穿越 / 单 chunk ≤ 10MB /
  累计 ≤ 200MB / 同 user 并行 ≤ 5 / 流式落盘 1MB chunks 不占内存 / 最后一片
  自动合并 + ffprobe 时长校验 + 创建 oral_session。
- 临时目录 `/opt/ssp/uploads/oral/_uploading/<uid>_<id>`。
- 前端 5MB 分片循环 + 单片失败 3 次重试(1s 间隔)+ 实时显示速度 MB/s + ETA。
- 进度条 80px 高度,#0d0d0d 黑色填充,transition 0.2s。
- backend test_oral.py 55 全过 / frontend build 0 error。
- ✅ 蓝绿 deploy(blue → green,04:00 切换)。

### P5 续 — 浏览器侧 MediaRecorder 视频压缩(commit `d58e2fc`,13:30)

诊断:P5 fix 解了"分片不会失败",但低带宽用户传 60s 1080p 原始 50-100MB 仍要
1-3 分钟;再加一层浏览器压缩可以砍 80% 流量,不依赖后端 / 第三方 SDK / wasm。

`frontend/src/lib/utils/videoCompress.ts`(新文件 202 行):
- canvas drawImage 跟 video.currentTime 重绘,降到 1280px 宽。
- canvas.captureStream(30) + video.captureStream() 取 audio track(后端 ASR 要从
  音轨提音频文本,不能丢)。
- MediaRecorder vp9+opus / 1.5Mbps 视频 + 128kbps 音频 → webm(后端
  LONG_VIDEO_MIMES 已含)。
- 兼容性 fallback(MediaRecorder / captureStream / 编码器 不支持)→ 返原文件
  透明继续走分片上传。

`oral-broadcast/page.tsx`:createNew 先压缩再分片;ratio < 0.9 才用压缩版本
(< 10% 收益不值得换 webm);双阶段 UI phase ∈ {idle, compress, upload}。

i18n:zh + en 各加 oral.compressing / oral.compressDone。
frontend npm run build 0 error 0 warning,后端无改动。

**⚠️ 本 commit 上次会话结束时未 deploy 也未 push,本续(2026-04-29)统一收尾。**

### 总账(七十七续)

- 7 个 commit,后端 +800/前端 +900 行(含测试 +40)
- 后端测试 422 → 462(+40 全过)
- frontend 路由 +2:`/video/oral-broadcast` 静态 + `[id]` 动态
- 已完成:经济档(MiniMax + wan-vace + veed/lipsync)端到端
- 待用户:
  - **真实视频 PoC** — 跑一段验证 wan-vace salient tracking 鲁棒性(规划 §14.4)
  - **ElevenLabs API key** — 解锁标准/顶级档(P6)
  - **L2 AIGC 水印**(ffmpeg drawtext burn-in)留 Phase 4 合规批次
- 下一步默认:等 PoC 反馈 → 调档位/分辨率/价格,再 P6 接 EL。

---

## 2026-04-29 七十六续(长视频模型可切换架构 + admin 灰度 kling/reference)

### 诊断
用户长视频工作台 video/replace/element 出现"空气穿衣"(衣服飘人物外不贴合)。
要架构层可切换 + 灰度通道,但**默认行为零变化**(不能偷偷动现有付费用户 mode=o3)。

### Step 2 — 架构(commit `ec7444f`)

`config.py`:加 3 个 env(默认全空 → 走代码默认值)
- `STUDIO_VIDEO_MODEL_EDIT`       覆盖 mode=edit
- `STUDIO_VIDEO_MODEL_EDIT_O3`    覆盖 mode=o3(中文口播)
- `STUDIO_VIDEO_MODEL_OVERRIDE`   非空时无视 mode 全用它(灰度开关)

`services/fal_service.py`:`FalVideoService` 重构
- `MODELS` class-dict → `DEFAULT_ENDPOINTS` + `LABELS`(`MODELS @property` 保留兼容)
- `_resolve_endpoint(model_key)` → `(endpoint, source)`;优先级 `OVERRIDE > 单 mode env > DEFAULT`
- `_generate_video` 加 fallback:override 路径用独立 circuit_breaker key
  (`override:{endpoint}`),失败 3 次自动熔断 + 回退 default。每步 stderr 打日志
  (`FAL_SUBMIT[source]` / `FAL_OVERRIDE_FAIL` / `FAL_OVERRIDE_CIRCUIT_OPEN`)给 Sentry 抓
- 返回新增 `model_source` 字段给前端/admin 看实际跑哪个

`api/admin.py`:`GET /api/admin/studio-model-status`
- 当前 3 个 env 值
- 每个 mode 解析后的 endpoint + source
- `STUDIO_TASKS` 内 batch_results 聚合(GC 24h 自然就是近 24h)
- 失败原因 top 3(给灰度判断)

测试 +12(test_studio_model_switch.py)— 4 种优先级组合 + fallback 三路径 + admin 端点鉴权与聚合。

### Step 3 — admin 灰度(commit `1179e91`)

`video_studio.py:476` 选 `model_key`:
- `mode=o3` → `kling/edit-o3`(中文口播,不动)
- **admin role + mode=edit → `kling/reference`**(空间引导,改善空气穿衣)
- 普通用户 → `kling/edit`(零影响)

stderr 打 `STUDIO_GRAYSCALE` 日志便于 Sentry / 巡检看灰度命中。
新增 4 例覆盖 admin/普通 × edit/o3 四种组合,**全套 422 passed**。

### 已 deploy 进生产 ✅(2026-04-29 02:06)
蓝绿 green → blue。验证:
- `GET /api/admin/studio-model-status` 401(端点存在,鉴权 OK)
- `GET /api/jobs/list` 401(七十五续 jobs 合并代码生效)
- `https://ailixiao.com/` 200

### 灰度阶段(用户主导)
admin 账号自测 3-5 段真实视频,对比新老模型,肉眼标注"贴合 / 不贴合 / 部分贴合",
积累比例数据再决定 Step 4 全量切换。

---

## 2026-04-29 七十五续(My Tasks 合并 long-video sessions — 入口找不到 fix)

### 用户反馈
"长视频翻拍跑完后 My Tasks 找不到入口" — 因为 `STUDIO_TASKS` 是独立 in-memory dict,
`/api/jobs/list` 只读 `JOBS`,长视频 session 完全不进 My Tasks。

### 修(虚拟 job 视图,不动 `STUDIO_TASKS` 真实结构)

`backend/app/api/jobs.py`:
- 新增 `_studio_sessions_as_virtual_jobs(user_id)`:从 `STUDIO_TASKS` 把当前用户的、
  有 `batch_results` 的 session 转成虚拟 job
- id 形如 `studio_{sid}`,`type=long_video`
- status 推导:`final_url` → completed / 全 failed → failed / 其他 → running
- 标题动态:`X/Y 完成,Z 生成中` / `等待合并` / `全部完成`
- `/list` 接口在排序前 `extend` 进 `mine[]`

`frontend/src/components/JobPanel.tsx`:
- Job interface 加 `_long_video` / `_session_id` / `_route`
- card 整条点击 `router.push(_route)`,关弹层
- 删除按钮换成 `↗ 打开`(防误删长任务,且 `studio_xxx` 没 DELETE)
- typeLabel 加 `long_video` → "长视频翻拍"

`test_jobs.py` 全套 20 passed,frontend build 通过。

### 已 deploy 进生产 ✅(2026-04-29 同 Step 3 一起 deploy)

---

## 2026-04-28 七十四续(batch-status + split fal-upload 并发化 — 4-5s → 1s)

### 诊断
用户反馈"上传/使用很慢",backend log 真因清晰:
- `/api/studio/batch-status` 每次 4.3-4.9s(用户每 3s 轮询,体验灾难)
- `/api/studio/split` 45s(N 段 ffmpeg 完后串行 fal upload 跨境)

### 修(`asyncio.gather` 并发,commit `466165c`)

1. **batch-status**:之前 4 段 fal `get_task_status` 串行 4×1.5s=6s,并发后 max ≈ 1.5s。
   已完成 / 已失败的段不查 fal,直接走内存计数。`gather return_exceptions=True`
   防 fal 抖动单段挂全请求。
2. **split**:ffmpeg 切片仍 `Semaphore` 串行(CPU bound),但 fal upload 抽出来
   `asyncio.gather` 并发。N 段视频(典型 4-8 段)总上传时间从 N×5s → max(单段)≈ 5s。

测试 +23,全套 400 passed。

---

## 2026-04-28 七十三续(前端图片压缩 — 5MB → 500KB,上传 30s → 3s)

### 诊断真因(用户给定)
- A 方案中转(用户→服务器→Pillow→fal)走两段网络 + 一次重压缩
- 单次上传 30-50s,占满 nginx worker,拖慢全站
- B 方案 OSS 直传是终极解,但用户开 COS 账号需时间

### 解(commit `5b1c9a2`)
`frontend` 引 `browser-image-compression` 浏览器侧压缩:
- maxSizeMB=0.5 / maxWidthOrHeight=1920 / use webworker
- 上传前压缩,5MB JPG → ~500KB,30-50s → 3-5s
- 透明降级:压缩失败 / 浏览器不支持 → 走原图(不阻塞流程)

挂在所有 image upload 入口(image / video / studio / quick-ad)。

---

## 2026-04-28 七十二续(B 方案 OSS 直传 STS 凭证签发 — 解出口带宽 32Mbps 瓶颈)

### 诊断
- 服务器轻量套餐出口 32 Mbps + 跨境 fal CDN(美国)= 100MB 视频 1-3 分钟
- 入口 206 Mbps 不慢,慢的是服务器→fal 出口段(A 方案中转放大瓶颈)

### B 方案(commit `fb56877`)
- 用户前端 → COS 直传(走自己上行 50-200 Mbps + COS 国内 GB/s 入口)
- 服务器只签 STS 临时凭证(15 分钟有效),无文件流量
- 上传完 public_url 喂 fal API,fal 自己拉
- **预期 3-10 倍提升**

代码:
- `services/storage_sts.py`:腾讯云 STS SDK 调 `GetFederationToken`,policy 限定只
  `PutObject` 到 `uploads/<user_id>/<timestamp>_<filename>`
- `api/storage.py POST /api/storage/sts`:鉴权 + 签发凭证
- `config.py`:7 个 `STORAGE_*` 字段(默认空,`STORAGE_DIRECT_UPLOAD_ENABLED=false` 时 503)
- `requirements.txt`:`tencentcloud-sdk-python-sts`(只 STS 子包)

### 安全
- STS 凭证 15 分钟过期 + 路径隔离
- 文件名清洗(`/`、`..`、特殊字符 → `_`)
- 子账号最小权限(只 STS 签发,不用 root key)
- bucket 格式校验(必须 name-appid 形式)

测试 +6(394 → 400):未启用 503 / 鉴权 401 / 启用后 mock STS 返凭证。

**待用户**:开 COS 账号 + 配 STS 子账号 + 填 env 启用。

---

六十九 ~ 七十一续(快速带货 prompt 工具 + 视频处理 5 项优化 + 独立 GC)
已归档到 [`docs/PROGRESS-archive/2026-04.md`](docs/PROGRESS-archive/2026-04.md);
更早的三十四 ~ 六十八续也在该文件,本文件仅留最近 5 续。
