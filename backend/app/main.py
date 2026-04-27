import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.services.logger import setup_logger, log_info, log_error

logger = setup_logger()
settings = get_settings()

try:
    settings.validate()
    log_info("配置验证通过")
except ValueError as e:
    log_error(f"配置验证失败：{e}")
    raise

# P5: Sentry 错误监控(SENTRY_DSN 为空时跳过 — 不阻塞本地/测试启动)
if settings.SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from app.services.sentry_filter import before_send
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT or "production",
            integrations=[
                StarletteIntegration(transaction_style="endpoint"),
                FastApiIntegration(transaction_style="endpoint"),
            ],
            traces_sample_rate=0.1,     # 10% trace 采样,免费额度够用
            profiles_sample_rate=0.0,    # profiling 关闭(免费额度有限)
            send_default_pii=False,     # 不上报 user-agent / IP / cookie 之类
            attach_stacktrace=True,
            before_send=before_send,    # 隐藏雷 #3:过滤 4xx + fal 瞬时错
        )
        log_info(f"Sentry 已启用 (env={settings.ENVIRONMENT}, 过滤 4xx + fal 瞬时错)")
    except ImportError:
        log_error("SENTRY_DSN 已配置但 sentry-sdk 未安装,跳过(pip install sentry-sdk[fastapi])")
    except Exception as e:
        log_error(f"Sentry 初始化失败,继续不带监控:{e}")
else:
    log_info("Sentry 未配置(SENTRY_DSN 为空),跳过")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期:取代 deprecated @app.on_event('startup'/'shutdown')"""
    # 启动
    log_info("AI 创意平台 启动成功")
    yield
    # 关闭
    log_info("AI 创意平台 正在关闭...")


app = FastAPI(title="AI 创意平台", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "")
origins_list = [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
if not origins_list:
    origins_list = ["http://localhost:3000"]

app.add_middleware(CORSMiddleware, allow_origins=origins_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

from app.services.rate_limiter import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

from app.middleware.request_id import RequestIdMiddleware
app.add_middleware(RequestIdMiddleware)

from app.database import init_db
from app.services.fal_service import init_fal_services
from app.services.circuit_breaker import init_circuit_breaker
from app.services.alert import init_alert_service
from app.services.task_queue import init_task_queue

init_db()
if settings.FAL_KEY:
    os.environ["FAL_KEY"] = settings.FAL_KEY
    init_fal_services(settings.FAL_KEY)
init_circuit_breaker()
init_alert_service(settings.ALIYUN_ACCESS_KEY_ID, settings.ALIYUN_ACCESS_KEY_SECRET, "AI创意平台", settings.ALIYUN_SMS_TEMPLATE_CODE, [settings.DEVELOPER_PHONE] if settings.DEVELOPER_PHONE else [])
init_task_queue()

# 2026-04-28 v3: AI 带货视频 - VLM 服务(走 fal OpenRouter,复用 FAL_KEY,零新依赖)
from app.services.vlm_service import init_vlm_service
init_vlm_service()

from app.api import image, video, digital_human, tasks, content, products, admin, avatar, auth, payment, video_studio, jobs, ad_video
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(payment.router, prefix="/api/payment", tags=["支付"])
app.include_router(image.router, prefix="/api/image", tags=["图片"])
app.include_router(video.router, prefix="/api/video", tags=["视频"])
app.include_router(video_studio.router, prefix="/api/studio", tags=["长视频工作台"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["任务队列"])
app.include_router(digital_human.router, prefix="/api/digital-human", tags=["数字人"])
app.include_router(avatar.router, prefix="/api/avatar", tags=["Avatar"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["任务"])
app.include_router(content.router, prefix="/api/content", tags=["内容"])
app.include_router(products.router, prefix="/api/products", tags=["产品"])
app.include_router(ad_video.router, prefix="/api/ad-video", tags=["AI带货视频"])
app.include_router(admin.router, prefix="/api/admin", tags=["管理员"])

@app.get("/")
def root():
    return {"message": "AI 创意平台 API", "status": "ok"}

@app.get("/health")
async def health():
    from app.services.health_check import get_health_checker
    checker = get_health_checker()
    return await checker.get_full_health()

# startup / shutdown 已由顶部 @asynccontextmanager 管理(取代 deprecated @app.on_event)
