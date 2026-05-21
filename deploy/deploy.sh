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
ACTIVE_DIR=/opt/ssp-${ACTIVE}
echo "当前激活：$ACTIVE  部署目标：$STANDBY ($STANDBY_DIR)" | tee -a $LOG

# ── 0.5 双槽 db symlink 强制校验（防止孤立数据库导致用户积分丢失）────
REAL_DB="/opt/ssp/backend/dev.db"
for SLOT_LABEL in "$ACTIVE:$ACTIVE_DIR" "$STANDBY:$STANDBY_DIR"; do
    LABEL="${SLOT_LABEL%%:*}"
    DIR="${SLOT_LABEL##*:}"
    SLOT_DB="$DIR/backend/dev.db"
    if [ ! -L "$SLOT_DB" ] || [ "$(readlink "$SLOT_DB")" != "$REAL_DB" ]; then
        echo "⚠️  [$LABEL] db 不是正确 symlink，立即修正: $SLOT_DB → $REAL_DB" | tee -a $LOG
        rm -f "$SLOT_DB"
        ln -s "$REAL_DB" "$SLOT_DB"
        chown -h ssp-app:ssp-app "$SLOT_DB"
    fi
done
echo "✅ 双槽 db symlink 验证通过" | tee -a $LOG
echo "" | tee -a $LOG
echo "📋 部署步骤："
echo "  [0/5] 预部署测试（核心路径）"
echo "  [1/5] rsync 代码 → $STANDBY_DIR"
echo "  [2/5] 构建前端"
echo "  [3/5] 启动 $STANDBY"
echo "  [4/5] nginx 切换流量"
echo "  [4.5/5] 冒烟测试（6 项核心接口）"
echo "  [5/5] 关闭 $ACTIVE（旧代码保留可 rollback）"
echo ""

ALERT_SCRIPT="$(dirname "$0")/push-alert.sh"

# ── 0. 预部署测试关卡（失败立即中止，保护用户）────────────────────────
echo "[0/5] 预部署测试（积分/认证/任务/退款核心路径）" | tee -a $LOG
cd /root/ssp/backend
PYTHONPATH=/root/ssp/backend /opt/ssp/backend/venv/bin/pytest \
    tests/test_billing.py \
    tests/test_refund_tracker.py \
    tests/test_jobs_internals.py \
    tests/test_audit.py \
    tests/test_auth.py \
    tests/test_payment.py \
    -q --tb=short 2>&1 | tee -a $LOG
if [ "${PIPESTATUS[0]}" -ne 0 ]; then
    echo "❌ 预部署测试失败，部署中止。请修复测试后重试。" | tee -a $LOG
    TS=$(date '+%Y-%m-%d %H:%M:%S')
    [ -x "$ALERT_SCRIPT" ] && bash "$ALERT_SCRIPT" "🔴 部署失败（测试未通过）" \
        "🕐 时间: $TS\n❌ 问题: 预部署核心测试失败，部署已中止\n🎯 功能: 积分/认证/任务/退款核心路径\n📋 详情: 请修复失败测试后重新部署\n日志: $LOG" || true
    exit 1
fi
echo "✅ 预部署测试通过" | tee -a $LOG
cd /root/ssp

# ── 1. rsync 代码到 standby slot（不动 active slot）─────────────────
echo "[1/5] rsync /root/ssp → $STANDBY_DIR" | tee -a $LOG
rsync -a --exclude='*.pyc' --exclude='__pycache__/' \
    /root/ssp/backend/app/ $STANDBY_DIR/backend/app/
rsync -a /root/ssp/frontend/src/    $STANDBY_DIR/frontend/src/
rsync -a /root/ssp/frontend/public/ $STANDBY_DIR/frontend/public/
# 前端配置文件直接复制到 slot(Turbopack 不支持 symlink 指向 project 外的配置文件)
for f in package.json next.config.ts next.config.js next-env.d.ts \
          tsconfig.json postcss.config.mjs eslint.config.mjs; do
    [ -e /root/ssp/frontend/$f ] && cp /root/ssp/frontend/$f $STANDBY_DIR/frontend/$f
