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

    # AI 带货视频(2026-04-28 v3 新增)
    # analyze: VLM 视觉调用(qwen3-vl 235B via fal openrouter),成本约 $0.02 → 1 积分
    # preview: Nano Banana 单图,成本约 $0.04 → 2 积分
    # scene_regen: VLM 文本调用,几乎免费 → 1 积分
    # generate: Seedance 2.0 1080p 15s 带音,成本约 $4.20 → 30 积分(留毛利)
    "ad_video/analyze": 1,
    "ad_video/preview": 2,
    "ad_video/scene_regen": 1,
    "ad_video/generate": 30,
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
    """原子扣减用户额度。

    在 SQL 层 ``WHERE credits >= ?`` 保证"检查 + 扣减"原子,杜绝
    并发竞态把余额扣到负数。返回值即真实结果:
      True  = 余额充足,扣减成功
      False = 余额不足 / 用户不存在 / amount 非正数,数据未变
    """
    if amount <= 0:
        return False
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
               SET credits = credits - ?, updated_at = CURRENT_TIMESTAMP
             WHERE id = ? AND credits >= ?
        """, (amount, user_id, amount))
        conn.commit()
        return cursor.rowcount == 1


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
