"""
支付订单 API
- 套餐购买
- 额度充值
- 订单查询
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Optional, List, Literal
from ..api.auth import get_current_user
from ..database import get_db
import uuid

router = APIRouter()


# 套餐配置(2026-05-13 老板锁定:50 积分 = 1 元,套餐价 = credits / 50 × 折扣)
PACKAGES = [
    {
        "id": "monthly",
        "name": "月卡",
        "credits": 12500,
        "price": 199.00,
        "discount": "8 折",
        "description": "每月 12500 积分(≈¥250 等值,8 折回赠)",
    },
    {
        "id": "quarterly",
        "name": "季卡",
        "credits": 35000,
        "price": 499.00,
        "discount": "7 折",
        "description": "每季 35000 积分(≈¥700 等值,7 折回赠)",
    },
    {
        "id": "yearly",
        "name": "年卡",
        "credits": 140000,
        "price": 1699.00,
        "discount": "6 折",
        "description": "每年 140000 积分(≈¥2800 等值,6 折回赠)",
    },
]

# 充值包(2026-05-13:50 积分 = 1 元,大额送积分)
CREDIT_PACKS = [
    {"id": "small",  "credits": 500,  "price": 10.00},   # 10 元 = 500 积分(1:1 兑换)
    {"id": "medium", "credits": 2500, "price": 50.00},   # 50 元 = 2500 积分(1:1 兑换)
    {"id": "large",  "credits": 5250, "price": 100.00},  # 100 元 = 5250 积分(9.5 折,送 250)
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

    return {
        "order_id": order_id,
        "type": req.type,
        "amount": amount,
        "price": price,
        "status": "pending",
        # 实际部署时需要返回支付链接或二维码
        "payment_url": f"/api/payment/pay/{order_id}",
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
