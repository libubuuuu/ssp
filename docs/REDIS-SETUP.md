# Redis 接入指南(P9)

> 后端代码已写好可选 Redis 后端 — 等用户准备好启用,或保持内存版默认状态。

## 当前状态

- 默认:**内存版**(`InMemoryRateLimiter`),单 worker uvicorn 下完全够用
- 重启会丢 60s 短窗口的计数(自然在 60s 内自愈,无大问题)
- 跨重启需要持久的部分(注册 IP 限流、注册失败软配额)**已经在 SQLite**(P3-3 / BUG-1),不依赖 Redis

## 何时该启用 Redis

需要 Redis 的场景:
- 多 worker uvicorn(`--workers 4`)— 每个 worker 自己的内存计数器,**实际限流被绕过**
- 多机部署 / 横向扩展
- 频繁 deploy(每次 deploy 失计数,某些场景下用户感知明显)

不需要的场景:
- 当前单 worker 单机部署 — 内存版完全够用

## 启用步骤(用户操作)

### 1. 安装 Redis

```bash
sudo apt update
sudo apt install redis-server -y
```

### 2. 配置 Redis(`/etc/redis/redis.conf`)

```ini
# 仅本地访问,不暴露公网(关键)
bind 127.0.0.1 ::1
protected-mode yes

# 端口默认 6379(无需改)
port 6379

# 持久化:rate limit 数据丢了不致命,关 fsync 提速
save ""           # 关闭 RDB 快照
appendonly no     # 关闭 AOF

# 内存上限(防 OOM)
maxmemory 256mb
maxmemory-policy allkeys-lru
```

```bash
sudo systemctl enable redis-server
sudo systemctl restart redis-server
redis-cli ping   # 应返回 PONG
```

### 3. 后端配置:写 REDIS_URL 到 .env

```bash
cd /opt/ssp/backend
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d -in .env.enc -pass file:/etc/ssp/master.key > /tmp/.env.work
echo 'REDIS_URL=redis://localhost:6379/0' >> /tmp/.env.work
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt -in /tmp/.env.work -out .env.enc -pass file:/etc/ssp/master.key
shred -u /tmp/.env.work
supervisorctl restart ssp-backend-blue   # 或 green
```

### 4. 验证生效

启动日志应有:
```
RateLimiter: Redis 后端启用 (redis://localhost:6379/0)
```

检查 Redis key:
```bash
redis-cli KEYS "rl:*"     # 限流 key 前缀
redis-cli MONITOR         # 实时观察 Redis 操作流(测完 Ctrl-C)
```

## 优雅降级

代码设计:
- **启动时** Redis 不可达 → log warning + **回退内存版**,服务正常起
- **运行期** Redis 临时挂(网络抖动、Redis 重启) → `check_ip_limit` 返回 `(True, -1)`(fail-open,不挂请求)
- 不会因为 Redis 故障导致 5xx 雪崩

如果 Redis 恢复后想立刻切回:
```bash
supervisorctl restart ssp-backend-{blue,green}
```

## 监控建议

watchdog.sh 可加一行:
```bash
redis-cli ping > /dev/null 2>&1 || echo "🔴 Redis 不可达,限流降级到内存"
```

## 不要做的事

- ❌ Redis 暴露公网(`bind 0.0.0.0`):无密码 = 整机沦陷
- ❌ 设密码后明文写到 .env:走加密 .env.enc 跟其他凭据同样保护
- ❌ 设 `appendonly yes`:rate limit 数据写盘没必要,纯增 IO 开销
- ❌ 期待 Redis 替代 SQLite 的注册 IP 限流:那两个表(register_ip_log / register_ip_failure_log)是 24h 长窗口审计性数据,SQLite 是正确选择,不要改

## 算法说明

### 60s 窗口 IP/User 限流(check_ip_limit / check_user_limit)
- 固定窗口(fixed window)算法
- key = `rl:{kind}:{key}:{window_start}`,window_start = floor(time / 60) * 60
- INCR + EXPIRE(70s,稍长于窗口防边界丢)
- **缺点**:窗口切换瞬间允许 2x 突发(58-60s 用满 + 0-2s 又用满)
- 升级方案:用 sorted set 实现 sliding window(代价是每次 ZADD + ZRANGE,Redis 内存约 4-5x)

### 失败计数(record_failure / should_require_captcha)
- 普通 INCR,EXPIRE 86400(24h)
- 24h 内累计 5 次失败触发验证码
- `reset_failure` (登录成功) → DEL
