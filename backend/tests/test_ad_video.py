"""
AI 带货视频功能测试
关注点:
- /analyze 鉴权、扣费、Claude mock(不真调外部 API)
- /preview 扣费、错误返还、内容审核
- /generate 走 jobs 队列、扣费、jobs 类型识别
- /scene/regenerate 单镜头重生成
- 内容审核拦截(违禁词)
"""
import io
from unittest.mock import patch, AsyncMock
import pytest
from PIL import Image


def _fake_image_bytes(size=(800, 1000)) -> bytes:
    """构造一张测试用 JPEG"""
    img = Image.new("RGB", size, (255, 240, 100))  # 黄色
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


_FAKE_CLAUDE_OK = {
    "audit": {
        "is_valid": True,
        "category": "运动背心",
        "color": "芒果黄",
        "material": "螺纹弹力面料",
        "quality_score": 8.5,
        "issues": [],
        "violations": [],
        "target_audience": "年轻女性",
    },
    "script": {
        "overall_setting": "UGC 自拍风格",
        "model_description": "young female, blonde hair",
        "scenes": [
            {
                "id": 1,
                "time_range": "0-5s",
                "purpose": "开场",
                "shot_language": "前置自拍",
                "content": "拿起背心",
                "visual_prompt": "selfie shot",
                "speech": "OMG check this out",
            },
            {
                "id": 2,
                "time_range": "5-10s",
                "purpose": "展示",
                "shot_language": "特写",
                "content": "面料展示",
                "visual_prompt": "close-up fabric",
                "speech": "Soft fabric",
            },
            {
                "id": 3,
                "time_range": "10-15s",
                "purpose": "促单",
                "shot_language": "回到自拍",
                "content": "挥手",
                "visual_prompt": "selfie wave",
                "speech": "Link in bio",
            },
        ],
    },
}


@pytest.fixture()
def app_with_ad_video(app):
    """在共用 app 上注册 ad_video 路由(测试用)"""
    from app.api import ad_video as ad_video_module
    # 已 included 就不重复
    if not any("ad-video" in str(r.path) for r in app.routes):
        app.include_router(ad_video_module.router, prefix="/api/ad-video")
    return app


@pytest.fixture()
def client_av(app_with_ad_video):
    from fastapi.testclient import TestClient
    return TestClient(app_with_ad_video)


@pytest.fixture()
def mock_fal_upload():
    """
    自动 mock fal_client.upload_file_async,避免测试真去打 fal CDN。
    /analyze 端点内部会上传到 fal storage,所有 /analyze 测试都需要这个。
    """
    with patch("fal_client.upload_file_async", new=AsyncMock(return_value="https://fal.media/files/test/fake-product.jpg")):
        yield


# ==================== /analyze ====================


def test_analyze_unauthenticated_rejected(client_av, mock_fal_upload):
    """未登录直接拒"""
    files = {"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")}
    r = client_av.post("/api/ad-video/analyze", files=files)
    assert r.status_code in (401, 403)


def test_analyze_success_deducts_credits(client_av, mock_fal_upload, register, auth_header, set_credits):
    """成功路径 + 扣费 1 积分"""
    token, user = register(client_av, "av-a@example.com")
    set_credits(user["id"], 50)

    with patch("app.api.ad_video.get_vlm_service") as mock_svc:
        mock_instance = AsyncMock()
        mock_instance.analyze_product = AsyncMock(return_value=_FAKE_CLAUDE_OK)
        mock_svc.return_value = mock_instance

        files = {"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")}
        r = client_av.post("/api/ad-video/analyze", files=files, headers=auth_header(token))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["audit"]["category"] == "运动背心"
    assert len(body["script"]["scenes"]) == 3
    # v3: /analyze 内部上传图到 fal storage,返回 URL 给前端复用
    assert "product_image_url" in body
    assert body["product_image_url"].startswith("https://fal.media/")

    # 扣 1 积分
    me = client_av.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 49


def test_analyze_insufficient_credits_402(client_av, mock_fal_upload, register, auth_header, set_credits):
    """余额不足拦截"""
    token, user = register(client_av, "av-b@example.com")
    set_credits(user["id"], 0)

    files = {"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")}
    r = client_av.post("/api/ad-video/analyze", files=files, headers=auth_header(token))
    assert r.status_code == 402


