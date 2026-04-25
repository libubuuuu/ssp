"""
pytest 共享 fixture
约定:
- 测试用一次性临时 SQLite,绝不碰 backend/dev.db
- 必要的 env 变量在导入应用前设好,避免 settings.validate() 抛错
- _EMAIL_CODES 等内存状态每个 test function 重置,防止用例间污染
"""
import os
import sys
import tempfile
import importlib

import pytest

# === 关键:必须在 import app.* 之前把 env 设好 ===
# JWT_SECRET / FAL_KEY 是 settings.validate() 强制要求的
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-for-production-1234567890")
os.environ.setdefault("FAL_KEY", "test-fal-key-fake")
# RESEND_API_KEY 留空,_send_email_code 会 fallback 到打印模式,不真发邮件
os.environ.setdefault("RESEND_API_KEY", "")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:3000")

# 测试库:每个 pytest session 一份 tmp 文件
_TEST_DB_FD, _TEST_DB_PATH = tempfile.mkstemp(suffix=".db", prefix="ssp_test_")
os.close(_TEST_DB_FD)
os.environ["DATABASE_PATH"] = _TEST_DB_PATH

# 把 backend/ 加到 sys.path,这样 `import app.xxx` 能解析
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


@pytest.fixture(scope="session")
def app():
    """整个测试 session 共用一个 FastAPI app(避免反复 init)"""
    # 注意:这里才 import app.main,确保 env 已经设好
    from app.database import init_db
    init_db()  # 在 tmp DB 上建表

    from fastapi import FastAPI
    from app.api import auth as auth_module

    test_app = FastAPI()
    test_app.include_router(auth_module.router, prefix="/api/auth")
    return test_app


@pytest.fixture()
def client(app):
    """每个 test 一个干净 TestClient"""
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_email_codes():
    """每个 test 前后清空内存里的验证码缓存,防止跨用例污染"""
    from app.api import auth as auth_module
    auth_module._EMAIL_CODES.clear()
    yield
    auth_module._EMAIL_CODES.clear()


@pytest.fixture(autouse=True)
def reset_database():
    """每个 test 前 truncate 主要表,保证用例独立"""
    from app.database import get_db
    with get_db() as conn:
        c = conn.cursor()
        for table in ("users", "tasks", "credit_orders", "generation_history",
                      "merchants", "products", "orders", "order_items",
                      "body_models", "body_measurements", "model_health"):
            c.execute(f"DELETE FROM {table}")
        conn.commit()
    yield


def pytest_sessionfinish(session, exitstatus):
    """session 结束时清理 tmp 库"""
    try:
        os.unlink(_TEST_DB_PATH)
    except OSError:
        pass
