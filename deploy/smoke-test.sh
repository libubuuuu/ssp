#!/bin/bash
# 部署后冒烟测试 — 验证新 slot 上线后核心路径正常
#
# 用法: bash smoke-test.sh <backend_port> <frontend_port>
# 例:   bash smoke-test.sh 8001 3002
#
# 返回码: 0 = 全部通过  1 = 有项失败
# 失败时: 打印具体失败项 + 通过 push-alert.sh 推送告警

set -euo pipefail

BACKEND_PORT="${1:-8000}"
FRONTEND_PORT="${2:-3000}"
BACKEND="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND="http://127.0.0.1:${FRONTEND_PORT}"

PASS=0
FAIL=0
FAILURES=""

LOG="/var/log/deploy.log"

_check() {
    local name="$1"
    local url="$2"
    local expected_code="${3:-200}"
    local extra_check="${4:-}"  # 可选: grep 关键词

    local actual
    actual=$(curl -s -o /tmp/smoke_body.txt -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")

    if [ "$actual" != "$expected_code" ]; then
        FAIL=$((FAIL + 1))
        local msg="FAIL [$name] 期望 HTTP $expected_code，实际 $actual — $url"
        echo "  ❌ $msg" | tee -a "$LOG"
        FAILURES="${FAILURES}${msg}\n"
        return
    fi

    if [ -n "$extra_check" ]; then
        if ! grep -q "$extra_check" /tmp/smoke_body.txt 2>/dev/null; then
            FAIL=$((FAIL + 1))
            local msg="FAIL [$name] 响应体缺少 '$extra_check' — $url"
            echo "  ❌ $msg" | tee -a "$LOG"
            FAILURES="${FAILURES}${msg}\n"
            return
        fi
    fi

    PASS=$((PASS + 1))
    echo "  ✅ PASS [$name] HTTP $actual" | tee -a "$LOG"
}

echo "── 冒烟测试开始 (backend=$BACKEND_PORT frontend=$FRONTEND_PORT) ──" | tee -a "$LOG"

# ── 后端核心接口 ─────────────────────────────────────────────────────────────

# 1. /health — 后端存活 + DB 可读
_check "backend /health" "$BACKEND/health" "200" "database"

# 2. / — 根路径 API 自描述
_check "backend /" "$BACKEND/" "200" "AI"

# 3. /api/auth/register — POST-only 路由，GET 应 405，证明路由已注册
_check "backend /api/auth/register 405" "$BACKEND/api/auth/register" "405"

# 4. /api/payment/credit-packs — 公开积分套餐列表（无需登录）
_check "backend /api/payment/credit-packs" "$BACKEND/api/payment/credit-packs" "200"

# 5. /internal/active-jobs — drain 端点存活
_check "backend /internal/active-jobs" "$BACKEND/internal/active-jobs" "200" "active_jobs"

# ── 前端 ─────────────────────────────────────────────────────────────────────

# 6. 前端首页 200（Next.js 服务正常）
_check "frontend /" "$FRONTEND/" "200"

# ── 结果汇总 ──────────────────────────────────────────────────────────────────

echo "── 冒烟测试结果: ${PASS} 通过 / ${FAIL} 失败 ──" | tee -a "$LOG"

if [ "$FAIL" -gt 0 ]; then
    echo "⚠️  部署冒烟测试发现 ${FAIL} 项失败，请人工检查！" | tee -a "$LOG"
    # 推送告警（脚本不存在则跳过）
    ALERT_SCRIPT="$(dirname "$0")/push-alert.sh"
    if [ -x "$ALERT_SCRIPT" ]; then
        bash "$ALERT_SCRIPT" "⚠️ 部署冒烟测试失败" \
            "端口 backend=${BACKEND_PORT} frontend=${FRONTEND_PORT}\n\n${FAILURES}" || true
    fi
    exit 1
fi

exit 0
