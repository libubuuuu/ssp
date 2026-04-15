"""
用户认证 API
- 注册
- 登录
- 获取当前用户信息
- 刷新 Token
"""
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional
from ..services.auth import (
    create_user,
    get_user_by_email,
    verify_password,
    create_jwt_token,
    decode_jwt_token,
    get_user_by_id,
)

router = APIRouter()


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    token: str
    user: dict


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """获取当前登录用户"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    # Bearer <token>
    parts = authorization.split()
    if len(parts) != 2 or parts[0] != "Bearer":
        raise HTTPException(status_code=401, detail="Token 格式错误")

    token = parts[1]
    payload = decode_jwt_token(token)

    if not payload:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    user = get_user_by_id(payload["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")

    # 不返回密码哈希
    user.pop("password_hash", None)
    return user


@router.post("/register")
async def register(req: RegisterRequest):
    """用户注册"""
    # 检查邮箱是否已存在
    existing = get_user_by_email(req.email)
    if existing:
        raise HTTPException(status_code=400, detail="该邮箱已被注册")

    # 创建用户
    user = create_user(req.email, req.password, req.name)
    if not user:
        raise HTTPException(status_code=500, detail="注册失败")

    # 生成 Token
    token = create_jwt_token(user["id"], user["email"], user["role"])

    return {
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
            "credits": user["credits"],
        },
    }


@router.post("/login")
async def login(req: LoginRequest):
    """用户登录"""
    user = get_user_by_email(req.email)
    if not user:
        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    # 验证密码
    stored_user = get_user_by_email(req.email)
    # 需要获取密码哈希进行验证
    from ..database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE email = ?", (req.email,))
        row = cursor.fetchone()
        if not row or not verify_password(req.password, row[0]):
            raise HTTPException(status_code=401, detail="邮箱或密码错误")

    # 生成 Token
    token = create_jwt_token(user["id"], user["email"], user["role"])

    return {
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
            "credits": user["credits"],
        },
    }


@router.get("/me")
async def get_current_user_info(current_user: dict = Depends(get_current_user)):
    """获取当前用户信息"""
    return current_user


@router.post("/refresh")
async def refresh_token(authorization: Optional[str] = Header(None)):
    """刷新 Token"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    parts = authorization.split()
    if len(parts) != 2 or parts[0] != "Bearer":
        raise HTTPException(status_code=401, detail="Token 格式错误")

    token = parts[1]
    payload = decode_jwt_token(token)

    if not payload:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    # 生成新 Token
    new_token = create_jwt_token(payload["user_id"], payload["email"], payload["role"])

    return {"token": new_token}
