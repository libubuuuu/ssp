"""微信支付 V3 客户端 stub — 用户拿商户号后开 WECHAT_PAY_ENABLED=true 即用

接口选型:**Native(扫码)**
- 适合 PC web 场景,统一下单返回 code_url(二维码 URL),前端 qrcode 渲染
- 用户用微信扫码完成支付,异步回调通知商户
- 跟现有 /pricing 流程对接最自然(替代手动转账截图)

V3 核心特征:
- 签名算法:RSA-SHA256(商户私钥签名 + 微信平台公钥验签)
- 回调通知 body 加密:AES-GCM(用 APIv3 密钥)
- HTTPS 必须

Stub 完成度:
- ✅ 统一下单(Native)
- ✅ 回调验签 + 解密
- ✅ 订单查询
- ⏸ 退款(scope 控制,留下次)
- ⏸ 对账下载(scope 控制)

启用 SOP 见 docs/WECHAT-PAY-SETUP.md。
"""
import base64
import hashlib
import json
import time
import uuid
from typing import Optional

import httpx

from app.config import get_settings


WECHAT_PAY_API_BASE = "https://api.mch.weixin.qq.com"


class WeChatPayDisabled(Exception):
    """WeChat Pay 未启用(WECHAT_PAY_ENABLED=false 或必要字段缺失)"""


class WeChatPaySignatureError(Exception):
    """签名 / 验签失败"""


def _check_enabled() -> None:
    s = get_settings()
    if not s.WECHAT_PAY_ENABLED:
        raise WeChatPayDisabled("WECHAT_PAY_ENABLED=false,功能未启用")
    required = {
        "WECHAT_PAY_MCH_ID": s.WECHAT_PAY_MCH_ID,
        "WECHAT_PAY_APP_ID": s.WECHAT_PAY_APP_ID,
        "WECHAT_PAY_API_V3_KEY": s.WECHAT_PAY_API_V3_KEY,
        "WECHAT_PAY_CERT_SERIAL": s.WECHAT_PAY_CERT_SERIAL,
        "WECHAT_PAY_PRIVATE_KEY_PATH": s.WECHAT_PAY_PRIVATE_KEY_PATH,
        "WECHAT_PAY_NOTIFY_URL": s.WECHAT_PAY_NOTIFY_URL,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise WeChatPayDisabled(f"WeChat Pay 配置缺失: {missing}")


def _load_private_key():
    """加载商户 API 私钥 PEM(每次调用都读;实际用可缓存)"""
    from cryptography.hazmat.primitives import serialization
    s = get_settings()
    with open(s.WECHAT_PAY_PRIVATE_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _sign(method: str, url_path: str, body: str = "") -> tuple[str, str]:
    """商户私钥签名,返回 (timestamp, nonce, signature) 用于 Authorization 头。

    V3 签名串:method\nurl_path\ntimestamp\nnonce\nbody\n
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    sign_str = f"{method}\n{url_path}\n{timestamp}\n{nonce}\n{body}\n"

    key = _load_private_key()
    signature = key.sign(sign_str.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return timestamp, nonce, base64.b64encode(signature).decode("ascii")


def _auth_header(method: str, url_path: str, body: str = "") -> str:
    """构造 Authorization 头(WECHATPAY2-SHA256-RSA2048)"""
    s = get_settings()
    timestamp, nonce, signature = _sign(method, url_path, body)
    return (
        f'WECHATPAY2-SHA256-RSA2048 '
        f'mchid="{s.WECHAT_PAY_MCH_ID}",'
        f'nonce_str="{nonce}",'
        f'timestamp="{timestamp}",'
        f'serial_no="{s.WECHAT_PAY_CERT_SERIAL}",'
        f'signature="{signature}"'
    )


async def create_native_order(
    out_trade_no: str,
    amount_fen: int,
    description: str,
    user_id: Optional[str] = None,
) -> dict:
    """统一下单(Native 扫码),返回 {code_url, ...}。

    code_url 是 weixin:// 协议链接,前端用 qrcode 渲染图片让用户扫。

    参数:
        out_trade_no: 商户订单号(本地 credit_orders.id)
        amount_fen: 金额(分)— ¥1.99 → 199
        description: 商品描述,如 "AI Lixiao 月卡 500 积分"
        user_id: 业务侧用户 id(可选,记 attach 字段)
    """
    _check_enabled()
    s = get_settings()
    url_path = "/v3/pay/transactions/native"

    payload = {
        "appid": s.WECHAT_PAY_APP_ID,
        "mchid": s.WECHAT_PAY_MCH_ID,
        "description": description,
        "out_trade_no": out_trade_no,
        "notify_url": s.WECHAT_PAY_NOTIFY_URL,
        "amount": {"total": amount_fen, "currency": "CNY"},
    }
    if user_id:
        payload["attach"] = f"user:{user_id}"

    body = json.dumps(payload, separators=(",", ":"))
    headers = {
        "Authorization": _auth_header("POST", url_path, body),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(WECHAT_PAY_API_BASE + url_path, content=body, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"WeChat Pay 下单失败 {resp.status_code}: {resp.text}")
    return resp.json()


async def query_order(out_trade_no: str) -> dict:
    """查订单状态:trade_state ∈ {NOTPAY, SUCCESS, REFUND, NOTPAY, CLOSED, REVOKED, USERPAYING, PAYERROR}"""
    _check_enabled()
    s = get_settings()
    url_path = f"/v3/pay/transactions/out-trade-no/{out_trade_no}?mchid={s.WECHAT_PAY_MCH_ID}"
    headers = {"Authorization": _auth_header("GET", url_path), "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(WECHAT_PAY_API_BASE + url_path, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"WeChat Pay 查询失败 {resp.status_code}: {resp.text}")
    return resp.json()


def decrypt_notify_resource(ciphertext: str, nonce: str, associated_data: str) -> dict:
    """解密回调通知 resource 字段(AES-GCM,密钥 = APIv3 密钥)。

    回调通知 POST body 形如:
    {
      "id": "...", "create_time": "...", "event_type": "TRANSACTION.SUCCESS",
      "resource_type": "encrypt-resource",
      "resource": {
        "ciphertext": "<base64>",
        "nonce": "<12 字节>",
        "associated_data": "<text>",
        "algorithm": "AEAD_AES_256_GCM"
      }
    }
    解密后返回订单详情 dict(含 trade_state / out_trade_no / amount.total 等)
    """
    _check_enabled()
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    s = get_settings()
    key = s.WECHAT_PAY_API_V3_KEY.encode("utf-8")
    if len(key) != 32:
        raise WeChatPaySignatureError("APIv3 密钥必须 32 字节")

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(
        nonce.encode("utf-8"),
        base64.b64decode(ciphertext),
        associated_data.encode("utf-8"),
    )
    return json.loads(plaintext)


def verify_notify_signature(
    timestamp: str, nonce: str, body: str, signature_b64: str, wechat_pub_key_pem: bytes
) -> bool:
    """验签回调通知:用微信平台公钥验 Wechatpay-Signature 头。

    平台公钥从 https://api.mch.weixin.qq.com/v3/certificates 拉(本 stub 不实现,
    生产用应缓存平台公钥并定期刷新,或通过证书序列号选择)。
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    sign_str = f"{timestamp}\n{nonce}\n{body}\n"
    pub_key = serialization.load_pem_public_key(wechat_pub_key_pem)
    try:
        pub_key.verify(
            base64.b64decode(signature_b64),
            sign_str.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False
