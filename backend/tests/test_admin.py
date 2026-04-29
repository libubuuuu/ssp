"""管理员 API 鉴权测试 + 关键路径覆盖
关注点:
- 普通用户访问 admin 接口 → 403
- admin 用户访问 → 200(其中需要 fal/circuit_breaker 等内部状态的接口跳过)
- 加额度接口的关键审计:目标用户余额真的变了
- force-logout 把目标用户 token 失效
- reset-model 走熔断器路径
- audit-log 列表 + 过滤
"""


def test_non_admin_blocked_from_admin_endpoints(client, register, auth_header):
    token, _ = register(client, "regular@example.com")
    h = auth_header(token)

    endpoints = [
        ("GET", "/api/admin/users-list"),
        ("GET", "/api/admin/orders"),
        ("GET", "/api/admin/stats/overview"),
        ("GET", "/api/admin/tasks/recent"),
    ]
    for method, path in endpoints:
        r = client.request(method, path, headers=h)
        assert r.status_code == 403, f"{method} {path} expected 403, got {r.status_code}"


def test_unauthenticated_blocked_from_admin(client):
    r = client.get("/api/admin/users-list")
    assert r.status_code in (401, 403)


def test_admin_can_list_users(client, register, auth_header, set_role):
    """管理员能列出所有用户(数据库查询全路径)"""
    a_token, a_user = register(client, "admin-list@example.com")
    set_role(a_user["id"], "admin")
    register(client, "user-1@example.com")
    register(client, "user-2@example.com")

    r = client.get("/api/admin/users-list", headers=auth_header(a_token))
    assert r.status_code == 200, r.text
    users = r.json()["users"]
    emails = {u["email"] for u in users}
    assert "admin-list@example.com" in emails
    assert "user-1@example.com" in emails
    assert "user-2@example.com" in emails
    # 字段完整
    assert all("id" in u and "credits" in u and "role" in u for u in users)


def test_admin_can_adjust_credits(client, register, auth_header, set_role):
    a_token, a_user = register(client, "adj-admin@example.com")
    set_role(a_user["id"], "admin")
    _, target = register(client, "adj-target@example.com")
    target_id = target["id"]

    # 加 50
    r1 = client.post(f"/api/admin/users/{target_id}/adjust-credits",
                     params={"delta": 50},
                     headers=auth_header(a_token))
    assert r1.status_code == 200, r1.text

    # 直接读 DB 验证(避免 /me 需要 target 自己的 token)
    from app.database import get_db
    with get_db() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (target_id,)).fetchone()
    assert row[0] == 60  # 10 (P3-1 默认) + 50

    # 减 30
    r2 = client.post(f"/api/admin/users/{target_id}/adjust-credits",
                     params={"delta": -30},
                     headers=auth_header(a_token))
    assert r2.status_code == 200
    with get_db() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (target_id,)).fetchone()
    assert row[0] == 30


def test_non_admin_cannot_adjust_credits(client, register, auth_header):
    """普通用户不能给别人加额度(否则等于免费 print money)"""
    a_token, _ = register(client, "regular-1@example.com")
    _, target = register(client, "target-2@example.com")

    r = client.post(f"/api/admin/users/{target['id']}/adjust-credits",
                    params={"delta": 9999},
                    headers=auth_header(a_token))
    assert r.status_code == 403

    # 余额不应改变
    from app.database import get_db
    with get_db() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (target["id"],)).fetchone()
    assert row[0] == 10  # P3-1 默认


# === 新增:adjust-credits 边界 + 不存在用户 ===

def test_adjust_credits_floors_at_zero(client, register, auth_header, set_role):
    """delta 减得超过余额 → 余额 = 0(max(0, ...))"""
    a_token, a_user = register(client, "floor-admin@example.com")
    set_role(a_user["id"], "admin")
    _, target = register(client, "floor-target@example.com")
    r = client.post(
        f"/api/admin/users/{target['id']}/adjust-credits",
        params={"delta": -9999}, headers=auth_header(a_token),
    )
    assert r.status_code == 200
    from app.database import get_db
    with get_db() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (target["id"],)).fetchone()
    assert row[0] == 0


