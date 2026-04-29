#!/usr/bin/env bash
# 口播带货 session 60 天 GC(七十七续 P12)
#
# 跑 backend Python 调用 oral_gc.clean_old_oral_sessions,
# 输出到 /var/log/ssp-oral-gc.log
set -uo pipefail

LOG=/var/log/ssp-oral-gc.log
SSP_ROOT="${SSP_ROOT:-/opt/ssp}"
RETENTION="${SSP_ORAL_RETENTION_DAYS:-60}"

cd "$SSP_ROOT/backend"
echo "[$(date '+%F %T')] oral-gc start (retention=${RETENTION}d)" >> "$LOG"

# 切到 ssp-app 跑(uploads/oral/<uid>/<sid>/ 是 ssp-app 拥有,root 删的话 audit 会乱)
sudo -u ssp-app -E HOME=/opt/ssp \
    "$SSP_ROOT/backend/venv/bin/python" -c "
import os, json
os.environ.setdefault('JWT_SECRET', 'gc-noop')
os.environ.setdefault('FAL_KEY', 'gc-noop')
os.environ['SSP_ORAL_RETENTION_DAYS'] = '${RETENTION}'
from app.services.oral_gc import clean_old_oral_sessions
result = clean_old_oral_sessions(days=${RETENTION}, dry_run=False)
print(json.dumps(result, ensure_ascii=False))
" >> "$LOG" 2>&1

echo "[$(date '+%F %T')] oral-gc done" >> "$LOG"
