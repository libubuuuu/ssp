#!/bin/bash
# P221 A2 deploy — 含 db 备份 + 失败回滚 + 先后端再前端 + cron 自动安装
# 用法:bash /root/ssp/deploy/p221-a2-deploy.sh
# ⚠️ 不擅自跑,等用户显式授权才执行

set -e
LOG="/var/log/p221-a2-deploy.log"
TS=$(date +%Y%m%d-%H%M%S)
ARCHIVE_DIR="/root/.p221-a2-prod-archive-${TS}"

log() { echo "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG"; }

log "════════════════════════════════════════════════"
log "P221 A2 deploy 开始 ts=${TS}"
log "════════════════════════════════════════════════"

# ──────────────────────────────────────────────────
# 0. 前置检查
# ──────────────────────────────────────────────────
log ""
log "[0/9] 前置检查"

# 0.1 当前激活的 backend port + frontend port
CURRENT_BACKEND=$(grep -oP 'proxy_pass http://127.0.0.1:\K[0-9]+' /etc/nginx/sites-enabled/default | grep -E "^(8000|8001)$" | head -1)
CURRENT_FRONTEND=$(grep -oP 'proxy_pass http://127.0.0.1:\K[0-9]+' /etc/nginx/sites-enabled/default | grep -E "^(3000|3002)$" | head -1)
log "  nginx → backend port: $CURRENT_BACKEND, frontend port: $CURRENT_FRONTEND"

if [ "$CURRENT_BACKEND" = "8001" ]; then
    ACTIVE_BACKEND="ssp-backend-green"
elif [ "$CURRENT_BACKEND" = "8000" ]; then
    ACTIVE_BACKEND="ssp-backend-blue"
else
    log "  ❌ nginx backend port 异常,中止"; exit 1
fi

if [ "$CURRENT_FRONTEND" = "3002" ]; then
    ACTIVE_FRONTEND="ssp-frontend-green"
elif [ "$CURRENT_FRONTEND" = "3000" ]; then
    ACTIVE_FRONTEND="ssp-frontend-blue"
else
    log "  ❌ nginx frontend port 异常,中止"; exit 1
fi
log "  active: backend=$ACTIVE_BACKEND, frontend=$ACTIVE_FRONTEND"

# 0.2 V2 flag 状态(2026-05-10 起仅 warning 不 abort)
# 2026-05-10 修:V2 已正式上线(commit a733e50 起 prod 跑 V2),这条 abort 检查过时
# 改成 warning 不 abort,保留 V2 flag 状态可视(deploy 后能查 prod V2 是否 active)
# 何时该恢复 abort:如果未来再有 V3 / V4 灰度上线流程,可复用本检查模板,
# 把 ENABLE_VIDEO_CLONE_V2 换成对应 flag,abort 防擅自上线
ACTIVE_PID=$(supervisorctl pid "$ACTIVE_BACKEND")
V2_FLAG=$(cat "/proc/$ACTIVE_PID/environ" 2>/dev/null | tr '\0' '\n' | grep -E "^ENABLE_VIDEO_CLONE_V2=" || echo "")
if [ -n "$V2_FLAG" ] && [ "$V2_FLAG" != "ENABLE_VIDEO_CLONE_V2=false" ]; then
    log "  ⚠️  V2 flag = $V2_FLAG(V2 已上线,跳过 deploy 前必须 false 检查)"
else
    log "  V2 flag: ${V2_FLAG:-(未设置=默认 false)}"
fi