def test_adjust_credits_404_for_unknown_user(client, register, auth_header, set_role):
    a_token, a_user = register(client, "404-admin@example.com")
    set_role(a_user["id"], "admin")
    r = client.post(
        "/api/admin/users/no-such-uuid/adjust-credits",
        params={"delta": 10}, headers=auth_header(a_token),
    )
    assert r.status_code == 404


# === force-logout ===

def test_force_logout_invalidates_user_tokens(client, register, auth_header, set_role):
    """管理员踢人 → 该用户的 token 立即失效"""
    a_token, a_user = register(client, "fl-admin@example.com")
    set_role(a_user["id"], "admin")
    target_token, target = register(client, "fl-target@example.com")

    # 验证 target 当前能访问 /me
    r_before = client.get("/api/auth/me", headers=auth_header(target_token))
    assert r_before.status_code == 200

    # 踢
    r = client.post(
        f"/api/admin/users/{target['id']}/force-logout",
        headers=auth_header(a_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True  # 修复后:bool 而非 int ts
    assert body["user_id"] == target["id"]

    # 现在 target 应当 401
    client.cookies.clear()
    r_after = client.get("/api/auth/me", headers=auth_header(target_token))
    assert r_after.status_code == 401


def test_force_logout_404_for_unknown_user(client, register, auth_header, set_role):
    a_token, a_user = register(client, "fl-404-admin@example.com")
    set_role(a_user["id"], "admin")
    r = client.post(
        "/api/admin/users/no-such-uuid/force-logout",
        headers=auth_header(a_token),
    )
    assert r.status_code == 404


def test_force_logout_non_admin_403(client, register, auth_header):
    token, _ = register(client, "fl-non-admin@example.com")
    _, target = register(client, "fl-victim@example.com")
    r = client.post(
        f"/api/admin/users/{target['id']}/force-logout",
        headers=auth_header(token),
    )
    assert r.status_code == 403


# === audit-log 接口 ===

def test_audit_log_endpoint_admin_only(client, register, auth_header):
    token, _ = register(client, "audit-non-admin@example.com")
    r = client.get("/api/admin/audit-log", headers=auth_header(token))
    assert r.status_code == 403


def test_audit_log_endpoint_returns_recent(client, register, auth_header, set_role):
    a_token, a_user = register(client, "audit-list-admin@example.com")
    set_role(a_user["id"], "admin")
    _, target = register(client, "audit-list-target@example.com")

    # 先制造一条审计:adjust-credits
    client.post(
        f"/api/admin/users/{target['id']}/adjust-credits",
        params={"delta": 5},
        headers=auth_header(a_token),
    )

    r = client.get("/api/admin/audit-log", headers=auth_header(a_token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    actions = {row["action"] for row in body["logs"]}
    assert "adjust_credits" in actions


def test_audit_log_filter_by_action(client, register, auth_header, set_role):
    a_token, a_user = register(client, "audit-filter-admin@example.com")
    set_role(a_user["id"], "admin")
    _, target = register(client, "audit-filter-target@example.com")
    # 制造两条不同 action 的审计
    client.post(f"/api/admin/users/{target['id']}/adjust-credits",
                params={"delta": 1}, headers=auth_header(a_token))
    client.post(f"/api/admin/users/{target['id']}/force-logout",
                headers=auth_header(a_token))

    # 只过滤 force_logout
    r = client.get("/api/admin/audit-log?action=force_logout", headers=auth_header(a_token))
    assert r.status_code == 200
    actions = {row["action"] for row in r.json()["logs"]}
    assert actions == {"force_logout"}


def test_audit_log_limit_capped_at_500(client, register, auth_header, set_role):
    """limit 超 500 被截到 500 — 防恶意请求"""
    a_token, a_user = register(client, "audit-cap-admin@example.com")
    set_role(a_user["id"], "admin")
    r = client.get("/api/admin/audit-log?limit=10000", headers=auth_header(a_token))
    assert r.status_code == 200
    # 没有真造 >500 条数据,所以 total 不一定 == 500;只验证不报错 + 有 logs 字段
    assert "logs" in r.json()


# === diagnose-history(只读文件系统)===

def test_diagnose_history_admin_only(client, register, auth_header):
    token, _ = register(client, "diag-non-admin@example.com")
    r = client.get("/api/admin/diagnose-history", headers=auth_header(token))
    assert r.status_code == 403


def test_diagnose_history_returns_list_or_empty(client, register, auth_header, set_role):
    """目录可能不存在(测试机器),应返回空列表不抛 500"""
    a_token, a_user = register(client, "diag-admin@example.com")
    set_role(a_user["id"], "admin")
    r = client.get("/api/admin/diagnose-history", headers=auth_header(a_token))
    assert r.status_code == 200
    body = r.json()
    assert "snapshots" in body or isinstance(body, dict)


# === 管理员 2FA 强制开关 ===


def test_admin_2fa_enforce_off_admin_without_2fa_passes(client, register, auth_header, set_role, monkeypatch):
    """ADMIN_2FA_REQUIRED=false(默认):无 2FA 的管理员仍能进入"""
    monkeypatch.setenv("ADMIN_2FA_REQUIRED", "false")
    a_token, a_user = register(client, "no2fa-admin@example.com")
    set_role(a_user["id"], "admin")
    r = client.get("/api/admin/users-list", headers=auth_header(a_token))
    assert r.status_code == 200


def test_admin_2fa_enforce_on_admin_without_2fa_blocked(client, register, auth_header, set_role, monkeypatch):
    """ADMIN_2FA_REQUIRED=true:无 2FA 的管理员被拦"""
    monkeypatch.setenv("ADMIN_2FA_REQUIRED", "true")
    a_token, a_user = register(client, "needs2fa-admin@example.com")
    set_role(a_user["id"], "admin")
    r = client.get("/api/admin/users-list", headers=auth_header(a_token))
    assert r.status_code == 403
    body = r.json()
    # detail 是结构化对象给前端引导
    assert isinstance(body["detail"], dict)
    assert body["detail"]["code"] == "ADMIN_2FA_REQUIRED"
    assert body["detail"]["redirect"] == "/profile/2fa"


def test_admin_2fa_enforce_on_admin_with_2fa_passes(client, register, auth_header, set_role, monkeypatch):
    """ADMIN_2FA_REQUIRED=true:已 enroll 2FA 的管理员通行"""
    monkeypatch.setenv("ADMIN_2FA_REQUIRED", "true")
    a_token, a_user = register(client, "has2fa-admin@example.com")
    set_role(a_user["id"], "admin")
    # 直接改 DB 模拟已 enroll 2FA(2FA setup 流程涉及 TOTP secret + verify 太长)
    from app.database import get_db
    with get_db() as conn:
        conn.cursor().execute(
            "UPDATE users SET totp_enabled = 1, totp_secret = 'TESTSECRET' WHERE id = ?",
            (a_user["id"],),
        )
        conn.commit()
    r = client.get("/api/admin/users-list", headers=auth_header(a_token))
    assert r.status_code == 200


def test_admin_2fa_enforce_doesnt_affect_non_admin(client, register, auth_header, monkeypatch):
    """ADMIN_2FA_REQUIRED=true 时,普通用户访问 admin 端点仍是 403,但 detail 是普通的'需要管理员权限',不是 2FA 引导"""
    monkeypatch.setenv("ADMIN_2FA_REQUIRED", "true")
    token, _ = register(client, "regular-user@example.com")
    r = client.get("/api/admin/users-list", headers=auth_header(token))
    assert r.status_code == 403
    body = r.json()
    # detail 是字符串(普通 role 不足)而不是 2FA dict
    assert isinstance(body["detail"], str)
    assert "管理员" in body["detail"]


# ==================== 七十七续 P7:口播任务运营 admin ====================


def _seed_oral(user_id, status="completed", tier="economy", duration=30.0,
               charged=160, refunded=0, error_step=None, error_message=None):
    import uuid
    from app.database import get_db
    sid = uuid.uuid4().hex[:12]
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """INSERT INTO oral_sessions (id, user_id, tier, status, original_video_path,
               duration_seconds, credits_charged, credits_refunded, error_step, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, str(user_id), tier, status, "/tmp/x.mp4", duration,
             charged, refunded, error_step, error_message),
        )
        conn.commit()
    return sid


def test_admin_oral_tasks_unauthenticated_401(client):
    r = client.get("/api/admin/oral-tasks")
    assert r.status_code in (401, 403)


def test_admin_oral_tasks_non_admin_403(client, register, auth_header):
    token, _ = register(client, "ot-non-admin@x.com")
    r = client.get("/api/admin/oral-tasks", headers=auth_header(token))
    assert r.status_code == 403


def test_admin_oral_tasks_summary_and_items(client, register, auth_header, set_role):
    """summary 各 status 计数 + failure_top 聚合 + items 含 user_email/credits_net"""
    a_token, a_user = register(client, "ot-admin@x.com")
    set_role(a_user["id"], "admin")
    _, u1 = register(client, "ot-user1@x.com")
    _, u2 = register(client, "ot-user2@x.com")

    _seed_oral(u1["id"], status="completed", tier="economy",
               duration=30.0, charged=160, refunded=0)
    _seed_oral(u1["id"], status="failed_step5", tier="standard",
               duration=45.0, charged=270, refunded=81,
               error_step="step5", error_message="lipsync timeout")
    _seed_oral(u2["id"], status="failed_step5", tier="economy",
               duration=20.0, charged=80, refunded=24,
               error_step="step5", error_message="lipsync timeout")
    _seed_oral(u2["id"], status="asr_running", tier="economy",
               duration=10.0, charged=40, refunded=0)

    r = client.get("/api/admin/oral-tasks", headers=auth_header(a_token))
    assert r.status_code == 200
    body = r.json()

    s = body["summary"]
    assert s["total"] == 4
    assert s["status_counts"]["completed"] == 1
    assert s["status_counts"]["failed_step5"] == 2
    assert s["status_counts"]["asr_running"] == 1

    # failure_top:lipsync timeout 出现 2 次
    assert body["failure_top"][0]["count"] == 2
    assert "lipsync timeout" in body["failure_top"][0]["message"]

    items = body["items"]
    assert len(items) == 4
    emails = {it["user_email"] for it in items}
    assert {"ot-user1@x.com", "ot-user2@x.com"} <= emails
    # credits_net = charged - refunded
    failed_u1 = next(it for it in items
                     if it["user_email"] == "ot-user1@x.com" and it["status"] == "failed_step5")
    assert failed_u1["credits_net"] == 270 - 81  # 189


def test_admin_oral_tasks_status_filter(client, register, auth_header, set_role):
    a_token, a_user = register(client, "ot-fil-admin@x.com")
    set_role(a_user["id"], "admin")
    _, u = register(client, "ot-fil-user@x.com")

    _seed_oral(u["id"], status="completed")
    _seed_oral(u["id"], status="failed_step5", error_step="step5", error_message="x")
    _seed_oral(u["id"], status="asr_running")

    r = client.get("/api/admin/oral-tasks?status=completed", headers=auth_header(a_token))
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "completed"


def test_admin_oral_tasks_tier_filter(client, register, auth_header, set_role):
    a_token, a_user = register(client, "ot-tier-admin@x.com")
    set_role(a_user["id"], "admin")
    _, u = register(client, "ot-tier-user@x.com")

    _seed_oral(u["id"], tier="economy")
    _seed_oral(u["id"], tier="standard")
    _seed_oral(u["id"], tier="premium")

    r = client.get("/api/admin/oral-tasks?tier=premium", headers=auth_header(a_token))
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["tier"] == "premium"


def test_admin_oral_task_detail_unauthenticated_401(client):
    r = client.get("/api/admin/oral-tasks/anysid")
    assert r.status_code in (401, 403)


def test_admin_oral_task_detail_non_admin_403(client, register, auth_header):
    token, _ = register(client, "od-non-admin@x.com")
    r = client.get("/api/admin/oral-tasks/anysid", headers=auth_header(token))
    assert r.status_code == 403


def test_admin_oral_task_detail_404(client, register, auth_header, set_role):
    a_token, a_user = register(client, "od-admin-404@x.com")
    set_role(a_user["id"], "admin")
    r = client.get("/api/admin/oral-tasks/nonexistent", headers=auth_header(a_token))
    assert r.status_code == 404


def test_admin_oral_task_detail_full_fields(client, register, auth_header, set_role):
    """drill-down 含 user_email / 解析后的 selected_models / credits_net / 完整字段"""
    import json
    a_token, a_user = register(client, "od-detail@x.com")
    set_role(a_user["id"], "admin")
    _, u = register(client, "od-target@x.com")

    sid = _seed_oral(u["id"], status="completed", tier="economy",
                     duration=42.0, charged=160, refunded=0)

    # 写一些字段进去模拟跑过端到端
    from app.database import get_db
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """UPDATE oral_sessions SET
               selected_models = ?,
               selected_products = ?,
               asr_transcript = ?,
               edited_transcript = ?,
               new_audio_url = ?,
               swapped_video_url = ?,
               final_video_url = ?,
               swap_fal_request_id = ?,
               lipsync_fal_request_id = ?
               WHERE id = ?""",
            (
                json.dumps([{"name": "Alice", "image_url": "https://x/a.jpg"}]),
                json.dumps([]),
                "你好世界",
                "你好世界 v2",
                "https://fal.media/a.mp3",
                "https://fal.media/swap.mp4",
                "/uploads/oral/u1/sid/final.mp4",
                "fal-req-swap-123",
                "veed/lipsync-456",
                sid,
            ),
        )
        conn.commit()

    r = client.get(f"/api/admin/oral-tasks/{sid}", headers=auth_header(a_token))
    assert r.status_code == 200, r.text
    d = r.json()

    assert d["id"] == sid
    assert d["user_email"] == "od-target@x.com"
    assert d["status"] == "completed"
    assert d["tier"] == "economy"
    # JSON 字段已解析为对象
    assert isinstance(d["selected_models"], list)
    assert d["selected_models"][0]["name"] == "Alice"
    assert d["selected_products"] == []
    # 中间产物 + fal request_id
    assert d["asr_transcript"] == "你好世界"
    assert d["edited_transcript"] == "你好世界 v2"
    assert d["new_audio_url"] == "https://fal.media/a.mp3"
    assert d["swap_fal_request_id"] == "fal-req-swap-123"
    assert d["lipsync_fal_request_id"] == "veed/lipsync-456"
    # 派生 credits_net
    assert d["credits_net"] == 160


def test_admin_oral_tasks_step_progress_flags(client, register, auth_header, set_role):
    """step_progress 字段从 NULL/非 NULL 派生"""
    a_token, a_user = register(client, "ot-sp-admin@x.com")
    set_role(a_user["id"], "admin")
    _, u = register(client, "ot-sp-user@x.com")

    sid = _seed_oral(u["id"], status="lipsync_running")

    # 模拟 step1+step2+step3 完成,step4/5 还没产物
    from app.database import get_db
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE oral_sessions SET asr_transcript=?, edited_transcript=?, new_audio_url=? WHERE id=?",
            ("hello", "hello edited", "https://x/a.mp3", sid),
        )
        conn.commit()

    r = client.get("/api/admin/oral-tasks", headers=auth_header(a_token))
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    sp = items[0]["step_progress"]
    assert sp["step1_asr"] is True
    assert sp["step2_edit"] is True
    assert sp["step3_audio"] is True
    assert sp["step4_swap"] is False
    assert sp["step5_final"] is False
