#!/bin/bash
# 回滚脚本 - 立刻切回旧版本
# 使用：sudo bash /root/rollback.sh

set -e

LOG="/var/log/rollback.log"
echo "========================================" | tee -a $LOG
echo "回滚开始: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a $LOG

# 1. 找当前激活端口
CURRENT=$(grep -oP 'proxy_pass http://127.0.0.1:\K[0-9]+' /etc/nginx/sites-enabled/default | head -1)

if [ "$CURRENT" = "8000" ]; then
    ACTIVE="blue"
    OLD="green"
    OLD_BACKEND=8001
    OLD_FRONTEND=3002
    CURRENT_FRONTEND=3000
else
    ACTIVE="green"
    OLD="blue"
    OLD_BACKEND=8000
    OLD_FRONTEND=3000
    CURRENT_FRONTEND=3002
fi

echo "当前激活：$ACTIVE，要回滚到：$OLD" | tee -a $LOG

# 2. 启动老版本
echo "启动 $OLD 环境..." | tee -a $LOG
supervisorctl start ssp-backend-$OLD 2>&1 | tee -a $LOG
supervisorctl start ssp-frontend-$OLD 2>&1 | tee -a $LOG

# 3. 等待
sleep 15

# 4. 健康检查
BACKEND=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$OLD_BACKEND/api/payment/packages)
FRONTEND=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$OLD_FRONTEND)

if [ "$BACKEND" != "200" ] || [ "$FRONTEND" != "200" ]; then
    echo "❌ 老版本启动失败！检查日志：/var/log/ssp-*-$OLD.err.log" | tee -a $LOG
    exit 1
fi

# 5. nginx 切换 - backend + frontend 都要切（否则前后端版本错位）
sed -i "s|proxy_pass http://127.0.0.1:$CURRENT|proxy_pass http://127.0.0.1:$OLD_BACKEND|g" /etc/nginx/sites-enabled/default
sed -i "s|proxy_pass http://127.0.0.1:$CURRENT_FRONTEND|proxy_pass http://127.0.0.1:$OLD_FRONTEND|g" /etc/nginx/sites-enabled/default
nginx -t && nginx -s reload
echo "✅ 流量已回滚到 $OLD" | tee -a $LOG

# 6. 关闭有问题的版本
sleep 5
echo "关闭有问题的 $ACTIVE..." | tee -a $LOG
supervisorctl stop ssp-backend-$ACTIVE ssp-frontend-$ACTIVE 2>&1 | tee -a $LOG

echo "🎉 回滚完成！" | tee -a $LOG
echo "========================================" | tee -a $LOG
