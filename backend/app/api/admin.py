"""
管理员 API
- 模型健康状态
- 任务队列状态
- 平台统计数据
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
from ..services.circuit_breaker import get_circuit_breaker
from ..services.task_queue import get_task_queue
from ..database import get_db

router = APIRouter()


@router.get("/models/status")
async def get_models_status():
    """获取所有模型健康状态"""
    circuit_breaker = get_circuit_breaker()
    return {"models": circuit_breaker.get_all_models_status()}


@router.get("/models/{model_name}/status")
async def get_model_status(model_name: str):
    """获取指定模型健康状态"""
    circuit_breaker = get_circuit_breaker()
    return circuit_breaker.get_state(model_name)


@router.post("/models/{model_name}/reset")
async def reset_model(model_name: str):
    """重置模型状态（手动恢复）"""
    circuit_breaker = get_circuit_breaker()

    # 重置内存中的状态
    if model_name in circuit_breaker._states:
        circuit_breaker._states[model_name] = {
            "failures": 0,
            "successes": 0,
            "last_failure": None,
            "last_success": None,
            "state": "closed",
        }

    # 重置数据库状态
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE model_health
            SET success_count = 0, failure_count = 0, is_disabled = 0, last_error_at = NULL
            WHERE model_name = ?
        """, (model_name,))
        conn.commit()

    return {"message": f"模型 {model_name} 已重置"}


@router.get("/queue/status")
async def get_queue_status():
    """获取全局任务队列状态"""
    task_queue = get_task_queue()
    return task_queue.get_all_queues_status()


@router.get("/stats/overview")
async def get_stats_overview():
    """获取平台统计概览"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 用户总数
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]

        # 今日任务数
        cursor.execute("""
            SELECT COUNT(*) FROM tasks
            WHERE DATE(created_at) = DATE('now')
        """)
        today_tasks = cursor.fetchone()[0]

        # 总任务数
        cursor.execute("SELECT COUNT(*) FROM tasks")
        total_tasks = cursor.fetchone()[0]

        # 今日收入（完成的订单）
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) FROM credit_orders
            WHERE status = 'paid' AND DATE(paid_at) = DATE('now')
        """)
        today_revenue = cursor.fetchone()[0]

        # 模型使用统计
        cursor.execute("""
            SELECT model_used, COUNT(*) as count
            FROM tasks
            WHERE model_used IS NOT NULL
            GROUP BY model_used
        """)
        model_usage = [{"model": row[0], "count": row[1]} for row in cursor.fetchall()]

        # 任务状态统计
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM tasks
            GROUP BY status
        """)
        task_status = [{"status": row[0], "count": row[1]} for row in cursor.fetchall()]

        return {
            "total_users": total_users,
            "total_tasks": total_tasks,
            "today_tasks": today_tasks,
            "today_revenue": today_revenue,
            "model_usage": model_usage,
            "task_status": task_status,
        }


@router.get("/tasks/recent")
async def get_recent_tasks(limit: Optional[int] = 20):
    """获取最近的任务"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, module, status, model_used, cost_credits, created_at
            FROM tasks
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))

        tasks = []
        for row in cursor.fetchall():
            tasks.append({
                "id": row[0],
                "user_id": row[1],
                "module": row[2],
                "status": row[3],
                "model_used": row[4],
                "cost_credits": row[5],
                "created_at": row[6],
            })

        return {"tasks": tasks}
