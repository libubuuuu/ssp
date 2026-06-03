"""
aiview.club Open API 图片服务（文生图，新模型「专业版模式」）。

对接要点（见对接文档 v1.1.0）：
- 认证：HMAC-SHA256 签名，4 个请求头 X-App-Key / X-Timestamp / X-Nonce / X-Signature
- StringToSign = METHOD\\nPATH\\nTimestamp\\nNonce\\nSHA256(RequestBody)
- 文生图为异步：POST /open/v1/image/generate 拿 request_id，再 GET /open/v1/image/query/{id} 轮询
- 所有调用必须在服务端发起，AppSecret 绝不进前端 / git
"""
import hashlib
import hmac
import json
import time
import uuid

import httpx

from app.config import get_settings


# 本地科学上网(Clash 等)会用 fake-ip 劫持 aiview.club 的 DNS、并按 SNI 拦截 TLS,
# 导致后端连不上 aiview(国内服务,本不该走代理)。下面:正常直连失败时,用 DoH
# (HTTPS 查公共 DNS,绕过 fake-ip)拿真实 IP,再连 IP + 带 Host 头(SNI 不暴露域名)
# 绕过 SNI 拦截。生产环境无代理时走正常直连(带证书校验),不受影响。
_AIVIEW_REAL_IP: dict = {}   # host -> 真实IP(进程内缓存)


async def _resolve_via_doh(host: str) -> str:
    """用 DoH(DNS over HTTPS)查真实 IP,绕过本地 Clash 的 fake-ip DNS 劫持。"""
    if _AIVIEW_REAL_IP.get(host):
        return _AIVIEW_REAL_IP[host]
    for doh in ("https://223.5.5.5/resolve", "https://1.12.12.12/resolve", "https://120.53.53.53/resolve"):
        try:
            async with httpx.AsyncClient(timeout=6, trust_env=False, verify=False) as c:
                r = await c.get(doh, params={"name": host, "type": "A"},
                                headers={"accept": "application/dns-json"})
            ans = [a["data"] for a in r.json().get("Answer", []) if a.get("type") == 1]
            if ans:
                _AIVIEW_REAL_IP[host] = ans[0]
                return ans[0]
        except Exception:
            continue
    return ""


