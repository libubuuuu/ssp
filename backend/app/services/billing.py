"""
额度扣费服务
- 任务提交时扣费
- 任务失败返还
- 额度不足拦截
"""
import time
import uuid
from typing import Optional, Dict
from ..database import get_db
from .auth import get_user_by_id


def _ledger_in_tx(cursor, user_id: str, delta: int, balance_after: int,
                  reason: str, ref_id: Optional[str], module: Optional[str]) -> None:
    """同一事务内写 credits_ledger，由调用方负责 commit。"""
    cursor.execute("""
        INSERT INTO credits_ledger (id, user_id, delta, balance_after, reason, ref_id, module, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (uuid.uuid4().hex, user_id, int(delta), int(balance_after), reason, ref_id, module, time.time()))

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

    # 口播 ad_video 系列
    "ad_video/analyze":     5,   # 视频拆解分析
    "ad_video/preview":    20,   # 首帧预览图,等同一张图片
    "ad_video/generate":   50,   # 完整视频生成(提交到 jobs 队列)
    "ad_video/scene_regen": 5,   # 单场景重新生成

    # 通用 video_general / frame-extract / replicate
    "video/general/analyze":      5,
    "video/general/storyboard":   20,
    "video/general/generate":     55,  # 实际按 sum(scenes.duration) * 55(图片复刻 ¥1.1/s)
    "video/frame-extract/analyze": 5,
    "video/frame-extract/replace": 20, # 单张九宫格 20
    "video/frame-extract/generate": 65, # 实际按 sum(scenes.duration) * 65(分镜复刻 ¥1.3/s)
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
    """原子扣减用户额度 + 在同一事务写 credits_ledger。

    SQL 层 ``WHERE credits >= ?`` 保证"检查 + 扣减"原子，不存在竞态窗口。
    ledger 写入与扣费在同一 commit，两者要么同时成功要么同时回滚。
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
        if cursor.rowcount != 1:
            conn.commit()
            return False
        cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
        new_credits = cursor.fetchone()[0]
        _ledger_in_tx(cursor, user_id, -amount, new_credits, "task_charge", ref_id, module)
        conn.commit()
    return True


def add_credits(user_id: str, amount: int, *, reason: str = "task_refund", ref_id: str = None, module: str = None) -> bool:
    """原子增加用户额度 + 在同一事务写 credits_ledger。

    ledger 写入与加费在同一 commit，两者要么同时成功要么同时回滚。
    """
    if amount <= 0:
        return False
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET credits = credits + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (amount, user_id),
        )
        if cursor.rowcount != 1:
            conn.commit()
            return False
        cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
        new_credits = cursor.fetchone()[0]
        _ledger_in_tx(cursor, user_id, amount, new_credits, reason, ref_id, module)
        conn.commit()
    return True


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
