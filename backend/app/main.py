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
    # ── 启动前：数据库完整性校验（防止蓝绿切槽时读到孤立数据库导致用户数据丢失）
    _db_path = os.environ.get("DATABASE_PATH", "./dev.db")
    _is_test = "tmp" in _db_path or "test" in _db_path or "dev-" in _db_path
    _is_dev  = os.environ.get("SKIP_EMAIL_VERIFY", "").lower() in ("1", "true")
    if not _is_test and not _is_dev:
        _PROD_DB = "/opt/ssp/backend/dev.db"
        _real = os.path.realpath(_db_path)
        if _real != _PROD_DB:
            # 数据库不是生产库，拒绝启动，健康检查不通过，nginx 不会切流量
            import sys
            msg = f"[CRITICAL] 数据库路径异常！期望 {_PROD_DB}，实际解析为 {_real}。拒绝启动，保护用户数据。"
            print(msg, flush=True)
            sys.exit(1)
        log_info(f"数据库校验通过: {_db_path} → {_real}")
    # 启动
    log_info("AI 创意平台 启动成功")
    # P163: 服务重启时把 jobs.json 里 status=running 的孤儿 job 标 failed + 退积分
    try:
        from app.api.jobs import cleanup_orphan_jobs_on_startup
        n = cleanup_orphan_jobs_on_startup()
        if n:
            log_info(f"启动清理: {n} 个孤儿 job 标 failed + 退积分")
    except Exception as e:
        log_error(f"orphan cleanup failed at startup: {e}")
    # 启动时跑一次超时订单取消，再启后台 10 分钟循环
    try:
        from app.services.order_gc import cancel_expired_pending_orders, order_gc_loop
        m = cancel_expired_pending_orders()
        if m:
            log_info(f"启动清理: {m} 个超时 pending 订单已取消")
        import asyncio as _asyncio
        _asyncio.create_task(order_gc_loop())
    except Exception as e:
        log_error(f"order_gc startup failed: {e}")
    # 启动时跑一次数据库备份 + 对账，再启后台 24 小时循环
    try:
        from app.services.db_backup import run_backup_and_reconcile, db_backup_loop
        import asyncio as _asyncio
        run_backup_and_reconcile()
        _asyncio.create_task(db_backup_loop())
    except Exception as e:
        log_error(f"db_backup startup failed: {e}")
    # 启动趋势更新后台循环（每天凌晨3点）
    try:
        from app.services.trend_updater import trend_updater_loop
        import asyncio as _asyncio
        _asyncio.create_task(trend_updater_loop())
    except Exception as e:
        log_error(f"trend_updater startup failed: {e}")
    # 启动实时监控循环（每2分钟检查数据库健康）
    try:
        from app.services.monitoring import monitoring_loop
        import asyncio as _asyncio
        _asyncio.create_task(monitoring_loop())
        log_info("实时监控循环已启动（DB健康检查间隔2分钟）")
    except Exception as e:
        log_error(f"monitoring startup failed: {e}")
    yield
    # 关闭:等所有后台任务完成再退出（最多 10 分钟）
    log_info("AI 创意平台 正在关闭...")
    try:
        from app.api.jobs import wait_all_bg_tasks
        await wait_all_bg_tasks(timeout=600.0)
    except Exception as e:
        log_error(f"shutdown wait_all_bg_tasks 失败: {e}")


app = FastAPI(title="AI 创意平台", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

# allow_origins=["*"] 与 allow_credentials=True 不兼容（CORS 规范禁止）。
# 开发模式：["*"] + allow_credentials=False，Authorization header 仍可用，cookie 不传。
# 生产模式：显式白名单 + allow_credentials=True，cookie 正常。
_dev_mode = os.environ.get("ENVIRONMENT", "").lower() == "development"
if _dev_mode:
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
else:
    ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "")
    origins_list = [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
    if not origins_list:
        origins_list = ["https://ailixiao.com", "https://www.ailixiao.com", "https://admin.ailixiao.com"]
    for _o in ["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000"]:
        if _o not in origins_list:
            origins_list.append(_o)
    app.add_middleware(CORSMiddleware, allow_origins=origins_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

from app.services.rate_limiter import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

from app.middleware.request_id import RequestIdMiddleware
app.add_middleware(RequestIdMiddleware)

from app.middleware.error_spike import ErrorSpikeMiddleware
app.add_middleware(ErrorSpikeMiddleware)

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

from app.api import image, video, tasks, content, products, admin, auth, payment, jobs, wechat_pay, storage, replicate, video_general, video_clone, video_clone_v2, video_frame_extract
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(payment.router, prefix="/api/payment", tags=["支付"])
app.include_router(wechat_pay.router, prefix="/api/wechat-pay", tags=["微信支付"])
app.include_router(storage.router, prefix="/api/storage", tags=["对象存储直传"])
app.include_router(image.router, prefix="/api/image", tags=["图片"])
app.include_router(video.router, prefix="/api/video", tags=["视频"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["任务队列"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["任务"])
app.include_router(content.router, prefix="/api/content", tags=["内容"])
app.include_router(products.router, prefix="/api/products", tags=["产品"])
app.include_router(replicate.router, prefix="/api/video/replicate", tags=["视频复刻工作台"])
app.include_router(video_general.router, prefix="/api/video/general", tags=["通用产品视频"])
app.include_router(video_clone.router, prefix="/api/video/clone", tags=["视频复刻 Seedance r2v"])
app.include_router(video_clone_v2.router, prefix="/api/video/clone-v2", tags=["视频复刻 V2(Seedance r2v 双版本)"])
app.include_router(video_frame_extract.router, prefix="/api/video/frame-extract", tags=["视频拆帧 storyboard"])
app.include_router(admin.router, prefix="/api/admin", tags=["管理员"])

from app.api import billing
app.include_router(billing.router, prefix="/api/billing", tags=["积分流水"])

@app.get("/")
def root():
    return {"message": "AI 创意平台 API", "status": "ok"}

@app.get("/health")
async def health():
    from app.services.health_check import get_health_checker
    checker = get_health_checker()
    return await checker.get_full_health()

@app.get("/internal/active-jobs")
async def internal_active_jobs():
    """deploy.sh drain 专用：返回当前进程内正在运行的任务数，不需要鉴权。"""
    from app.api.jobs import JOBS
    active = sum(1 for j in JOBS.values() if j.get("status") in ("running", "pending"))
    return {"active_jobs": active}

# startup / shutdown 已由顶部 @asynccontextmanager 管理(取代 deprecated @app.on_event)
