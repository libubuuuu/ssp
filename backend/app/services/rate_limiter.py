"""
限流防刷中间件
- IP 限流
- 用户限流
- 验证码触发
"""
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Optional
import time


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
