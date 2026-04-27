"""锁定 FalAvatarService 支持的模型清单(plan §4.2)

防止误删 / 笔误。纯静态字典断言,不调 fal,跑得很快。

2026-04-28 增量:从 2 个模型扩到 4 个(加 Creatify Aurora + ByteDance Omnihuman v1.5)
"""


def test_avatar_service_has_four_models():
    from app.services.fal_service import FalAvatarService
    assert set(FalAvatarService.MODELS.keys()) == {
        "hunyuan-avatar",
        "pixverse-lipsync",
        "creatify-aurora",
        "omnihuman-v1.5",
    }


def test_avatar_models_have_endpoint_and_label():
    from app.services.fal_service import FalAvatarService
    for key, info in FalAvatarService.MODELS.items():
        assert "endpoint" in info, f"{key} 缺 endpoint"
        assert "label" in info, f"{key} 缺 label"
        assert info["endpoint"].startswith("fal-ai/"), f"{key} endpoint 格式可疑: {info['endpoint']}"


def test_omnihuman_endpoint_includes_v15():
    """Omnihuman 必须用 /v1.5 — plan 里旧版字符串(无 /v1.5)是错的"""
    from app.services.fal_service import FalAvatarService
    assert FalAvatarService.MODELS["omnihuman-v1.5"]["endpoint"] == "fal-ai/bytedance/omnihuman/v1.5"


def test_aurora_endpoint_is_creatify():
    from app.services.fal_service import FalAvatarService
    assert FalAvatarService.MODELS["creatify-aurora"]["endpoint"] == "fal-ai/creatify/aurora"
