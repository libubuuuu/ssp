"""P3-3 + P3-4 注册 IP 限流测试

覆盖:
- 同 IP 24h 内成功注册 3 次后第 4 次 429
- 不同 IP 不互相影响
- 24h 之前的旧记录不计入(GC + 时间窗口)
- count_recent_registers_from_ip / record_register_ip 单元
"""
import time as _time

from app.services import rate_limiter as rl


def _put_code(email: str, code: str = "999999"):
    from app.api import auth as auth_module
    auth_module._EMAIL_CODES[email] = {
        "code": code,
        "expires_at": _time.time() + 300,
        "sent_at": _time.time(),
        "purpose": "register",
    }


def _register_with_ip(client, email: str, ip: str = "1.2.3.4") -> int:
    """走 register 端点,通过 X-Forwarded-For 模拟 IP;返回 status_code"""
    _put_code(email)
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": "secret123", "code": "999999"},
        headers={"X-Forwarded-For": ip},
    )
    return r.status_code


# === 单元测试:rate_limiter 内部函数 ===

def test_count_zero_for_fresh_ip():
    assert rl.count_recent_registers_from_ip("1.1.1.1") == 0


def test_record_then_count():
    rl.record_register_ip("9.9.9.9")
    assert rl.count_recent_registers_from_ip("9.9.9.9") == 1
    rl.record_register_ip("9.9.9.9")
    assert rl.count_recent_registers_from_ip("9.9.9.9") == 2


def test_old_records_not_counted():
    """超过 24h 的记录不算"""
    from app.database import get_db
    old_ts = _time.time() - rl.REGISTER_IP_WINDOW - 100
    with get_db() as conn:
        conn.execute("INSERT INTO register_ip_log (ip, registered_at_ts) VALUES (?, ?)",
                     ("8.8.8.8", old_ts))
        conn.commit()
    assert rl.count_recent_registers_from_ip("8.8.8.8") == 0


def test_assert_within_quota_passes():
    rl.assert_register_ip_quota("2.2.2.2")  # 0 次,不抛
    rl.record_register_ip("2.2.2.2")
    rl.record_register_ip("2.2.2.2")
    rl.assert_register_ip_quota("2.2.2.2")  # 2 次,仍允许


def test_assert_at_limit_raises_429():
    import pytest
    from fastapi import HTTPException
    for _ in range(rl.REGISTER_IP_LIMIT):
        rl.record_register_ip("3.3.3.3")
    with pytest.raises(HTTPException) as ei:
        rl.assert_register_ip_quota("3.3.3.3")
    assert ei.value.status_code == 429
    assert "上限" in ei.value.detail


# === 集成测试:走 register 端点 ===

def test_first_three_register_succeed_same_ip(client):
    for i in range(3):
        sc = _register_with_ip(client, f"u{i}@ipsame.com", ip="10.0.0.1")
        assert sc == 200, f"第 {i+1} 次注册应成功"


def test_fourth_register_same_ip_429(client):
    for i in range(3):
        sc = _register_with_ip(client, f"u{i}@ipfourth.com", ip="10.0.0.2")
        assert sc == 200
    sc = _register_with_ip(client, "u4@ipfourth.com", ip="10.0.0.2")
    assert sc == 429


def test_different_ips_isolated(client):
    """A IP 满了 B IP 不受影响"""
    for i in range(3):
        sc = _register_with_ip(client, f"a{i}@isolated.com", ip="10.0.0.10")
        assert sc == 200
    # A IP 第 4 次:429
    sc_a4 = _register_with_ip(client, "a4@isolated.com", ip="10.0.0.10")
    assert sc_a4 == 429
    # B IP 第 1 次:200
    sc_b1 = _register_with_ip(client, "b1@isolated.com", ip="10.0.0.20")
    assert sc_b1 == 200


def test_failed_register_does_not_count_success_quota(client):
    """注册失败不计成功配额(P3-3 行为),但会计 BUG-1 失败软配额"""
    from app.api import auth as auth_module
    # 3 次失败仍未到失败软配额(10),所以仍能成功注册
    for i in range(3):
        _put_code(f"fail{i}@nope.com", code="111111")
        r = client.post(
            "/api/auth/register",
            json={"email": f"fail{i}@nope.com", "password": "secret123", "code": "222222"},
            headers={"X-Forwarded-For": "10.0.0.30"},
        )
        assert r.status_code == 400

    # 同 IP 3 次失败后还能成功 — 失败配额(10)还没到
    sc = _register_with_ip(client, "ok@later.com", ip="10.0.0.30")
    assert sc == 200


