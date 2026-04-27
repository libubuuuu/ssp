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


def test_failed_register_does_not_count(client):
    """注册失败(错误验证码)不应记录 IP"""
    # 用错误的 code,会 400 — 不应记录
    from app.api import auth as auth_module
    for i in range(3):
        _put_code(f"fail{i}@nope.com", code="111111")
        r = client.post(
            "/api/auth/register",
            json={"email": f"fail{i}@nope.com", "password": "secret123", "code": "222222"},
            headers={"X-Forwarded-For": "10.0.0.30"},
        )
        assert r.status_code == 400

    # 同 IP 3 次失败后还应能成功(record 只在 success 后)
    sc = _register_with_ip(client, "ok@later.com", ip="10.0.0.30")
    assert sc == 200
