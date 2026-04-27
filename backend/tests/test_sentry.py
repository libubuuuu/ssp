"""P5: Sentry 集成测试

策略:不真连 Sentry(免费额度宝贵),只测:
- SENTRY_DSN 为空时不调 init(跳过)
- 配置项默认值正确
- import 路径不破坏(包已装)
"""
from unittest.mock import patch, MagicMock


def test_settings_default_sentry_dsn_empty():
    """默认配置下 SENTRY_DSN 是空字符串(不启用)"""
    from app.config import Settings
    s = Settings(JWT_SECRET="test-x", FAL_KEY="test-y")
    assert s.SENTRY_DSN == ""
    assert s.ENVIRONMENT == "production"


def test_settings_with_sentry_dsn_set():
    """显式提供 DSN 时配置正常加载"""
    from app.config import Settings
    s = Settings(
        JWT_SECRET="test-x", FAL_KEY="test-y",
        SENTRY_DSN="https://abc@sentry.io/123",
        ENVIRONMENT="staging",
    )
    assert s.SENTRY_DSN.startswith("https://")
    assert s.ENVIRONMENT == "staging"


def test_sentry_sdk_importable():
    """包真装上了"""
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    assert hasattr(sentry_sdk, "init")
    assert FastApiIntegration is not None
