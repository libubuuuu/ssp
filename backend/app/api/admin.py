import os
"""
管理员 API
- 模型健康状态
- 任务队列状态
- 平台统计数据
"""
from fastapi import UploadFile, File, APIRouter, HTTPException, Depends
from typing import Optional
from ..services.circuit_breaker import get_circuit_breaker
from ..services.task_queue import get_task_queue
from ..database import get_db
from .auth import get_current_user

router = APIRouter()


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """验证管理员权限"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user


@router.get("/models/status")
async def get_models_status(_admin: dict = Depends(require_admin)):
    """获取所有模型健康状态"""
    circuit_breaker = get_circuit_breaker()
    return {"models": circuit_breaker.get_all_models_status()}


@router.get("/models/{model_name}/status")
async def get_model_status(model_name: str, _admin: dict = Depends(require_admin)):
    """获取指定模型健康状态"""
    circuit_breaker = get_circuit_breaker()
    return circuit_breaker.get_state(model_name)


@router.post("/models/{model_name}/reset")
async def reset_model(model_name: str, _admin: dict = Depends(require_admin)):
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
async def get_queue_status(_admin: dict = Depends(require_admin)):
    """获取全局任务队列状态"""
    task_queue = get_task_queue()
    return task_queue.get_all_queues_status()


@router.get("/stats/overview")
async def get_stats_overview(_admin: dict = Depends(require_admin)):
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
async def get_recent_tasks(limit: Optional[int] = 20, _admin: dict = Depends(require_admin)):
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


@router.get("/orders")
async def admin_list_orders(status: str = "all", current_user: dict = Depends(get_current_user)):
    """管理员：查所有订单（status=pending/paid/all）"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="无权限")
    
    with get_db() as conn:
        cursor = conn.cursor()
        if status == "all":
            cursor.execute("""
                SELECT o.id, o.user_id, u.email, o.amount, o.price, o.status, o.created_at, o.paid_at
                FROM credit_orders o LEFT JOIN users u ON o.user_id = u.id
                ORDER BY o.created_at DESC LIMIT 200
            """)
        else:
            cursor.execute("""
                SELECT o.id, o.user_id, u.email, o.amount, o.price, o.status, o.created_at, o.paid_at
                FROM credit_orders o LEFT JOIN users u ON o.user_id = u.id
                WHERE o.status = ?
                ORDER BY o.created_at DESC LIMIT 200
            """, (status,))
        rows = cursor.fetchall()
    
    orders = [{
        "id": r[0], "user_id": r[1], "user_email": r[2],
        "credits": r[3], "price": r[4], "status": r[5],
        "created_at": r[6], "paid_at": r[7],
    } for r in rows]
    return {"orders": orders, "total": len(orders)}


@router.get("/users-list")
async def admin_list_users(current_user: dict = Depends(get_current_user)):
    """管理员：列出所有用户"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="无权限")
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, email, name, role, credits, created_at
            FROM users ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
    
    users = [{"id": r[0], "email": r[1], "name": r[2], "role": r[3], "credits": r[4], "created_at": r[5]} for r in rows]
    return {"users": users}


@router.post("/users/{user_id}/adjust-credits")
async def admin_adjust_credits(user_id: str, delta: int, current_user: dict = Depends(get_current_user)):
    """管理员：手动加/减用户积分（delta 可正可负）"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="无权限")
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        new_credits = max(0, row[0] + delta)
        cursor.execute("UPDATE users SET credits = ? WHERE id = ?", (new_credits, user_id))
        conn.commit()
    return {"success": True, "user_id": user_id, "new_credits": new_credits, "delta": delta}


@router.post("/upload-qr")
async def admin_upload_qr(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """管理员上传收款码图片"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="无权限")
    
    # 保存到 frontend/public/qr-payment.png
    target = "/root/ssp/frontend/public/qr-payment.png"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    
    contents = await file.read()
    # 简单校验：必须是图片
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="必须上传图片")
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片超过 5MB")
    
    with open(target, "wb") as f:
        f.write(contents)
    
    # 加个时间戳避免浏览器缓存
    import time
    return {"success": True, "url": f"/qr-payment.png?v={int(time.time())}", "size": len(contents)}
