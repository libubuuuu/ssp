"""七十二续:对象存储直传 API — 给前端签发 STS 临时凭证

端点:
- POST /api/storage/sts  鉴权;签发 15 分钟临时凭证 + object_key

前端流程:
  1. 调 /api/storage/sts {filename: "video.mp4"} 拿凭证
  2. 用 cos-js-sdk-v5 配 sessionToken / tmpSecretId / tmpSecretKey
  3. 直接 PUT 到 bucket(走用户上行带宽 + 国内 CDN,不经服务器)
  4. 上传完调 /api/studio/upload-from-url(或 ad-video 等)告诉服务器 OSS URL
  5. 服务器把 URL 喂 fal API(fal 自己拉取,服务器不再中转)

未启用:STORAGE_DIRECT_UPLOAD_ENABLED=false 时 503,不影响 A 方案中转。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.services import storage_sts
from app.services.storage_sts import StorageNotConfigured
from app.services.logger import log_info, log_error

router = APIRouter()


class STSRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=200,
                          description="原始文件名,服务器会清洗 + 加 timestamp 前缀")


@router.post("/sts")
async def issue_sts(req: STSRequest, current_user: dict = Depends(get_current_user)):
    """签发 STS 临时凭证(限定 user_id 隔离路径,15 分钟有效)"""
    try:
        result = storage_sts.issue_sts_credentials(
            user_id=str(current_user["id"]),
            filename=req.filename,
        )
    except StorageNotConfigured as e:
        raise HTTPException(status_code=503, detail=f"对象存储直传未启用: {e}")
    except Exception as e:
        log_error("STS 签发失败", exc_info=True, user_id=current_user.get("id"), error=str(e))
        raise HTTPException(status_code=502, detail="STS 凭证签发失败,请稍后重试")

    log_info(f"STS 签发: user={current_user['id']} key={result['object_key']}")
    return result


@router.post("/presigned-put")
async def issue_presigned_put(req: STSRequest, current_user: dict = Depends(get_current_user)):
    """八十四续 P5:签发 COS PUT presigned URL,浏览器 zero-deps fetch PUT 直传。

    比 STS + cos-js-sdk-v5 更稳:不依赖任何浏览器 SDK,绕开 turbopack
    polyfill 问题。Presigned URL 内嵌 q-sign 签名,有效期 15 分钟。
    """
    from qcloud_cos import CosConfig, CosS3Client
    from app.config import settings
    from app.services.storage_sts import _check_enabled, _build_resource_path
    try:
        _check_enabled()
    except StorageNotConfigured as e:
        raise HTTPException(503, f"对象存储未启用: {e}")

    user_id = str(current_user["id"])
    object_key, _ = _build_resource_path(user_id, req.filename)

    config = CosConfig(
        Region=settings.STORAGE_REGION,
        SecretId=settings.STORAGE_SECRET_ID,
        SecretKey=settings.STORAGE_SECRET_KEY,
    )
    client = CosS3Client(config)
    try:
        # 签 PUT presigned URL,15min 有效
        url = client.get_presigned_url(
            Method="PUT",
            Bucket=settings.STORAGE_BUCKET,
            Key=object_key,
            Expired=900,
        )
    except Exception as e:
        log_error("presigned PUT 签发失败", exc_info=True, error=str(e))
        raise HTTPException(502, f"presigned URL 签发失败: {str(e)[:200]}")

    log_info(f"presigned PUT 签发: user={user_id} key={object_key}")
    return {
        "upload_url": url,
        "object_key": object_key,
        "bucket": settings.STORAGE_BUCKET,
        "region": settings.STORAGE_REGION,
        "expires_in": 900,
    }
