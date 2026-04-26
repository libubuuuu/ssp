"""请求级 trace_id 中间件。

为每个 HTTP 请求生成唯一 trace_id(短 UUID 12 位),写入:
- `request.state.trace_id`:后端代码可读
- 响应头 `X-Request-ID`:前端 / 用户 / 排查时引用

也记录请求开始 / 结束日志,带耗时。

未来接 Sentry / ELK 时,日志按 trace_id 串成调用链。
"""
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from ..services.logger import log_info, log_error


def _gen_trace_id() -> str:
    """生成 12 位短 trace_id(UUID 前 12 位,充分够用且短)"""
    return uuid.uuid4().hex[:12]


class RequestIdMiddleware(BaseHTTPMiddleware):
    """生成 trace_id + 记录请求日志(进入/退出)"""

    async def dispatch(self, request: Request, call_next):
        # 优先复用上游传入的 X-Request-ID(如 nginx 或网关注入),否则自己生成
        incoming = request.headers.get("X-Request-ID")
        trace_id = incoming if incoming and len(incoming) <= 64 else _gen_trace_id()
        request.state.trace_id = trace_id

        start = time.perf_counter()
        method = request.method
        path = request.url.path
        client_ip = request.client.host if request.client else "?"

        # 跳过健康检查 / 静态资源 减少日志噪音
        is_noisy = path in ("/health", "/") or path.startswith(("/static/", "/_next/"))

        if not is_noisy:
            log_info(f"→ {method} {path}", trace=trace_id, ip=client_ip)

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            log_error(
                f"✗ {method} {path} 抛异常",
                trace=trace_id, ip=client_ip, duration_ms=round(duration_ms, 1),
                exc_info=True,
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = trace_id

        if not is_noisy:
            log_info(
                f"← {method} {path} {response.status_code}",
                trace=trace_id, duration_ms=round(duration_ms, 1),
            )

        return response
