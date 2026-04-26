#!/usr/bin/env bash
# 合成用户旅程测试 — 模拟真实用户路径,bug 在用户撞到前抓
#
# 测的事(每 30 分钟一次):
#   1. 登录(POST /api/auth/login)— 验证整个 auth 链路
#   2. 看自己资料(GET /api/auth/me)— 验证 token 鉴权 + 用户表
#   3. 列任务(GET /api/jobs/list)— 验证 jobs 路径
#   4. 列充值套餐(GET /api/payment/packages)— 验证支付路径
#
# 任意一步失败 → 推送 watchdog 告警 + 写诊断快照
# 用专用账号 synthetic-monitor@ailixiao.com,凭证在 /root/.ssp-watchdog-config

set -uo pipefail

PROD_HOST="https://ailixiao.com"
CONFIG=/root/.ssp-watchdog-config
LOG=/var/log/ssp-synthetic-test.log
TS=$(date +"%Y-%m-%d %H:%M:%S")

log() { echo "[$TS] $1" >> "$LOG"; }

# 加载凭证
if [[ ! -f "$CONFIG" ]]; then
    log "[ERR] 配置文件 $CONFIG 不存在"
    exit 1
fi
EMAIL=$(grep '^SYNTHETIC_TEST_EMAIL=' "$CONFIG" | head -1 | cut -d= -f2)
PASSWORD=$(grep '^SYNTHETIC_TEST_PASSWORD=' "$CONFIG" | head -1 | cut -d= -f2-)

if [[ -z "$EMAIL" || -z "$PASSWORD" ]]; then
    log "[ERR] EMAIL/PASSWORD 未在 $CONFIG 配置"
    exit 1
fi

# 触发告警的辅助函数
fail_alert() {
    local step="$1"
    local detail="$2"
    log "[FAIL] step=$step detail=$detail"
    bash "$(dirname "$0")/push-alert.sh" \
        "🤖 SSP 合成用户测试失败" \
        "时间: $TS

❌ 失败步骤: $step
详情: $detail

测试账号: $EMAIL
完整诊断: admin.ailixiao.com/admin/diagnose

⚠️ 这表示真实用户操作链路可能也已经挂掉,立刻处理" >> "$LOG" 2>&1 || true
    exit 1
}

# === 1. 登录 ===
LOGIN_RESP=$(curl -s -m 15 -X POST "$PROD_HOST/api/auth/login" \
    -H "Content-Type: application/json" \
    -d "$(printf '{"email":"%s","password":"%s"}' "$EMAIL" "$PASSWORD")" 2>&1) || \
    fail_alert "登录" "网络错误: $LOGIN_RESP"

TOKEN=$(echo "$LOGIN_RESP" | python3 -c '
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get("access_token") or d.get("token") or "")
except Exception:
    print("")
' 2>/dev/null)

if [[ -z "$TOKEN" ]]; then
    fail_alert "登录" "拿不到 token: $(echo "$LOGIN_RESP" | head -c 300)"
fi

# === 2. /api/auth/me ===
ME_CODE=$(curl -s -m 10 -o /tmp/synth-me.json -w "%{http_code}" \
    -H "Authorization: Bearer $TOKEN" \
    "$PROD_HOST/api/auth/me" 2>/dev/null || echo "0")
if [[ "$ME_CODE" != "200" ]]; then
    fail_alert "/api/auth/me" "返回 $ME_CODE: $(cat /tmp/synth-me.json 2>/dev/null | head -c 300)"
fi

# === 3. /api/jobs/list ===
JOBS_CODE=$(curl -s -m 10 -o /tmp/synth-jobs.json -w "%{http_code}" \
    -H "Authorization: Bearer $TOKEN" \
    "$PROD_HOST/api/jobs/list" 2>/dev/null || echo "0")
if [[ "$JOBS_CODE" != "200" ]]; then
    fail_alert "/api/jobs/list" "返回 $JOBS_CODE: $(cat /tmp/synth-jobs.json 2>/dev/null | head -c 300)"
fi

# === 4. /api/payment/packages(无认证也应该 200)===
PKG_CODE=$(curl -s -m 10 -o /dev/null -w "%{http_code}" \
    "$PROD_HOST/api/payment/packages" 2>/dev/null || echo "0")
if [[ "$PKG_CODE" != "200" ]]; then
    fail_alert "/api/payment/packages" "返回 $PKG_CODE"
fi

# 全部通过
log "OK 全 4 步通过(login=200 me=200 jobs=$JOBS_CODE packages=$PKG_CODE)"
rm -f /tmp/synth-me.json /tmp/synth-jobs.json
exit 0
