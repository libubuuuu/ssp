"""
AI 创意平台 - 主入口
- 企业级日志
- 健康检查
- 熔断保护
- 限流防刷
"""
import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import image, video, digital_human, tasks, content, products, admin, avatar, auth, payment
from app.services.rate_limiter import RateLimitMiddleware
from app.database import init_db
from app.config import get_settings
from app.services.fal_service import init_fal_services
from app.services.circuit_breaker import init_circuit_breaker
from app.services.alert import init_alert_service
from app.services.task_queue import init_task_queue
from app.services.health_check import get_health_checker
from app.services.logger import log_info, log_error, setup_logger

# 设置全局日志
logger = setup_logger()

app = FastAPI(
    title="AI 创意平台",
    description="图片生成 | 视频生成 | 数字人",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 限流中间件
app.add_middleware(RateLimitMiddleware)

# 初始化服务
settings = get_settings()

# 初始化 FAL AI 服务
if settings.FAL_KEY:
    os.environ["FAL_KEY"] = settings.FAL_KEY
    init_fal_services(settings.FAL_KEY)
    log_info("FAL AI 服务已初始化")

# 初始化熔断器
init_circuit_breaker()
log_info("熔断器已初始化")

# 初始化告警服务（阿里云短信）
init_alert_service(
    access_key_id=settings.ALIYUN_ACCESS_KEY_ID,
    access_key_secret=settings.ALIYUN_ACCESS_KEY_SECRET,
    sign_name="AI 创意平台",
    template_code=settings.ALIYUN_SMS_TEMPLATE_CODE,
    phone_numbers=[settings.DEVELOPER_PHONE] if hasattr(settings, 'DEVELOPER_PHONE') and settings.DEVELOPER_PHONE else [],
)
log_info("告警服务已初始化")

# 初始化任务队列
init_task_queue()
log_info("任务队列已初始化")

# 路由
app.include_router(auth.router, prefix="/api/auth", tags=["用户认证"])
app.include_router(payment.router, prefix="/api/payment", tags=["支付订单"])
app.include_router(image.router, prefix="/api/image", tags=["图片生成"])
app.include_router(video.router, prefix="/api/video", tags=["视频生成"])
app.include_router(digital_human.router, prefix="/api/digital-human", tags=["数字人"])
app.include_router(avatar.router, prefix="/api/avatar", tags=["数字人/语音"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["任务"])
app.include_router(content.router, prefix="/api/content", tags=["内容增强"])
app.include_router(products.router, prefix="/api/products", tags=["产品"])
app.include_router(admin.router, prefix="/api/admin", tags=["管理员"])

log_info("所有路由已注册")


@app.get("/")
def root():
    log_info("根路径访问")
    return {"message": "AI 创意平台 API", "status": "ok"}


@app.get("/health")
async def health():
    """企业级健康检查"""
    checker = get_health_checker()
    health_status = await checker.get_full_health()

    if health_status["status"] == "healthy":
        log_info("健康检查通过")
    else:
        log_error(f"健康检查异常：{health_status}")

    return health_status


@app.on_event("startup")
async def startup_event():
    """启动事件"""
    log_info("=" * 50)
    log_info("AI 创意平台 启动中...")
    log_info(f"环境：{os.environ.get('ENV', 'development')}")
    log_info("=" * 50)


@app.on_event("shutdown")
async def shutdown_event():
    """关闭事件"""
    log_info("AI 创意平台 正在关闭...")
    logging.shutdown()
