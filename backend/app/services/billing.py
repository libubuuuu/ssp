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
    "video/replicate": 0,  # 入历史用,真正定价按时长在 replicate.py 算

    # AI 带货视频(2026-04-28 v3 新增)
    # analyze: VLM 视觉调用(qwen3-vl 235B via fal openrouter),成本约 $0.02 → 1 积分
    # preview: Nano Banana 单图,成本约 $0.04 → 2 积分
    # scene_regen: VLM 文本调用,几乎免费 → 1 积分
    # generate: Seedance 2.0 1080p 15s 带音,成本约 $4.20 → 30 积分(留毛利)




    # 七十七续:口播带货工作台 3 档定价(每分钟成片;按秒折算见 oral.compute_charge)
    # 经济 ¥80 / 标准 ¥180 / 顶级 ¥350,假设 1 积分 ≈ ¥0.50
    "oral_broadcast/economy":  160,   # 160 积分/分钟 = 2.67 积分/秒
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
