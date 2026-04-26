#!/bin/bash
# 零停机部署脚本 - Blue-Green 切换
# 使用：sudo bash /root/deploy.sh

set -e  # 任何命令失败都停止

LOG="/var/log/deploy.log"
echo "========================================" | tee -a $LOG
echo "部署开始: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a $LOG
echo "========================================" | tee -a $LOG

# 1. 确定当前哪个是激活状态（看 nginx 反代哪个端口）
CURRENT=$(grep -oP 'proxy_pass http://127.0.0.1:\K[0-9]+' /etc/nginx/sites-enabled/default | head -1)

if [ "$CURRENT" = "8000" ]; then
    ACTIVE="blue"
    STANDBY="green"
    STANDBY_BACKEND=8001
    STANDBY_FRONTEND=3002
else
    ACTIVE="green"
    STANDBY="blue"
    STANDBY_BACKEND=8000
    STANDBY_FRONTEND=3000
fi

echo "当前激活：$ACTIVE  待命：$STANDBY" | tee -a $LOG

# 2. 提示用户
echo "" | tee -a $LOG
echo "📋 部署步骤："
echo "  [1/5] Git pull 拉取最新代码"
echo "  [2/5] 启动 $STANDBY 环境"
echo "  [3/5] 等待健康检查"
echo "  [4/5] nginx 切换流量到 $STANDBY"
echo "  [5/5] 关闭 $ACTIVE（回滚备用）"
echo "" | tee -a $LOG

# 3. 前端 build（如果改了前端）
if [ "$1" = "frontend" ] || [ "$1" = "all" ]; then
    echo "[前端] 构建..." | tee -a $LOG
    cd /opt/ssp/frontend
    rm -rf .next
    npm run build 2>&1 | tail -3 | tee -a $LOG
fi

# 4. 启动 standby 服务
echo "[2/5] 启动 ssp-backend-$STANDBY 和 ssp-frontend-$STANDBY" | tee -a $LOG
supervisorctl start ssp-backend-$STANDBY 2>&1 | tee -a $LOG
supervisorctl start ssp-frontend-$STANDBY 2>&1 | tee -a $LOG

# 5. 等 standby 启动（健康检查）
echo "[3/5] 健康检查（等 15 秒）..." | tee -a $LOG
sleep 15

for i in 1 2 3; do
    BACKEND_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$STANDBY_BACKEND/api/payment/packages)
    FRONTEND_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$STANDBY_FRONTEND)
    
    if [ "$BACKEND_STATUS" = "200" ] && [ "$FRONTEND_STATUS" = "200" ]; then
        echo "✅ $STANDBY 健康检查通过 (backend=$BACKEND_STATUS, frontend=$FRONTEND_STATUS)" | tee -a $LOG
        break
    fi
    
    if [ $i -eq 3 ]; then
        echo "❌ $STANDBY 启动失败！自动回滚。" | tee -a $LOG
        echo "   backend=$BACKEND_STATUS, frontend=$FRONTEND_STATUS" | tee -a $LOG
        supervisorctl stop ssp-backend-$STANDBY ssp-frontend-$STANDBY
        exit 1
    fi
    
    echo "⏳ 第 $i 次检查... backend=$BACKEND_STATUS, frontend=$FRONTEND_STATUS" | tee -a $LOG
    sleep 5
done

# 6. nginx 切换流量
echo "[4/5] nginx 切换流量到 $STANDBY" | tee -a $LOG
# 同时切换 backend 和 frontend 端口
CURRENT_FRONTEND=$((CURRENT == 8000 ? 3000 : 3002))
sed -i "s|proxy_pass http://127.0.0.1:$CURRENT|proxy_pass http://127.0.0.1:$STANDBY_BACKEND|g" /etc/nginx/sites-enabled/default
sed -i "s|proxy_pass http://127.0.0.1:$CURRENT_FRONTEND|proxy_pass http://127.0.0.1:$STANDBY_FRONTEND|g" /etc/nginx/sites-enabled/default
nginx -t 2>&1 | tee -a $LOG
nginx -s reload
echo "✅ 流量已切换" | tee -a $LOG

# 7. 等 10 秒，确保无请求在老的
sleep 10

# 8. 关闭 active（保留作为下次的 standby）
echo "[5/5] 关闭 $ACTIVE（回滚备用）" | tee -a $LOG
supervisorctl stop ssp-backend-$ACTIVE ssp-frontend-$ACTIVE 2>&1 | tee -a $LOG

echo "" | tee -a $LOG
echo "🎉 部署成功！" | tee -a $LOG
echo "   激活：$STANDBY" | tee -a $LOG
echo "   待命：$ACTIVE" | tee -a $LOG
echo "   如需回滚：bash /root/rollback.sh" | tee -a $LOG
echo "========================================" | tee -a $LOG
