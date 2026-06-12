"""
支付订单 API
- 套餐购买
- 额度充值（虎皮椒自动支付）
- 订单查询
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from typing import Optional, List, Literal
from ..api.auth import get_current_user
from ..database import get_db
from ..services.logger import log_info, log_error
import uuid
import hashlib
import time as _time
import random
import string

router = APIRouter()


# ── 虎皮椒签名 ────────────────────────────────────────────────────────────
def _hpj_sign(params: dict, secret: str) -> str:
    """按 key 字母排序拼接后直接追加 secret（个人版，无 &appsecret= 前缀），MD5"""
    items = sorted((k, str(v)) for k, v in params.items() if v != "" and v is not None and k != "hash")
    query = "&".join(f"{k}={v}" for k, v in items) + secret
    return hashlib.md5(query.encode()).hexdigest()


async def _hpj_create_order(appid: str, secret: str, order_id: str,
                             total_fee: float, title: str,
                             notify_url: str, return_url: str) -> str:
    """调虎皮椒接口创建支付订单，返回支付页 URL"""
    import httpx
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    params = {
        "version": "1.1",
        "appid": appid,
        "trade_order_id": order_id,
        "total_fee": f"{total_fee:.2f}",
        "title": title,
        "time": str(int(_time.time())),
        "notify_url": notify_url,
        "return_url": return_url,
        "nonce_str": nonce,
    }
    params["hash"] = _hpj_sign(params, secret)
    async with httpx.AsyncClient(timeout=10) as cli:
        r = await cli.post("https://api.xunhupay.com/payment/do.html", data=params)
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"虎皮椒建单失败: {data.get('errmsg', '未知')}")
    return data["url"]


# 套餐配置(2026-06-13 调价:50 积分 = 1 元;credits 含赠送,到账即此数)
PACKAGES = [
    {
        "id": "monthly",
        "name": "基础版",
        "credits": 10000,
        "price": 200.00,
        "discount": "",
        "description": "基础版 10000 积分",
    },
    {
        "id": "quarterly",
        "name": "标准版",
        "credits": 26780,  # 25000 + 送 1780
        "price": 500.00,
        "discount": "9.5 折",
        "description": "标准版 25000 积分 + 送 1780 积分",
    },
    {
        "id": "yearly",
        "name": "高级版",
        "credits": 55000,  # 50000 + 送 5000
        "price": 1000.00,
        "discount": "9 折",
        "description": "高级版 50000 积分 + 送 5000 积分",
    },
]

# 充值包(50 积分 = 1 元)
CREDIT_PACKS = [
    {"id": "small",   "credits": 500,   "price": 10.00},
    {"id": "medium",  "credits": 2500,  "price": 50.00},
    {"id": "large",   "credits": 5000,  "price": 100.00},
    {"id": "xlarge",  "credits": 10000, "price": 200.00},
]


class CreateOrderRequest(BaseModel):
    """创建订单请求"""
    type: str  # "package" | "credit"
    package_id: Optional[str] = None  # 套餐 ID
    credit_pack_id: Optional[str] = None  # 充值包 ID


class OrderResponse(BaseModel):
    """订单响应"""
    id: str
    user_id: str
    amount: int  # 积分数量
    price: float  # 支付金额
    status: str  # pending | paid | failed
    created_at: str
    paid_at: Optional[str] = None


@router.get("/packages")
async def list_packages():
    """获取套餐列表"""
    return {"packages": PACKAGES}


@router.get("/credit-packs")
async def list_credit_packs():
    """获取充值包列表"""
    return {"packs": CREDIT_PACKS}


@router.post("/orders/create")
async def create_order(req: CreateOrderRequest, current_user: dict = Depends(get_current_user)):
    """创建订单"""
    order_id = str(uuid.uuid4())

    if req.type == "package":
        package = next((p for p in PACKAGES if p["id"] == req.package_id), None)
        if not package:
            raise HTTPException(status_code=400, detail="无效的套餐 ID")

        amount = package["credits"]
        price = package["price"]

    elif req.type == "credit":
        pack = next((p for p in CREDIT_PACKS if p["id"] == req.credit_pack_id), None)
        if not pack:
            raise HTTPException(status_code=400, detail="无效的充值包 ID")

        amount = pack["credits"]
        price = pack["price"]

    else:
        raise HTTPException(status_code=400, detail="无效的订单类型")

    # 创建订单记录
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO credit_orders
            (id, user_id, amount, price, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (order_id, current_user["id"], amount, price))
        conn.commit()

    # 调虎皮椒自动支付（package / credit 均走此流程）
    payment_url: str | None = None
    if req.type in ("credit", "package"):
        try:
            from app.config import get_settings
            s = get_settings()
            if s.HUPIJIAO_SECRET:
                title = f"积分充值 {amount} 积分" if req.type == "credit" else f"订阅套餐 {amount} 积分"
                payment_url = await _hpj_create_order(
                    appid=s.HUPIJIAO_APPID,
                    secret=s.HUPIJIAO_SECRET,
                    order_id=order_id,
                    total_fee=price,
                    title=title,
                    notify_url="https://ailixiao.com/api/payment/hupijiao/notify",
                    return_url="https://ailixiao.com/pricing?payment=success",
                )
                log_info(f"虎皮椒建单 order={order_id} fee={price} url={payment_url[:80]}")
        except Exception as e:
            log_error(f"虎皮椒建单失败（降级手动）: {e}")

    return {
        "order_id": order_id,
        "type": req.type,
        "amount": amount,
        "price": price,
        "status": "pending",
        "payment_url": payment_url,
    }


@router.get("/orders/{order_id}")
async def get_order(order_id: str, current_user: dict = Depends(get_current_user)):
    """查询订单状态"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, amount, price, status, created_at, paid_at
            FROM credit_orders WHERE id = ?
        """, (order_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="订单不存在")

        if row[1] != current_user["id"]:
            raise HTTPException(status_code=403, detail="无权访问此订单")

        return {
            "id": row[0],
            "user_id": row[1],
            "amount": row[2],
            "price": row[3],
            "status": row[4],
            "created_at": row[5],
            "paid_at": row[6],
        }


@router.get("/orders")
async def list_orders(current_user: dict = Depends(get_current_user)):
    """获取我的订单列表"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, amount, price, status, created_at, paid_at
            FROM credit_orders WHERE user_id = ?
            ORDER BY created_at DESC LIMIT 50
        """, (current_user["id"],))

        orders = []
        for row in cursor.fetchall():
            orders.append({
                "id": row[0],
                "amount": row[1],
                "price": row[2],
                "status": row[3],
                "created_at": row[4],
                "paid_at": row[5],
            })

        return {"orders": orders}


@router.post("/orders/{order_id}/confirm")
async def admin_confirm_order(order_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    """管理员确认订单入账（手动）"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="无权限，仅管理员可确认订单")
    # 原子操作：UPDATE WHERE status='pending'，rowcount==1 才入账（跟微信回调写法对齐）
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE credit_orders SET status='paid', paid_at=CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending'",
            (order_id,),
        )
        if cursor.rowcount != 1:
            cursor.execute("SELECT status FROM credit_orders WHERE id = ?", (order_id,))
            existing = cursor.fetchone()
            conn.commit()
            if not existing:
                raise HTTPException(status_code=404, detail="订单不存在")
            raise HTTPException(status_code=400, detail=f"订单状态非 pending（当前：{existing[0]}），无法重复入账")
        cursor.execute("SELECT user_id, amount FROM credit_orders WHERE id = ?", (order_id,))
        ord_row = cursor.fetchone()
        conn.commit()

    target_user_id = ord_row[0]
    credits_added = ord_row[1]
    from app.services.billing import add_credits as _add_credits
    _add_credits(target_user_id, credits_added, reason="recharge_manual", ref_id=order_id, module="payment/admin_confirm")

    # 审计:管理员手动入账等于"动钱",合规重点
    from app.services.audit import log_admin_action, ACTION_CONFIRM_ORDER
    log_admin_action(
        actor_user_id=current_user["id"],
        actor_email=current_user.get("email"),
        action=ACTION_CONFIRM_ORDER,
        target_type="order",
        target_id=order_id,
        details={"target_user_id": target_user_id, "credits_added": credits_added},
        ip=request.client.host if request.client else None,
    )

    return {"success": True, "order_id": order_id, "credits_added": credits_added, "user_id": target_user_id}


