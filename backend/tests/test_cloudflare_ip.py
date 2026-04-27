"""P6: get_client_ip 优先级测试

CF-Connecting-IP > X-Forwarded-For > X-Real-IP > request.client.host
"""
from unittest.mock import MagicMock

from app.services.rate_limiter import get_client_ip


def _mk_request(headers: dict, client_host: str = "127.0.0.1"):
    req = MagicMock()
    req.headers = headers
    req.client = MagicMock()
    req.client.host = client_host
    return req


def test_cf_connecting_ip_takes_priority():
    """CF-Connecting-IP 存在时,所有其他 header 都不算"""
    req = _mk_request({
        "CF-Connecting-IP": "1.2.3.4",
        "X-Forwarded-For": "5.6.7.8, 9.10.11.12",
        "X-Real-IP": "13.14.15.16",
    }, client_host="9.9.9.9")
    assert get_client_ip(req) == "1.2.3.4"


def test_x_forwarded_for_when_no_cf():
    """没 CF 头时回退到 X-Forwarded-For 第一个"""
    req = _mk_request({
        "X-Forwarded-For": "5.6.7.8, 9.10.11.12",
        "X-Real-IP": "13.14.15.16",
    })
    assert get_client_ip(req) == "5.6.7.8"


def test_x_real_ip_when_no_xff():
    req = _mk_request({"X-Real-IP": "13.14.15.16"})
    assert get_client_ip(req) == "13.14.15.16"


def test_falls_back_to_client_host():
    req = _mk_request({}, client_host="9.9.9.9")
    assert get_client_ip(req) == "9.9.9.9"


def test_no_client_falls_back_to_localhost():
    req = MagicMock()
    req.headers = {}
    req.client = None
    assert get_client_ip(req) == "127.0.0.1"


def test_cf_connecting_ip_strips_whitespace():
    """CF 头有时会带空格,要 strip"""
    req = _mk_request({"CF-Connecting-IP": "  1.2.3.4  "})
    assert get_client_ip(req) == "1.2.3.4"
