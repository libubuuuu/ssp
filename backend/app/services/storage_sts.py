"""七十二续:对象存储直传 STS 凭证签发(B 方案核心)

设计:
- 用户上传文件不再经服务器中转,直接 PUT 到腾讯云 COS / 阿里云 OSS
- 服务器只调云厂商 STS API 签发**临时凭证**(15 分钟有效),给前端
- 前端用云 SDK(cos-js-sdk-v5)直接上传到 bucket
- 上传完后告诉服务器"我上传到了 https://...",服务器拿 URL 喂 fal API

为什么:
- 当前 A 方案(用户→服务器→fal)受限于服务器出口带宽 32 Mbps + 跨境拥塞
- B 方案后,用户上传走自己上行带宽(50-200 Mbps)+ COS 国内 CDN(GB/s),
  100MB 视频上传时间从 1-3 分钟 → 10-30 秒(预期 3-10 倍提升)

权限隔离:
- STS 临时凭证只能写 `STORAGE_BUCKET_PREFIX/<user_id>/<random_id>` 路径
- 防恶意用户越权写他人文件 / 写 bucket 根目录
- 凭证 15 分钟过期,即使被截获影响有限

未启用前:
- 所有 STS 端点返 503,不影响现有 A 方案中转上传流程
"""
from typing import Optional
import time
import json

from app.config import get_settings


class StorageNotConfigured(Exception):
    """STORAGE_DIRECT_UPLOAD_ENABLED=false 或必要字段缺失"""


def _check_enabled() -> None:
    s = get_settings()
    if not s.STORAGE_DIRECT_UPLOAD_ENABLED:
        raise StorageNotConfigured("STORAGE_DIRECT_UPLOAD_ENABLED=false,功能未启用")
    required = {
        "STORAGE_PROVIDER": s.STORAGE_PROVIDER,
        "STORAGE_BUCKET": s.STORAGE_BUCKET,
        "STORAGE_REGION": s.STORAGE_REGION,
        "STORAGE_SECRET_ID": s.STORAGE_SECRET_ID,
        "STORAGE_SECRET_KEY": s.STORAGE_SECRET_KEY,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise StorageNotConfigured(f"OSS 直传配置缺失: {missing}")
    if s.STORAGE_PROVIDER != "tencent_cos":
        raise StorageNotConfigured(f"暂不支持 STORAGE_PROVIDER={s.STORAGE_PROVIDER}")


def _build_resource_path(user_id: str, filename: str) -> tuple[str, str]:
    """生成对象 key(带用户隔离)+ 完整 cos resource

    返回:
        object_key:`uploads/<user_id>/<timestamp>_<filename>`
        cos_resource:`qcs::cos:<region>:uid/<appid>:<bucket>/uploads/<user_id>/...`
                     (STS 策略中 resource 必须这个完整格式)
    """
    s = get_settings()
    # 文件名安全清洗(防路径穿越 / 防特殊字符破坏 STS policy)
    import re
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)[:100]
    timestamp = int(time.time())
    object_key = f"{s.STORAGE_BUCKET_PREFIX.rstrip('/')}/{user_id}/{timestamp}_{safe_name}"

    # bucket 名形如 ssp-uploads-1300000000(末尾 appid 数字),提取出来给 cos resource 用
    bucket_parts = s.STORAGE_BUCKET.rsplit("-", 1)
    if len(bucket_parts) != 2 or not bucket_parts[1].isdigit():
        raise StorageNotConfigured(f"STORAGE_BUCKET 格式应为 name-appid 形式: {s.STORAGE_BUCKET}")
    bucket_name, appid = bucket_parts

    cos_resource = f"qcs::cos:{s.STORAGE_REGION}:uid/{appid}:{s.STORAGE_BUCKET}/{object_key}"
    return object_key, cos_resource


def issue_sts_credentials(user_id: str, filename: str) -> dict:
    """签发 STS 临时凭证 — 限定只能 PutObject 到 user_id 隔离的 object key

    返回(给前端):
      {
        "credentials": {
          "tmpSecretId": "...",
          "tmpSecretKey": "...",
          "sessionToken": "..."
        },
        "expiredTime": 1730000000,   # unix timestamp
        "bucket": "ssp-uploads-1300000000",
        "region": "ap-guangzhou",
        "object_key": "uploads/<user>/...",  # 前端 PUT 时用这个 key
        "public_url": "https://<bucket>.cos.<region>.myqcloud.com/<key>"
                     # 上传完成后这个 URL 可直接喂 fal API
      }
    """
    _check_enabled()
    s = get_settings()

    object_key, cos_resource = _build_resource_path(user_id, filename)

    # 构造 STS Policy:仅允许 PutObject 到该 object_key
    policy = {
        "version": "2.0",
        "statement": [{
            "effect": "allow",
            "action": [
                "cos:PutObject",
                "cos:PostObject",        # form 上传(浏览器场景)
                "cos:InitiateMultipartUpload",  # 大文件分片
                "cos:ListMultipartUploads",
                "cos:ListParts",
                "cos:UploadPart",
                "cos:CompleteMultipartUpload",
                "cos:AbortMultipartUpload",
            ],
            "resource": [cos_resource],
        }]
    }

    # 调腾讯云 STS API
    from tencentcloud.common import credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.sts.v20180813 import sts_client, models

    cred = credential.Credential(s.STORAGE_SECRET_ID, s.STORAGE_SECRET_KEY)
    http_profile = HttpProfile()
    http_profile.endpoint = "sts.tencentcloudapi.com"
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    client = sts_client.StsClient(cred, s.STORAGE_REGION, client_profile)

    req = models.GetFederationTokenRequest()
    req.Name = f"ssp-upload-{user_id[:16]}"  # 子账号身份名(腾讯云需要,32 字符内)
    req.Policy = json.dumps(policy)
    req.DurationSeconds = s.STORAGE_DURATION_SECONDS

    resp = client.GetFederationToken(req)

    public_url = f"https://{s.STORAGE_BUCKET}.cos.{s.STORAGE_REGION}.myqcloud.com/{object_key}"

    return {
        "credentials": {
            "tmpSecretId": resp.Credentials.TmpSecretId,
            "tmpSecretKey": resp.Credentials.TmpSecretKey,
            "sessionToken": resp.Credentials.Token,
        },
        "expiredTime": resp.ExpiredTime,
        "bucket": s.STORAGE_BUCKET,
        "region": s.STORAGE_REGION,
        "object_key": object_key,
        "public_url": public_url,
    }
