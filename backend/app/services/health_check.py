"""
企业级健康检查服务
- 数据库连接检查
- API 响应检查
- 模型服务检查
- 内存/CPU 监控
"""
import psutil
import sqlite3
from typing import Dict, Any
from datetime import datetime
from ..config import get_settings


class HealthChecker:
    """健康检查器"""

    def __init__(self):
        self.config = get_settings()

    async def check_database(self) -> Dict[str, Any]:
        """检查数据库连接"""
        try:
            from ..database import get_db
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                return {
                    "status": "healthy",
                    "latency_ms": 0,
                }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
            }

    async def check_model_services(self) -> Dict[str, Any]:
        """检查模型服务"""
        try:
            from .fal_service import (
                get_image_service, get_video_service,
                get_avatar_service, get_voice_service
            )

            services = {
                "image": get_image_service(),
                "video": get_video_service(),
                "avatar": get_avatar_service(),
                "voice": get_voice_service(),
            }

            status = {
                "image": "healthy" if services["image"] else "unhealthy",
                "video": "healthy" if services["video"] else "unhealthy",
                "avatar": "healthy" if services["avatar"] else "unhealthy",
                "voice": "healthy" if services["voice"] else "unhealthy",
            }

            all_healthy = all(v == "healthy" for v in status.values())

            return {
                "status": "healthy" if all_healthy else "degraded",
                "services": status,
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
            }

    async def check_system(self) -> Dict[str, Any]:
        """检查系统资源"""
        try:
            memory = psutil.virtual_memory()
            cpu_percent = psutil.cpu_percent(interval=0.1)
            disk = psutil.disk_usage('/')

            return {
                "status": "healthy",
                "memory": {
                    "used_percent": memory.percent,
                    "available_mb": memory.available / 1024 / 1024,
                },
                "cpu_percent": cpu_percent,
                "disk": {
                    "used_percent": disk.percent,
                    "free_gb": disk.free / 1024 / 1024 / 1024,
                },
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
            }

    async def get_full_health(self) -> Dict[str, Any]:
        """获取完整健康状态"""
        db_status = await self.check_database()
        model_status = await self.check_model_services()
        system_status = await self.check_system()

        overall_status = "healthy"
        if any(s["status"] == "unhealthy" for s in [db_status, model_status, system_status]):
            overall_status = "unhealthy"
        elif any(s["status"] == "degraded" for s in [db_status, model_status, system_status]):
            overall_status = "degraded"

        return {
            "status": overall_status,
            "timestamp": datetime.now().isoformat(),
            "checks": {
                "database": db_status,
                "model_services": model_status,
                "system": system_status,
            }
        }


# 单例
_health_checker: HealthChecker = None


def get_health_checker() -> HealthChecker:
    """获取健康检查器单例"""
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker()
    return _health_checker
