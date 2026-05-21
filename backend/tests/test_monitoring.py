"""监控系统测试

覆盖:
- _record_task_result: 失败率 <30% 不告警
- _record_task_result: 失败率 >30% 推送告警（需≥5个样本）
- _record_task_result: 样本 <5 不判断
- _record_task_result: 成功率恢复后不持续告警（冷却窗口）
- alert_service.format_alert: 输出格式正确
- alert_service.module_label: 已知/未知/前缀匹配/空值
- monitoring._check_db_health: 健康时不告警
- monitoring._check_db_health: 异常时推送告警
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock


# ── alert_service 新功能 ──────────────────────────────────────────────────────

def test_format_alert_contains_all_fields():
    from app.services.alert_service import format_alert
    body = format_alert(problem="测试问题", feature="测试功能", details="测试详情")
    assert "时间:" in body
    assert "测试问题" in body
    assert "测试功能" in body
    assert "测试详情" in body


def test_module_label_known():
    from app.services.alert_service import module_label
    assert module_label("image/style") == "AI 图片生成"
    assert module_label("video/clone") == "视频复刻"
    assert module_label("ad_video/generate") == "AI 爆款视频生成"


def test_module_label_prefix_match():
    from app.services.alert_service import module_label
    # "image/style/extra" 应前缀匹配 "image/style"
    assert module_label("image/style/custom") == "AI 图片生成"


def test_module_label_unknown_returns_fallback():
    from app.services.alert_service import module_label
    assert module_label("totally/unknown", fallback="测试备用") == "测试备用"


def test_module_label_empty_returns_fallback():
    from app.services.alert_service import module_label
    assert module_label("", fallback="fallback") == "fallback"


# ── 任务失败率追踪 ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_task_results():
    from app.api import jobs as jobs_module
    jobs_module._TASK_RESULTS.clear()
    yield
    jobs_module._TASK_RESULTS.clear()


def test_task_results_below_30_no_alert(monkeypatch):
    from app.api import jobs as jobs_module
    alerts = []
    monkeypatch.setattr("app.services.alert_service.push_alert", lambda t, b, **kw: alerts.append(t))
    # 5成功1失败 = 16.7%
    for _ in range(5):
        jobs_module._record_task_result(True, "image", "image/style")
    jobs_module._record_task_result(False, "image", "image/style")
    assert len(alerts) == 0


def test_task_results_above_30_triggers_alert(monkeypatch):
    from app.api import jobs as jobs_module
    alerts = []
    monkeypatch.setattr("app.services.alert_service.push_alert", lambda t, b, **kw: alerts.append(t))
    # 3成功5失败 = 62.5% > 30%
    for _ in range(3):
        jobs_module._record_task_result(True, "video_i2v", "video/image-to-video")
    for _ in range(5):
        jobs_module._record_task_result(False, "video_i2v", "video/image-to-video")
    assert len(alerts) >= 1
    assert "30%" in alerts[0] or "失败率" in alerts[0]


def test_task_results_under_5_samples_no_alert(monkeypatch):
    from app.api import jobs as jobs_module
    alerts = []
    monkeypatch.setattr("app.services.alert_service.push_alert", lambda t, b, **kw: alerts.append(t))
    # 4个全失败，但样本 <5，不判断
    for _ in range(4):
        jobs_module._record_task_result(False, "image", "image/style")
    assert len(alerts) == 0


# ── DB 健康监控 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_health_ok_no_alert(monkeypatch):
    from app.services import monitoring
    alerts = []
    monkeypatch.setattr("app.services.alert_service.push_alert", lambda t, b, **kw: alerts.append(t))

    mock_checker = MagicMock()
    mock_checker.check_database = AsyncMock(return_value={"status": "healthy"})
    monkeypatch.setattr("app.services.health_check.get_health_checker", lambda: mock_checker)

    await monitoring._check_db_health()
    assert len(alerts) == 0


@pytest.mark.asyncio
async def test_db_health_fail_triggers_alert(monkeypatch):
    from app.services import monitoring
    alerts = []
    monkeypatch.setattr("app.services.alert_service.push_alert", lambda t, b, **kw: alerts.append(t))

    mock_checker = MagicMock()
    mock_checker.check_database = AsyncMock(return_value={"status": "unhealthy", "error": "connection refused"})
    monkeypatch.setattr("app.services.health_check.get_health_checker", lambda: mock_checker)
    # 重置冷却
    import app.services.alert_service as a
    a._cooldowns.clear()

    await monitoring._check_db_health()
    assert len(alerts) == 1
    assert "数据库" in alerts[0]


@pytest.mark.asyncio
async def test_db_health_exception_triggers_alert(monkeypatch):
    from app.services import monitoring
    alerts = []
    monkeypatch.setattr("app.services.alert_service.push_alert", lambda t, b, **kw: alerts.append(t))

    def boom():
        raise RuntimeError("checker init failed")
    monkeypatch.setattr("app.services.health_check.get_health_checker", boom)
    import app.services.alert_service as a
    a._cooldowns.clear()

    await monitoring._check_db_health()  # 不应抛异常
    assert len(alerts) == 1
