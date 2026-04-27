# Sentry 错误监控接入指南

> 后端 P5 已写代码 — 等用户开账号 + 贴 DSN 即可启用。

## 用户要做的(5 分钟)

### 1. 注册 Sentry

- 官网:https://sentry.io
- 选 **免费个人版**(每月 5K events 够小型 SaaS 用)
- 平台选 **Python / FastAPI**

### 2. 拿到 DSN

注册成功后会自动创建一个项目,在 **Project Settings → Client Keys (DSN)** 找到:

```
https://[hash]@[region].ingest.sentry.io/[project_id]
```

完整复制这个 URL。

### 3. 写到生产 .env

```bash
# 在服务器上(以 root):
cd /opt/ssp/backend
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d -in .env.enc -pass file:/etc/ssp/master.key > /tmp/.env.work
echo 'SENTRY_DSN=https://abc@xxx.ingest.sentry.io/123' >> /tmp/.env.work
echo 'ENVIRONMENT=production' >> /tmp/.env.work
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt -in /tmp/.env.work -out .env.enc -pass file:/etc/ssp/master.key
shred -u /tmp/.env.work
supervisorctl restart ssp-backend-blue   # 或 green,看当前 active
```

### 4. 验证生效

启动日志应该有:
```
Sentry 已启用 (env=production)
```

故意触发一个错误试试(在 admin 后台访问不存在的页面),Sentry 控制台 30 秒内应该收到 event。

### 5. 通知设置

Sentry 控制台 → Alerts → 创建新规则:
- 触发条件:`event.level >= error`
- 通知方式:邮件 + (可选)Slack / 微信(via webhook)
- 频率:每小时最多一次,避免风暴

## 我们配置的策略

`app/main.py` 已写好的初始化:

| 选项 | 值 | 原因 |
|---|---|---|
| `traces_sample_rate` | 0.1 | 10% 采样,免费额度够;100% 会把 5K 额度一周烧完 |
| `profiles_sample_rate` | 0.0 | 关闭,profiling 太耗 events |
| `send_default_pii` | False | 不上报 IP / cookie / user-agent,合规优先 |
| `attach_stacktrace` | True | 错误带完整栈,定位快 |
| `environment` | `settings.ENVIRONMENT` | dev / staging / production 各自分流 |
| `before_send` | `sentry_filter.before_send` | 隐藏雷 #3:过滤 4xx + fal 瞬时错 |

### before_send 过滤逻辑(`app/services/sentry_filter.py`)

| 异常类型 | 处理 | 原因 |
|---|---|---|
| HTTPException 4xx(401/402/403/404/422)| **丢** | 用户输入错,不是 bug |
| HTTPException 5xx(我们自己服务)| 留 | 真服务端问题 |
| fal.media / fal-ai/ + 限流/超时关键词 | **丢** | 上游瞬时错,业务已重试 + 降级 |
| ValueError / KeyError / etc | 留 | 业务异常 |
| 手动 capture_message | 留 | 显式上报 |

效果:5K 免费额度从"几小时烧光"延长到"够用一个月以上"。

## 不要做的事

- ❌ 把 SENTRY_DSN 提交到 git(它在 .env.enc 里,只随加密件入仓)
- ❌ 在前端 SDK 也接入(免费额度就 5K,后端用完了前端就没了;前端有 console + watchdog)
- ❌ 设 `traces_sample_rate=1.0`(立刻烧光额度)

## 月度复检

- Sentry 控制台 → Stats:确认 events 用量 < 4K(留 1K 缓冲)
- 如果用量超 4K,说明有错误风暴,先修代码再说,不是单纯加额度

## 配额接近上限告警(强烈推荐)

Sentry → Settings → Subscription → **Spend Allocation Notifications**:
- "When usage reaches **80%** of monthly quota → email me"
- "When usage reaches **100%** → email me + webhook"

或更主动:Settings → Quotas → Set custom limit per project,**强制不超免费额度**(避免意外升级账单)。

接近上限时排查清单:
1. Issues → 按 frequency 排序 → 找出最高频的 issue
2. 看 issue 详情:是不是 `before_send` 该过滤而没过滤的?如果是,补 sentry_filter.py
3. 真 bug → 修代码,不是加额度
4. 噪声重 → 在 Sentry UI 单独 mute / archive 该 issue

## 后端没装 sentry-sdk 时怎么办

代码已经写了 try/except ImportError:即使依赖没装,启动时只会打 warning 日志不报错。通常发生在 dev 环境 venv 不全的时候。生产 venv 装了就行。
