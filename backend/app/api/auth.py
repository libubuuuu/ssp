"""
用户认证 API
- 注册
- 登录
- 获取当前用户信息
- 刷新 Token
"""
from fastapi import APIRouter, HTTPException, Depends, Header, Request, Response
from pydantic import BaseModel, Field
from typing import Optional
import re
from ..config import get_settings
from ..services.auth import (
    create_user,
    get_user_by_email,
    verify_password,
    create_jwt_token,
    create_access_token,
    create_refresh_token,
    decode_jwt_token,
    decode_refresh_token,
    get_user_by_id,
    hash_password,
    invalidate_user_tokens,
)
from ..database import get_db

router = APIRouter()


# === P8: httpOnly Cookie 工具 ===
# Cookie 名固定,不要更名(前端依赖)
ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"
ACCESS_COOKIE_MAX_AGE = 3600          # 1 小时,跟 access JWT TTL 对齐
REFRESH_COOKIE_MAX_AGE = 30 * 86400   # 30 天,跟 refresh JWT TTL 对齐


def set_auth_cookies(response: Response, access_token: str, refresh_token: Optional[str] = None) -> None:
    """登录/注册/refresh 成功后调用。Cookie 属性:
    - HttpOnly:JS 读不到,挡 XSS 偷 token
    - Secure:仅 HTTPS(生产开,dev 关)
    - SameSite=Lax:挡 CSRF;Domain=.ailixiao.com 让主站 + admin 子域共用
    - access path=/(全站需要),refresh path=/api/auth(仅刷新端点用,减少传输面)
    """
    settings = get_settings()
    domain = settings.COOKIE_DOMAIN or None  # 空字符串 → 不设 Domain
    secure = settings.COOKIE_SECURE
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=access_token,
        max_age=ACCESS_COOKIE_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        domain=domain,
        path="/",
    )
    if refresh_token:
        response.set_cookie(
            key=REFRESH_COOKIE_NAME,
            value=refresh_token,
            max_age=REFRESH_COOKIE_MAX_AGE,
            httponly=True,
            secure=secure,
            samesite="lax",
            domain=domain,
            path="/api/auth",  # 减少 refresh cookie 在非鉴权请求里的暴露
        )


def clear_auth_cookies(response: Response) -> None:
    """登出时清两个 cookie。空字符串 + max_age=0 兼容老浏览器"""
    settings = get_settings()
    domain = settings.COOKIE_DOMAIN or None
    response.delete_cookie(ACCESS_COOKIE_NAME, domain=domain, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, domain=domain, path="/api/auth")


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=254)
    password: str = Field(..., min_length=6, max_length=128)
    name: Optional[str] = Field(None, max_length=50)
    # P3-2: 注册必须先 /api/auth/send-code 拿到 6 位邮箱码再附在请求里
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class LoginRequest(BaseModel):
    email: str
    password: str
    totp_code: str = ""


class UpdateNameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6, max_length=128)


class AuthResponse(BaseModel):
    token: str
    user: dict


