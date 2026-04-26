"""
用户认证服务
- 邮箱登录/注册
- JWT Token 生成和验证
- 密码加密
"""
import uuid
import jwt
import bcrypt
from datetime import datetime, timedelta
from typing import Optional, Dict
from ..database import get_db
from ..config import get_settings

settings = get_settings()

# JWT 配置 — 强制从环境变量读取，不允许 fallback
if not settings.JWT_SECRET:
    raise ValueError("JWT_SECRET 环境变量未设置，服务无法启动")
JWT_SECRET = settings.JWT_SECRET
JWT_ALGORITHM = "HS256"
# access token:平时用,过期短,泄漏窗口小
# refresh token:只用于 /refresh 换 access,过期长,平时不传
JWT_ACCESS_EXPIRATION_HOURS = 1       # 1 小时(前端 401 拦截 + 主动续期阈值 10 分 + 5 分轮询已就位)
JWT_REFRESH_EXPIRATION_DAYS = 30      # refresh token 30 天


def hash_password(password: str) -> str:
    """密码加密"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))


def create_access_token(user_id: str, email: str, role: str) -> str:
    """签发 access token(平时调业务接口用)"""
    expire = datetime.utcnow() + timedelta(hours=JWT_ACCESS_EXPIRATION_HOURS)
    payload = {
        "user_id": user_id,
        "email": email,
        "role": role,
        "type": "access",
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str, email: str, role: str) -> str:
    """签发 refresh token(仅用于 /refresh 换新 access,平时不传)"""
    expire = datetime.utcnow() + timedelta(days=JWT_REFRESH_EXPIRATION_DAYS)
    payload = {
        "user_id": user_id,
        "email": email,
        "role": role,
        "type": "refresh",
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_jwt_token(user_id: str, email: str, role: str) -> str:
    """向后兼容别名 = create_access_token"""
    return create_access_token(user_id, email, role)


def decode_jwt_token(token: str) -> Optional[Dict]:
    """解析 access token,验证未被用户级吊销。

    流程:
    1. 验证签名 + 过期时间
    2. **拒绝 refresh token**(只允许 access 调业务接口,refresh 单独走 decode_refresh_token)
    3. 查用户 tokens_invalid_before:若 > token.iat,说明用户已主动/被动撤销所有 token
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

    # 拒绝 refresh token 调业务接口(没有 type 字段视为 access,向后兼容旧 token)
    if payload.get("type") == "refresh":
        return None

    # 用户级吊销检查
    user_id = payload.get("user_id")
    iat = payload.get("iat")
    if user_id and iat is not None:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT tokens_invalid_before FROM users WHERE id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if row and row[0] and row[0] > iat:
                return None  # 该用户所有 token 已撤销

    return payload


def decode_refresh_token(token: str) -> Optional[Dict]:
    """解析 refresh token(必须 type=refresh,且未被用户级吊销)"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

    # 必须是 refresh 类型,排除 access / 旧无 type 的 token
    if payload.get("type") != "refresh":
        return None

    # 用户级吊销同样适用于 refresh
    user_id = payload.get("user_id")
    iat = payload.get("iat")
    if user_id and iat is not None:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT tokens_invalid_before FROM users WHERE id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if row and row[0] and row[0] > iat:
                return None

    return payload


def invalidate_user_tokens(user_id: str) -> bool:
    """把用户的 tokens_invalid_before 设为当前 unix 时间戳。
    效果:用户当前所有有效 token 立即失效,需重新登录。
    用于:
      - 用户主动"登出所有设备"
      - 管理员强制踢人
      - 改密码(防止泄漏密码后旧 token 仍可用)
    """
    import time
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET tokens_invalid_before = ? WHERE id = ?",
            (int(time.time()), user_id),
        )
        conn.commit()
        return cursor.rowcount == 1


def get_user_by_email(email: str) -> Optional[Dict]:
    """根据邮箱获取用户"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, email, name, role, credits, phone, created_at
            FROM users WHERE email = ?
        """, (email,))
        row = cursor.fetchone()
        if row:
            return {
                "id": row[0],
                "email": row[1],
                "name": row[2],
                "role": row[3],
                "credits": row[4],
                "phone": row[5],
                "created_at": row[6],
            }
        return None


def get_user_by_id(user_id: str) -> Optional[Dict]:
    """根据 ID 获取用户"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, email, name, role, credits, phone, created_at
            FROM users WHERE id = ?
        """, (user_id,))
        row = cursor.fetchone()
        if row:
            return {
                "id": row[0],
                "email": row[1],
                "name": row[2],
                "role": row[3],
                "credits": row[4],
                "phone": row[5],
                "created_at": row[6],
            }
        return None


def create_user(email: str, password: str, name: Optional[str] = None) -> Optional[Dict]:
    """创建新用户"""
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            user_id = str(uuid.uuid4())
            hashed = hash_password(password)
            cursor.execute("""
                INSERT INTO users (id, email, password_hash, name, credits)
                VALUES (?, ?, ?, ?, 100)
            """, (user_id, email, hashed, name or email.split('@')[0]))
            conn.commit()
            return get_user_by_email(email)
        except Exception as e:
            print(f"创建用户失败：{e}")
            return None


def update_user_credits(user_id: str, amount: int) -> bool:
    """更新用户额度（可正可负）"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET credits = credits + ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (amount, user_id))
        conn.commit()
        return cursor.rowcount > 0


def set_user_credits(user_id: str, amount: int) -> bool:
    """设置用户额度（覆盖）"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET credits = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (amount, user_id))
        conn.commit()
        return cursor.rowcount > 0
