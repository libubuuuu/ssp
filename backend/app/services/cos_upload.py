import logging
import os
import uuid
from urllib.parse import urlparse
from fastapi import HTTPException
from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosServiceError

logger = logging.getLogger("ai_platform")

_URL_VALIDITY = 7 * 24 * 3600  # 7 天，覆盖用户上传后隔天提交的场景


def _raise_cos_unavailable(e: CosServiceError):
    """把腾讯云 COS 的原始 code/message 透传给前端,返回干净 503 而非裸 500。

    为什么:账户欠费时 COS 返 UnavailableForLegalReasons,旧代码直接 500 +
    暴露内部 traceback 路径(2026-06-24 已踩,全站上传瘫痪)。这里保留上游
    原始错误,但给用户一句可读提示,不脑补不外推。
    """
    code = getattr(e, "get_error_code", lambda: "")() or ""
    msg = getattr(e, "get_error_msg", lambda: "")() or str(e)
    logger.error("COS put_object 失败 | code=%s | msg=%s", code, msg)
    detail = f"存储服务暂时不可用（上游 {code}），请稍后重试" if code else "存储服务暂时不可用，请稍后重试"
    raise HTTPException(status_code=503, detail=detail)


def _make_client():
    config = CosConfig(
        Region=os.environ.get("STORAGE_REGION", "ap-guangzhou"),
        SecretId=os.environ.get("STORAGE_SECRET_ID", ""),
        SecretKey=os.environ.get("STORAGE_SECRET_KEY", ""),
    )
    return CosS3Client(config), os.environ.get("STORAGE_BUCKET", "")


def upload_to_cos(file_path: str) -> str:
    client, bucket = _make_client()
    ext = os.path.splitext(file_path)[1]
    key = f"uploads/{uuid.uuid4().hex}{ext}"
    try:
        with open(file_path, "rb") as f:
            client.put_object(Bucket=bucket, Body=f, Key=key)
    except CosServiceError as e:
        _raise_cos_unavailable(e)
    return client.get_presigned_url(Method='GET', Bucket=bucket, Key=key, Expired=_URL_VALIDITY)


def regenerate_cos_url(url: str) -> str:
    """对已存储的 COS 预签名 URL 重新签名，返回新的 7 天有效链接。
    只处理 *.cos.*.myqcloud.com 的 URL，其他原样返回。"""
    parsed = urlparse(url)
    if "myqcloud.com" not in (parsed.hostname or ""):
        return url
    key = parsed.path.lstrip("/")
    if not key:
        return url
    client, bucket = _make_client()
    return client.get_presigned_url(Method='GET', Bucket=bucket, Key=key, Expired=_URL_VALIDITY)
