#!/usr/bin/env bash
# studio _uploading/ GC — 清超 24h 没动的分片上传残留
#
# 场景:用户分片上传过程中关 tab / 网断,_uploading/{user}_{upload_id}/
#   留半成品累积 → 磁盘缓慢被啃。本脚本每 6h 跑一次清掉 stale 目录。
#
# 与 uploads-gc.sh 的区别:
# - uploads-gc 清 /opt/ssp/uploads/(用户成片归档),保留期 90 天
# - 本脚本清 /opt/ssp/studio_workspace/_uploading/(分片上传中转),保留期 24h
set -uo pipefail

LOG=/var/log/ssp-studio-uploading-gc.log
SSP_ROOT="${SSP_ROOT:-/opt/ssp}"
HOURS="${SSP_UPLOADING_RETENTION_HOURS:-24}"

cd "$SSP_ROOT/backend"
echo "[$(date '+%F %T')] studio-uploading-gc start (retention=${HOURS}h)" >> "$LOG"

sudo -u ssp-app -E HOME=/opt/ssp \
    "$SSP_ROOT/backend/venv/bin/python" -c "
import os, json
os.environ.setdefault('JWT_SECRET', 'gc-noop')
os.environ.setdefault('FAL_KEY', 'gc-noop')
from app.api.video_studio import clean_stale_uploads
print(json.dumps(clean_stale_uploads(hours=${HOURS}), ensure_ascii=False))
" >> "$LOG" 2>&1

echo "[$(date '+%F %T')] studio-uploading-gc done" >> "$LOG"