def test_analyze_violation_returns_400_and_refunds(client_av, mock_fal_upload, register, auth_header, set_credits):
    """审核失败应返还积分(400 触发装饰器返还)"""
    token, user = register(client_av, "av-c@example.com")
    set_credits(user["id"], 50)

    bad_result = {
        "audit": {
            "is_valid": False,
            "category": "违禁",
            "color": "",
            "material": "",
            "quality_score": 0,
            "issues": [],
            "violations": ["疑似侵权 logo"],
            "target_audience": "",
        },
        "script": {"overall_setting": "", "model_description": "", "scenes": []},
    }

    with patch("app.api.ad_video.get_vlm_service") as mock_svc:
        mock_instance = AsyncMock()
        mock_instance.analyze_product = AsyncMock(return_value=bad_result)
        mock_svc.return_value = mock_instance

        files = {"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")}
        r = client_av.post("/api/ad-video/analyze", files=files, headers=auth_header(token))

    assert r.status_code == 400
    # 装饰器应返还 1 积分(扣了又退)
    me = client_av.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 50


def test_analyze_oversized_file_rejected(client_av, mock_fal_upload, register, auth_header, set_credits):
    """图片 > 10MB 拒收(upload_guard 返 413)"""
    token, user = register(client_av, "av-d@example.com")
    set_credits(user["id"], 50)

    huge = b"x" * (11 * 1024 * 1024)
    files = {"file": ("big.jpg", huge, "image/jpeg")}
    r = client_av.post("/api/ad-video/analyze", files=files, headers=auth_header(token))
    assert r.status_code == 413


def test_analyze_unsupported_mime_rejected(client_av, mock_fal_upload, register, auth_header, set_credits):
    """不支持的 MIME 拒收(upload_guard 返 415)"""
    token, user = register(client_av, "av-e@example.com")
    set_credits(user["id"], 50)

    files = {"file": ("test.bmp", b"BM_fake", "image/bmp")}
    r = client_av.post("/api/ad-video/analyze", files=files, headers=auth_header(token))
    assert r.status_code == 415


def test_analyze_service_unavailable_503(client_av, mock_fal_upload, register, auth_header, set_credits):
    """ANTHROPIC_API_KEY 未配置时返回 503 + 返还积分"""
    token, user = register(client_av, "av-f@example.com")
    set_credits(user["id"], 50)

    with patch("app.api.ad_video.get_vlm_service", return_value=None):
        files = {"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")}
        r = client_av.post("/api/ad-video/analyze", files=files, headers=auth_header(token))

    assert r.status_code == 503
    me = client_av.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 50  # 服务不可用全额返还


# ==================== /preview ====================


def test_preview_success_deducts_credits(client_av, register, auth_header, set_credits):
    token, user = register(client_av, "av-g@example.com")
    set_credits(user["id"], 50)

    with patch("app.api.ad_video.ad_video_models.compose_first_frame") as mock_compose:
        mock_compose.return_value = {
            "image_url": "https://fake.fal.media/img.jpg",
            "model": "fal-ai/nano-banana-2/edit",
        }
        # 也 mock archive_url 避免真去下载
        with patch("app.api.ad_video.archive_url", new=AsyncMock(return_value="https://archived/img.jpg")):
            r = client_av.post(
                "/api/ad-video/preview",
                json={
                    "product_image_url": "https://fal.storage/p.jpg",
                    "background_image_url": None,
                    "model_description": "young female",
                    "scene_visual_prompt": "selfie shot in bedroom",
                },
                headers=auth_header(token),
            )

    assert r.status_code == 200, r.text
    assert r.json()["image_url"] == "https://archived/img.jpg"
    me = client_av.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 48  # 50 - 2


def test_preview_blocks_unsafe_prompt(client_av, register, auth_header, set_credits):
    """内容审核拦截(应返还积分)"""
    token, user = register(client_av, "av-h@example.com")
    set_credits(user["id"], 50)

    r = client_av.post(
        "/api/ad-video/preview",
        json={
            "product_image_url": "https://fal.storage/p.jpg",
            "background_image_url": None,
            "model_description": "naked person",  # 命中色情黑名单
            "scene_visual_prompt": "anything",
        },
        headers=auth_header(token),
    )
    assert r.status_code == 400
    me = client_av.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 50  # 拦截前无扣费(content_filter 在 require_credits 之后但 raise 触发返还)


# ==================== /generate ====================


