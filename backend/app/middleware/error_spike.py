"""连续错误峰值检测中间件

任何 API 端点连续返回 5xx 达到阈值（默认 5 次）立即推送告警。
2xx/3xx 响应重置该端点计数器。
4xx（用户错误）不计入，避免误报。
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import threading

_SPIKE_THRESHOLD = 5
_counters: dict[str, int] = {}   # endpoint → consecutive 5xx count
_alerted: dict[str, int] = {}    # endpoint → last alerted count (防同端点重复告警)
_lock = threading.Lock()

# 健康检查路径不纳入统计
_SKIP_PATHS = {"/health", "/", "/internal/active-jobs"}


class ErrorSpikeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        response = await call_next(request)

        if path in _SKIP_PATHS or not path.startswith("/api/"):
            return response

        status = response.status_code
        endpoint = f"{request.method} {path}"

        if 500 <= status <= 599:
            with _lock:
                _counters[endpoint] = _counters.get(endpoint, 0) + 1
                count = _counters[endpoint]
            if count >= _SPIKE_THRESHOLD and _alerted.get(endpoint, 0) < count:
                _alerted[endpoint] = count
                from app.services.alert_service import push_alert
                push_alert(
                    f"🔴 接口连续报错 {count} 次",
                    f"端点: {endpoint}\nHTTP {status}\n已连续报错 {count} 次，请立即检查日志",
                    alert_key=f"spike:{endpoint}",
                    cooldown=300,
                )
        elif status < 400:
            # 成功响应重置计数
            with _lock:
                if _counters.get(endpoint, 0) > 0:
                    _counters[endpoint] = 0

        return response
