"""微信支付 V3 API 端点 — 用户拿商户号开 WECHAT_PAY_ENABLED 后即用

3 个端点:
- POST /api/wechat-pay/create/{order_id}  鉴权;基于已建的 credit_orders 起 Native 下单
- GET  /api/wechat-pay/query/{order_id}   鉴权;主动查支付状态(前端轮询备用)
- POST /api/wechat-pay/notify             无鉴权(微信回调);验签 + 解密 + 自动入账

跟现有 payment.py 共生:
- 用户调 /api/payment/orders/create 建订单(已存在)
- 然后调 /api/wechat-pay/create/{order_id} 拿 code_url 渲染二维码
- 微信回调 /api/wechat-pay/notify 自动入账(等价于 admin_confirm_order 路径但全自动)
- 前端轮询 /api/wechat-pay/query/{order_id} 看是否 paid

未启用时:所有端点 503,不影响现有手动入账流程。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from app.api.auth import get_current_user
from app.database import get_db
from app.services import wechat_pay
from app.services.wechat_pay import WeChatPayDisabled
from app.services.audit import log_admin_action, ACTION_CONFIRM_ORDER
from app.services.logger import log_info, log_error

router = APIRouter()


@router.post("/create/{order_id}")
async def create_payment(order_id: str, current_user: dict = Depends(get_current_user)):
    """对已建订单起微信 Native 支付,返回 code_url(前端渲染二维码)"""
    # 校订单归属
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, amount, price, status FROM credit_orders WHERE id = ?",
            (order_id,),
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="订单不存在")
    if row[0] != current_user["id"]:
        raise HTTPException(status_code=403, detail="非本人订单")
    if row[3] != "pending":
        raise HTTPException(status_code=400, detail=f"订单状态非 pending(当前: {row[3]})")

    amount_yuan = float(row[2])
    amount_fen = int(round(amount_yuan * 100))
    description = f"AI Lixiao 充值:{row[1]} 积分"

    try:
        result = await wechat_pay.create_native_order(
            out_trade_no=order_id,
            amount_fen=amount_fen,
            description=description,
            user_id=current_user["id"],
        )
    except WeChatPayDisabled as e:
        raise HTTPException(status_code=503, detail=f"微信支付未启用: {e}")
    except Exception as e:
        log_error("微信下单失败", exc_info=True, order_id=order_id, error=str(e))
        raise HTTPException(status_code=502, detail="微信支付下单失败,请稍后重试")

    return {
        "order_id": order_id,
        "code_url": result.get("code_url"),
        "amount_fen": amount_fen,
    }


@router.get("/query/{order_id}")
async def query_payment(order_id: str, current_user: dict = Depends(get_current_user)):
    """主动查支付状态(前端轮询备用,正常应靠回调)"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, status FROM credit_orders WHERE id = ?", (order_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="订单不存在")
    if row[0] != current_user["id"]:
        raise HTTPException(status_code=403, detail="非本人订单")

    # 本地已 paid → 直接返,不走微信
    if row[1] == "paid":
        return {"order_id": order_id, "trade_state": "SUCCESS", "local_status": "paid"}

    try:
        result = await wechat_pay.query_order(order_id)
    except WeChatPayDisabled as e:
        raise HTTPException(status_code=503, detail=f"微信支付未启用: {e}")
    except Exception as e:
        log_error("微信查询失败", exc_info=True, order_id=order_id, error=str(e))
        raise HTTPException(status_code=502, detail="微信支付查询失败")

    return {
        "order_id": order_id,
        "trade_state": result.get("trade_state"),
        "local_status": row[1],
    }


@router.post("/notify")
async def wechat_notify(request: Request):
    """微信回调通知 — **无鉴权**(微信服务器调,通过签名校验身份)。

    流程:
      1. 读 Wechatpay-Signature / Timestamp / Nonce / Serial 头
      2. 平台公钥验签(防伪造)— 本 stub 验签需平台公钥,未实现完整 cert 拉取
      3. 解密 resource(AESGCM)拿订单详情
      4. trade_state == SUCCESS → 给用户加积分 + 标 paid + 写 audit_log
      5. 返 200 + {"code": "SUCCESS"} 告诉微信"已收到"

    幂等:同一 out_trade_no 多次回调只入账一次(SQL UPDATE WHERE status='pending')。
    """
    try:
        body = (await request.body()).decode("utf-8")
        notification = (await request.json()) if body else {}
    except Exception:
        raise HTTPException(status_code=400, detail="invalid body")

    # TODO 生产环境必须做平台公钥验签:
    # signature = request.headers.get("Wechatpay-Signature")
    # timestamp = request.headers.get("Wechatpay-Timestamp")
    # nonce = request.headers.get("Wechatpay-Nonce")
    # if not wechat_pay.verify_notify_signature(timestamp, nonce, body, signature, WECHAT_PUB_KEY):
    #     raise HTTPException(401, "signature invalid")
    # 本 stub 验签需要先实现平台公钥拉取/缓存,生产用必须补,启用前必加!

    if notification.get("event_type") != "TRANSACTION.SUCCESS":
        # 其他事件(REFUND、CLOSED 等)本 stub 不处理,直接 200 应答防重发
        return {"code": "SUCCESS", "message": "ignored event"}

    resource = notification.get("resource", {})
    try:
        decrypted = wechat_pay.decrypt_notify_resource(
            ciphertext=resource["ciphertext"],
            nonce=resource["nonce"],
            associated_data=resource.get("associated_data", ""),
        )
    except WeChatPayDisabled as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        log_error("微信回调解密失败", exc_info=True, error=str(e))
        raise HTTPException(status_code=400, detail="decrypt failed")

    out_trade_no = decrypted.get("out_trade_no")
    trade_state = decrypted.get("trade_state")
    if trade_state != "SUCCESS":
        return {"code": "SUCCESS", "message": "not success"}

    # 入账(原子:UPDATE WHERE status='pending' rowcount==1 才真加积分,防双回调)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE credit_orders SET status='paid', paid_at=CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending'",
            (out_trade_no,),
        )
        if cursor.rowcount != 1:
            conn.commit()
            log_info(f"微信回调:订单 {out_trade_no} 已是 paid,跳过", order_id=out_trade_no)
            return {"code": "SUCCESS", "message": "already paid"}

        cursor.execute(
            "SELECT user_id, amount FROM credit_orders WHERE id = ?", (out_trade_no,)
        )
        ord_row = cursor.fetchone()
        cursor.execute(
            "UPDATE users SET credits = credits + ? WHERE id = ?",
            (ord_row[1], ord_row[0]),
        )
        conn.commit()

    # 审计日志(系统自动入账,actor=system)
    log_admin_action(
        actor_user_id="system_wechat_pay",
        actor_email="wechat-pay-callback",
        action=ACTION_CONFIRM_ORDER,
        target_type="order",
        target_id=out_trade_no,
        details={"target_user_id": ord_row[0], "credits_added": ord_row[1], "channel": "wechat_native"},
        ip=request.client.host if request.client else None,
    )
    log_info(f"微信支付回调入账:order={out_trade_no} user={ord_row[0]} credits=+{ord_row[1]}")

    return {"code": "SUCCESS", "message": "ok"}
