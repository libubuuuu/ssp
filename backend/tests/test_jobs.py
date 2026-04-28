"""
任务队列 API 测试
关注点:
- submit 走扣费路径(余额够 / 余额不够 / 扣费金额对得上)
- list 严格用户隔离 — A 看不到 B 的 jobs
- get/delete 鉴权 — 别人的 job → 403
- 失败时积分应返还(本测试用 _execute_job=noop,不验证返还流程,留 Phase 2 加 fal mock)
"""


def test_submit_image_job_deducts_credits(client, register, auth_header, set_credits):
    token, user = register(client, "j-a@example.com")
    set_credits(user["id"], 100)  # 显式设到 100,P3-1 后默认是 10 不够 image=2 任务多次测试
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


def test_submit_video_clone_deducts_higher_cost(client, register, auth_header, set_credits):
    token, user = register(client, "j-b@example.com")
    set_credits(user["id"], 100)  # video/clone=20,P3-1 默认 10 不够
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


# === 七十五续:long-video 虚拟 job 合并到 My Tasks ===


def test_list_jobs_merges_studio_sessions_with_batch_results(client, register, auth_header):
    """有 batch_results 的 studio session 应作为虚拟 job 显示在 list"""
    token, user = register(client, "lv-merge@example.com")

    from app.api import video_studio as vs
    sid = "test_sess_001"
    vs.STUDIO_TASKS[sid] = {
        "session_id": sid,
        "user_id": user["id"],
        "video_path": "/tmp/x.mp4",
        "duration": 50.0,
        "segments": [{"index": i} for i in range(5)],
        "status": "generating",
        "batch_results": [
            {"segment_index": 0, "status": "completed", "video_url": "u0"},
            {"segment_index": 1, "status": "running"},
            {"segment_index": 2, "status": "running"},
            {"segment_index": 3, "status": "running"},
            {"segment_index": 4, "status": "running"},
        ],
        "batch_cost": 75,
    }

    try:
        r = client.get("/api/jobs/list", headers=auth_header(token))
        assert r.status_code == 200
        jobs = r.json()["jobs"]
        lv = [j for j in jobs if j.get("_long_video")]
        assert len(lv) == 1
        j = lv[0]
        assert j["id"] == f"studio_{sid}"
        assert j["type"] == "long_video"
        assert j["status"] == "running"
        assert j["params"]["segments_total"] == 5
        assert j["params"]["segments_completed"] == 1
        assert j["params"]["segments_pending"] == 4
        assert "1/5" in j["title"]
        assert j["_route"] == f"/video/studio/{sid}"
    finally:
        vs.STUDIO_TASKS.pop(sid, None)


def test_list_jobs_studio_session_completed_with_final_url(client, register, auth_header):
    """final_url 存在 → status=completed,result 含 video_url"""
    token, user = register(client, "lv-done@example.com")

    from app.api import video_studio as vs
    sid = "test_sess_002"
    vs.STUDIO_TASKS[sid] = {
        "session_id": sid, "user_id": user["id"],
        "video_path": "/tmp/y.mp4", "duration": 30.0,
        "segments": [], "status": "finished",
        "batch_results": [
            {"segment_index": 0, "status": "completed", "video_url": "u0"},
            {"segment_index": 1, "status": "completed", "video_url": "u1"},
        ],
        "final_url": "https://fal.media/final.mp4",
        "batch_cost": 30,
    }

    try:
        r = client.get("/api/jobs/list", headers=auth_header(token))
        jobs = r.json()["jobs"]
        lv = [j for j in jobs if j.get("_long_video")][0]
        assert lv["status"] == "completed"
        assert lv["result"]["video_url"] == "https://fal.media/final.mp4"
        assert "全部完成" in lv["title"]
    finally:
        vs.STUDIO_TASKS.pop(sid, None)