class AiviewImageService:
    def __init__(self, app_key: str, app_secret: str, base_url: str):
        self.app_key = app_key or ""
        self.app_secret = app_secret or ""
        self.base_url = (base_url or "https://www.aiview.club").rstrip("/")

    def is_available(self) -> bool:
        return bool(self.app_key and self.app_secret)

    def _headers(self, method: str, path: str, body_str: str) -> dict:
        """按文档规则构造签名头。body_str 必须与实际发送的请求体字节完全一致。"""
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        body_hash = hashlib.sha256(body_str.encode("utf-8")).hexdigest()
        string_to_sign = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}"
        signature = hmac.new(
            self.app_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-App-Key": self.app_key,
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": signature,
            "Content-Type": "application/json",
        }

    async def _send(self, method: str, path: str, body_str: str = "") -> httpx.Response:
        """发请求(自带防代理劫持)。先正常直连(带证书校验,生产走这条);
        连不上(本地被 Clash fake-ip/SNI 劫持)→ DoH 拿真实 IP,连 IP + Host 头绕过。
        签名不依赖 Host/IP,所以两条路径用同一份签名头都有效。"""
        headers = self._headers(method, path, body_str)
        content = body_str.encode("utf-8") if body_str else None
        # 1) 正常直连(trust_env=False:aiview 国内,不走系统代理)
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as c:
                return await c.request(method, self.base_url + path, headers=headers, content=content)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            pass  # 本地被劫持 → 走回退
        # 2) 回退:DoH 查真实 IP → 连 IP + Host 头(SNI 用 IP 不暴露域名,绕过 SNI 拦截)
        from urllib.parse import urlparse
        host = urlparse(self.base_url).hostname or ""
        ip = await _resolve_via_doh(host)
        if not ip:
            raise httpx.ConnectError("aiview 连不上:正常直连不通,DoH 也拿不到真实 IP")
        headers["Host"] = host
        async with httpx.AsyncClient(timeout=30, trust_env=False, verify=False) as c:
            return await c.request(method, f"https://{ip}{path}", headers=headers, content=content)

    async def submit(self, prompt: str, ratio: str = None, size: str = "2K",
                     n: int = 1, image_urls=None) -> dict:
        """提交文生图任务。成功返回 {"request_id": ...}，失败返回 {"error": ...}。"""
        path = "/open/v1/image/generate"
        body = {"prompt": prompt, "n": n, "size": size}
        if ratio:
            body["ratio"] = ratio
        if image_urls:
            body["image_urls"] = list(image_urls)[:5]
        # 必须先序列化为字符串，签名与发送用同一份字节，避免哈希不一致
        body_str = json.dumps(body, ensure_ascii=False)
        try:
            resp = await self._send("POST", path, body_str)
            data = resp.json()
        except Exception as e:
            return {"error": f"aiview 提交请求失败: {type(e).__name__}: {str(e)[:200]}"}
        if data.get("code") != 0:
            return {"error": data.get("message") or f"aiview 提交失败 code={data.get('code')}"}
        d = data.get("data") or {}
        rid = d.get("request_id") or data.get("request_id")
        if not rid:
            return {"error": "aiview 未返回 request_id"}
        return {"request_id": rid, "credits_used": d.get("credits_used")}

    async def query(self, request_id: str) -> dict:
        """查询任务结果。返回 {"status": "completed"/"processing"/"failed", ...}。"""
        path = f"/open/v1/image/query/{request_id}"
        try:
            resp = await self._send("GET", path, "")
            data = resp.json()
        except Exception as e:
            return {"status": "processing", "_warn": f"查询异常,稍后重试: {type(e).__name__}: {str(e)[:120]}"}
        if data.get("code") != 0:
            return {"status": "failed", "error": data.get("message") or "查询失败"}
        d = data.get("data") or {}
        status = (d.get("status") or "").upper()
        if status == "COMPLETED":
            urls = d.get("image_urls") or []
            if not urls:
                return {"status": "failed", "error": "已完成但未返回图片"}
            from .logger import log_info as _li
            _li(f"[AIVIEW-IMG] query completed rid={request_id} url={urls[0][:80]}")
            return {"status": "completed", "image_url": urls[0], "image_urls": urls}
        if status in ("FAILED", "ERROR"):
            from .logger import log_info as _li
            _li(f"[AIVIEW-IMG] query failed rid={request_id} msg={d.get('message','')[:80]}")
            return {"status": "failed", "error": d.get("message") or "生成失败"}
        return {"status": "processing"}

    # ─── 图生视频 / 参考视频复刻(异步 提交→轮询)───────────────────────────
    async def submit_video(self, *, reference_video_url: str = None, image_urls=None,
                           image_url: str = None, dynamic_prompt: str = "",
                           input_video_duration: int = None, duration: int = 5,
                           model: str = "seedance-2-0-fast", resolution: str = "480p",
                           ratio: str = "adaptive", seed: int = None,
                           generate_audio: bool = False) -> dict:
        """提交视频任务。成功返回 {"request_id": ...}，失败返回 {"error": ...}。"""
        path = "/open/v1/video/generate"
        body = {
            "model": model,
            "dynamic_prompt": dynamic_prompt or "",
            "resolution": resolution,
            "duration": int(duration),
            "ratio": ratio,
            "generate_audio": generate_audio,
        }
        if reference_video_url:
            body["reference_video_url"] = reference_video_url
            # 传参考视频时必须带 input_video_duration(1~15)
            body["input_video_duration"] = max(1, min(15, int(input_video_duration or duration)))
        if image_urls:
            body["image_urls"] = list(image_urls)[:6]
        elif image_url:
            body["image_url"] = image_url
        if seed is not None:
            body["seed"] = seed
        body_str = json.dumps(body, ensure_ascii=False)
        try:
            resp = await self._send("POST", path, body_str)
            data = resp.json()
        except Exception as e:
            # ConnectError 等 str(e) 常为空 → 带上异常类型,否则错误一片空白没法排查
            _detail = str(e)[:200] or "(无详情,多为代理/DNS劫持/网络不通)"
            return {"error": f"aiview 视频提交失败: {type(e).__name__}: {_detail}"}
        if data.get("code") != 0:
            return {"error": data.get("message") or f"aiview 视频提交失败 code={data.get('code')}"}
        d = data.get("data") or {}
        rid = d.get("request_id") or data.get("request_id")
        if not rid:
            return {"error": "aiview 未返回 request_id"}
        return {"request_id": rid, "credits_used": d.get("credits_used")}

    async def query_video(self, request_id: str) -> dict:
        """查询视频结果。返回 {"status": "completed"/"processing"/"failed", ...}。"""
        path = f"/open/v1/video/query/{request_id}"
        try:
            resp = await self._send("GET", path, "")
            data = resp.json()
        except Exception as e:
            return {"status": "processing", "_warn": f"查询异常,稍后重试: {type(e).__name__}: {str(e)[:120]}"}
        if data.get("code") != 0:
            return {"status": "failed", "error": data.get("message") or "查询失败"}
        d = data.get("data") or {}
        status = (d.get("status") or "").upper()
        if status == "COMPLETED":
            url = d.get("video_url")
            if not url:
                return {"status": "failed", "error": "已完成但未返回视频"}
            return {"status": "completed", "video_url": url, "raw": d}
        if status in ("FAILED", "ERROR"):
            from .logger import log_error as _le
            _le(f"[AIVIEW-VIDEO-FAIL] rid={request_id} raw_data={str(d)[:300]}")
            return {"status": "failed", "error": d.get("message") or d.get("fail_reason") or "生成失败"}
        return {"status": "processing"}


_aiview_image_service: AiviewImageService = None


def init_aiview_service():
    global _aiview_image_service
    s = get_settings()
    _aiview_image_service = AiviewImageService(
        s.AIVIEW_API_KEY, s.AIVIEW_API_SECRET, s.AIVIEW_BASE_URL,
    )


def get_aiview_image_service() -> AiviewImageService:
    global _aiview_image_service
    if _aiview_image_service is None:
        init_aiview_service()
    return _aiview_image_service
