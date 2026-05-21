"""后台监控循环

每 2 分钟检查一次：
- 数据库连接是否正常

检测到异常立即通过 alert_service 推送微信告警。
"""
import asyncio
from app.services.logger import log_error, log_info

_DB_CHECK_INTERVAL = 120  # 2 分钟


async def monitoring_loop() -> None:
    """主监控循环，在 main.py lifespan 中作为 background task 启动。"""
    while True:
        await asyncio.sleep(_DB_CHECK_INTERVAL)
        await _check_db_health()


async def _check_db_health() -> None:
    try:
        from app.services.health_check import get_health_checker
        checker = get_health_checker()
        result = await checker.check_database()
        if result.get("status") != "healthy":
            from app.services.alert_service import push_alert, format_alert
            push_alert(
                "🚨 数据库异常",
                format_alert(
                    problem=f"数据库健康检查失败: {result.get('error', '未知错误')}",
                    feature="所有功能（数据库是底层基础）",
                    details=f"错误详情: {result}\n日志: /var/log/ssp-backend-*.log\n数据库路径: /opt/ssp/backend/dev.db",
                ),
                alert_key="db_health",
                cooldown=300,
            )
    except Exception as e:
        log_error(f"monitoring: db health check 异常: {e}")
        # DB 检查本身抛异常也告警
        try:
            from app.services.alert_service import push_alert, format_alert
            push_alert(
                "🚨 数据库监控异常",
                format_alert(
                    problem=f"无法执行数据库健康检查: {e}",
                    feature="所有功能（数据库是底层基础）",
                    details="监控程序本身异常，数据库状态未知\n请立即检查服务器",
                ),
                alert_key="db_monitor_error",
                cooldown=300,
            )
        except Exception:
            pass