done
chown -R ssp-app:ssp-app $STANDBY_DIR/backend/app $STANDBY_DIR/frontend/src \
    $STANDBY_DIR/frontend/public $STANDBY_DIR/frontend/package.json \
    $STANDBY_DIR/frontend/tsconfig.json 2>/dev/null || true

echo "✅ rsync 完成" | tee -a $LOG

# ── 2. 前端 build（在 /root/ssp/frontend，有真实 node_modules）────────
# Turbopack 不允许 symlink 指向 project 外，故在 git 工作树里 build，
# 产物 .next/ 再 rsync 到 standby slot
echo "[2/5] 构建前端（在 /root/ssp/frontend）..." | tee -a $LOG
cd /root/ssp/frontend
npm run build 2>&1 | tail -5 | tee -a $LOG
rsync -a /root/ssp/frontend/.next/ $STANDBY_DIR/frontend/.next/
chown -R ssp-app:ssp-app $STANDBY_DIR/frontend/.next
echo "✅ 前端构建完成" | tee -a $LOG

# ── 3. 启动 standby ──────────────────────────────────────────────────
echo "[3/5] 启动 ssp-backend-$STANDBY 和 ssp-frontend-$STANDBY" | tee -a $LOG
supervisorctl start ssp-backend-$STANDBY  2>&1 | tee -a $LOG
supervisorctl start ssp-frontend-$STANDBY 2>&1 | tee -a $LOG

echo "等待 $STANDBY 健康就绪（最多 60s）..." | tee -a $LOG
HEALTH_OK=0
for i in $(seq 1 12); do
    BACKEND_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://127.0.0.1:$STANDBY_BACKEND/health)
    FRONTEND_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://127.0.0.1:$STANDBY_FRONTEND)
    if [ "$BACKEND_STATUS" = "200" ] && [ "$FRONTEND_STATUS" = "200" ]; then
        echo "✅ $STANDBY 健康检查通过 (backend=$BACKEND_STATUS, frontend=$FRONTEND_STATUS) — ${i}x5s" | tee -a $LOG
        HEALTH_OK=1
        break
    fi
    echo "⏳ 等待中 ($((i*5))s/60s) backend=$BACKEND_STATUS frontend=$FRONTEND_STATUS" | tee -a $LOG
    sleep 5
done
if [ "$HEALTH_OK" != "1" ]; then
    echo "❌ $STANDBY 启动失败，停止 standby，$ACTIVE 继续在线" | tee -a $LOG
    supervisorctl stop ssp-backend-$STANDBY ssp-frontend-$STANDBY 2>/dev/null || true
    TS=$(date '+%Y-%m-%d %H:%M:%S')
    [ -x "$ALERT_SCRIPT" ] && bash "$ALERT_SCRIPT" "🔴 部署失败（新 slot 启动超时）" \
        "🕐 时间: $TS\n❌ 问题: $STANDBY slot 60s 内未通过健康检查\n🎯 功能: 所有功能（服务未成功启动）\n📋 详情: $ACTIVE 仍在线，用户未受影响\n请检查: supervisorctl status + /var/log/ssp-backend-${STANDBY}.log" || true
    exit 1
fi

# ── 4. nginx 切换 ────────────────────────────────────────────────────
echo "[4/5] nginx 切换流量到 $STANDBY" | tee -a $LOG
sed -i "s|proxy_pass http://127.0.0.1:$ACTIVE_BACKEND;|proxy_pass http://127.0.0.1:$STANDBY_BACKEND;|g" /etc/nginx/sites-enabled/default
sed -i "s|proxy_pass http://127.0.0.1:$ACTIVE_FRONTEND;|proxy_pass http://127.0.0.1:$STANDBY_FRONTEND;|g" /etc/nginx/sites-enabled/default
nginx -t 2>&1 | tee -a $LOG
nginx -s reload
echo "✅ 流量已切换到 $STANDBY" | tee -a $LOG

# ── 4.5 部署后冒烟测试（流量已切，验证新 slot 对外可用）────────────────────
echo "[4.5/5] 冒烟测试 $STANDBY (backend=$STANDBY_BACKEND frontend=$STANDBY_FRONTEND)" | tee -a $LOG
SMOKE_SCRIPT="$(dirname "$0")/smoke-test.sh"

