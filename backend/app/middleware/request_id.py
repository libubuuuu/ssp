"""请求级 trace_id 中间件(纯 ASGI 实现,不用 BaseHTTPMiddleware)。

为什么不用 BaseHTTPMiddleware:
  starlette BaseHTTPMiddleware 在 streaming response / 客户端中途断开 时
  抛 "RuntimeError: No response returned.",拦不住会让请求 500。
  pure ASGI middleware 直接 wrap send,不受影响,且性能更好。

功能:
- 为每个 HTTP 请求生成 12 位短 UUID 作为 trace_id
- 注入 request.state.trace_id(端点可读)
- 在响应头加 X-Request-ID
- 复用上游传入的 X-Request-ID(网关链路追踪连贯)
- 记录请求开始/结束/异常日志(跳过 /health 等噪音端点)
"""
import time
import uuid
from typing import Callable

from ..services.logger import log_info, log_error


def _gen_trace_id() -> str:
    """生成 12 位短 trace_id(UUID 前 12 位,充分够用且短)"""
    return uuid.uuid4().hex[:12]


def _is_noisy_path(path: str) -> bool:
    """跳过日志的端点(高频且无信息量)"""
    if path in ("/health", "/"):
        return True
    return path.startswith(("/static/", "/_next/"))


class RequestIdMiddleware:
    """纯 ASGI 中间件,不继承 BaseHTTPMiddleware,免疫 streaming/disconnect 问题"""

    def __init__(self, app: Callable):
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        # 只处理 HTTP(websocket / lifespan 直通)
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 取/生成 trace_id
        incoming: str | None = None
        for k, v in scope.get("headers", []):
            if k.lower() == b"x-request-id":
                try:
                    incoming = v.decode("ascii")
                except Exception:
                    incoming = None
                break
        trace_id = incoming if (incoming and 0 < len(incoming) <= 64) else _gen_trace_id()

        # 注入到 scope["state"](starlette Request.state 从这里读)
        state = scope.setdefault("state", {})
        state["trace_id"] = trace_id

        # 准备日志元数据
        method = scope.get("method", "?")
        path = scope.get("path", "/")
        client = scope.get("client")
        client_ip = client[0] if client else "?"
        is_noisy = _is_noisy_path(path)

        if not is_noisy:
            log_info(f"→ {method} {path}", trace=trace_id, ip=client_ip)

        start = time.perf_counter()
        # 用 list 装 status_code 防止 wrapped_send 闭包 issue
        status_holder = [500]

        async def wrapped_send(msg: dict) -> None:
            if msg.get("type") == "http.response.start":
                # 在响应头加 X-Request-ID(同时移除已有的,防重复)
                headers = [
                    (k, v) for k, v in msg.get("headers", [])
                    if k.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", trace_id.encode("ascii")))
                msg["headers"] = headers
                status_holder[0] = msg.get("status", 500)
            await send(msg)

        try:
            await self.app(scope, receive, wrapped_send)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            log_error(
                f"✗ {method} {path} 抛异常",
                trace=trace_id, ip=client_ip,
                duration_ms=round(duration_ms, 1),
                exc_info=True,
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        if not is_noisy:
            log_info(
                f"← {method} {path} {status_holder[0]}",
                trace=trace_id,
                duration_ms=round(duration_ms, 1),
            )
