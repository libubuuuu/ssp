#!/bin/bash
# 零停机部署脚本 - Blue-Green 切换
# 使用：sudo bash /root/deploy.sh
#
# 流程：归档旧版本 → rsync 新代码 → build 前端 → 启动 standby →
#        健康检查 → nginx 切换 → 关闭 active
# 回滚：bash /root/rollback.sh（从最新 archive 恢复）

set -e

LOG="/var/log/deploy.log"
ARCHIVE_ROOT="/root/.ssp-archives"
TIMESTAMP=$(date '+%Y%m%d-%H%M%S')
ARCHIVE_DIR="$ARCHIVE_ROOT/$TIMESTAMP"

echo "========================================" | tee -a $LOG
echo "部署开始: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a $LOG
echo "========================================" | tee -a $LOG

# ── 0. 归档当前 /opt/ssp 代码（rsync 前）─────────────────────────────
echo "[0/6] 归档当前版本 → $ARCHIVE_DIR" | tee -a $LOG
mkdir -p "$ARCHIVE_DIR/backend" "$ARCHIVE_DIR/frontend"
rsync -a /opt/ssp/backend/app/   "$ARCHIVE_DIR/backend/app/"
rsync -a /opt/ssp/frontend/src/  "$ARCHIVE_DIR/frontend/src/"
rsync -a /opt/ssp/frontend/public/ "$ARCHIVE_DIR/frontend/public/"
# .next 存在时才归档（首次部署可能还没 build）
if [ -d /opt/ssp/frontend/.next ]; then
    rsync -a /opt/ssp/frontend/.next/ "$ARCHIVE_DIR/frontend/.next/"
fi
echo "✅ 归档完成 ($ARCHIVE_DIR)" | tee -a $LOG

# 保留最新 3 份，清理旧的
ls -dt "$ARCHIVE_ROOT"/20* 2>/dev/null | tail -n +4 | xargs -r rm -rf
KEPT=$(ls -dt "$ARCHIVE_ROOT"/20* 2>/dev/null | wc -l)
echo "   当前 archive 数量: $KEPT" | tee -a $LOG

# ── 1. rsync 新代码 /root/ssp → /opt/ssp ────────────────────────────
echo "[1/6] rsync /root/ssp → /opt/ssp" | tee -a $LOG
# backend/app 只同步 app/ 目录（不动 venv/dev.db/.env.enc/uploads/jobs_data）
rsync -a --exclude='*.pyc' --exclude='__pycache__/' \
    /root/ssp/backend/app/ /opt/ssp/backend/app/
# frontend 同步 src/ 和 public/（不动 node_modules/.next）
rsync -a /root/ssp/frontend/src/    /opt/ssp/frontend/src/
rsync -a /root/ssp/frontend/public/ /opt/ssp/frontend/public/
# 配置文件（package.json / next.config 等，根目录单文件）
rsync -a --exclude='node_modules/' --exclude='.next/' --exclude='*.log' \
    --include='*.json' --include='*.js' --include='*.ts' --include='*.mjs' \
    --include='*.cjs' --exclude='*' \
    /root/ssp/frontend/ /opt/ssp/frontend/
chown -R ssp-app:ssp-app /opt/ssp/backend/app /opt/ssp/frontend/src \
    /opt/ssp/frontend/public
echo "✅ rsync 完成" | tee -a $LOG

# ── 2. 确定蓝绿状态 ──────────────────────────────────────────────────
CURRENT=$(grep -oP 'proxy_pass http://127.0.0.1:\K[0-9]+' /etc/nginx/sites-enabled/default | head -1)

if [ "$CURRENT" = "8000" ]; then
    ACTIVE="blue"; STANDBY="green"
    STANDBY_BACKEND=8001; STANDBY_FRONTEND=3002
else
    ACTIVE="green"; STANDBY="blue"
    STANDBY_BACKEND=8000; STANDBY_FRONTEND=3000
fi

echo "当前激活：$ACTIVE  待命：$STANDBY" | tee -a $LOG

echo "" | tee -a $LOG
echo "📋 部署步骤："
echo "  [3/6] 构建前端"
echo "  [4/6] 启动 $STANDBY 环境"
echo "  [5/6] nginx 切换流量到 $STANDBY"
echo "  [6/6] 关闭 $ACTIVE（回滚备用）"
echo "" | tee -a $LOG

# ── 3. 前端 build ────────────────────────────────────────────────────
echo "[3/6] 构建前端..." | tee -a $LOG
cd /opt/ssp/frontend
npm run build 2>&1 | tail -5 | tee -a $LOG
chown -R ssp-app:ssp-app /opt/ssp/frontend/.next
echo "✅ 前端构建完成" | tee -a $LOG

# ── 4. 启动 standby ──────────────────────────────────────────────────
echo "[4/6] 启动 ssp-backend-$STANDBY 和 ssp-frontend-$STANDBY" | tee -a $LOG
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
        echo "❌ $STANDBY 启动失败，自动回滚..." | tee -a $LOG
        supervisorctl stop ssp-backend-$STANDBY ssp-frontend-$STANDBY 2>/dev/null || true
        bash /root/rollback.sh
        exit 1
    fi

    echo "⏳ 第 $i 次检查... backend=$BACKEND_STATUS, frontend=$FRONTEND_STATUS" | tee -a $LOG
    sleep 5
done

# ── 5. nginx 切换 ────────────────────────────────────────────────────
echo "[5/6] nginx 切换流量到 $STANDBY" | tee -a $LOG
CURRENT_FRONTEND=$((CURRENT == 8000 ? 3000 : 3002))
sed -i "s|proxy_pass http://127.0.0.1:$CURRENT;|proxy_pass http://127.0.0.1:$STANDBY_BACKEND;|g" /etc/nginx/sites-enabled/default
sed -i "s|proxy_pass http://127.0.0.1:$CURRENT_FRONTEND;|proxy_pass http://127.0.0.1:$STANDBY_FRONTEND;|g" /etc/nginx/sites-enabled/default
nginx -t 2>&1 | tee -a $LOG
nginx -s reload
echo "✅ 流量已切换" | tee -a $LOG

sleep 10

# ── 6. 关闭 active ───────────────────────────────────────────────────
echo "[6/6] 关闭 $ACTIVE（回滚备用）" | tee -a $LOG
supervisorctl stop ssp-backend-$ACTIVE ssp-frontend-$ACTIVE 2>&1 | tee -a $LOG

echo "" | tee -a $LOG
echo "🎉 部署成功！" | tee -a $LOG
echo "   激活：$STANDBY" | tee -a $LOG
echo "   待命：$ACTIVE" | tee -a $LOG
echo "   archive：$ARCHIVE_DIR" | tee -a $LOG
echo "   如需回滚：bash /root/rollback.sh" | tee -a $LOG
echo "========================================" | tee -a $LOG
