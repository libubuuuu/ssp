"""
通用装饰器
- require_credits: 额度扣费装饰器，自动处理检查/扣费/返还/消费记录
"""
from functools import wraps
from fastapi import HTTPException
from .billing import get_task_cost, check_user_credits, deduct_credits, add_credits, create_consumption_record
# get_current_user 由 FastAPI Depends() 注入,这里不需要 import(且会循环导入)
import uuid


def require_credits(module: str):
    """
    额度扣费装饰器

    用法:
        @router.post("/style")
        @require_credits("image/style")
        async def generate(req: ImageStyleRequest, current_user: dict):
            ...

    装饰器自动处理:
    1. 获取该模块的任务成本
    2. 检查用户积分是否充足
    3. 扣减积分
    4. 任务成功 → 创建消费记录
    5. 任务失败 → 返还积分
    """
    cost = get_task_cost(module)

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 从 kwargs 中提取 current_user
            current_user = kwargs.get("current_user")
            if not current_user:
                # 尝试从Depends获取
                for arg in args:
                    if isinstance(arg, dict) and "id" in arg:
                        current_user = arg
                        break

            if not current_user:
                raise HTTPException(status_code=401, detail="未登录")

            user_id = current_user["id"]
            role = current_user.get("role", "user")

            # P161(2026-05-06)任务级限流(每分钟次数 + 每日 quota,admin 跳过)
            from .rate_limiter import check_task_quota
            allowed, reason = check_task_quota(user_id, cost, role=role)
            if not allowed:
                raise HTTPException(status_code=429, detail=reason)

            # 检查额度
            if not check_user_credits(user_id, cost):
                raise HTTPException(
                    status_code=402,
                    detail=f"额度不足，需要 {cost} 积分，当前剩余 {get_user_credits(user_id)} 积分"
                )

            task_id = str(uuid.uuid4())

            # P158 扣减额度 + 埋 ledger(传 ref_id=task_id)
            if not deduct_credits(user_id, cost, ref_id=task_id, module=module):
                raise HTTPException(status_code=500, detail="扣费失败，请重试")

            try:
                result = await func(*args, **kwargs)

                # 成功：创建消费记录
                description = module
                if isinstance(result, dict) and "description" in result:
                    description = result["description"]

                # 从 result 提取 URL,sync API 立刻有,async API task_id 会留给后续 polling 回写
                hist_images: list[str] = []
                hist_videos: list[str] = []
                if isinstance(result, dict):
                    if result.get("image_url"):
                        hist_images.append(result["image_url"])
                    if result.get("video_url"):
                        hist_videos.append(result["video_url"])

                # 异步任务用 FAL task_id 作 record_id,后续 polling 完成时能 UPDATE 回填 URL
                # sync API 没 task_id → 用内部 uuid
                record_id = (result.get("task_id") if isinstance(result, dict) else None) or task_id
                create_consumption_record(
                    user_id=user_id,
                    task_id=record_id,
                    module=module,
                    cost=cost,
                    description=description,
                    images=hist_images or None,
                    videos=hist_videos or None,
                )

                # 异步任务:登记 fal task_id 备退款,polling 检测到 failed 时由 refund_tracker.try_refund 退
                if isinstance(result, dict) and result.get("task_id"):
                    from .refund_tracker import register as register_refund
                    register_refund(result["task_id"], user_id, cost)

                # P158:附加 cost + new_credits 字段(前端用 server 真实余额覆盖 localStorage)
                if isinstance(result, dict):
                    result["cost"] = cost
                    result["new_credits"] = get_user_credits(user_id)

                return result

            except HTTPException:
                # P158 退款 + 埋 ledger(reason='task_refund')
                add_credits(user_id, cost, reason="task_refund", ref_id=task_id, module=module)
                raise
            except Exception as e:
                add_credits(user_id, cost, reason="task_refund", ref_id=task_id, module=module)
                raise HTTPException(status_code=500, detail=str(e))

        return wrapper

    return decorator


def get_user_credits(user_id: int) -> int:
    """获取用户剩余积分"""
    from ..database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 0