# === BUG-1:失败软配额测试 ===

def test_failure_quota_blocks_after_10_wrong_codes(client):
    """同 IP 失败 10 次后,第 11 次直接 429(脚本爆破挡板)"""
    ip = "10.0.99.1"
    for i in range(10):
        _put_code(f"brute{i}@nope.com", code="111111")
        r = client.post(
            "/api/auth/register",
            json={"email": f"brute{i}@nope.com", "password": "secret123", "code": "222222"},
            headers={"X-Forwarded-For": ip},
        )
        assert r.status_code == 400, f"第 {i+1} 次应该 400(错码),实际 {r.status_code}"

    # 第 11 次 — 不管 code 对不对,先撞 429
    _put_code("brute11@nope.com", code="222222")
    r = client.post(
        "/api/auth/register",
        json={"email": "brute11@nope.com", "password": "secret123", "code": "222222"},
        headers={"X-Forwarded-For": ip},
    )
    assert r.status_code == 429
    assert "失败次数过多" in r.json()["detail"]


def test_failure_quota_blocks_even_correct_code(client):
    """关键:被失败软配额封后,正确码也注不进来(挡脚本"试出来再用对码注")"""
    ip = "10.0.99.2"
    # 先打 10 次错码
    for i in range(10):
        _put_code(f"x{i}@nope.com", code="111111")
        client.post(
            "/api/auth/register",
            json={"email": f"x{i}@nope.com", "password": "secret123", "code": "222222"},
            headers={"X-Forwarded-For": ip},
        )

    # 现在用正确的 code 注册,也 429
    _put_code("legit@later.com", code="999999")
    r = client.post(
        "/api/auth/register",
        json={"email": "legit@later.com", "password": "secret123", "code": "999999"},
        headers={"X-Forwarded-For": ip},
    )
    assert r.status_code == 429


def test_failure_quota_isolated_per_ip(client):
    """A IP 失败 10 次封了,B IP 不受影响"""
    ip_a = "10.0.99.3"
    ip_b = "10.0.99.4"
    for i in range(10):
        _put_code(f"a{i}@nope.com", code="111111")
        client.post(
            "/api/auth/register",
            json={"email": f"a{i}@nope.com", "password": "secret123", "code": "222222"},
            headers={"X-Forwarded-For": ip_a},
        )

    # B IP 第 1 次,正常
    sc = _register_with_ip(client, "b1@isolated.com", ip=ip_b)
    assert sc == 200


def test_failure_quota_unit():
    """单元:rate_limiter 内部函数计数 / 临界 / 隔离"""
    from app.services import rate_limiter as rl
    assert rl.count_recent_register_failures_from_ip("4.4.4.4") == 0
    rl.record_register_ip_failure("4.4.4.4", "wrong_code")
    rl.record_register_ip_failure("4.4.4.4", "expired_code")
    assert rl.count_recent_register_failures_from_ip("4.4.4.4") == 2
    # 5.5.5.5 独立
    assert rl.count_recent_register_failures_from_ip("5.5.5.5") == 0


def test_failure_quota_old_records_gc():
    """超过 24h 的失败记录不计入"""
    from app.database import get_db
    from app.services import rate_limiter as rl
    old_ts = _time.time() - rl.REGISTER_IP_FAILURE_WINDOW - 100
    with get_db() as conn:
        conn.execute(
            "INSERT INTO register_ip_failure_log (ip, attempted_at_ts, reason) VALUES (?, ?, ?)",
            ("6.6.6.6", old_ts, "old"),
        )
        conn.commit()
    assert rl.count_recent_register_failures_from_ip("6.6.6.6") == 0


def test_failure_quota_assert_at_limit_raises_429():
    import pytest
    from fastapi import HTTPException
    from app.services import rate_limiter as rl
    for _ in range(rl.REGISTER_IP_FAILURE_LIMIT):
        rl.record_register_ip_failure("7.7.7.7", "test")
    with pytest.raises(HTTPException) as ei:
        rl.assert_register_ip_failure_quota("7.7.7.7")
    assert ei.value.status_code == 429
    assert "失败次数过多" in ei.value.detail