def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> dict:
    """获取当前登录用户(P8 双轨:cookie 优先,Authorization header 兜底)

    顺序:
    1. cookie access_token(httpOnly,新方式)
    2. Authorization: Bearer <token>(老方式,过渡期保留)
    """
    token: Optional[str] = None

    # 1. 尝试 cookie
    cookie_token = request.cookies.get(ACCESS_COOKIE_NAME)
    if cookie_token:
        token = cookie_token

    # 2. fallback 到 Authorization header
    if not token and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0] == "Bearer":
            token = parts[1]
        else:
            raise HTTPException(status_code=401, detail="Token 格式错误")

    if not token:
        raise HTTPException(status_code=401, detail="未登录")

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
async def register(req: RegisterRequest, request: Request, response: Response):
    """用户注册(P3-2 邮箱码 + P3-3 IP 限流 + BUG-1 失败软配额 + P8 set_cookie)"""
    from app.services.rate_limiter import (
        get_client_ip,
        assert_register_ip_quota,
        assert_register_ip_failure_quota,
        record_register_ip,
        record_register_ip_failure,
    )

    # 1. IP 双重限流(优先级最高,挡批量羊毛党 + 脚本爆破)
    ip = get_client_ip(request)
    assert_register_ip_failure_quota(ip)  # BUG-1: 失败软配额(脚本)
    assert_register_ip_quota(ip)          # P3-3: 成功硬配额(羊毛党)

    # 2. 邮箱码校验(必须在创建用户前,失败永不落库;失败计入 IP 失败配额)
    cache = _EMAIL_CODES.get(req.email)
    if not cache:
        record_register_ip_failure(ip, "no_code")
        raise HTTPException(status_code=400, detail="请先发送邮箱验证码")
    if cache["expires_at"] < _time.time():
        _EMAIL_CODES.pop(req.email, None)
        record_register_ip_failure(ip, "expired_code")
        raise HTTPException(status_code=400, detail="验证码已过期,请重新发送")
    if cache["code"] != req.code:
        record_register_ip_failure(ip, "wrong_code")
        raise HTTPException(status_code=400, detail="验证码错误")
    # 通过 — 立刻作废,防重放
    _EMAIL_CODES.pop(req.email, None)

    # 3. 检查邮箱是否已存在
    existing = get_user_by_email(req.email)
    if existing:
        record_register_ip_failure(ip, "duplicate")
        raise HTTPException(status_code=400, detail="该邮箱已被注册")

    # 4. 创建用户
    user = create_user(req.email, req.password, req.name)
    if not user:
        # 服务端错误也计 — 否则脚本可以利用 server bug 反复打
        record_register_ip_failure(ip, "create_failed")
        raise HTTPException(status_code=500, detail="注册失败")

    # 5. 注册成功 — 记录 IP(为下一次成功配额提供基线)
    record_register_ip(ip)

    # 同时签发 access + refresh
    access = create_access_token(user["id"], user["email"], user["role"])
    refresh = create_refresh_token(user["id"], user["email"], user["role"])

    # P8: 写 httpOnly cookie(双轨,继续返 body 兼容老前端)
    set_auth_cookies(response, access, refresh)

    return {
        "token": access,           # 向后兼容字段
        "access_token": access,
        "refresh_token": refresh,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
            "credits": user["credits"],
        },
    }


