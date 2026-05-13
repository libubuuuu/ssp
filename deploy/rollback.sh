#!/bin/bash
# 回滚脚本 — 启动待命 slot（旧代码），切换 nginx，关闭有问题的 active
# 待命 slot 的代码从未被覆盖，无需 archive 恢复

set -e

LOG="/var/log/rollback.log"

echo "========================================" | tee -a $LOG
echo "回滚开始: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a $LOG

# ── 1. 确定当前激活/待命 ─────────────────────────────────────────────
CURRENT=$(grep -oP 'proxy_pass http://127.0.0.1:\K[0-9]+' /etc/nginx/sites-enabled/default | head -1)
if [ "$CURRENT" = "8000" ]; then
    ACTIVE="blue";  FALLBACK="green"
    ACTIVE_BACKEND=8000; ACTIVE_FRONTEND=3000
    FALLBACK_BACKEND=8001; FALLBACK_FRONTEND=3002
else
    ACTIVE="green"; FALLBACK="blue"
    ACTIVE_BACKEND=8001; ACTIVE_FRONTEND=3002
    FALLBACK_BACKEND=8000; FALLBACK_FRONTEND=3000
fi

FALLBACK_DIR=/opt/ssp-${FALLBACK}
echo "当前激活：$ACTIVE → 回滚到：$FALLBACK（$FALLBACK_DIR）" | tee -a $LOG

# ── 2. 启动 fallback slot（旧代码）──────────────────────────────────
echo "启动 $FALLBACK 服务..." | tee -a $LOG
supervisorctl start ssp-backend-$FALLBACK  2>&1 | tee -a $LOG
supervisorctl start ssp-frontend-$FALLBACK 2>&1 | tee -a $LOG

echo "健康检查（等 15 秒）..." | tee -a $LOG
sleep 15

# ── 3. 健康检查 ─────────────────────────────────────────────────────
BACKEND=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$FALLBACK_BACKEND/api/payment/packages)
FRONTEND=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$FALLBACK_FRONTEND)
if [ "$BACKEND" != "200" ] || [ "$FRONTEND" != "200" ]; then
    echo "❌ $FALLBACK 启动失败！backend=$BACKEND frontend=$FRONTEND" | tee -a $LOG
    supervisorctl stop ssp-backend-$FALLBACK ssp-frontend-$FALLBACK 2>/dev/null || true
    echo "   保持 $ACTIVE 在线，请手动检查日志：/var/log/ssp-*" | tee -a $LOG
    exit 1
fi
echo "✅ $FALLBACK 健康检查通过" | tee -a $LOG

# ── 4. nginx 切换 ────────────────────────────────────────────────────
sed -i "s|proxy_pass http://127.0.0.1:$ACTIVE_BACKEND;|proxy_pass http://127.0.0.1:$FALLBACK_BACKEND;|g" /etc/nginx/sites-enabled/default
sed -i "s|proxy_pass http://127.0.0.1:$ACTIVE_FRONTEND;|proxy_pass http://127.0.0.1:$FALLBACK_FRONTEND;|g" /etc/nginx/sites-enabled/default
nginx -t && nginx -s reload
echo "✅ 流量已切换到 $FALLBACK" | tee -a $LOG

sleep 5

# ── 5. 关闭有问题的 active ────────────────────────────────────────────
supervisorctl stop ssp-backend-$ACTIVE ssp-frontend-$ACTIVE 2>&1 | tee -a $LOG

echo "" | tee -a $LOG
echo "🎉 回滚完成！" | tee -a $LOG
echo "   激活：$FALLBACK（旧代码 → $FALLBACK_DIR）" | tee -a $LOG
echo "   待命：$ACTIVE （已停止）" | tee -a $LOG
echo "========================================" | tee -a $LOG