def test_generate_submits_to_jobs_queue(client_av, register, auth_header, set_credits):
    """提交后应进 jobs 队列"""
    token, user = register(client_av, "av-i@example.com")
    set_credits(user["id"], 100)

    payload = {
        "image_url": "https://fal.storage/first-frame.jpg",
        "script": _FAKE_CLAUDE_OK["script"],
        "duration": 15,
        "aspect_ratio": "9:16",
        "resolution": "1080p",
        "enable_audio": True,
    }
    r = client_av.post("/api/ad-video/generate", json=payload, headers=auth_header(token))

    assert r.status_code == 200, r.text
    body = r.json()
    assert "job_id" in body
    assert body["cost"] == 30
    assert body["status"] == "pending"

    # 余额应扣 30
    me = client_av.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 70

    # 验证 jobs 队列里确实有
    job_id = body["job_id"]
    r_j = client_av.get(f"/api/jobs/{job_id}", headers=auth_header(token))
    assert r_j.status_code == 200
    job = r_j.json()
    assert job["type"] == "ad_video"
    assert job["module"] == "ad_video/generate"


def test_generate_insufficient_credits_402(client_av, register, auth_header, set_credits):
    token, user = register(client_av, "av-j@example.com")
    set_credits(user["id"], 5)  # 不够 30

    r = client_av.post(
        "/api/ad-video/generate",
        json={
            "image_url": "https://fal.storage/x.jpg",
            "script": _FAKE_CLAUDE_OK["script"],
        },
        headers=auth_header(token),
    )
    assert r.status_code == 402
    me = client_av.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 5  # 不应扣


def test_generate_blocks_unsafe_speech(client_av, register, auth_header, set_credits):
    """脚本含暴力词 → 拦截"""
    token, user = register(client_av, "av-k@example.com")
    set_credits(user["id"], 100)

    bad_script = {
        "overall_setting": "x",
        "model_description": "y",
        "scenes": [
            {
                "id": 1,
                "time_range": "0-5s",
                "purpose": "z",
                "shot_language": "z",
                "content": "z",
                "visual_prompt": "kill them all",  # 命中暴力词
                "speech": "z",
            }
        ],
    }
    r = client_av.post(
        "/api/ad-video/generate",
        json={"image_url": "https://x", "script": bad_script},
        headers=auth_header(token),
    )
    assert r.status_code == 400
    me = client_av.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 100  # 在扣费前拦截


# ==================== /scene/regenerate ====================


def test_scene_regenerate_success(client_av, register, auth_header, set_credits):
    token, user = register(client_av, "av-l@example.com")
    set_credits(user["id"], 50)

    new_scene = {
        "id": 1,
        "time_range": "0-5s",
        "purpose": "开场",
        "shot_language": "改后",
        "content": "改后",
        "visual_prompt": "new prompt",
        "speech": "new line",
    }

    with patch("app.api.ad_video.get_vlm_service") as mock_svc:
        mock_instance = AsyncMock()
        mock_instance.regenerate_scene = AsyncMock(return_value=new_scene)
        mock_svc.return_value = mock_instance

        r = client_av.post(
            "/api/ad-video/scene/regenerate",
            json={
                "original_scene": _FAKE_CLAUDE_OK["script"]["scenes"][0],
                "instruction": "更激情一些",
            },
            headers=auth_header(token),
        )

    assert r.status_code == 200, r.text
    assert r.json()["scene"]["speech"] == "new line"
    me = client_av.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 49  # 1 积分


# ==================== 用户隔离 ====================


def test_user_isolation_in_jobs(client_av, register, auth_header, set_credits):
    """A 提交的 ad_video job,B 看不到/拿不到"""
    a_token, a_user = register(client_av, "av-iso-a@example.com")
    b_token, b_user = register(client_av, "av-iso-b@example.com")
    set_credits(a_user["id"], 100)
    set_credits(b_user["id"], 100)

    r = client_av.post(
        "/api/ad-video/generate",
        json={"image_url": "https://x", "script": _FAKE_CLAUDE_OK["script"]},
        headers=auth_header(a_token),
    )
    assert r.status_code == 200
    a_job_id = r.json()["job_id"]

    # B 拿不到
    r_b = client_av.get(f"/api/jobs/{a_job_id}", headers=auth_header(b_token))
    assert r_b.status_code == 403


# ==================== /upload/image(upload_guard 守卫) ====================


def test_upload_image_oversize_returns_413(client_av, register, auth_header):
    """upload_guard:>10MB 拒收 413"""
    token, _ = register(client_av, "av-up-big@example.com")
    huge = b"x" * (11 * 1024 * 1024)
    files = {"file": ("big.jpg", huge, "image/jpeg")}
    r = client_av.post("/api/ad-video/upload/image", files=files, headers=auth_header(token))
    assert r.status_code == 413


