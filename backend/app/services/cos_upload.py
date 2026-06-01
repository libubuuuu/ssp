import os
import uuid
from qcloud_cos import CosConfig, CosS3Client


def upload_to_cos(file_path: str) -> str:
    secret_id = os.environ.get("STORAGE_SECRET_ID", "")
    secret_key = os.environ.get("STORAGE_SECRET_KEY", "")
    region = os.environ.get("STORAGE_REGION", "ap-guangzhou")
    bucket = os.environ.get("STORAGE_BUCKET", "")
    config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key)
    client = CosS3Client(config)
    ext = os.path.splitext(file_path)[1]
    key = f"uploads/{uuid.uuid4().hex}{ext}"
    with open(file_path, "rb") as f:
        client.put_object(Bucket=bucket, Body=f, Key=key)
    return client.get_presigned_url(Method='GET', Bucket=bucket, Key=key, Expired=86400)
