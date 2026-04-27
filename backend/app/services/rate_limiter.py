"""
限流防刷中间件
- IP 限流(中间件,内存)
- 用户限流(内存)
- 验证码触发
- 注册 IP 限流(SQLite 持久,P3-3 反羊毛党)
"""
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Optional
import time


# === P3-3:注册 IP 限流(SQLite 持久) ===

REGISTER_IP_LIMIT = 3      # 同 IP 24 小时内最多成功注册次数
REGISTER_IP_WINDOW = 86400  # 时间窗口(秒)

# === BUG-1:注册失败软配额(防脚本爆破 code) ===
REGISTER_IP_FAILURE_LIMIT = 10   # 同 IP 24h 失败 >= 10 次 → 429
REGISTER_IP_FAILURE_WINDOW = 86400


def get_client_ip(request: Request) -> str:
    """统一从 request 抽 IP(模块级公用)"""
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


class RateLimiter:
    """限流器"""

    def __init__(self):
        # IP 请求计数：{ip: [(timestamp, count)]}
        self.ip_requests: Dict[str, list] = defaultdict(list)
        # 用户请求计数：{user_id: [(timestamp, count)]}
        self.user_requests: Dict[str, list] = defaultdict(list)
        # 失败计数（用于触发验证码）：{ip: count}
        self.failure_count: Dict[str, int] = defaultdict(int)

        # 配置
        self.ip_limit = 60  # 每 IP 每分钟最多请求数
        self.user_limit = 100  # 每用户每分钟最多请求数
        self.failure_threshold = 5  # 失败多少次触发验证码
        self.window_seconds = 60  # 时间窗口（秒）

    def _clean_old_records(self, records: list, current_time: float) -> list:
        """清理超出时间窗口的记录"""
        cutoff = current_time - self.window_seconds
        return [r for r in records if r[0] > cutoff]

    def check_ip_limit(self, ip: str) -> tuple[bool, int]:
        """
        检查 IP 限流
        返回：(是否允许，剩余次数)
        """
        current_time = time.time()
        self.ip_requests[ip] = self._clean_old_records(self.ip_requests[ip], current_time)

        count = len(self.ip_requests[ip])
        remaining = self.ip_limit - count

        if count >= self.ip_limit:
            return False, 0

        self.ip_requests[ip].append((current_time, count + 1))
        return True, remaining - 1

    def check_user_limit(self, user_id: str) -> tuple[bool, int]:
        """
        检查用户限流
        返回：(是否允许，剩余次数)
        """
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
        """
        记录失败
        返回：是否需要验证码
        """
        self.failure_count[ip] += 1
        return self.failure_count[ip] >= self.failure_threshold

    def reset_failure(self, ip: str):
        """重置失败计数（成功登录后调用）"""
        self.failure_count[ip] = 0

    def should_require_captcha(self, ip: str) -> bool:
        """检查是否需要验证码"""
        return self.failure_count[ip] >= self.failure_threshold


# 全局限流器实例
rate_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """限流中间件"""

    async def dispatch(self, request: Request, call_next):
        # 获取 IP
        ip = self._get_client_ip(request)

        # 检查 IP 限流
        allowed, remaining = rate_limiter.check_ip_limit(ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "请求过于频繁，请稍后再试",
                    "retry_after": 60,
                },
            )

        # 检查是否需要验证码
        if rate_limiter.should_require_captcha(ip):
            # 可以在响应头中添加标记
            pass

        # 添加限流信息到响应头
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Limit"] = str(rate_limiter.ip_limit)

        return response

    def _get_client_ip(self, request: Request) -> str:
        """获取客户端 IP"""
        # 优先从 X-Forwarded-For 获取（经过代理的情况）
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()

        # 从 X-Real-IP 获取
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # 直接从连接获取
        return request.client.host if request.client else "127.0.0.1"


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
