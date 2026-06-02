#!/usr/bin/env bash
# 每日数据库备份上传腾讯云 COS —— 永久保留，不删除。
# 上传路径：db-backups/YYYY-MM-DD/backup-YYYYMMDD-HHMM.db
# 用法：bash /root/ssp/deploy/backup_cos.sh

set -euo pipefail

SSP_ROOT="${SSP_ROOT:-/opt/ssp}"
MASTER_KEY="${MASTER_KEY:-/etc/ssp/master.key}"
TS=$(date +%Y%m%d-%H%M)
DATE=$(date +%Y-%m-%d)
LOG_TAG="[backup-cos]"

# 读取 COS 配置
ENV_VARS=$(openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d \
    -in "$SSP_ROOT/backend/.env.enc" -pass file:"$MASTER_KEY" 2>/dev/null)

BUCKET=$(echo "$ENV_VARS" | grep '^STORAGE_BUCKET=' | cut -d= -f2-)
REGION=$(echo "$ENV_VARS" | grep '^STORAGE_REGION=' | cut -d= -f2-)
SECRET_ID=$(echo "$ENV_VARS" | grep '^STORAGE_SECRET_ID=' | cut -d= -f2-)
SECRET_KEY=$(echo "$ENV_VARS" | grep '^STORAGE_SECRET_KEY=' | cut -d= -f2-)

if [[ -z "$BUCKET" || -z "$SECRET_ID" ]]; then
    echo "$LOG_TAG 错误：COS 配置缺失" >&2; exit 1
fi

DB_FILE="$SSP_ROOT/backend/dev.db"
[[ -f "$DB_FILE" ]] || { echo "$LOG_TAG 错误：dev.db 不存在" >&2; exit 1; }

# 打包 db + jobs.json
TMP=$(mktemp /tmp/backup_cos_XXXXXX.tar.gz)
trap "rm -f $TMP" EXIT

TAR_INPUTS=("backend/dev.db" "backend/.env.enc")
[[ -f "$SSP_ROOT/jobs_data/jobs.json" ]] && TAR_INPUTS+=("jobs_data/jobs.json")
tar -czf "$TMP" -C "$SSP_ROOT" "${TAR_INPUTS[@]}"
SIZE=$(du -h "$TMP" | cut -f1)
echo "$LOG_TAG 打包完成：$SIZE"

COS_KEY="db-backups/${DATE}/backup-${TS}.tar.gz"

# 用 Python COS SDK 上传
"$SSP_ROOT/backend/venv/bin/python3" << PYEOF
import sys
from qcloud_cos import CosConfig, CosS3Client

config = CosConfig(
    Region="${REGION}",
    SecretId="${SECRET_ID}",
    SecretKey="${SECRET_KEY}",
)
client = CosS3Client(config)

with open("${TMP}", "rb") as f:
    response = client.put_object(
        Bucket="${BUCKET}",
        Body=f,
        Key="${COS_KEY}",
    )

print(f"上传成功: cos://${BUCKET}/${COS_KEY}  ETag={response.get('ETag','')}")
PYEOF

echo "$LOG_TAG COS 备份完成: db-backups/${DATE}/backup-${TS}.tar.gz"
echo "$LOG_TAG $(date)" >> /var/log/ssp-backup.log