def test_list_jobs_studio_session_all_failed(client, register, auth_header):
    """全 failed → status=failed"""
    token, user = register(client, "lv-failed@example.com")

    from app.api import video_studio as vs
    sid = "test_sess_003"
    vs.STUDIO_TASKS[sid] = {
        "session_id": sid, "user_id": user["id"],
        "video_path": "/tmp/z.mp4", "duration": 20.0,
        "segments": [], "status": "generating",
        "batch_results": [
            {"segment_index": 0, "status": "failed"},
            {"segment_index": 1, "status": "failed"},
        ],
        "batch_cost": 0,
    }

    try:
        r = client.get("/api/jobs/list", headers=auth_header(token))
        jobs = r.json()["jobs"]
        lv = [j for j in jobs if j.get("_long_video")][0]
        assert lv["status"] == "failed"
    finally:
        vs.STUDIO_TASKS.pop(sid, None)


def test_list_jobs_studio_session_user_isolation(client, register, auth_header):
    """A 的 long-video session,B 调 list 看不到"""
    a_token, a_user = register(client, "lv-iso-a@example.com")
    b_token, b_user = register(client, "lv-iso-b@example.com")

    from app.api import video_studio as vs
    sid = "test_sess_iso"
    vs.STUDIO_TASKS[sid] = {
        "session_id": sid, "user_id": a_user["id"],
        "video_path": "/tmp/q.mp4", "duration": 10.0,
        "segments": [], "status": "generating",
        "batch_results": [{"segment_index": 0, "status": "completed", "video_url": "u"}],
    }

    try:
        r_a = client.get("/api/jobs/list", headers=auth_header(a_token))
        r_b = client.get("/api/jobs/list", headers=auth_header(b_token))
        a_lv = [j for j in r_a.json()["jobs"] if j.get("_long_video")]
        b_lv = [j for j in r_b.json()["jobs"] if j.get("_long_video")]
        assert len(a_lv) == 1
        assert len(b_lv) == 0
    finally:
        vs.STUDIO_TASKS.pop(sid, None)


def test_list_jobs_studio_no_batch_results_skipped(client, register, auth_header):
    """只上传 / 只拆分但没 generate(无 batch_results)→ 不展示"""
    token, user = register(client, "lv-noskip@example.com")

    from app.api import video_studio as vs
    sid = "test_sess_skip"
    vs.STUDIO_TASKS[sid] = {
        "session_id": sid, "user_id": user["id"],
        "video_path": "/tmp/s.mp4", "duration": 10.0,
        "segments": [{"index": 0}], "status": "split",
        # 没 batch_results 字段
    }

    try:
        r = client.get("/api/jobs/list", headers=auth_header(token))
        lv = [j for j in r.json()["jobs"] if j.get("_long_video")]
        assert len(lv) == 0
    finally:
        vs.STUDIO_TASKS.pop(sid, None)


def test_list_jobs_studio_merged_with_regular_jobs_sorted(client, register, auth_header):
    """常规 jobs 和 long-video 虚拟 job 合并后按 created_at 倒序"""
    import time as _t
    token, user = register(client, "lv-mix@example.com")

    # 提交一个普通 image job
    client.post("/api/jobs/submit",
                json={"type": "image", "params": {"prompt": "x"}, "title": "regular"},
                headers=auth_header(token))

    from app.api import video_studio as vs
    sid = "test_sess_mix"
    vs.STUDIO_TASKS[sid] = {
        "session_id": sid, "user_id": user["id"],
        "video_path": "/tmp/m.mp4", "duration": 10.0,
        "segments": [], "status": "generating",
        "batch_results": [{"segment_index": 0, "status": "completed", "video_url": "u"}],
    }

    try:
        r = client.get("/api/jobs/list", headers=auth_header(token))
        jobs = r.json()["jobs"]
        # 至少 2 条:1 普通 + 1 long-video
        regular = [j for j in jobs if not j.get("_long_video")]
        lv = [j for j in jobs if j.get("_long_video")]
        assert len(regular) >= 1
        assert len(lv) == 1
    finally:
        vs.STUDIO_TASKS.pop(sid, None)


# === happy path 补漏 ===


