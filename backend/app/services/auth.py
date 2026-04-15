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
JWT_EXPIRATION_HOURS = 24 * 7  # 7 天


def hash_password(password: str) -> str:
    """密码加密"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))


def create_jwt_token(user_id: str, email: str, role: str) -> str:
    """生成 JWT Token"""
    expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    payload = {
        "user_id": user_id,
        "email": email,
        "role": role,
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt_token(token: str) -> Optional[Dict]:
    """解析 JWT Token"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


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
