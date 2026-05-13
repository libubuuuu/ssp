#!/bin/bash
# 零停机部署 — 真蓝绿切换
# 每次只动 standby slot，active slot 代码保持不变随时可 rollback
# 回滚：bash /root/rollback.sh

set -e

LOG="/var/log/deploy.log"

echo "========================================" | tee -a $LOG
echo "部署开始: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a $LOG
echo "========================================" | tee -a $LOG

# ── 0. 确定蓝绿状态 ──────────────────────────────────────────────────
CURRENT=$(grep -oP 'proxy_pass http://127.0.0.1:\K[0-9]+' /etc/nginx/sites-enabled/default | head -1)
if [ "$CURRENT" = "8000" ]; then
    ACTIVE="blue";  STANDBY="green"
    ACTIVE_BACKEND=8000; ACTIVE_FRONTEND=3000
    STANDBY_BACKEND=8001; STANDBY_FRONTEND=3002
else
    ACTIVE="green"; STANDBY="blue"
    ACTIVE_BACKEND=8001; ACTIVE_FRONTEND=3002
    STANDBY_BACKEND=8000; STANDBY_FRONTEND=3000
fi

STANDBY_DIR=/opt/ssp-${STANDBY}
echo "当前激活：$ACTIVE  部署目标：$STANDBY ($STANDBY_DIR)" | tee -a $LOG
echo "" | tee -a $LOG
echo "📋 部署步骤："
echo "  [1/5] rsync 代码 → $STANDBY_DIR"
echo "  [2/5] 构建前端"
echo "  [3/5] 启动 $STANDBY"
echo "  [4/5] nginx 切换流量"
echo "  [5/5] 关闭 $ACTIVE（旧代码保留可 rollback）"
echo ""

# ── 1. rsync 代码到 standby slot（不动 active slot）─────────────────
echo "[1/5] rsync /root/ssp → $STANDBY_DIR" | tee -a $LOG
rsync -a --exclude='*.pyc' --exclude='__pycache__/' \
    /root/ssp/backend/app/ $STANDBY_DIR/backend/app/
rsync -a /root/ssp/frontend/src/    $STANDBY_DIR/frontend/src/
rsync -a /root/ssp/frontend/public/ $STANDBY_DIR/frontend/public/
# 前端 shared 配置文件同步到 /opt/ssp/frontend/（symlinks 自动指向这里）
rsync -a --exclude='node_modules/' --exclude='.next/' --exclude='src/' \
    --exclude='public/' --exclude='*.log' \
    --include='*.json' --include='*.js' --include='*.ts' --include='*.mjs' \
    --include='*.cjs' --exclude='*' \
    /root/ssp/frontend/ /opt/ssp/frontend/
chown -R ssp-app:ssp-app $STANDBY_DIR/backend/app $STANDBY_DIR/frontend/src \
    $STANDBY_DIR/frontend/public /opt/ssp/frontend
echo "✅ rsync 完成" | tee -a $LOG

# ── 2. 前端 build（在 standby slot 里）────────────────────────────────
echo "[2/5] 构建前端（在 $STANDBY_DIR/frontend）..." | tee -a $LOG
cd $STANDBY_DIR/frontend
npm run build 2>&1 | tail -5 | tee -a $LOG
chown -R ssp-app:ssp-app $STANDBY_DIR/frontend/.next
echo "✅ 前端构建完成" | tee -a $LOG

# ── 3. 启动 standby ──────────────────────────────────────────────────
echo "[3/5] 启动 ssp-backend-$STANDBY 和 ssp-frontend-$STANDBY" | tee -a $LOG
supervisorctl start ssp-backend-$STANDBY  2>&1 | tee -a $LOG
supervisorctl start ssp-frontend-$STANDBY 2>&1 | tee -a $LOG

echo "健康检查（等 15 秒）..." | tee -a $LOG
sleep 15

for i in 1 2 3; do
    BACKEND_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$STANDBY_BACKEND/api/payment/packages)
    FRONTEND_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$STANDBY_FRONTEND)
    if [ "$BACKEND_STATUS" = "200" ] && [ "$FRONTEND_STATUS" = "200" ]; then
        echo "✅ $STANDBY 健康检查通过 (backend=$BACKEND_STATUS, frontend=$FRONTEND_STATUS)" | tee -a $LOG
        break
    fi
    if [ $i -eq 3 ]; then
        echo "❌ $STANDBY 启动失败，停止 standby，$ACTIVE 继续在线" | tee -a $LOG
        supervisorctl stop ssp-backend-$STANDBY ssp-frontend-$STANDBY 2>/dev/null || true
        exit 1
    fi
    echo "⏳ 第 $i 次检查... backend=$BACKEND_STATUS, frontend=$FRONTEND_STATUS" | tee -a $LOG
    sleep 5
done

# ── 4. nginx 切换 ────────────────────────────────────────────────────
echo "[4/5] nginx 切换流量到 $STANDBY" | tee -a $LOG
sed -i "s|proxy_pass http://127.0.0.1:$ACTIVE_BACKEND;|proxy_pass http://127.0.0.1:$STANDBY_BACKEND;|g" /etc/nginx/sites-enabled/default
sed -i "s|proxy_pass http://127.0.0.1:$ACTIVE_FRONTEND;|proxy_pass http://127.0.0.1:$STANDBY_FRONTEND;|g" /etc/nginx/sites-enabled/default
nginx -t 2>&1 | tee -a $LOG
nginx -s reload
echo "✅ 流量已切换到 $STANDBY" | tee -a $LOG

sleep 10

# ── 5. 关闭 active（旧代码留在 /opt/ssp-$ACTIVE/ 供 rollback）─────────
echo "[5/5] 关闭 $ACTIVE（旧代码保留在 /opt/ssp-$ACTIVE/）" | tee -a $LOG
supervisorctl stop ssp-backend-$ACTIVE ssp-frontend-$ACTIVE 2>&1 | tee -a $LOG

echo "" | tee -a $LOG
echo "🎉 部署成功！" | tee -a $LOG
echo "   激活：$STANDBY（新代码 → $STANDBY_DIR）" | tee -a $LOG
echo "   待命：$ACTIVE （旧代码 → /opt/ssp-$ACTIVE/ — rollback 随时可用）" | tee -a $LOG
echo "   如需回滚：bash /root/rollback.sh" | tee -a $LOG
echo "========================================" | tee -a $LOG