@router.get("/admin/orders")
async def admin_list_all_orders(
    status: str = "pending",
    current_user: dict = Depends(get_current_user),
):
    """管理员：列出所有订单（支持过滤）"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="无权限")
    with get_db() as conn:
        cursor = conn.cursor()
        if status == "all":
            cursor.execute("""
                SELECT o.id, o.user_id, o.amount, o.price, o.status, o.created_at, o.paid_at,
                       u.email, u.name
                FROM credit_orders o
                LEFT JOIN users u ON o.user_id = u.id
                ORDER BY o.created_at DESC LIMIT 200
            """)
        else:
            cursor.execute("""
                SELECT o.id, o.user_id, o.amount, o.price, o.status, o.created_at, o.paid_at,
                       u.email, u.name
                FROM credit_orders o
                LEFT JOIN users u ON o.user_id = u.id
                WHERE o.status = ?
                ORDER BY o.created_at DESC LIMIT 200
            """, (status,))
        orders = []
        for row in cursor.fetchall():
            orders.append({
                "id": row[0],
                "user_id": row[1],
                "amount": row[2],
                "price": row[3],
                "status": row[4],
                "created_at": row[5],
                "paid_at": row[6],
                "user_email": row[7],
                "user_name": row[8],
            })
        return {"orders": orders}


# ── 虎皮椒异步通知 ────────────────────────────────────────────────────────
@router.post("/hupijiao/notify")
async def hupijiao_notify(request: Request):
    """虎皮椒支付回调（无需鉴权）"""
    from app.config import get_settings
    from app.services.billing import add_credits as _add_credits
    s = get_settings()

    # 支持 form 和 json 两种格式
    try:
        data = dict(await request.form())
    except Exception:
        data = await request.json()

    received_hash = data.pop("hash", "")
    expected_hash = _hpj_sign(dict(data), s.HUPIJIAO_SECRET)
    if received_hash != expected_hash:
        log_error(f"虎皮椒回调签名错误: received={received_hash} expected={expected_hash}")
        return PlainTextResponse("fail")

    # 只处理支付成功（status=OD）
    if data.get("status") != "OD":
        return PlainTextResponse("success")

    order_id = data.get("trade_order_id", "")
    if not order_id:
        return PlainTextResponse("fail")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_id, amount, status, price FROM credit_orders WHERE id = ?", (order_id,))
        row = cursor.fetchone()
        if not row:
            log_error(f"虎皮椒回调：订单不存在 {order_id}")
            return PlainTextResponse("fail")
        if row[3] == "paid":
            return PlainTextResponse("success")  # 幂等

        # 验证实际支付金额和订单金额一致
        paid_fee = float(data.get("total_fee", "0"))
        order_price = float(row[4])
        if abs(paid_fee - order_price) > 0.01:
            log_error(f"虎皮椒回调金额不符: paid={paid_fee} expected={order_price} order={order_id}")
            return PlainTextResponse("fail")

        user_id, credits = row[1], row[2]

        # 同一事务：订单状态 + 用户积分原子更新
        cursor.execute(
            "UPDATE credit_orders SET status='paid', paid_at=CURRENT_TIMESTAMP WHERE id = ?",
            (order_id,)
        )
        cursor.execute(
            "UPDATE users SET credits = credits + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (credits, user_id)
        )
        cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
        new_credits_row = cursor.fetchone()
        new_credits = new_credits_row[0] if new_credits_row else 0
        conn.commit()

    # ledger 在 commit 后写（审计轨迹，非资金操作，允许单独失败）
    from app.services.credits_ledger import record_credits_change
    record_credits_change(
        user_id=user_id, delta=credits, balance_after=new_credits,
        reason="recharge_hupijiao", ref_id=order_id, module="payment/hupijiao",
    )
    log_info(f"虎皮椒回调成功: order={order_id} user={user_id} credits={credits} new_balance={new_credits}")
    return PlainTextResponse("success")
