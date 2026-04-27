"""
配置
"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from functools import lru_cache

# .env 文件路径 (backend 根目录)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    # API
    API_PREFIX: str = "/api"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # 存储 (S3 兼容)
    S3_ENDPOINT: str = ""
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_BUCKET: str = "ai-creative"

    # 数据库
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost:5432/ai_creative"

    # 飞书
    FEISHU_WEBHOOK_URL: str = ""

    # AI 服务 (按需配置)
    OPENAI_API_KEY: str = ""
    RUNWAY_API_KEY: str = ""

    # FAL AI (最低成本配置)
    FAL_KEY: str = ""
    FAL_IMAGE_MODEL: str = "fal-ai/nano-banana-2"
    FAL_VIDEO_MODEL: str = "fal-ai/kling-video/o3/standard/image-to-video"

    # 阿里云短信
    ALIYUN_ACCESS_KEY_ID: str = ""
    ALIYUN_ACCESS_KEY_SECRET: str = ""
    ALIYUN_SMS_TEMPLATE_CODE: str = ""  # 短信模板代码
    DEVELOPER_PHONE: str = ""  # 开发者手机号

    # JWT 认证（必须从环境变量设置，无默认值）
    JWT_SECRET: str = ""

    # P5: Sentry 错误监控(可选 — 空时不启用)
    SENTRY_DSN: str = ""
    ENVIRONMENT: str = "production"  # 也用于 Sentry 标签;dev/staging/production

    # P8: httpOnly Cookie 配置
    # 生产推荐:COOKIE_DOMAIN=".ailixiao.com",COOKIE_SECURE=True
    # 本地/测试:COOKIE_DOMAIN="",COOKIE_SECURE=False(http 也能 set)
    COOKIE_DOMAIN: str = ""        # 空字符串 = 不设 Domain 属性(浏览器默认 exact host)
    COOKIE_SECURE: bool = True     # production 必 True;dev http 设 False

    class Config:
        env_file = str(_ENV_PATH)
        extra = "ignore"

    def validate(self) -> None:
        """验证关键配置是否存在"""
        if not self.JWT_SECRET or self.JWT_SECRET == "change-this-secret-in-production-2026":
            raise ValueError("JWT_SECRET 必须从环境变量设置，且不能使用默认值")
        if not self.FAL_KEY:
            raise ValueError("FAL_KEY 必须从环境变量设置")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