def test_upload_image_unsupported_mime_returns_415(client_av, register, auth_header):
    """upload_guard:非白名单 MIME 拒收 415"""
    token, _ = register(client_av, "av-up-mime@example.com")
    files = {"file": ("a.bmp", b"BM_fake", "image/bmp")}
    r = client_av.post("/api/ad-video/upload/image", files=files, headers=auth_header(token))
    assert r.status_code == 415


def test_upload_image_valid_passes_guard(client_av, register, auth_header):
    """合规图片 < 10MB + image/jpeg → 通过 upload_guard 进 Pillow 流程

    fal_client.upload_file_async 走真路径会出网,这里 mock 掉。
    业务返回 200 即证 guard 放行。
    """
    token, _ = register(client_av, "av-up-ok@example.com")
    valid = _fake_image_bytes(size=(1000, 1000))
    files = {"file": ("ok.jpg", valid, "image/jpeg")}
    with patch("fal_client.upload_file_async", new=AsyncMock(return_value="https://fal.media/test.jpg")):
        r = client_av.post("/api/ad-video/upload/image", files=files, headers=auth_header(token))
    assert r.status_code == 200, r.text


# ==================== /quick-prompt(七十续 简化带货 prompt 工具)====================


def test_quick_prompt_unauthenticated_rejected(client_av, mock_fal_upload):
    files = {"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")}
    r = client_av.post("/api/ad-video/quick-prompt", files=files)
    assert r.status_code == 401


def test_quick_prompt_oversize_returns_413(client_av, mock_fal_upload, register, auth_header):
    """upload_guard >10MB 拒收"""
    token, _ = register(client_av, "qp-big@example.com")
    huge = b"x" * (11 * 1024 * 1024)
    files = {"file": ("big.jpg", huge, "image/jpeg")}
    r = client_av.post("/api/ad-video/quick-prompt", files=files, headers=auth_header(token))
    assert r.status_code == 413


def test_quick_prompt_success_returns_string_prompt(client_av, mock_fal_upload, register, auth_header, set_credits):
    """快速 prompt 成功:返 prompt 字符串 + product_image_url + 扣 1 积分"""
    token, user = register(client_av, "qp-ok@example.com")
    set_credits(user["id"], 50)

    fake_prompt = "年轻女性,长发披肩,身穿黄色背心,自拍角度,自然光,close-up shot,展示面料质感"
    with patch("app.api.ad_video.get_vlm_service") as mock_factory:
        mock_factory.return_value.generate_quick_prompt = AsyncMock(return_value={"prompt": fake_prompt})
        files = {"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")}
        r = client_av.post("/api/ad-video/quick-prompt", files=files, headers=auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["prompt"] == fake_prompt
    assert body["product_image_url"].startswith("https://fal.media/")

    me = client_av.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 50 - 1  # 扣 1 积分(ad_video/analyze 定价)


def test_quick_prompt_vlm_error_refunds(client_av, mock_fal_upload, register, auth_header, set_credits):
    """VLM 失败 → require_credits 装饰器返还积分"""
    token, user = register(client_av, "qp-err@example.com")
    set_credits(user["id"], 50)

    with patch("app.api.ad_video.get_vlm_service") as mock_factory:
        mock_factory.return_value.generate_quick_prompt = AsyncMock(return_value={"error": "VLM 调用失败"})
        files = {"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")}
        r = client_av.post("/api/ad-video/quick-prompt", files=files, headers=auth_header(token))
    assert r.status_code == 500

    me = client_av.get("/api/auth/me", headers=auth_header(token)).json()
    assert me["credits"] == 50  # 已退


def test_quick_prompt_unsafe_content_rejects(client_av, mock_fal_upload, register, auth_header, set_credits):
    """VLM 返敏感词 → assert_safe_prompt 拦 + 退积分"""
    token, user = register(client_av, "qp-unsafe@example.com")
    set_credits(user["id"], 50)

    # 用一个会被 assert_safe_prompt 拦的敏感词(从 content_filter 黑名单)
    with patch("app.api.ad_video.get_vlm_service") as mock_factory:
        mock_factory.return_value.generate_quick_prompt = AsyncMock(
            return_value={"prompt": "色情内容描述测试敏感"}
        )
        with patch("app.api.ad_video.assert_safe_prompt", side_effect=Exception("blocked")):
            # patch 强制让 assert_safe_prompt 抛 → 走 400 分支
            from fastapi import HTTPException as HE
            with patch("app.api.ad_video.assert_safe_prompt", side_effect=HE(status_code=400, detail="敏感")):
                files = {"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")}
                r = client_av.post("/api/ad-video/quick-prompt", files=files, headers=auth_header(token))
    assert r.status_code == 400
