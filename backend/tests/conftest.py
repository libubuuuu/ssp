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

# jobs 队列文件:测试用 tmp 路径,绝不碰 /root/ssp/jobs_data/
_TEST_JOBS_FD, _TEST_JOBS_PATH = tempfile.mkstemp(suffix=".json", prefix="ssp_test_jobs_")
os.close(_TEST_JOBS_FD)
os.environ["JOBS_FILE"] = _TEST_JOBS_PATH

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
    from app.api import jobs as jobs_module
    from app.api import admin as admin_module
    from app.api import payment as payment_module
    from app.api import digital_human as digital_human_module

    # 把真实 _execute_job 替成 no-op,避免测试触发 FAL API
    async def _noop_execute_job(job_id):  # pragma: no cover - test helper
        return None
    jobs_module._execute_job = _noop_execute_job

    test_app = FastAPI()
    test_app.include_router(auth_module.router, prefix="/api/auth")
    test_app.include_router(jobs_module.router, prefix="/api/jobs")
    test_app.include_router(admin_module.router, prefix="/api/admin")
    test_app.include_router(payment_module.router, prefix="/api/payment")
    test_app.include_router(digital_human_module.router, prefix="/api/digital-human")
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
def reset_jobs():
    """每个 test 前后清空 jobs 内存 dict,防止跨用例污染"""
    from app.api import jobs as jobs_module
    jobs_module.JOBS.clear()
    yield
    jobs_module.JOBS.clear()


@pytest.fixture(autouse=True)
def reset_database(app):
    """每个 test 前 truncate 主要表,保证用例独立。
    依赖 app fixture 触发 init_db,这样不用 client 的纯函数测试也能拿到 schema。
    """
    from app.database import get_db
    with get_db() as conn:
        c = conn.cursor()
        for table in ("users", "tasks", "credit_orders", "generation_history",
                      "merchants", "products", "orders", "order_items",
                      "body_models", "body_measurements", "model_health",
                      "audit_log"):
            c.execute(f"DELETE FROM {table}")
        conn.commit()
    yield


def pytest_sessionfinish(session, exitstatus):
    """session 结束时清理 tmp 文件"""
    for path in (_TEST_DB_PATH, _TEST_JOBS_PATH):
        try:
            os.unlink(path)
        except OSError:
            pass


# === 公用辅助 ===

def _register(client, email: str, password: str = "secret123", name: str | None = None):
    """注册,返回 (token, user_dict)

    P3-2 后注册要求邮箱码:helper 自动注入 _EMAIL_CODES + 附 code 字段。
    单独测试"无 code"或"错 code"路径走 client.post 自己构造,不通过此 helper。
    """
    import time as _time
    from app.api import auth as auth_module
    auth_module._EMAIL_CODES[email] = {
        "code": "999999",
        "expires_at": _time.time() + 300,
        "sent_at": _time.time(),
        "purpose": "register",
    }
    payload = {"email": email, "password": password, "code": "999999"}
    if name:
        payload["name"] = name
    r = client.post("/api/auth/register", json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    return data["token"], data["user"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _set_role(user_id: str, role: str) -> None:
    """直接改 DB 把 role 设为 admin(没有 API,只能这样)"""
    from app.database import get_db
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        conn.commit()


def _set_credits(user_id: str, credits: int) -> None:
    """直接改 DB 设额度"""
    from app.database import get_db
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET credits = ? WHERE id = ?", (credits, user_id))
        conn.commit()


# 暴露给测试文件
@pytest.fixture()
def register():
    return _register


@pytest.fixture()
def auth_header():
    return _auth


@pytest.fixture()
def set_role():
    return _set_role


@pytest.fixture()
def set_credits():
    return _set_credits
