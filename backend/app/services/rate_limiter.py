"""
限流防刷中间件
- IP 限流(中间件)
- 用户限流
- 登录失败计数(触发验证码)
- 注册 IP 限流(SQLite 持久,P3-3 反羊毛党)
- 注册失败软配额(SQLite 持久,BUG-1)

P9 backend 抽象:
- 默认内存版(InMemoryRateLimiter)— 重启丢计数,单 worker OK
- 可选 Redis 版(RedisRateLimiter)— 跨重启 / 跨 worker,需配 REDIS_URL
- Redis 不可达 init 阶段静默回退内存版 + warning
"""
import logging
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Optional
import time

logger = logging.getLogger(__name__)


# === P3-3:注册 IP 限流(SQLite 持久) ===

REGISTER_IP_LIMIT = 3      # 同 IP 24 小时内最多成功注册次数
REGISTER_IP_WINDOW = 86400  # 时间窗口(秒)

# === BUG-1:注册失败软配额(防脚本爆破 code) ===
REGISTER_IP_FAILURE_LIMIT = 10   # 同 IP 24h 失败 >= 10 次 → 429
REGISTER_IP_FAILURE_WINDOW = 86400


def get_client_ip(request: Request) -> str:
    """统一从 request 抽真实客户端 IP(模块级公用)

    优先级(P6 Cloudflare 后):
    1. CF-Connecting-IP — Cloudflare 透传的真用户 IP(最权威,只有 CF 会塞)
    2. X-Forwarded-For — 标准代理头(取第一个,nginx 已加保护)
    3. X-Real-IP — nginx real_ip 模块设置
    4. request.client.host — 直连或 nginx 转发的 socket 地址

    安全:nginx.conf 的 set_real_ip_from 配置必须包含 CF IP 段,否则
    用户可以伪造 CF-Connecting-IP 头绕过 IP 限流。
    """
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "127.0.0.1"


def count_recent_registers_from_ip(ip: str) -> int:
    """查 24h 内此 IP 成功注册次数(用 SQLite,跨重启)"""
    from app.database import get_db
    cutoff_ts = time.time() - REGISTER_IP_WINDOW
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM register_ip_log WHERE ip = ? AND registered_at_ts >= ?",
            (ip, cutoff_ts),
        ).fetchone()
    return int(row[0]) if row else 0


def record_register_ip(ip: str) -> None:
    """注册成功后写一条;清掉 24h 之前的旧条目"""
    from app.database import get_db
    now_ts = time.time()
    cutoff_ts = now_ts - REGISTER_IP_WINDOW
    with get_db() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO register_ip_log (ip, registered_at_ts) VALUES (?, ?)", (ip, now_ts))
        # 顺手 GC,防表无限膨胀
        c.execute("DELETE FROM register_ip_log WHERE registered_at_ts < ?", (cutoff_ts,))
        conn.commit()


def assert_register_ip_quota(ip: str) -> None:
    """超额直接 raise HTTPException(429)"""
    used = count_recent_registers_from_ip(ip)
    if used >= REGISTER_IP_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"该 IP 24 小时内已注册 {used} 次,达到上限({REGISTER_IP_LIMIT}),请明天再来",
        )


# === BUG-1:失败软配额 ===

def count_recent_register_failures_from_ip(ip: str) -> int:
    """24h 内此 IP 失败注册次数"""
    from app.database import get_db
    cutoff_ts = time.time() - REGISTER_IP_FAILURE_WINDOW
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM register_ip_failure_log WHERE ip = ? AND attempted_at_ts >= ?",
            (ip, cutoff_ts),
        ).fetchone()
    return int(row[0]) if row else 0


def record_register_ip_failure(ip: str, reason: str = "") -> None:
    """注册失败后写一条 + GC 24h 之前的旧条目

    reason: 自由文本(如 "wrong_code" / "expired_code" / "no_code" / "duplicate"),
    便于后续审计但不参与限流逻辑。"""
    from app.database import get_db
    now_ts = time.time()
    cutoff_ts = now_ts - REGISTER_IP_FAILURE_WINDOW
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO register_ip_failure_log (ip, attempted_at_ts, reason) VALUES (?, ?, ?)",
            (ip, now_ts, reason[:64]),
        )
        c.execute("DELETE FROM register_ip_failure_log WHERE attempted_at_ts < ?", (cutoff_ts,))
        conn.commit()


