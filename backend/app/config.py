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

    # 七十六续:长视频工作台模型可切换(env 留空 = 用代码默认值,行为不变)
    # OVERRIDE 非空时,无视 mode 全部用 OVERRIDE(灰度/全量切换的开关);失败 3 次自动回退对应 mode 默认值
    STUDIO_VIDEO_MODEL_EDIT: str = ""           # 默认 fal-ai/kling-video/o1/video-to-video/edit
    STUDIO_VIDEO_MODEL_EDIT_O3: str = ""        # 默认 fal-ai/kling-video/o3/pro/video-to-video/edit
    STUDIO_VIDEO_MODEL_OVERRIDE: str = ""       # 灰度/全量开关,非空时所有 mode 都走它

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

    # 七十二续:对象存储直传(B 方案 — 用户上传走前端 → COS 直传,服务器只签 STS 凭证)
    # 默认关,启用前 /api/storage/sts 返 503;启用后所有上传端点应改走 OSS URL
    STORAGE_DIRECT_UPLOAD_ENABLED: bool = False
    STORAGE_PROVIDER: str = "tencent_cos"        # 当前只实现 tencent_cos,留接口给 aliyun_oss 等
    STORAGE_BUCKET: str = ""                     # COS bucket 名,如 "ssp-uploads-1300000000"
    STORAGE_REGION: str = ""                     # 区域,如 "ap-guangzhou"
    STORAGE_SECRET_ID: str = ""                  # 子账号 SecretId(读 STS 权限即可,不用 root)
    STORAGE_SECRET_KEY: str = ""                 # 子账号 SecretKey
    STORAGE_DURATION_SECONDS: int = 900          # 临时凭证有效期(15 分钟,够大文件上传)
    STORAGE_BUCKET_PREFIX: str = "uploads/"      # 限制 STS 凭证只能写这个前缀,防越权

    # 六十八续:微信支付 V3(默认关,等用户开商户号 + 配 cert 后启用)
    WECHAT_PAY_ENABLED: bool = False
    WECHAT_PAY_MCH_ID: str = ""                  # 商户号(10 位数字)
    WECHAT_PAY_APP_ID: str = ""                  # 公众号 / 小程序 / 开放平台 AppID
    WECHAT_PAY_API_V3_KEY: str = ""              # APIv3 密钥(32 位,商户后台设置)
    WECHAT_PAY_CERT_SERIAL: str = ""             # 商户 API 证书序列号
    WECHAT_PAY_PRIVATE_KEY_PATH: str = ""        # 商户 API 私钥 PEM 文件路径
    WECHAT_PAY_NOTIFY_URL: str = ""              # 异步回调 URL,如 https://ailixiao.com/api/wechat-pay/notify

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
