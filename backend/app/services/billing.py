"""
额度扣费服务
- 任务提交时扣费
- 任务失败返还
- 额度不足拦截
"""
from typing import Optional, Dict
from ..database import get_db
from .auth import get_user_by_id

# 各功能定价(积分/次)
# 2026-05-13 老板重定:50 积分 = 1 元,视频 50 积分/秒,图片 20 积分/张,文案 5 积分/次
PRICING: Dict[str, int] = {
    # 图片生成(GPT-image-2)统一 20/张
    "image/style": 20,
    "image/realistic": 20,
    "image/multi-reference": 20,
    "image/inpaint": 20,

    # 视频生成(动态:实际按 duration_sec * 50 计算,这里写下限作 fallback)
    "video/image-to-video": 50,
    "video/replace/element": 50,
    "video/clone": 50,
    "video/editor/parse": 5,
    "video/editor/regenerate": 50,
    "video/editor/compose": 50,
    "video/replicate": 0,  # 真正定价按时长 replicate.py 算 (* 50)

    # AI 带货视频
    "ad_video/analyze": 5,        # 生成文案
    "ad_video/preview": 20,       # 分镜图 1 张
    "ad_video/scene_regen": 5,    # 重出一段文案
    "ad_video/generate": 50,      # 实际按 duration_sec * 50

    # 通用 video_general / frame-extract / replicate
    "video/general/analyze":      5,
    "video/general/storyboard":   20,
    "video/general/generate":     50,  # 实际按 sum(scenes.duration) * 50
    "video/frame-extract/analyze": 5,
    "video/frame-extract/replace": 20, # 单张九宫格 20
    "video/frame-extract/generate": 50, # 实际按 sum(scenes.duration) * 50
    "video/replicate/analyze": 5,
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


def deduct_credits(user_id: str, amount: int, *, ref_id: str = None, module: str = None) -> bool:
    """原子扣减用户额度 + 写 credits_ledger(P158)。

    保留 bool 返回接口(21 处调用方不用改)。需要 new_credits 用 get_user_credits()。
    SQL 层 ``WHERE credits >= ?`` 保证"检查 + 扣减"原子。
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
        success = cursor.rowcount == 1
        if success:
            cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            new_credits = row[0] if row else 0
        conn.commit()

    if success:
        # P157 ledger 埋点
        from .credits_ledger import record_credits_change
        record_credits_change(
            user_id=user_id, delta=-amount, balance_after=new_credits,
            reason="task_charge", ref_id=ref_id, module=module,
        )
    return success


def add_credits(user_id: str, amount: int, *, reason: str = "task_refund", ref_id: str = None, module: str = None) -> bool:
    """增加用户额度 + 写 credits_ledger(P158 原子,防 race)。

    Args:
        reason: 'task_refund' / 'recharge_wx' / 'recharge_alipay' / 'system_compensation'
    """
    if amount <= 0:
        return False
    with get_db() as conn:
        cursor = conn.cursor()
        # P158 原子加(防 race,替代之前 update_user_credits 的 SET)
        cursor.execute(
            "UPDATE users SET credits = credits + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (amount, user_id),
        )
        success = cursor.rowcount == 1
        if success:
            cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            new_credits = row[0] if row else 0
        conn.commit()

    if success:
        from .credits_ledger import record_credits_change
        record_credits_change(
            user_id=user_id, delta=amount, balance_after=new_credits,
            reason=reason, ref_id=ref_id, module=module,
        )
    return success


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
            # 用传入的 task_id 当 record_id(异步任务能由 tasks.py /status 完成时按 task_id UPDATE 回填 URL)
            # 老代码忽略了 task_id 永远 uuid4(),tasks.py SELECT WHERE id=fal_task_id 永不命中 → 重复插
            record_id = task_id if task_id else str(uuid.uuid4())
            cursor.execute("""
                INSERT OR REPLACE INTO generation_history
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
