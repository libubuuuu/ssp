#!/bin/bash
# 回滚脚本 — 从最新 archive 恢复代码，重启当前 active 服务
# 使用：sudo bash /root/rollback.sh
#
# 回滚逻辑：不切换蓝绿，直接把 archive 代码覆写到 /opt/ssp，重启 active 进程。
# archive 由 deploy.sh 在每次 rsync 前自动生成，保留最新 3 份。

set -e

LOG="/var/log/rollback.log"
ARCHIVE_ROOT="/root/.ssp-archives"

echo "========================================" | tee -a $LOG
echo "回滚开始: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a $LOG

# ── 1. 找最新 archive ────────────────────────────────────────────────
LATEST=$(ls -dt "$ARCHIVE_ROOT"/20* 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
    echo "❌ 没有可用的 archive（$ARCHIVE_ROOT 为空）" | tee -a $LOG
    echo "   请用 git revert + bash /root/deploy.sh 手动回滚" | tee -a $LOG
    exit 1
fi
echo "使用 archive：$LATEST" | tee -a $LOG

# ── 2. 确定当前激活服务 ──────────────────────────────────────────────
CURRENT=$(grep -oP 'proxy_pass http://127.0.0.1:\K[0-9]+' /etc/nginx/sites-enabled/default | head -1)
if [ "$CURRENT" = "8000" ]; then
    ACTIVE="blue"; ACTIVE_BACKEND=8000; ACTIVE_FRONTEND=3000
else
    ACTIVE="green"; ACTIVE_BACKEND=8001; ACTIVE_FRONTEND=3002
fi
echo "当前激活：$ACTIVE（nginx → $CURRENT）" | tee -a $LOG

# ── 3. 恢复代码 ─────────────────────────────────────────────────────
echo "恢复代码 from $LATEST ..." | tee -a $LOG
rsync -a "$LATEST/backend/app/"      /opt/ssp/backend/app/
rsync -a "$LATEST/frontend/src/"     /opt/ssp/frontend/src/
rsync -a "$LATEST/frontend/public/"  /opt/ssp/frontend/public/
if [ -d "$LATEST/frontend/.next" ]; then
    rsync -a "$LATEST/frontend/.next/" /opt/ssp/frontend/.next/
fi
chown -R ssp-app:ssp-app /opt/ssp/backend/app /opt/ssp/frontend/src \
    /opt/ssp/frontend/public /opt/ssp/frontend/.next 2>/dev/null || true
echo "✅ 代码已恢复" | tee -a $LOG

# ── 4. 重启 active 服务（nginx 不动，流量不断）───────────────────────
echo "重启 $ACTIVE 服务..." | tee -a $LOG
supervisorctl restart ssp-backend-$ACTIVE  2>&1 | tee -a $LOG
supervisorctl restart ssp-frontend-$ACTIVE 2>&1 | tee -a $LOG

sleep 15

# ── 5. 健康检查 ─────────────────────────────────────────────────────
BACKEND=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$ACTIVE_BACKEND/api/payment/packages)
FRONTEND=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$ACTIVE_FRONTEND)

if [ "$BACKEND" = "200" ] && [ "$FRONTEND" = "200" ]; then
    echo "✅ 回滚成功！$ACTIVE 已用旧版本代码重启" | tee -a $LOG
    echo "   archive：$LATEST" | tee -a $LOG
else
    echo "❌ 回滚后健康检查失败！backend=$BACKEND frontend=$FRONTEND" | tee -a $LOG
    echo "   检查日志：journalctl -u supervisor 或 /var/log/ssp-*" | tee -a $LOG
    exit 1
fi

echo "🎉 回滚完成！" | tee -a $LOG
echo "========================================" | tee -a $LOG
