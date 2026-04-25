"""
任务队列 API 测试
关注点:
- submit 走扣费路径(余额够 / 余额不够 / 扣费金额对得上)
- list 严格用户隔离 — A 看不到 B 的 jobs
- get/delete 鉴权 — 别人的 job → 403
- 失败时积分应返还(本测试用 _execute_job=noop,不验证返还流程,留 Phase 2 加 fal mock)
"""


def test_submit_image_job_deducts_credits(client, register, auth_header):
    token, user = register(client, "j-a@example.com")
    # 默认注册送 100 额度,image/style = 2 积分
    r = client.post("/api/jobs/submit",
                    json={"type": "image", "params": {"prompt": "hello"}, "title": "t1"},
                    headers=auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["cost"] == 2

    # 余额应当从 100 减到 98
    r2 = client.get("/api/auth/me", headers=auth_header(token))
    assert r2.json()["credits"] == 98


def test_submit_video_clone_deducts_higher_cost(client, register, auth_header):
    token, _ = register(client, "j-b@example.com")
    r = client.post("/api/jobs/submit",
                    json={"type": "video_clone",
                          "params": {"reference_video_url": "x", "model_image_url": "y"}},
                    headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["cost"] == 20  # video/clone = 20

    me = client.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 80


def test_submit_insufficient_credits_402(client, register, auth_header, set_credits):
    token, user = register(client, "j-c@example.com")
    set_credits(user["id"], 1)  # 余额仅 1,做不起 image(2)
    r = client.post("/api/jobs/submit",
                    json={"type": "image", "params": {"prompt": "no money"}},
                    headers=auth_header(token))
    assert r.status_code == 402
    # 余额不应被扣
    me = client.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 1


def test_list_jobs_user_isolation(client, register, auth_header):
    """关键:用户 A 看不到 B 的 job"""
    a_token, _ = register(client, "iso-a@example.com")
    b_token, _ = register(client, "iso-b@example.com")

    # A 提交 1 个 job, B 提交 2 个
    client.post("/api/jobs/submit", json={"type": "image", "params": {"prompt": "A1"}},
                headers=auth_header(a_token))
    client.post("/api/jobs/submit", json={"type": "image", "params": {"prompt": "B1"}},
                headers=auth_header(b_token))
    client.post("/api/jobs/submit", json={"type": "image", "params": {"prompt": "B2"}},
                headers=auth_header(b_token))

    r_a = client.get("/api/jobs/list", headers=auth_header(a_token))
    r_b = client.get("/api/jobs/list", headers=auth_header(b_token))
    a_jobs = r_a.json()["jobs"]
    b_jobs = r_b.json()["jobs"]

    assert len(a_jobs) == 1
    assert len(b_jobs) == 2
    assert all(j.get("title") != "B1" and j.get("title") != "B2" for j in a_jobs)


def test_get_job_returns_403_for_other_user(client, register, auth_header):
    a_token, _ = register(client, "iso-c@example.com")
    b_token, _ = register(client, "iso-d@example.com")

    r = client.post("/api/jobs/submit",
                    json={"type": "image", "params": {"prompt": "secret"}, "title": "topsecret"},
                    headers=auth_header(a_token))
    a_job_id = r.json()["job_id"]

    # B 试图读 A 的 job
    r_b = client.get(f"/api/jobs/{a_job_id}", headers=auth_header(b_token))
    assert r_b.status_code == 403


def test_delete_job_returns_403_for_other_user(client, register, auth_header):
    a_token, _ = register(client, "iso-e@example.com")
    b_token, _ = register(client, "iso-f@example.com")

    r = client.post("/api/jobs/submit",
                    json={"type": "image", "params": {"prompt": "x"}},
                    headers=auth_header(a_token))
    a_job_id = r.json()["job_id"]

    r_del = client.delete(f"/api/jobs/{a_job_id}", headers=auth_header(b_token))
    assert r_del.status_code == 403

    # job 应仍存在
    r_get = client.get(f"/api/jobs/{a_job_id}", headers=auth_header(a_token))
    assert r_get.status_code == 200


def test_unauthenticated_jobs_calls_rejected(client):
    r1 = client.post("/api/jobs/submit", json={"type": "image", "params": {}})
    r2 = client.get("/api/jobs/list")
    r3 = client.get("/api/jobs/anything")
    for r in (r1, r2, r3):
        assert r.status_code in (401, 403)
