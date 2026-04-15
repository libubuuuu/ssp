"""
模型熔断器服务
- 连续失败 3 次自动切断模型路由
- 1 分钟后尝试恢复
- 触发告警通知
"""
import asyncio
from typing import Dict, Optional
from datetime import datetime, timedelta
from ..database import get_db


class CircuitBreaker:
    """熔断器实现"""

    # 配置
    FAILURE_THRESHOLD = 3  # 连续失败次数阈值
    RESET_TIMEOUT = 60  # 恢复超时（秒）
    MONITORING_WINDOW = 300  # 监控窗口（秒）

    def __init__(self):
        # 内存缓存：model_name -> {failures: int, last_failure: datetime, state: str}
        self._states: Dict[str, dict] = {}

    def _get_state(self, model_name: str) -> dict:
        """获取模型状态"""
        if model_name not in self._states:
            self._states[model_name] = {
                "failures": 0,
                "successes": 0,
                "last_failure": None,
                "last_success": None,
                "state": "closed",  # closed: 正常，open: 熔断，half-open: 半开
            }
        return self._states[model_name]

    async def record_success(self, model_name: str) -> None:
        """记录成功"""
        state = self._get_state(model_name)
        state["failures"] = 0
        state["successes"] += 1
        state["last_success"] = datetime.now()
        state["state"] = "closed"

        # 更新数据库
        await self._update_db(model_name, success=True)

    async def record_failure(self, model_name: str) -> bool:
        """
        记录失败
        返回：是否需要触发告警
        """
        state = self._get_state(model_name)
        state["failures"] += 1
        state["last_failure"] = datetime.now()

        # 更新数据库
        await self._update_db(model_name, success=False)

        # 检查是否触发熔断
        if state["failures"] >= self.FAILURE_THRESHOLD:
            state["state"] = "open"
            return True  # 需要告警

        return False

    def is_available(self, model_name: str) -> bool:
        """检查模型是否可用"""
        state = self._get_state(model_name)

        if state["state"] == "closed":
            return True

        if state["state"] == "open":
            # 检查是否已过恢复超时
            if state["last_failure"]:
                elapsed = (datetime.now() - state["last_failure"]).total_seconds()
                if elapsed >= self.RESET_TIMEOUT:
                    state["state"] = "half-open"
                    return True
            return False

        # half-open 状态允许一次尝试
        return True

    def get_state(self, model_name: str) -> dict:
        """获取模型完整状态"""
        state = self._get_state(model_name)
        return {
            "model_name": model_name,
            "state": state["state"],
            "failures": state["failures"],
            "successes": state["successes"],
            "last_failure": state["last_failure"].isoformat() if state["last_failure"] else None,
            "last_success": state["last_success"].isoformat() if state["last_success"] else None,
        }

    async def _update_db(self, model_name: str, success: bool) -> None:
        """更新数据库记录"""
        try:
            with get_db() as conn:
                cursor = conn.cursor()

                # 先检查记录是否存在
                cursor.execute("SELECT id, success_count, failure_count FROM model_health WHERE model_name = ?", (model_name,))
                row = cursor.fetchone()

                if row:
                    # 记录存在，更新计数
                    success_count = row[1] + (1 if success else 0)
                    failure_count = row[2] + (0 if success else 1)
                    cursor.execute("""
                        UPDATE model_health
                        SET success_count = ?, failure_count = ?, last_error_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE last_error_at END, updated_at = CURRENT_TIMESTAMP
                        WHERE model_name = ?
                    """, (success_count, failure_count, not success, model_name))
                else:
                    # 记录不存在，插入新记录
                    cursor.execute("""
                        INSERT INTO model_health (model_name, success_count, failure_count, last_error_at, updated_at)
                        VALUES (?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END, CURRENT_TIMESTAMP)
                    """, (model_name, 1 if success else 0, 0 if success else 1, not success))

                # 如果失败次数达到阈值，禁用模型
                cursor.execute("SELECT failure_count FROM model_health WHERE model_name = ?", (model_name,))
                row = cursor.fetchone()
                if row and row[0] >= self.FAILURE_THRESHOLD:
                    cursor.execute("""
                        UPDATE model_health SET is_disabled = 1 WHERE model_name = ?
                    """, (model_name,))

                conn.commit()
        except Exception as e:
            print(f"Error updating model health DB: {e}")

    def get_all_models_status(self) -> list:
        """获取所有模型状态"""
        return [self.get_state(name) for name in self._states.keys()]


# 单例
_circuit_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker() -> CircuitBreaker:
    """获取熔断器单例"""
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker()
    return _circuit_breaker


def init_circuit_breaker() -> CircuitBreaker:
    """初始化熔断器"""
    global _circuit_breaker
    _circuit_breaker = CircuitBreaker()
    return _circuit_breaker
