import os
import uuid
from urllib.parse import urlparse
from qcloud_cos import CosConfig, CosS3Client

_URL_VALIDITY = 7 * 24 * 3600  # 7 天，覆盖用户上传后隔天提交的场景


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
    with open(file_path, "rb") as f:
        client.put_object(Bucket=bucket, Body=f, Key=key)
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