@router.post("/login")
async def login(req: LoginRequest, response: Response):
    """用户登录(P8 set_cookie)"""
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

    # 同时签发 access + refresh
    access = create_access_token(user["id"], user["email"], user["role"])
    refresh = create_refresh_token(user["id"], user["email"], user["role"])

    # P8: 写 httpOnly cookie(双轨)
    set_auth_cookies(response, access, refresh)

    return {
        "token": access,           # 向后兼容字段
        "access_token": access,
        "refresh_token": refresh,
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


class RefreshRequest(BaseModel):
    # P8 起 refresh_token 字段可空(改用 cookie);留兼容老前端 body 传 refresh_token
    refresh_token: Optional[str] = None


@router.post("/refresh")
async def refresh_access_token(req: RefreshRequest, request: Request, response: Response):
    """用 refresh token 换新 access token。

    设计(P8 双轨):
    - 优先从 cookie(refresh_token)读;fallback 到 body req.refresh_token
    - 只接受 refresh token(type=refresh),access 不能用于刷新
    - 用户级吊销同样适用(改密码 / 强制下线后,refresh 也失效)
    - 此次实现不轮换 refresh(refresh 仍是原来那个,用到过期为止)
    """
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME) or req.refresh_token
    if not refresh_token:
        raise HTTPException(status_code=401, detail="缺少 refresh_token")

    payload = decode_refresh_token(refresh_token)
    if not payload:
        raise HTTPException(status_code=401, detail="refresh_token 无效或已过期")

    # 取最新用户信息(防止角色 / email 已变更)
    user = get_user_by_id(payload["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")

    new_access = create_access_token(user["id"], user["email"], user["role"])

    # P8: 同时刷新 cookie 让 access 1h 续期到当下;refresh 不轮换不续期
    set_auth_cookies(response, new_access, refresh_token=None)

    return {
        "token": new_access,           # 向后兼容
        "access_token": new_access,
    }


@router.post("/logout")
async def logout(response: Response, current_user: dict = Depends(get_current_user)):
    """登出(本设备):清 cookie。不调用 invalidate_user_tokens(那是 logout-all-devices 干的事)

    P8 新增。前端老的 fetch.delete localStorage 路径继续兼容,但应改用本端点 + 不再读 localStorage。
    """
    clear_auth_cookies(response)
    return {"success": True, "message": "已登出"}


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
async def change_password(req: ChangePasswordRequest, request: Request, current_user: dict = Depends(get_current_user)):
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

    # 改密后吊销该用户所有现有 token(防泄漏密码后旧 token 仍可用)
    invalidate_user_tokens(current_user["id"])

    # 审计:改密码是合规重点(自己改自己,actor = target = user)
    from app.services.audit import log_admin_action, ACTION_CHANGE_PASSWORD
    log_admin_action(
        actor_user_id=current_user["id"],
        actor_email=current_user.get("email"),
        action=ACTION_CHANGE_PASSWORD,
        target_type="user",
        target_id=current_user["id"],
        ip=request.client.host if request.client else None,
    )

    return {"message": "密码已修改,所有设备已自动登出,请重新登录"}


@router.post("/logout-all-devices")
async def logout_all_devices(request: Request, current_user: dict = Depends(get_current_user)):
    """用户主动登出所有设备:把当前账号在所有设备的 token 一次性失效"""
    invalidate_user_tokens(current_user["id"])

    # 审计
    from app.services.audit import log_admin_action, ACTION_LOGOUT_ALL_DEVICES
    log_admin_action(
        actor_user_id=current_user["id"],
        actor_email=current_user.get("email"),
        action=ACTION_LOGOUT_ALL_DEVICES,
        target_type="user",
        target_id=current_user["id"],
        ip=request.client.host if request.client else None,
    )

    return {"message": "已登出所有设备"}


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


# ==================== 邮箱验证码系统 ====================
import os
import random
import time as _time
from datetime import datetime

# 验证码缓存（内存，5 分钟过期）
_EMAIL_CODES: dict = {}  # {email: {code, expires_at, purpose}}


def _send_email_code(email: str, code: str, purpose: str = "verify") -> bool:
    """用 Resend 发送验证码邮件"""
    api_key = os.environ.get("RESEND_API_KEY", "")
    from_email = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")
    if not api_key:
        print(f"[WARN] 未配置 RESEND_API_KEY，验证码打印到控制台: {email} → {code}")
        return True  # 开发模式
    
    try:
        import resend
        resend.api_key = api_key
        
        subject_map = {
            "register": "【AI Lixiao】注册验证码",
            "login": "【AI Lixiao】登录验证码",
            "reset": "【AI Lixiao】密码重置验证码",
            "verify": "【AI Lixiao】邮箱验证码",
        }
        
        html = f"""
<div style="font-family:sans-serif;max-width:500px;margin:40px auto;padding:40px;background:#fff;border-radius:16px;box-shadow:0 4px 20px rgba(0,0,0,0.08);">
  <h2 style="color:#0d0d0d;margin:0 0 20px 0;font-weight:500;">AI Lixiao</h2>
  <p style="color:#666;line-height:1.6;">你的验证码是：</p>
  <div style="font-size:2.5rem;font-weight:700;color:#0d0d0d;letter-spacing:0.5rem;padding:20px;background:#f5f3ed;border-radius:12px;text-align:center;margin:20px 0;">{code}</div>
  <p style="color:#888;font-size:0.9rem;">验证码 5 分钟内有效，请勿泄露。</p>
  <p style="color:#aaa;font-size:0.8rem;margin-top:30px;">如非本人操作，请忽略此邮件。</p>
</div>
"""
        
        params = {
            "from": f"AI Lixiao <{from_email}>",
            "to": [email],
            "subject": subject_map.get(purpose, "验证码"),
            "html": html,
        }
        
        resend.Emails.send(params)
        print(f"[OK] 邮件已发送: {email} / {purpose}")
        return True
    except Exception as e:
        print(f"[ERR] 邮件发送失败: {e}")
        return False


class SendCodeRequest(BaseModel):
    email: str
    purpose: str = "verify"  # register / login / reset


@router.post("/send-code")
async def send_email_code(req: SendCodeRequest):
    """发送邮箱验证码"""
    # 基础校验
    if not req.email or "@" not in req.email:
        raise HTTPException(status_code=400, detail="邮箱格式错误")
    
    # 频率限制：同一邮箱 60 秒内只能发 1 次
    cache = _EMAIL_CODES.get(req.email)
    if cache and cache.get("sent_at", 0) + 60 > _time.time():
        wait = int(cache["sent_at"] + 60 - _time.time())
        raise HTTPException(status_code=429, detail=f"请 {wait} 秒后再试")
    
    # 生成 6 位数字码
    code = str(random.randint(100000, 999999))
    _EMAIL_CODES[req.email] = {
        "code": code,
        "expires_at": _time.time() + 300,  # 5 分钟
        "sent_at": _time.time(),
        "purpose": req.purpose,
    }
    
    # 发送
    if not _send_email_code(req.email, code, req.purpose):
        raise HTTPException(status_code=500, detail="邮件发送失败，请稍后重试")
    
    return {"success": True, "message": "验证码已发送"}


class VerifyCodeLoginRequest(BaseModel):
    email: str
    code: str


@router.post("/login-by-code")
async def login_by_code(req: VerifyCodeLoginRequest):
    """邮箱验证码登录（无密码）。首次登录自动注册。"""
    cache = _EMAIL_CODES.get(req.email)
    if not cache:
        raise HTTPException(status_code=400, detail="请先发送验证码")
    if cache["expires_at"] < _time.time():
        _EMAIL_CODES.pop(req.email, None)
        raise HTTPException(status_code=400, detail="验证码已过期")
    if cache["code"] != req.code:
        raise HTTPException(status_code=400, detail="验证码错误")
    
    # 验证成功，作废验证码
    _EMAIL_CODES.pop(req.email, None)
    
    # 查找或创建用户
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, email, name, role, credits FROM users WHERE email = ?", (req.email,))
        row = cursor.fetchone()
        
        if not row:
            # 自动注册
            import uuid as _uuid
            user_id = str(_uuid.uuid4())
            default_name = req.email.split("@")[0]
            cursor.execute(
                "INSERT INTO users (id, email, name, role, credits, password_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, req.email, default_name, "user", 10, "", datetime.utcnow().isoformat())
            )
            conn.commit()
            user_data = {"id": user_id, "email": req.email, "name": default_name, "role": "user", "credits": 10}
        else:
            user_data = {"id": row[0], "email": row[1], "name": row[2], "role": row[3], "credits": row[4]}
    
    # 同时签发 access + refresh
    access = create_access_token(user_data["id"], user_data["email"], user_data["role"])
    refresh = create_refresh_token(user_data["id"], user_data["email"], user_data["role"])
    return {
        "token": access,           # 向后兼容
        "access_token": access,
        "refresh_token": refresh,
        "user": user_data,
    }


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str


@router.post("/reset-password-by-code")
async def reset_password_by_code(req: ResetPasswordRequest, request: Request):
    """凭验证码重置密码"""
    cache = _EMAIL_CODES.get(req.email)
    if not cache:
        raise HTTPException(status_code=400, detail="请先发送验证码")
    if cache["expires_at"] < _time.time():
        _EMAIL_CODES.pop(req.email, None)
        raise HTTPException(status_code=400, detail="验证码已过期")
    if cache["code"] != req.code:
        raise HTTPException(status_code=400, detail="验证码错误")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    
    _EMAIL_CODES.pop(req.email, None)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (req.email,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="邮箱未注册")
        cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(req.new_password), row[0]))
        conn.commit()

    # 重置密码后吊销该用户所有现有 token
    invalidate_user_tokens(row[0])

    # 审计:邮箱验证码重置密码 — 安全关键事件(actor = target = user 自己,但通过 email 凭证)
    from app.services.audit import log_admin_action, ACTION_RESET_PASSWORD
    log_admin_action(
        actor_user_id=row[0],
        actor_email=req.email,
        action=ACTION_RESET_PASSWORD,
        target_type="user",
        target_id=row[0],
        details={"via": "email_code"},
        ip=request.client.host if request.client else None,
    )

    return {"success": True, "message": "密码已重置,请重新登录"}

