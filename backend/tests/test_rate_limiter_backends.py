"""P9 限流后端测试:InMemory + Redis 双轨

策略:
- InMemory 直接跑(本来就在用)
- Redis 用 unittest.mock 替 redis.Redis,不真起 Redis 进程
- _make_rate_limiter 工厂:REDIS_URL 空 / 设 / 设但 Redis 挂三种路径
"""
import os
from unittest.mock import patch, MagicMock

import pytest

from app.services import rate_limiter as rl


# === InMemory 兜底测试(保留既存语义)===

def test_in_memory_ip_limit_allows_under_threshold():
    lim = rl.InMemoryRateLimiter()
    for i in range(lim.ip_limit):
        ok, remaining = lim.check_ip_limit("1.1.1.1")
        assert ok, f"第 {i+1} 次应该通过"


def test_in_memory_ip_limit_blocks_over_threshold():
    lim = rl.InMemoryRateLimiter()
    for _ in range(lim.ip_limit):
        lim.check_ip_limit("2.2.2.2")
    ok, remaining = lim.check_ip_limit("2.2.2.2")
    assert not ok and remaining == 0


def test_in_memory_failure_count_triggers_captcha():
    lim = rl.InMemoryRateLimiter()
    for _ in range(lim.failure_threshold - 1):
        triggered = lim.record_failure("3.3.3.3")
        assert not triggered
    triggered = lim.record_failure("3.3.3.3")
    assert triggered
    assert lim.should_require_captcha("3.3.3.3")
    lim.reset_failure("3.3.3.3")
    assert not lim.should_require_captcha("3.3.3.3")


# === Redis 后端测试(mock client)===

def _mk_redis_mock(incr_return=1):
    """模拟 redis.Redis 实例:incr / expire / get / delete / ping 都不真连"""
    client = MagicMock()
    client.ping.return_value = True
    # 默认 incr 从 1 涨,测试可以重置
    client._counter = 0
    def incr_fn(key):
        client._counter += 1
        return client._counter
    client.incr.side_effect = incr_fn
    client.expire.return_value = True
    client.get.return_value = None
    client.delete.return_value = 1
    return client


def test_redis_factory_returns_redis_when_url_and_reachable(monkeypatch):
    """REDIS_URL 设了 + Redis 可达 → 用 RedisRateLimiter"""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    fake_client = _mk_redis_mock()
    with patch("redis.Redis.from_url", return_value=fake_client):
        instance = rl._make_rate_limiter()
    assert isinstance(instance, rl.RedisRateLimiter)


def test_redis_factory_falls_back_when_redis_unreachable(monkeypatch):
    """REDIS_URL 设了但 Redis 挂 → 回退 InMemory + warning"""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:9999/0")
    bad_client = MagicMock()
    bad_client.ping.side_effect = ConnectionError("nope")
    with patch("redis.Redis.from_url", return_value=bad_client):
        instance = rl._make_rate_limiter()
    assert isinstance(instance, rl.InMemoryRateLimiter)


def test_redis_factory_uses_in_memory_when_no_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    instance = rl._make_rate_limiter()
    assert isinstance(instance, rl.InMemoryRateLimiter)


def test_redis_check_ip_limit_first_call_sets_expire(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    fake = _mk_redis_mock()
    with patch("redis.Redis.from_url", return_value=fake):
        lim = rl.RedisRateLimiter("redis://localhost:6379/0")
    ok, remaining = lim.check_ip_limit("4.4.4.4")
    assert ok
    fake.incr.assert_called()
    # 第一次 INCR 后应该 EXPIRE,而后续不再 expire
    assert fake.expire.call_count == 1


def test_redis_check_ip_limit_blocks_at_limit(monkeypatch):
    fake = _mk_redis_mock()
    with patch("redis.Redis.from_url", return_value=fake):
        lim = rl.RedisRateLimiter("redis://localhost:6379/0")
    # 把计数器推到 limit + 1
    fake._counter = lim.ip_limit  # 下次 incr 返 limit+1
    ok, remaining = lim.check_ip_limit("5.5.5.5")
    assert not ok and remaining == 0


def test_redis_failure_when_redis_dies_fails_open(monkeypatch):
    """Redis 临时挂(运行期):check_ip_limit 不能让请求 500;fail-open 允许"""
    fake = _mk_redis_mock()
    fake.incr.side_effect = ConnectionError("transient")
    with patch("redis.Redis.from_url", return_value=fake):
        lim = rl.RedisRateLimiter("redis://localhost:6379/0")
    ok, remaining = lim.check_ip_limit("6.6.6.6")
    assert ok  # 重要:不挂请求
    assert remaining == -1  # 信号:无法判断


def test_redis_record_failure_uses_24h_expire(monkeypatch):
    fake = _mk_redis_mock()
    with patch("redis.Redis.from_url", return_value=fake):
        lim = rl.RedisRateLimiter("redis://localhost:6379/0")
    lim.record_failure("7.7.7.7")
    # expire 调用应该带 86400
    args = fake.expire.call_args
    assert args is not None
    assert 86400 in args.args


def test_redis_reset_failure_deletes_key(monkeypatch):
    fake = _mk_redis_mock()
    with patch("redis.Redis.from_url", return_value=fake):
        lim = rl.RedisRateLimiter("redis://localhost:6379/0")
    lim.reset_failure("8.8.8.8")
    fake.delete.assert_called_once()
    delete_key = fake.delete.call_args.args[0]
    assert "fail" in delete_key
    assert "8.8.8.8" in delete_key