if [ -x "$SMOKE_SCRIPT" ]; then
    if bash "$SMOKE_SCRIPT" "$STANDBY_BACKEND" "$STANDBY_FRONTEND" 2>&1 | tee -a $LOG; then
        echo "✅ 冒烟测试通过" | tee -a $LOG
    else
        echo "❌ 冒烟测试失败！自动回滚到 $ACTIVE..." | tee -a $LOG

        # ── 自动回滚：把 nginx 切回 ACTIVE，停掉有问题的 STANDBY ──────────
        sed -i "s|proxy_pass http://127.0.0.1:$STANDBY_BACKEND;|proxy_pass http://127.0.0.1:$ACTIVE_BACKEND;|g" /etc/nginx/sites-enabled/default
        sed -i "s|proxy_pass http://127.0.0.1:$STANDBY_FRONTEND;|proxy_pass http://127.0.0.1:$ACTIVE_FRONTEND;|g" /etc/nginx/sites-enabled/default
        nginx -t 2>&1 | tee -a $LOG && nginx -s reload
        echo "✅ 流量已切回 $ACTIVE（旧代码）" | tee -a $LOG

        supervisorctl stop ssp-backend-$STANDBY ssp-frontend-$STANDBY 2>&1 | tee -a $LOG || true
        echo "✅ 停止 $STANDBY（问题新代码）" | tee -a $LOG

        # 推送告警
        if [ -x "$ALERT_SCRIPT" ]; then
            bash "$ALERT_SCRIPT" "🔴 部署自动回滚" \
                "冒烟测试失败，已自动切回 $ACTIVE（旧代码）\n请检查日志：/var/log/deploy.log\n及 /var/log/ssp-backend-${STANDBY}.log" || true
        fi

        echo "========================================" | tee -a $LOG
        echo "🔄 自动回滚完成: $STANDBY → $ACTIVE" | tee -a $LOG
        echo "   激活：$ACTIVE（旧代码，用户不受影响）" | tee -a $LOG
        echo "   请修复代码后重新部署" | tee -a $LOG
        echo "========================================" | tee -a $LOG
        exit 1
    fi
else
    echo "⚠️  smoke-test.sh 不存在，跳过" | tee -a $LOG
fi

# ── 5. drain：查旧进程内存中的活跃任务数，等完成后再停（最多 15 分钟）──
echo "[5/5] 关闭 $ACTIVE（先 drain 进行中任务）" | tee -a $LOG

DRAIN_MAX=900   # 最多等 15 分钟
DRAIN_ELAPSED=0

# 查旧槽进程内存（不用 jobs.json，防止被新槽 cleanup 污染）
_get_active() {
    curl -s --max-time 3 http://127.0.0.1:${ACTIVE_BACKEND}/internal/active-jobs \
        2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('active_jobs',0))" 2>/dev/null || echo 0
}

while [ $DRAIN_ELAPSED -lt $DRAIN_MAX ]; do
    RUNNING_COUNT=$(_get_active)

    if [ "$RUNNING_COUNT" = "0" ] || [ -z "$RUNNING_COUNT" ]; then
        echo "✅ 旧进程无进行中任务，可以安全停止" | tee -a $LOG
        break
    fi

    echo "⏳ 旧进程还有 $RUNNING_COUNT 个任务运行中，等待完成... (${DRAIN_ELAPSED}s/${DRAIN_MAX}s)" | tee -a $LOG
    sleep 15
    DRAIN_ELAPSED=$((DRAIN_ELAPSED + 15))
done

if [ $DRAIN_ELAPSED -ge $DRAIN_MAX ]; then
    echo "⚠️  等待超时(${DRAIN_MAX}s)，强制停止旧进程（积分已退还）" | tee -a $LOG
fi

supervisorctl stop ssp-backend-$ACTIVE ssp-frontend-$ACTIVE 2>&1 | tee -a $LOG

echo "" | tee -a $LOG
echo "🎉 部署成功！" | tee -a $LOG
echo "   激活：$STANDBY（新代码 → $STANDBY_DIR）" | tee -a $LOG
echo "   待命：$ACTIVE （旧代码 → /opt/ssp-$ACTIVE/ — rollback 随时可用）" | tee -a $LOG
echo "   如需回滚：bash /root/rollback.sh" | tee -a $LOG
echo "========================================" | tee -a $LOG
