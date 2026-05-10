#!/usr/bin/env bash
# P221 V2 视频缓存 GC(Path B)
#
# 跑 backend Python 调用 video_clone_v2_cache.clean_old,
# 默认清 30 分钟前的 /tmp/v2_cache/{sha256}.mp4 文件。
# 输出到 /var/log/ssp-v2-cache-gc.log
#
# 调用关系:upload/video 写缓存,check-duration 读;30 分钟内用户走完
# upload→选档→create 流程,过期文件不再有用 → 清掉省 /tmp 空间。
#
# 安装位置:**ssp-app crontab**(不是 root)
# 理由:/tmp/v2_cache/ 是 ssp-app 写的,清也归 ssp-app;
#       ssp-app 没 sudo 权限,所以脚本内不能再 sudo
set -uo pipefail

LOG=/var/log/ssp-v2-cache-gc.log
SSP_ROOT="${SSP_ROOT:-/opt/ssp}"
MAX_AGE="${VC2_CACHE_MAX_AGE_SECONDS:-1800}"

cd "$SSP_ROOT/backend"
echo "[$(date '+%F %T')] v2-cache-gc start (max_age=${MAX_AGE}s, user=$(whoami))" >> "$LOG"

# 直接以当前身份(应是 ssp-app)跑 Python — cron 装在 ssp-app crontab
"$SSP_ROOT/backend/venv/bin/python" -c "
import os, json
os.environ.setdefault('JWT_SECRET', 'gc-noop')
os.environ.setdefault('FAL_KEY', 'gc-noop')
from app.services.video_clone_v2_cache import clean_old
result = clean_old(max_age_seconds=${MAX_AGE})
print(json.dumps(result, ensure_ascii=False))
" >> "$LOG" 2>&1

echo "[$(date '+%F %T')] v2-cache-gc done" >> "$LOG"
