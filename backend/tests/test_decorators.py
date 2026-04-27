"""@require_credits 装饰器单测(P7 覆盖率补齐)

decorators.py 是扣费命脉。原覆盖率 27% 仅靠端点间接测;
本文件直接测装饰器逻辑,覆盖:
- 未登录拒(无 current_user)
- 余额不足拒(402,credits 未变)
- 扣费成功 → 写 generation_history
- 函数 raise HTTPException → 返还积分,re-raise
- 函数 raise 普通 Exception → 返还积分,转成 500
- result 是 dict → 附 cost 字段
- result 非 dict → 不报错(不附 cost)
- description 从 result.description 提取
"""
import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from app.services.decorators import require_credits
from app.services.billing import get_user_credits
from app.services.auth import create_user, set_user_credits


def _mk_user(email: str, credits: int = 100) -> dict:
    user = create_user(email=email, password="secret123", name=email.split("@")[0])
    assert user is not None
    set_user_credits(user["id"], credits)
    return user


def _run(coro):
    return asyncio.run(coro)


# === 未登录 ===

def test_no_current_user_raises_401():
    @require_credits("image/style")
    async def handler(**kw):
        return {"ok": True}

    with pytest.raises(HTTPException) as ei:
        _run(handler())
    assert ei.value.status_code == 401


def test_current_user_via_kwargs():
    user = _mk_user("dec-kw@example.com", 50)

    @require_credits("image/style")  # cost=2
    async def handler(**kw):
        return {"ok": True}

    res = _run(handler(current_user=user))
    assert res["ok"] is True
    assert res["cost"] == 2
    assert get_user_credits(user["id"]) == 48


def test_current_user_via_args_dict():
    """current_user 通过位置参数传入(args 里的 dict 含 id 字段)"""
    user = _mk_user("dec-args@example.com", 50)

    @require_credits("image/style")
    async def handler(req, current_user):
        return {"ok": True}

    res = _run(handler({"prompt": "x"}, user))
    assert res["ok"] is True
    assert get_user_credits(user["id"]) == 48


# === 余额不足 ===

def test_insufficient_credits_402_no_deduct():
    user = _mk_user("dec-poor@example.com", 1)  # < 2

    @require_credits("image/style")
    async def handler(**kw):
        return {"ok": True}

    with pytest.raises(HTTPException) as ei:
        _run(handler(current_user=user))
    assert ei.value.status_code == 402
    assert "积分" in ei.value.detail
    # 余额不变(未扣)
    assert get_user_credits(user["id"]) == 1


# === 扣费成功路径 ===

def test_success_writes_generation_history():
    user = _mk_user("dec-hist@example.com", 100)

    @require_credits("image/style")
    async def handler(**kw):
        return {"image_url": "https://x", "description": "test prompt"}

    res = _run(handler(current_user=user))
    assert "image_url" in res
    assert res["cost"] == 2

    # 验证写入 generation_history
    from app.database import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT module, prompt, cost FROM generation_history WHERE user_id = ?",
            (user["id"],)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "image/style"
    assert rows[0][1] == "test prompt"  # description 字段提取
    assert rows[0][2] == 2


def test_result_without_description_uses_module_as_desc():
    user = _mk_user("dec-nodesc@example.com", 100)

    @require_credits("image/style")
    async def handler(**kw):
        return {"image_url": "https://x"}  # 无 description

    _run(handler(current_user=user))
    from app.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT prompt FROM generation_history WHERE user_id = ?",
            (user["id"],)
        ).fetchone()
    assert row[0] == "image/style"  # fallback 到 module 名


def test_non_dict_result_no_cost_attached():
    """函数返字符串 / list 等非 dict → 不附 cost,不报错"""
    user = _mk_user("dec-nondict@example.com", 100)

    @require_credits("image/style")
    async def handler(**kw):
        return "raw string"

    res = _run(handler(current_user=user))
    assert res == "raw string"
    # 仍扣费
    assert get_user_credits(user["id"]) == 98


# === 失败返还 ===

def test_http_exception_refunds_credits():
    user = _mk_user("dec-http-err@example.com", 100)

    @require_credits("image/style")
    async def handler(**kw):
        raise HTTPException(status_code=400, detail="bad input")

    with pytest.raises(HTTPException) as ei:
        _run(handler(current_user=user))
    assert ei.value.status_code == 400
    # 积分被返还
    assert get_user_credits(user["id"]) == 100


def test_unknown_exception_refunds_and_500():
    user = _mk_user("dec-unk-err@example.com", 100)

    @require_credits("image/style")
    async def handler(**kw):
        raise RuntimeError("upstream boom")

    with pytest.raises(HTTPException) as ei:
        _run(handler(current_user=user))
    assert ei.value.status_code == 500
    assert "boom" in ei.value.detail
    # 积分被返还
    assert get_user_credits(user["id"]) == 100


def test_value_error_also_refunds():
    user = _mk_user("dec-ve@example.com", 100)

    @require_credits("video/clone")  # cost=20,确认大额也返还
    async def handler(**kw):
        raise ValueError("malformed")

    with pytest.raises(HTTPException) as ei:
        _run(handler(current_user=user))
    assert ei.value.status_code == 500
    assert get_user_credits(user["id"]) == 100  # 20 退回


# === get_user_credits 工具函数(decorators 内部依赖)===

def test_get_user_credits_returns_zero_for_unknown_user():
    from app.services.decorators import get_user_credits as decorators_gc
    assert decorators_gc("nonexistent-uuid") == 0


def test_get_user_credits_returns_actual_credits():
    user = _mk_user("dec-gc@example.com", 77)
    from app.services.decorators import get_user_credits as decorators_gc
    assert decorators_gc(user["id"]) == 77
