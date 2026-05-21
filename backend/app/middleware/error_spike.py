"""连续错误峰值检测中间件

任何 API 端点连续返回 5xx 达到阈值（3 次）立即推送告警。
2xx/3xx 响应重置该端点计数器。
4xx（用户错误）不计入，避免误报。
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import threading

_SPIKE_THRESHOLD = 3
_counters: dict[str, int] = {}   # endpoint → consecutive 5xx count
_alerted: dict[str, int] = {}    # endpoint → last alerted count (防同端点重复告警)
_lock = threading.Lock()

_SKIP_PATHS = {"/health", "/", "/internal/active-jobs"}

# 端点 → 中文功能名（方便告警正文可读）
_ENDPOINT_LABELS: dict[str, str] = {
    "/api/jobs/submit":           "AI任务提交",
    "/api/jobs/":                 "任务队列",
    "/api/auth/login":            "用户登录",
    "/api/auth/register":         "用户注册",
    "/api/payment/":              "支付系统",
    "/api/billing/":              "积分流水",
    "/api/image/":                "图片生成",
    "/api/video/":                "视频生成",
    "/api/ad-video/":             "AI爆款视频",
    "/api/video/clone-v2/":       "视频复刻V2",
    "/api/video/frame-extract/":  "视频拆帧",
    "/api/admin/":                "管理后台",
}


def _endpoint_label(path: str) -> str:
    for prefix, label in _ENDPOINT_LABELS.items():
        if path.startswith(prefix):
            return label
    return path


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
                from app.services.alert_service import push_alert, format_alert
                push_alert(
                    f"🔴 接口连续报错 {count} 次",
                    format_alert(
                        problem=f"HTTP {status}，已连续报错 {count} 次",
                        feature=_endpoint_label(path),
                        details=f"端点: {endpoint}\n请立即查看日志: /var/log/ssp-backend-*.log",
                    ),
                    alert_key=f"spike:{endpoint}",
                    cooldown=300,
                )
        elif status < 400:
            with _lock:
                if _counters.get(endpoint, 0) > 0:
                    _counters[endpoint] = 0

        return response
