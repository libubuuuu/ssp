"""
用户认证 API
- 注册
- 登录
- 获取当前用户信息
- 刷新 Token
"""
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, Field
from typing import Optional
import re
from ..services.auth import (
    create_user,
    get_user_by_email,
    verify_password,
    create_jwt_token,
    decode_jwt_token,
    get_user_by_id,
    hash_password,
)
from ..database import get_db

router = APIRouter()


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=254)
    password: str = Field(..., min_length=6, max_length=128)
    name: Optional[str] = Field(None, max_length=50)


class LoginRequest(BaseModel):
    email: str
    password: str


class UpdateNameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6, max_length=128)


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


@router.put("/me")
async def update_user_name(req: UpdateNameRequest, current_user: dict = Depends(get_current_user)):
    """更新用户昵称"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
        """, (req.name, current_user["id"]))
        conn.commit()
    return {"message": "昵称已更新", "name": req.name}


@router.post("/change-password")
async def change_password(req: ChangePasswordRequest, current_user: dict = Depends(get_current_user)):
    """修改密码"""
    # 验证当前密码
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE id = ?", (current_user["id"],))
        row = cursor.fetchone()
        if not row or not verify_password(req.current_password, row[0]):
            raise HTTPException(status_code=400, detail="当前密码错误")

        # 更新密码
        new_hash = hash_password(req.new_password)
        cursor.execute("""
            UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
        """, (new_hash, current_user["id"]))
        conn.commit()

    return {"message": "密码已修改"}


@router.post("/forgot-password")
async def forgot_password(req: dict):
    """密码找回 - 发送重置链接（模拟）"""
    email = req.get("email", "")
    # TODO: 实际部署时发送重置邮件
    return {"message": "重置链接已发送到邮箱", "email": email}
