#!/usr/bin/env bash
# uploads GC cron 脚本(隐藏雷 #1)
#
# 跑 backend Python 调用 uploads_gc.clean_old_uploads,
# 输出到 /var/log/ssp-uploads-gc.log
set -uo pipefail

LOG=/var/log/ssp-uploads-gc.log
SSP_ROOT="${SSP_ROOT:-/opt/ssp}"
RETENTION="${SSP_UPLOADS_RETENTION_DAYS:-90}"

cd "$SSP_ROOT/backend"
echo "[$(date '+%F %T')] uploads-gc start (retention=${RETENTION}d)" >> "$LOG"

# 加载加密 .env 拿 master key 路径(部分日志依赖)
# 不加载也行,uploads_gc 不需要任何 secret

# 通过 sudo 切到 ssp-app 跑(uploads/ 是 ssp-app 拥有,root 删的话 audit 会乱)
sudo -u ssp-app -E HOME=/opt/ssp \
    "$SSP_ROOT/backend/venv/bin/python" -c "
import os, json, sys
os.environ.setdefault('JWT_SECRET', 'gc-noop')
os.environ.setdefault('FAL_KEY', 'gc-noop')
os.environ['SSP_UPLOADS_RETENTION_DAYS'] = '${RETENTION}'
from app.services.uploads_gc import clean_old_uploads, disk_usage_pct
result = clean_old_uploads(days=${RETENTION}, dry_run=False)
pct = disk_usage_pct()
print(json.dumps({'gc': result, 'disk_pct': pct}, ensure_ascii=False))
" >> "$LOG" 2>&1

echo "[$(date '+%F %T')] uploads-gc done" >> "$LOG"
