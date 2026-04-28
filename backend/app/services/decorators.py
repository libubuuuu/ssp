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

            # 检查额度
            if not check_user_credits(user_id, cost):
                raise HTTPException(
                    status_code=402,
                    detail=f"额度不足，需要 {cost} 积分，当前剩余 {get_user_credits(user_id)} 积分"
                )

            # 扣减额度
            if not deduct_credits(user_id, cost):
                raise HTTPException(status_code=500, detail="扣费失败，请重试")

            task_id = str(uuid.uuid4())

            try:
                result = await func(*args, **kwargs)

                # 成功：创建消费记录
                description = module
                if isinstance(result, dict) and "description" in result:
                    description = result["description"]

                create_consumption_record(
                    user_id=user_id,
                    task_id=task_id,
                    module=module,
                    cost=cost,
                    description=description,
                )

                # 异步任务:登记 fal task_id 备退款,polling 检测到 failed 时由 refund_tracker.try_refund 退
                # 同步任务(result 无 task_id)走原 except 路径,这里 noop
                if isinstance(result, dict) and result.get("task_id"):
                    from .refund_tracker import register as register_refund
                    register_refund(result["task_id"], user_id, cost)

                # 附加 cost 字段
                if isinstance(result, dict):
                    result["cost"] = cost

                return result

            except HTTPException:
                # 明确抛出的 HTTP 异常：返还积分
                add_credits(user_id, cost)
                raise
            except Exception as e:
                # 未知错误：返还积分
                add_credits(user_id, cost)
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
