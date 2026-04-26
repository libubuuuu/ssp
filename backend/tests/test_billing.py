"""
billing 服务单测(直接调函数,绕过 HTTP)
关注点:定价表、扣费、返还、不足拦截、消费记录写入
"""
import pytest

from app.services import billing


def _make_user(email: str, credits: int = 100) -> str:
    """创建用户并返回 user_id;直接走 services.auth"""
    from app.services.auth import create_user, set_user_credits
    user = create_user(email=email, password="secret123", name=email.split("@")[0])
    assert user is not None
    if credits != 100:
        set_user_credits(user["id"], credits)
    return user["id"]


def test_pricing_exact_match():
    assert billing.get_task_cost("image/style") == 2
    assert billing.get_task_cost("video/clone") == 20
    assert billing.get_task_cost("avatar/generate") == 10
    assert billing.get_task_cost("voice/tts") == 2


def test_pricing_prefix_match():
    # "image/style/anything" 应当通过前缀匹配命中 "image/style"
    assert billing.get_task_cost("image/style/extra/path") == 2
    # 完全没匹配时走默认 5
    assert billing.get_task_cost("totally/unknown/endpoint") == 5


def test_check_user_credits_sufficient():
    uid = _make_user("billing-a@example.com", credits=50)
    assert billing.check_user_credits(uid, 30) is True
    assert billing.check_user_credits(uid, 50) is True


def test_check_user_credits_insufficient():
    uid = _make_user("billing-b@example.com", credits=5)
    assert billing.check_user_credits(uid, 10) is False


def test_check_user_credits_unknown_user():
    assert billing.check_user_credits("ghost-user-id-not-real", 1) is False


def test_deduct_then_refund_round_trip():
    uid = _make_user("billing-c@example.com", credits=100)
    assert billing.deduct_credits(uid, 30) is True
    assert billing.get_user_credits(uid) == 70
    assert billing.add_credits(uid, 30) is True
    assert billing.get_user_credits(uid) == 100


def test_deduct_insufficient_returns_false_and_balance_unchanged():
    """余额不足 → 返回 False,余额不被扣到负数"""
    uid = _make_user("billing-low@example.com", credits=5)
    assert billing.deduct_credits(uid, 10) is False
    assert billing.get_user_credits(uid) == 5  # 未变


def test_deduct_atomic_no_negative_via_double_call():
    """串行模拟竞态:两次都过 check 余额够,但 SQL 原子拦下第二次"""
    uid = _make_user("billing-race@example.com", credits=10)
    # 第一次扣 10 成功,余额 → 0
    assert billing.deduct_credits(uid, 10) is True
    assert billing.get_user_credits(uid) == 0
    # 第二次再扣 10,SQL WHERE credits >= 10 拦下,余额保持 0(不会变 -10)
    assert billing.deduct_credits(uid, 10) is False
    assert billing.get_user_credits(uid) == 0


def test_deduct_zero_or_negative_amount_rejected():
    """边界:amount <= 0 直接 False,余额不动"""
    uid = _make_user("billing-zero@example.com", credits=100)
    assert billing.deduct_credits(uid, 0) is False
    assert billing.deduct_credits(uid, -5) is False
    assert billing.get_user_credits(uid) == 100


def test_deduct_unknown_user_returns_false():
    assert billing.deduct_credits("ghost-user-id-not-real", 1) is False


def test_consumption_record_persists():
    uid = _make_user("billing-d@example.com")
    ok = billing.create_consumption_record(
        user_id=uid,
        task_id="task-xxx",
        module="image/style",
        cost=2,
        description="测试任务",
        images=["https://cdn/x.png"],
    )
    assert ok is True

    from app.database import get_db
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT module, cost, images FROM generation_history WHERE user_id = ?", (uid,))
        rows = c.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "image/style"
    assert rows[0][1] == 2
    assert "https://cdn/x.png" in rows[0][2]


def test_get_user_credits_for_unknown():
    assert billing.get_user_credits("ghost-user-id-not-real") == 0