# 0.3 running session 检查
JOBS_RUNNING=$(python3 -c "
import json
try:
    d = json.load(open('/opt/ssp/jobs_data/jobs.json'))
    print(sum(1 for j in d.values() if j.get('status') in ('running','pending')))
except Exception:
    print(0)
")
log "  jobs.json running/pending: $JOBS_RUNNING"
if [ "$JOBS_RUNNING" -gt 0 ]; then
    log "  ❌ 有 $JOBS_RUNNING 个用户任务在跑,deploy 会杀进程,中止"
    exit 1
fi

# ──────────────────────────────────────────────────
# 1. 备份(prod 当前状态完整快照,失败可回滚)
# ──────────────────────────────────────────────────
log ""
log "[1/9] 备份 prod 当前状态到 $ARCHIVE_DIR"
mkdir -p "$ARCHIVE_DIR"

cp /opt/ssp/backend/dev.db "$ARCHIVE_DIR/dev.db.before-A2"
cp /opt/ssp/backend/dev.db-shm "$ARCHIVE_DIR/" 2>/dev/null || true
cp /opt/ssp/backend/dev.db-wal "$ARCHIVE_DIR/" 2>/dev/null || true
log "  ✅ dev.db 已备份到 $ARCHIVE_DIR/dev.db.before-A2"

rsync -a --delete \
  --exclude='venv/' --exclude='.env' --exclude='.env.enc.bak.*' \
  --exclude='dev.db*' --exclude='ssp.db' \
  --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='app/logs/' --exclude='uvicorn.log' \
  --exclude='.pytest_cache/' --exclude='.ruff_cache/' \
  /opt/ssp/backend/ "$ARCHIVE_DIR/backend-before-A2/"
log "  ✅ prod backend 代码 archive 到 $ARCHIVE_DIR/backend-before-A2/"

cp /opt/ssp/backend/.env.enc "$ARCHIVE_DIR/.env.enc.before-A2"
log "  ✅ .env.enc 已备份"

# ──────────────────────────────────────────────────
# 2. backend rsync(/root/ssp → /opt/ssp)— 先后端
# ──────────────────────────────────────────────────
log ""
log "[2/9] rsync /root/ssp/backend → /opt/ssp/backend"
rsync -av --delete \
  --exclude='venv/' --exclude='.env' --exclude='.env.enc' --exclude='.env.enc.bak.*' \
  --exclude='dev.db' --exclude='dev.db-shm' --exclude='dev.db-wal' --exclude='dev.db.bak*' \
  --exclude='ssp.db' \
  --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='app/logs/' --exclude='uvicorn.log' \
  --exclude='.pytest_cache/' --exclude='.ruff_cache/' \
  /root/ssp/backend/ /opt/ssp/backend/ 2>&1 | tail -5 | tee -a "$LOG"

# 2.1 backend chown
chown -R ssp-app:ssp-app /opt/ssp/backend
log "  ✅ backend owner = ssp-app"

# ──────────────────────────────────────────────────
# 3. 重启 backend(让 init_db 跑 _patch_video_clone_v2_columns)
# ──────────────────────────────────────────────────
log ""
log "[3/9] supervisorctl restart $ACTIVE_BACKEND"
supervisorctl restart "$ACTIVE_BACKEND" 2>&1 | tee -a "$LOG"
sleep 8

# ──────────────────────────────────────────────────
# 4. 后端健康检查(挂了立刻回滚,不动前端)
# ──────────────────────────────────────────────────
log ""
log "[4/9] 后端健康检查"

STATUS=$(supervisorctl status "$ACTIVE_BACKEND" | awk '{print $2}')
if [ "$STATUS" != "RUNNING" ]; then
    log "  ❌ $ACTIVE_BACKEND 状态:$STATUS"
    log "  → 触发回滚"
    bash "$0".rollback "$ARCHIVE_DIR"
    exit 1
fi
log "  ✅ $ACTIVE_BACKEND RUNNING"

HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$CURRENT_BACKEND/health")
if [ "$HEALTH" != "200" ]; then
    log "  ❌ backend /health = $HEALTH"
    log "  → 触发回滚"
    bash "$0".rollback "$ARCHIVE_DIR"
    exit 1
fi
log "  ✅ /health = 200"

NEW_PID=$(supervisorctl pid "$ACTIVE_BACKEND")
NEW_FLAG=$(cat "/proc/$NEW_PID/environ" 2>/dev/null | tr '\0' '\n' | grep -E "^ENABLE_VIDEO_CLONE_V2=" || echo "")
if [ -n "$NEW_FLAG" ] && [ "$NEW_FLAG" != "ENABLE_VIDEO_CLONE_V2=false" ]; then
    log "  ❌ V2 flag 不是 false:$NEW_FLAG"
    log "  → 触发回滚"
    bash "$0".rollback "$ARCHIVE_DIR"
    exit 1
fi
log "  ✅ V2 flag = false"

TRIM_COLS=$(sqlite3 /opt/ssp/backend/dev.db "PRAGMA table_info(video_clone_v2_jobs);" | grep -cE "trim_start|trim_end|trimmed_seconds")
if [ "$TRIM_COLS" != "3" ]; then
    log "  ❌ video_clone_v2_jobs trim 字段数 $TRIM_COLS(应 3)"
    log "  → 触发回滚"
    bash "$0".rollback "$ARCHIVE_DIR"
    exit 1
fi
log "  ✅ db schema:trim 3 字段就位"

V2_ENDPOINTS=$(curl -s "http://127.0.0.1:$CURRENT_BACKEND/openapi.json" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(1 for p in d.get('paths',{}) if '/video/clone-v2' in p))" 2>/dev/null || echo 0)
log "  V2 端点数 = $V2_ENDPOINTS(预期 10)"
if [ "$V2_ENDPOINTS" -lt 10 ]; then
    log "  ⚠️ V2 端点数不足 10,但灰度未开,先继续"
fi

# ──────────────────────────────────────────────────
# 5. frontend rsync(后端 OK 才动前端,防版本错位)
# ──────────────────────────────────────────────────
log ""
log "[5/9] rsync /root/ssp/frontend → /opt/ssp/frontend"
# 2026-05-10 修:支持 prod 端 npm build 后 deploy。
# 原设计假设 prod 不 build,.next 离线传,但实际工作流是 prod 端 root 用户 npm build → rsync 到 /opt。
# 现在 .next 带过去,/opt 跑的是 /root build 出来的最新版本。
rsync -av --delete \
  --exclude='node_modules/' \
  /root/ssp/frontend/ /opt/ssp/frontend/ 2>&1 | tail -3 | tee -a "$LOG"

chown -R ssp-app:ssp-app /opt/ssp/frontend
log "  ✅ frontend owner = ssp-app"

# ──────────────────────────────────────────────────
# 6. 重启 frontend
# ──────────────────────────────────────────────────
log ""
log "[6/9] supervisorctl restart $ACTIVE_FRONTEND"
supervisorctl restart "$ACTIVE_FRONTEND" 2>&1 | tee -a "$LOG"
sleep 8

# ──────────────────────────────────────────────────
# 7. 前端健康检查
# ──────────────────────────────────────────────────
log ""
log "[7/9] 前端健康检查"

FE_STATUS=$(supervisorctl status "$ACTIVE_FRONTEND" | awk '{print $2}')
if [ "$FE_STATUS" != "RUNNING" ]; then
    log "  ❌ $ACTIVE_FRONTEND 状态:$FE_STATUS"
    log "  → 触发回滚"
    bash "$0".rollback "$ARCHIVE_DIR"
    exit 1
fi
log "  ✅ $ACTIVE_FRONTEND RUNNING"

FE_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$CURRENT_FRONTEND/")
if [ "$FE_HEALTH" != "200" ] && [ "$FE_HEALTH" != "307" ] && [ "$FE_HEALTH" != "308" ]; then
    log "  ❌ frontend / = $FE_HEALTH"
    log "  → 触发回滚"
    bash "$0".rollback "$ARCHIVE_DIR"
    exit 1
fi
log "  ✅ frontend / = $FE_HEALTH"

# ──────────────────────────────────────────────────
# 8. v2-cache-gc cron 自动安装(防 /tmp 爆)
# ──────────────────────────────────────────────────
log ""
log "[8/9] v2-cache-gc cron 安装(ssp-app crontab)"

CRON_LINE="*/30 * * * * /usr/bin/bash /opt/ssp/deploy/v2-cache-gc.sh"

# 8.1 预创建日志文件 owned by ssp-app(ssp-app 没法 touch /var/log/)
LOG_FILE=/var/log/ssp-v2-cache-gc.log
if [ ! -f "$LOG_FILE" ]; then
    touch "$LOG_FILE"
fi
chown ssp-app:ssp-app "$LOG_FILE"
chmod 644 "$LOG_FILE"
log "  ✅ $LOG_FILE owner=ssp-app"

# 8.2 装到 ssp-app crontab(不是 root!)
# 理由:/tmp/v2_cache 是 ssp-app 写的;ssp-app 没 sudo 权限,所以脚本里也不再 sudo
EXISTING=$(sudo -u ssp-app crontab -l 2>/dev/null || true)
if echo "$EXISTING" | grep -qF "/opt/ssp/deploy/v2-cache-gc.sh"; then
    log "  ✅ ssp-app cron 已存在,跳过"
else
    {
        echo "$EXISTING"
        echo "$CRON_LINE"
    } | grep -v '^$' | sudo -u ssp-app crontab -
    log "  ✅ cron 装入 ssp-app crontab"
fi

# 8.3 验证装上了
if sudo -u ssp-app crontab -l 2>/dev/null | grep -qF "/opt/ssp/deploy/v2-cache-gc.sh"; then
    log "  ✅ sudo -u ssp-app crontab -l 验证通过"
else
    log "  ❌ ssp-app crontab 验证失败,cron 没装上"
    log "  → 触发回滚"
    bash "$0".rollback "$ARCHIVE_DIR"
    exit 1
fi

# 8.4 立即手跑一次(以 ssp-app 身份)— 不等 30 分钟才发现挂了
log "  → 立即手跑一次 v2-cache-gc.sh(ssp-app 身份)"
sudo -u ssp-app /usr/bin/bash /opt/ssp/deploy/v2-cache-gc.sh
if grep -q "v2-cache-gc done" "$LOG_FILE"; then
    log "  ✅ v2-cache-gc.sh 跑通 → $LOG_FILE"
    log "  尾巴:"
    tail -3 "$LOG_FILE" | sed 's/^/    /' | tee -a "$LOG"
else
    log "  ❌ v2-cache-gc.sh 跑出问题,看 $LOG_FILE"
    log "  ⚠️ 部署继续(GC 不影响主功能,但需人工排查)"
fi

# ──────────────────────────────────────────────────
# 9. 完成
# ──────────────────────────────────────────────────
log ""
log "[9/9] 部署完成"
log "════════════════════════════════════════════════"
log "✅ deploy 成功"
log "  archive 路径(失败回滚用):$ARCHIVE_DIR"
log "  active backend:  $ACTIVE_BACKEND"
log "  active frontend: $ACTIVE_FRONTEND"
log "  V2 flag: false(未上线,等用户授权才开)"
log "  v2-cache-gc cron: 已装(每 30 分钟跑一次)"
log ""
log "回滚命令(如果用户授权后跑出问题):"
log "  bash /root/ssp/deploy/p221-a2-deploy.sh.rollback $ARCHIVE_DIR"
log "════════════════════════════════════════════════"