def assert_register_ip_failure_quota(ip: str) -> None:
    """超失败软配额直接 raise 429,挡脚本反复试错 code"""
    used = count_recent_register_failures_from_ip(ip)
    if used >= REGISTER_IP_FAILURE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"该 IP 24 小时内失败次数过多({used} 次),已临时封锁,请明天再来",
        )


class _LimiterCommon:
    """两个后端都用的常量"""
    ip_limit = 60          # 每 IP 每分钟最多请求数(常规接口)
    polling_ip_limit = 300 # 每 IP 每分钟最多轮询/状态查询数(单独桶,不和常规争)
    user_limit = 100       # 每用户每分钟最多请求数
    failure_threshold = 5  # 失败多少次触发验证码
    window_seconds = 60    # 时间窗口(秒)


class InMemoryRateLimiter(_LimiterCommon):
    """内存版(默认):单 worker 安全,重启丢计数"""

    def __init__(self):
        self.ip_requests: Dict[str, list] = defaultdict(list)
        self.ip_polling_requests: Dict[str, list] = defaultdict(list)
        self.user_requests: Dict[str, list] = defaultdict(list)
        self.failure_count: Dict[str, int] = defaultdict(int)

    def _clean_old_records(self, records: list, current_time: float) -> list:
        cutoff = current_time - self.window_seconds
        return [r for r in records if r[0] > cutoff]

    def check_ip_limit(self, ip: str) -> tuple[bool, int]:
        current_time = time.time()
        self.ip_requests[ip] = self._clean_old_records(self.ip_requests[ip], current_time)
        count = len(self.ip_requests[ip])
        remaining = self.ip_limit - count
        if count >= self.ip_limit:
            return False, 0
        self.ip_requests[ip].append((current_time, count + 1))
        return True, remaining - 1

    def check_ip_polling_limit(self, ip: str) -> tuple[bool, int]:
        current_time = time.time()
        self.ip_polling_requests[ip] = self._clean_old_records(
            self.ip_polling_requests[ip], current_time
        )
        count = len(self.ip_polling_requests[ip])
        remaining = self.polling_ip_limit - count
        if count >= self.polling_ip_limit:
            return False, 0
        self.ip_polling_requests[ip].append((current_time, count + 1))
        return True, remaining - 1

    def check_user_limit(self, user_id: str) -> tuple[bool, int]:
        current_time = time.time()
        self.user_requests[user_id] = self._clean_old_records(
            self.user_requests[user_id], current_time
        )
        count = len(self.user_requests[user_id])
        remaining = self.user_limit - count
        if count >= self.user_limit:
            return False, 0
        self.user_requests[user_id].append((current_time, count + 1))
        return True, remaining - 1

    def record_failure(self, ip: str) -> bool:
        self.failure_count[ip] += 1
        return self.failure_count[ip] >= self.failure_threshold

    def reset_failure(self, ip: str):
        self.failure_count[ip] = 0

    def should_require_captcha(self, ip: str) -> bool:
        return self.failure_count[ip] >= self.failure_threshold


