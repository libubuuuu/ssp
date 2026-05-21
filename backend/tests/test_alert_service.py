"""alert_service + ErrorSpikeMiddleware 单元测试

覆盖:
- push_alert 冷却窗口：同 key 5分钟内只推一次
- push_alert cooldown=0：强制每次推
- push_alert 后台线程不阻塞调用方
- ErrorSpikeMiddleware：连续5次5xx触发告警
- ErrorSpikeMiddleware：2xx重置计数器
- ErrorSpikeMiddleware：4xx不计入
- ErrorSpikeMiddleware：健康检查路径不计入
"""
import time
import threading
from unittest.mock import patch, MagicMock

import pytest

from app.services import alert_service
from app.services.alert_service import push_alert


@pytest.fixture(autouse=True)
def _reset_cooldowns():
    alert_service._cooldowns.clear()
    yield
    alert_service._cooldowns.clear()


# ── alert_service 单元 ────────────────────────────────────────────────────────

def test_push_alert_calls_push_sync(monkeypatch):
    calls = []
    monkeypatch.setattr(alert_service, "_push_sync", lambda t, b: calls.append((t, b)))
    push_alert("title1", "body1")
    # 后台线程，给一点时间
    time.sleep(0.05)
    assert len(calls) == 1
    assert calls[0] == ("title1", "body1")


def test_push_alert_cooldown_deduplicates(monkeypatch):
    calls = []
    monkeypatch.setattr(alert_service, "_push_sync", lambda t, b: calls.append((t, b)))
    push_alert("same_title", "body", alert_key="k1")
    push_alert("same_title", "body", alert_key="k1")
    push_alert("same_title", "body", alert_key="k1")
    time.sleep(0.05)
    assert len(calls) == 1  # 只推一次


def test_push_alert_cooldown_zero_always_pushes(monkeypatch):
    calls = []
    monkeypatch.setattr(alert_service, "_push_sync", lambda t, b: calls.append((t, b)))
    push_alert("t", "b", alert_key="k2", cooldown=0)
    push_alert("t", "b", alert_key="k2", cooldown=0)
    push_alert("t", "b", alert_key="k2", cooldown=0)
    time.sleep(0.05)
    assert len(calls) == 3  # 每次都推


def test_push_alert_different_keys_both_push(monkeypatch):
    calls = []
    monkeypatch.setattr(alert_service, "_push_sync", lambda t, b: calls.append((t, b)))
    push_alert("t", "b1", alert_key="key_a")
    push_alert("t", "b2", alert_key="key_b")
    time.sleep(0.05)
    assert len(calls) == 2


def test_push_sync_no_config_no_crash(monkeypatch):
    """没有配置文件时 _push_sync 不抛异常。"""
    monkeypatch.setattr(alert_service, "_CONFIG_PATH", "/nonexistent/file")
    alert_service._push_sync("title", "body")  # 不抛


# ── ErrorSpikeMiddleware 单元 ─────────────────────────────────────────────────

@pytest.fixture()
def spike_state():
    from app.middleware.error_spike import _counters, _alerted
    _counters.clear()
    _alerted.clear()
    yield
    _counters.clear()
    _alerted.clear()


def _make_request(path="/api/test", method="GET"):
    req = MagicMock()
    req.url.path = path
    req.method = method
    return req


@pytest.mark.asyncio
async def test_spike_five_consecutive_5xx_triggers_alert(spike_state, monkeypatch):
    from app.middleware.error_spike import ErrorSpikeMiddleware

    alerts = []
    monkeypatch.setattr("app.services.alert_service.push_alert", lambda t, b, **kw: alerts.append(t))

    middleware = ErrorSpikeMiddleware(app=MagicMock())
    req = _make_request()

    async def make_500(_):
        resp = MagicMock()
        resp.status_code = 500
        return resp

    for _ in range(5):
        await middleware.dispatch(req, make_500)

    assert len(alerts) == 1
    assert "5" in alerts[0]


@pytest.mark.asyncio
async def test_spike_2xx_resets_counter(spike_state, monkeypatch):
    from app.middleware.error_spike import ErrorSpikeMiddleware, _counters

    alerts = []
    monkeypatch.setattr("app.services.alert_service.push_alert", lambda t, b, **kw: alerts.append(t))

    middleware = ErrorSpikeMiddleware(app=MagicMock())
    req = _make_request()

    async def make_500(_):
        resp = MagicMock()
        resp.status_code = 500
        return resp

    async def make_200(_):
        resp = MagicMock()
        resp.status_code = 200
        return resp

    # 4次5xx
    for _ in range(4):
        await middleware.dispatch(req, make_500)
    # 1次200 → 计数清零
    await middleware.dispatch(req, make_200)
    assert _counters.get("GET /api/test", 0) == 0
    # 再4次5xx，不到5次，不告警
    for _ in range(4):
        await middleware.dispatch(req, make_500)
    assert len(alerts) == 0  # 没有告警


@pytest.mark.asyncio
async def test_spike_4xx_not_counted(spike_state, monkeypatch):
    from app.middleware.error_spike import ErrorSpikeMiddleware, _counters

    middleware = ErrorSpikeMiddleware(app=MagicMock())
    req = _make_request()

    async def make_404(_):
        resp = MagicMock()
        resp.status_code = 404
        return resp

    for _ in range(10):
        await middleware.dispatch(req, make_404)
    assert _counters.get("GET /api/test", 0) == 0


@pytest.mark.asyncio
async def test_spike_health_path_skipped(spike_state):
    from app.middleware.error_spike import ErrorSpikeMiddleware, _counters

    middleware = ErrorSpikeMiddleware(app=MagicMock())
    req = _make_request(path="/health")

    async def make_500(_):
        resp = MagicMock()
        resp.status_code = 500
        return resp

    for _ in range(10):
        await middleware.dispatch(req, make_500)
    assert _counters.get("GET /health", 0) == 0