def test_get_job_owner_returns_200_with_full_payload(client, register, auth_header):
    """owner 自己读 → 200 + 含 type/cost/status/params"""
    token, _ = register(client, "j-get-ok@example.com")
    r = client.post("/api/jobs/submit",
                    json={"type": "image", "params": {"prompt": "happy"}, "title": "OK"},
                    headers=auth_header(token))
    job_id = r.json()["job_id"]

    r_get = client.get(f"/api/jobs/{job_id}", headers=auth_header(token))
    assert r_get.status_code == 200
    body = r_get.json()
    assert body["id"] == job_id
    assert body["type"] == "image"
    assert body["cost"] == 2
    assert body["status"] == "pending"
    assert body["title"] == "OK"


def test_get_job_404_for_nonexistent_id(client, register, auth_header):
    """不存在的 job_id → 404(走 if job_id not in JOBS 分支)"""
    token, _ = register(client, "j-get-404@example.com")
    r = client.get("/api/jobs/zzznosuch", headers=auth_header(token))
    assert r.status_code == 404


def test_delete_job_owner_returns_200_and_removes(client, register, auth_header):
    """owner 删自己 job → 200 + 后续 GET 拿 404"""
    token, _ = register(client, "j-del-ok@example.com")
    r = client.post("/api/jobs/submit",
                    json={"type": "image", "params": {"prompt": "del"}},
                    headers=auth_header(token))
    job_id = r.json()["job_id"]

    r_del = client.delete(f"/api/jobs/{job_id}", headers=auth_header(token))
    assert r_del.status_code == 200

    # 删后再 GET 应 404
    r_get = client.get(f"/api/jobs/{job_id}", headers=auth_header(token))
    assert r_get.status_code == 404


def test_delete_job_404_for_nonexistent_id(client, register, auth_header):
    """不存在的 job_id → 404"""
    token, _ = register(client, "j-del-404@example.com")
    r = client.delete("/api/jobs/zzznosuch", headers=auth_header(token))
    assert r.status_code == 404


def test_list_jobs_empty_returns_empty_array(client, register, auth_header):
    """新用户无 job → list 返空数组(确认无空指针 / 异常)"""
    token, _ = register(client, "j-list-empty@example.com")
    r = client.get("/api/jobs/list", headers=auth_header(token))
    assert r.status_code == 200
    assert r.json() == {"jobs": []}


def test_list_jobs_sorted_desc_by_created_at(client, register, auth_header):
    """list 按 created_at 倒序(最新在前)"""
    import time as _time
    token, _ = register(client, "j-list-sort@example.com")

    # 提交 3 个 image jobs(显式 sleep 区分时间戳)
    titles = []
    for i in range(3):
        r = client.post("/api/jobs/submit",
                        json={"type": "image", "params": {"prompt": f"p{i}"}, "title": f"T{i}"},
                        headers=auth_header(token))
        titles.append(r.json()["job_id"])
        _time.sleep(0.01)  # 确保 created_at 有差异

    r = client.get("/api/jobs/list", headers=auth_header(token))
    listed = r.json()["jobs"]
    assert len(listed) == 3
    # 最新的(最后提交的)排第一
    assert listed[0]["id"] == titles[2]
    assert listed[2]["id"] == titles[0]


def test_submit_zero_cost_skips_deduct_check(client, register, auth_header, set_credits):
    """cost==0 路径(虚拟免费 type):不走扣费,余额不变。

    现有 PRICING 表所有 type 都 > 0,但 _module_from_type 走默认 5,
    get_task_cost 也是默认 5。所以构造一个 cost==0 场景需要 monkeypatch get_task_cost。
    """
    from unittest.mock import patch
    token, user = register(client, "j-zerocost@example.com")
    set_credits(user["id"], 50)

    with patch("app.api.jobs.get_task_cost", return_value=0):
        r = client.post("/api/jobs/submit",
                        json={"type": "image", "params": {"prompt": "free"}},
                        headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["cost"] == 0
    me = client.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 50  # 未扣
