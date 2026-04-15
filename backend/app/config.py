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

    # JWT 认证
    JWT_SECRET: str = "change-this-secret-in-production-2026"

    class Config:
        env_file = str(_ENV_PATH)
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
