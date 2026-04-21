"""
额度扣费服务
- 任务提交时扣费
- 任务失败返还
- 额度不足拦截
"""
from typing import Optional, Dict
from ..database import get_db
from .auth import get_user_by_id

# 各功能定价（积分/次）
PRICING: Dict[str, int] = {
    # 图片生成
    "image/style": 2,
    "image/realistic": 2,
    "image/multi-reference": 5,
    "image/inpaint": 3,

    # 视频生成
    "video/image-to-video": 10,
    "video/replace/element": 15,
    "video/clone": 20,
    "video/editor/parse": 5,
    "video/editor/regenerate": 10,
    "video/editor/compose": 15,

    # 数字人
    "avatar/generate": 10,

    # 语音
    "voice/clone": 5,
    "voice/tts": 2,
}


def get_task_cost(endpoint: str) -> int:
    """获取任务定价"""
    # 精确匹配
    if endpoint in PRICING:
        return PRICING[endpoint]

    # 前缀匹配
    for key, price in PRICING.items():
        if endpoint.startswith(key):
            return price

    # 默认价格
    return 5


def check_user_credits(user_id: str, required: int) -> bool:
    """检查用户额度是否充足"""
    user = get_user_by_id(user_id)
    if not user:
        return False
    return user.get("credits", 0) >= required


def deduct_credits(user_id: str, amount: int) -> bool:
    """扣减用户额度"""
    from .auth import update_user_credits
    return update_user_credits(user_id, -amount)


def add_credits(user_id: str, amount: int) -> bool:
    """增加用户额度（失败返还）"""
    from .auth import update_user_credits
    return update_user_credits(user_id, amount)


def get_user_credits(user_id: str) -> int:
    """获取用户当前额度"""
    user = get_user_by_id(user_id)
    if not user:
        return 0
    return user.get("credits", 0)


def create_consumption_record(
    user_id: str,
    task_id: str,
    module: str,
    cost: int,
    description: str,
    images: list = None,
    videos: list = None,
) -> bool:
    """创建消费记录（支持图片/视频URL）"""
    try:
        import json
        with get_db() as conn:
            cursor = conn.cursor()
            import uuid
            record_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO generation_history
                (id, user_id, module, prompt, images, videos, cost)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (record_id, user_id, module, description,
                  json.dumps(images or []),
                  json.dumps(videos or []),
                  cost))
            conn.commit()
            return True
    except Exception as e:
        from .logger import log_error
        log_error("创建消费记录失败", exc_info=True, error=str(e))
        return False
