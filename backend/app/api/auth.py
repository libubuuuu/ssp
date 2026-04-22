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
        cursor.execute("SELECT totp_secret, totp_enabled FROM users WHERE id = ?", (user["id"],))
        totp_row = cursor.fetchone()
        if totp_row and totp_row[1]:
            if not req.totp_code:
                raise HTTPException(status_code=401, detail={"need_2fa": True, "message": "请输入 2FA 验证码"})
            import pyotp
            totp = pyotp.TOTP(totp_row[0])
            if not totp.verify(req.totp_code, valid_window=1):
                raise HTTPException(status_code=401, detail="2FA 验证码错误")

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


# ===== 2FA 双因素认证 =====
import pyotp
import qrcode
import io
import base64
from pydantic import BaseModel

class TotpVerifyReq(BaseModel):
    code: str

class TotpEnableReq(BaseModel):
    secret: str
    code: str


@router.post("/2fa/setup")
async def totp_setup(current_user: dict = Depends(get_current_user)):
    """生成 2FA 密钥和二维码（绑定前调用）"""
    secret = pyotp.random_base32()
    issuer = "AI Lixiao"
    label = current_user.get("email", "user")
    
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=label, issuer_name=issuer)
    
    # 生成二维码图片（base64）
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()
    
    return {
        "secret": secret,
        "qr_code": f"data:image/png;base64,{qr_b64}",
        "manual_entry": secret,
    }


@router.post("/2fa/enable")
async def totp_enable(req: TotpEnableReq, current_user: dict = Depends(get_current_user)):
    """验证用户输入的 6 位码后启用 2FA"""
    totp = pyotp.TOTP(req.secret)
    if not totp.verify(req.code, valid_window=1):
        raise HTTPException(status_code=400, detail="验证码错误，请使用 App 当前显示的 6 位")
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET totp_secret = ?, totp_enabled = 1 WHERE id = ?",
            (req.secret, current_user["id"])
        )
        conn.commit()
    
    return {"success": True, "message": "2FA 已启用"}


@router.post("/2fa/disable")
async def totp_disable(req: TotpVerifyReq, current_user: dict = Depends(get_current_user)):
    """禁用 2FA（需先验证当前 6 位码）"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT totp_secret FROM users WHERE id = ?", (current_user["id"],))
        row = cursor.fetchone()
        if not row or not row[0]:
            raise HTTPException(status_code=400, detail="未启用 2FA")
        
        totp = pyotp.TOTP(row[0])
        if not totp.verify(req.code, valid_window=1):
            raise HTTPException(status_code=400, detail="验证码错误")
        
        cursor.execute(
            "UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE id = ?",
            (current_user["id"],)
        )
        conn.commit()
    
    return {"success": True, "message": "2FA 已禁用"}


@router.get("/2fa/status")
async def totp_status(current_user: dict = Depends(get_current_user)):
    """查询当前用户的 2FA 启用状态"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT totp_enabled FROM users WHERE id = ?", (current_user["id"],))
        row = cursor.fetchone()
        enabled = bool(row[0]) if row else False
    return {"enabled": enabled}
