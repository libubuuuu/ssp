"""
支付订单 API
- 套餐购买
- 额度充值
- 订单查询
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Literal
from ..api.auth import get_current_user
from ..database import get_db
import uuid

router = APIRouter()


# 套餐配置
PACKAGES = [
    {
        "id": "monthly",
        "name": "月卡",
        "credits": 500,
        "price": 199.00,
        "discount": "8 折",
        "description": "每月 500 积分，生成享 8 折优惠",
    },
    {
        "id": "quarterly",
        "name": "季卡",
        "credits": 1500,
        "price": 499.00,
        "discount": "7 折",
        "description": "每季 1500 积分，生成享 7 折优惠",
    },
    {
        "id": "yearly",
        "name": "年卡",
        "credits": 6000,
        "price": 1699.00,
        "discount": "6 折",
        "description": "每年 6000 积分，生成享 6 折优惠",
    },
]

# 充值包
CREDIT_PACKS = [
    {"id": "small", "credits": 100, "price": 99.00},
    {"id": "medium", "credits": 500, "price": 399.00},
    {"id": "large", "credits": 2000, "price": 1299.00},
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


@router.post("/orders/{order_id}/pay")
async def pay_order(order_id: str, current_user: dict = Depends(get_current_user)):
    """
    支付订单（模拟）
    实际部署时需要对接支付平台回调
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # 查询订单
        cursor.execute("""
            SELECT user_id, amount, status FROM credit_orders WHERE id = ?
        """, (order_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="订单不存在")

        if row[0] != current_user["id"]:
            raise HTTPException(status_code=403, detail="无权支付此订单")

        if row[2] == "paid":
            raise HTTPException(status_code=400, detail="订单已支付")

        # 更新订单状态
        cursor.execute("""
            UPDATE credit_orders
            SET status = 'paid', paid_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (order_id,))

        # 增加用户额度
        cursor.execute("""
            UPDATE users SET credits = credits + ? WHERE id = ?
        """, (row[1], row[0]))

        conn.commit()

    return {"message": "支付成功", "amount": row[1]}