class RedisRateLimiter(_LimiterCommon):
    """Redis 版(可选):跨重启 / 跨 worker 持久

    算法:固定窗口(fixed window)
    - key = "rl:ip:{ip}:{window_start}",window_start = floor(now / 60) * 60
    - INCR + EXPIRE(窗口 + 10s 余量)
    - 简单可靠;边界突发(window 切换瞬间双倍)可接受,需要严格平滑滑动用 sorted set

    失败计数:
    - key = "rl:fail:{ip}",普通 INCR,无过期(成功后人工 DEL)
    """

    KEY_PREFIX = "rl:"

    def __init__(self, redis_url: str):
        import redis
        # decode_responses=True 让返回值直接是 str,省一层 .decode()
        self.client = redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=2)
        # 在 init 里 ping 一下,挂了立刻 raise(由调用方决定 fallback)
        self.client.ping()

    def _window_key(self, kind: str, key: str) -> str:
        window_start = int(time.time() // self.window_seconds) * self.window_seconds
        return f"{self.KEY_PREFIX}{kind}:{key}:{window_start}"

    def _check_window(self, kind: str, key: str, limit: int) -> tuple[bool, int]:
        rkey = self._window_key(kind, key)
        try:
            count = self.client.incr(rkey)
            if count == 1:
                self.client.expire(rkey, self.window_seconds + 10)
        except Exception as e:  # Redis 临时故障不能让请求 500
            logger.warning("Redis incr failed (%s),fail-open allowing request", e)
            return True, -1
        if count > limit:
            return False, 0
        return True, max(0, limit - count)

    def check_ip_limit(self, ip: str) -> tuple[bool, int]:
        return self._check_window("ip", ip, self.ip_limit)

    def check_ip_polling_limit(self, ip: str) -> tuple[bool, int]:
        return self._check_window("ip_poll", ip, self.polling_ip_limit)

    def check_user_limit(self, user_id: str) -> tuple[bool, int]:
        return self._check_window("user", user_id, self.user_limit)

    def record_failure(self, ip: str) -> bool:
        rkey = f"{self.KEY_PREFIX}fail:{ip}"
        try:
            count = self.client.incr(rkey)
            # 失败计数 24h 自动作废(防永久卡住合法用户)
            if count == 1:
                self.client.expire(rkey, 86400)
            return count >= self.failure_threshold
        except Exception as e:
            logger.warning("Redis record_failure failed: %s", e)
            return False

    def reset_failure(self, ip: str):
        try:
            self.client.delete(f"{self.KEY_PREFIX}fail:{ip}")
        except Exception:
            pass

    def should_require_captcha(self, ip: str) -> bool:
        try:
            v = self.client.get(f"{self.KEY_PREFIX}fail:{ip}")
            return int(v or 0) >= self.failure_threshold
        except Exception:
            return False


def _make_rate_limiter():
    """工厂:根据 REDIS_URL 选后端;Redis 不可达静默回退到内存版 + warning"""
    import os
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        logger.info("RateLimiter: REDIS_URL 未配置,使用内存后端(单 worker OK,重启丢计数)")
        return InMemoryRateLimiter()
    try:
        rl = RedisRateLimiter(url)
        logger.info("RateLimiter: Redis 后端启用 (%s)", url)
        return rl
    except Exception as e:
        logger.warning(
            "RateLimiter: Redis 连接失败(%s),回退到内存后端;限流仍工作但跨重启不持久", e
        )
        return InMemoryRateLimiter()


# 全局限流器实例(模块加载时一次性选好后端)
rate_limiter = _make_rate_limiter()

# 向后兼容:外部代码 import RateLimiter 仍能拿到一个具体类
RateLimiter = InMemoryRateLimiter


# 轮询/状态查询类接口前缀:走单独的高配额桶(polling_ip_limit),
# 不和常规接口共享 60/min。这些接口纯查询、零外部 IO,前端 4-10s/次轮询正常。
# 注意:必须是真正的"读 + 廉价"端点,不要把写操作放进来。
POLLING_PATH_PREFIXES = (
    "/api/oral/status/",
    "/api/jobs/list",
    "/api/studio/batch-status/",
    "/api/health",
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """限流中间件"""

    async def dispatch(self, request: Request, call_next):
        ip = self._get_client_ip(request)
        path = request.url.path

        # 轮询接口走单独高配额桶(防 4s/次 polling 把常规桶吃光)
        is_polling = any(path.startswith(p) for p in POLLING_PATH_PREFIXES)
        if is_polling:
            allowed, remaining = rate_limiter.check_ip_polling_limit(ip)
            limit_for_header = rate_limiter.polling_ip_limit
        else:
            allowed, remaining = rate_limiter.check_ip_limit(ip)
            limit_for_header = rate_limiter.ip_limit

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "请求过于频繁，请稍后再试",
                    "retry_after": 60,
                },
            )

        if rate_limiter.should_require_captcha(ip):
            pass

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Limit"] = str(limit_for_header)

        return response

    def _get_client_ip(self, request: Request) -> str:
        """复用模块级 get_client_ip(支持 CF-Connecting-IP)。

        早期版本中间件自己写了一份漏掉 CF-Connecting-IP,导致 Cloudflare
        回源时所有用户共用 CF 边缘 IP 的同一个限流桶 (~60/min),
        高峰期合法轮询全被打成 429。
        """
        return get_client_ip(request)


# 用户限流装饰器
def user_rate_limit(func):
    """用户限流装饰器（需要在路由中使用）"""
    from functools import wraps

    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        # 从请求头获取用户 ID（由认证中间件设置）
        user_id = request.state.user_id if hasattr(request.state, 'user_id') else None

        if user_id:
            allowed, remaining = rate_limiter.check_user_limit(user_id)
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail="您的请求过于频繁，请稍后再试",
                )

        return await func(request, *args, **kwargs)

    return wrapper


# 辅助函数
def get_rate_limiter() -> RateLimiter:
    """获取限流器实例"""
    return rate_limiter
