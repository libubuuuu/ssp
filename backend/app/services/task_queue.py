"""
任务队列服务
- 单用户并发任务控制（最多 5 个）
- 超出任务排队
- 实时排队进度查询
"""
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, field
from ..database import get_db


@dataclass
class QueuedTask:
    """排队中的任务"""
    task_id: str
    user_id: str
    module: str
    input_data: dict
    created_at: datetime = field(default_factory=datetime.now)
    position: int = 0


class TaskQueue:
    """任务队列管理器"""

    # 配置
    USER_CONCURRENCY_LIMIT = 5  # 单用户最大并发数

    def __init__(self):
        # 运行中的任务：user_id -> [task_id, ...]
        self._running: Dict[str, List[str]] = {}
        # 排队中的任务：user_id -> [QueuedTask, ...]
        self._queues: Dict[str, List[QueuedTask]] = {}

    async def enqueue_task(
        self,
        task_id: str,
        user_id: str,
        module: str,
        input_data: dict
    ) -> dict:
        """
        提交任务
        返回：{status: "processing" | "queued", position?: int}
        """
        # 获取用户当前运行任务数
        running_count = len(self._running.get(user_id, []))

        if running_count < self.USER_CONCURRENCY_LIMIT:
            # 可以直接处理
            self._add_running(user_id, task_id)
            await self._update_task_status(task_id, "processing")
            return {"status": "processing"}
        else:
            # 需要排队
            queued_task = QueuedTask(
                task_id=task_id,
                user_id=user_id,
                module=module,
                input_data=input_data,
            )
            self._add_to_queue(user_id, queued_task)

            # 更新排队位置
            position = self._get_queue_position(user_id, task_id)
            await self._update_task_status(task_id, "queued", position)

            return {"status": "queued", "position": position}

    async def complete_task(self, user_id: str, task_id: str) -> Optional[QueuedTask]:
        """
        完成任务，返回下一个排队任务（如果有）
        """
        # 从运行中移除
        self._remove_running(user_id, task_id)

        # 从队列中取出下一个任务
        next_task = self._dequeue(user_id)
        if next_task:
            await self._update_task_status(next_task.task_id, "processing")
            self._add_running(user_id, next_task.task_id)

        return next_task

    def get_queue_status(self, user_id: str, task_id: str) -> dict:
        """获取任务排队状态"""
        position = self._get_queue_position(user_id, task_id)
        running_count = len(self._running.get(user_id, []))

        return {
            "status": "queued" if position > 0 else "processing",
            "position": position,
            "running_count": running_count,
            "limit": self.USER_CONCURRENCY_LIMIT,
        }

    def get_all_queues_status(self) -> dict:
        """获取全局队列状态（管理员用）"""
        total_running = sum(len(tasks) for tasks in self._running.values())
        total_queued = sum(len(tasks) for tasks in self._queues.values())

        user_stats = {}
        for user_id in set(list(self._running.keys()) + list(self._queues.keys())):
            user_stats[user_id] = {
                "running": len(self._running.get(user_id, [])),
                "queued": len(self._queues.get(user_id, [])),
            }

        return {
            "total_running": total_running,
            "total_queued": total_queued,
            "user_stats": user_stats,
        }

    def _add_running(self, user_id: str, task_id: str) -> None:
        """添加运行中任务"""
        if user_id not in self._running:
            self._running[user_id] = []
        self._running[user_id].append(task_id)

    def _remove_running(self, user_id: str, task_id: str) -> None:
        """移除运行中任务"""
        if user_id in self._running:
            self._running[user_id] = [t for t in self._running[user_id] if t != task_id]
            if not self._running[user_id]:
                del self._running[user_id]

    def _add_to_queue(self, user_id: str, task: QueuedTask) -> None:
        """添加排队任务"""
        if user_id not in self._queues:
            self._queues[user_id] = []
        self._queues[user_id].append(task)
        self._update_queue_positions(user_id)

    def _dequeue(self, user_id: str) -> Optional[QueuedTask]:
        """从队列中取出下一个任务"""
        if user_id not in self._queues or not self._queues[user_id]:
            return None

        task = self._queues[user_id].pop(0)
        self._update_queue_positions(user_id)
        return task

    def _update_queue_positions(self, user_id: str) -> None:
        """更新排队位置"""
        if user_id in self._queues:
            for i, task in enumerate(self._queues[user_id]):
                task.position = i + 1

    def _get_queue_position(self, user_id: str, task_id: str) -> int:
        """获取排队位置"""
        if user_id not in self._queues:
            return 0

        for task in self._queues[user_id]:
            if task.task_id == task_id:
                return task.position

        return 0

    async def _update_task_status(
        self,
        task_id: str,
        status: str,
        position: Optional[int] = None
    ) -> None:
        """更新数据库中的任务状态"""
        try:
            with get_db() as conn:
                cursor = conn.cursor()

                if position is not None:
                    cursor.execute("""
                        UPDATE tasks SET status = ?, queue_position = ? WHERE id = ?
                    """, (status, position, task_id))
                else:
                    cursor.execute("""
                        UPDATE tasks SET status = ? WHERE id = ?
                    """, (status, task_id))

                conn.commit()
        except Exception as e:
            print(f"Error updating task status DB: {e}")


# 单例
_task_queue: Optional[TaskQueue] = None


def get_task_queue() -> TaskQueue:
    """获取任务队列单例"""
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue()
    return _task_queue


def init_task_queue() -> TaskQueue:
    """初始化任务队列"""
    global _task_queue
    _task_queue = TaskQueue()
    return _task_queue
