"""数字人(图片+脚本)端点测试

历史 bug:此端点曾用 @require_credits 装饰器但函数返回 placeholder,
扣费 10 积分后给假 task_id。本文件锁住"绝不扣费"的行为防止回归。
"""
import io


def _post_generate(client, token: str):
    """构造一个合法的 multipart 请求 — 仍预期 501,只是确保不是因为
    参数解析失败才走非扣费分支。"""
    files = {
        "image": ("test.jpg", io.BytesIO(b"\xff\xd8\xff\xd9fake-jpeg"), "image/jpeg"),
    }
    data = {"script": "测试脚本"}
    return client.post(
        "/api/digital-human/generate",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )


def _get_credits(user_id: str) -> int:
    from app.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT credits FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return row[0]


def test_digital_human_generate_returns_503(client, register):
    token, user = register(client, "dh-503-a@example.com")
    r = _post_generate(client, token)
    assert r.status_code == 503, r.text
    body = r.json()
    assert "积分" in body["detail"] or "credit" in body["detail"].lower(), \
        "503 文案应明确说明不会扣费,避免用户误解"


def test_digital_human_generate_does_not_deduct_credits(client, register):
    """关键回归测试:无论调多少次,积分一分都不能少"""
    token, user = register(client, "dh-503-b@example.com")
    before = _get_credits(user["id"])
    assert before == 10  # 注册默认(P3-1 反羊毛党降到 10)

    # 调三次都应该 503
    for _ in range(3):
        r = _post_generate(client, token)
        assert r.status_code == 503

    after = _get_credits(user["id"])
    assert after == before, f"积分被扣了!{before} → {after}"


def test_digital_human_unauthenticated_rejected(client):
    """没 token 应该 401,而非泄漏 503"""
    files = {"image": ("test.jpg", io.BytesIO(b"x"), "image/jpeg")}
    data = {"script": "x"}
    r = client.post("/api/digital-human/generate", files=files, data=data)
    assert r.status_code == 401
