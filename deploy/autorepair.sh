#!/usr/bin/env bash
# SSP 自动修复脚本
# 每5分钟运行(cron)，发现异常自动修复
# 修复成功 → 推"已自动修复"，失败 → 推"需要人工处理"，无异常 → 静默
#
# 检查项:
#   1. 后端进程(supervisor RUNNING + /health 200)→ 异常自动重启
#   2. 前端进程(supervisor RUNNING + HTTP 200) → 异常自动重启（DB 异常也会触发此项，因为 /health 会反映 DB 状态）
#   3. 内存占用 > 90%                           → page cache 清理

set -uo pipefail

TS=$(date +"%Y-%m-%d %H:%M:%S")
LOG=/var/log/ssp-autorepair.log
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
ALERT_SCRIPT="${SCRIPT_DIR}/push-alert.sh"

log() { echo "[$TS] $1" >> "$LOG"; }

# 确定活跃 slot（从 nginx 配置读取）
CURRENT_PORT=$(grep -oP 'proxy_pass http://127.0.0.1:\K[0-9]+' /etc/nginx/sites-enabled/default 2>/dev/null | head -1 || echo "8001")
if [ "${CURRENT_PORT}" = "8000" ]; then
    ACTIVE="blue";  ACTIVE_BACKEND=8000; ACTIVE_FRONTEND=3000
else
    ACTIVE="green"; ACTIVE_BACKEND=8001; ACTIVE_FRONTEND=3002
fi

REPAIRS_DONE=()
REPAIRS_FAILED=()

# ── 检查并修复后端 ──────────────────────────────────────────────────────────
check_backend() {
    local sv_ok=0
    supervisorctl status "ssp-backend-${ACTIVE}" 2>/dev/null | grep -q "RUNNING" && sv_ok=1

    local h
    h=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 \
        "http://127.0.0.1:${ACTIVE_BACKEND}/health" 2>/dev/null || echo "0")

    [ "$sv_ok" = "1" ] && [ "$h" = "200" ] && return  # 一切正常，不干预

    local reason
    [ "$sv_ok" = "0" ] && reason="进程未 RUNNING" || reason="/health 返回 $h"
    log "后端异常: $reason — 重启 ssp-backend-${ACTIVE}"

    supervisorctl restart "ssp-backend-${ACTIVE}" >> "$LOG" 2>&1 || \
        supervisorctl start  "ssp-backend-${ACTIVE}" >> "$LOG" 2>&1 || true
    sleep 10

    h=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
        "http://127.0.0.1:${ACTIVE_BACKEND}/health" 2>/dev/null || echo "0")
    if [ "$h" = "200" ]; then
        log "后端重启后 /health 200 ✓"
        REPAIRS_DONE+=("后端异常($reason)，已自动重启恢复")
    else
        log "后端重启后 /health $h ✗"
        REPAIRS_FAILED+=("后端故障($reason)，重启后 /health 仍返回 $h")
    fi
}

# ── 检查并修复前端 ──────────────────────────────────────────────────────────
check_frontend() {
    local sv_ok=0
    supervisorctl status "ssp-frontend-${ACTIVE}" 2>/dev/null | grep -q "RUNNING" && sv_ok=1

    local h
    h=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
        "http://127.0.0.1:${ACTIVE_FRONTEND}" 2>/dev/null || echo "0")

    [ "$sv_ok" = "1" ] && [ "$h" = "200" ] && return

    local reason
    [ "$sv_ok" = "0" ] && reason="进程未 RUNNING" || reason="HTTP 返回 $h"
    log "前端异常: $reason — 重启 ssp-frontend-${ACTIVE}"

    supervisorctl restart "ssp-frontend-${ACTIVE}" >> "$LOG" 2>&1 || \
        supervisorctl start  "ssp-frontend-${ACTIVE}" >> "$LOG" 2>&1 || true
    sleep 20  # Next.js 冷启动较慢

    h=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 \
        "http://127.0.0.1:${ACTIVE_FRONTEND}" 2>/dev/null || echo "0")
    if [ "$h" = "200" ]; then
        log "前端重启后 HTTP 200 ✓"
        REPAIRS_DONE+=("前端异常($reason)，已自动重启恢复")
    else
        log "前端重启后 HTTP $h ✗"
        REPAIRS_FAILED+=("前端故障($reason)，重启后仍返回 $h")
    fi
}

# ── 检查内存并清理 ─────────────────────────────────────────────────────────
check_memory() {
    local pct
    pct=$(free | awk '/^Mem:/{printf "%d", int($3*100/$2)}' 2>/dev/null || echo "0")
    [ "${pct}" -le 90 ] && return

    log "内存 ${pct}% > 90% — 清理 page cache"
    sync && echo 1 > /proc/sys/vm/drop_caches 2>/dev/null || true
    sleep 2

    local new_pct
    new_pct=$(free | awk '/^Mem:/{printf "%d", int($3*100/$2)}' 2>/dev/null || echo "${pct}")
    if [ "${new_pct}" -le 90 ]; then
        log "内存: ${pct}% → ${new_pct}% ✓"
        REPAIRS_DONE+=("内存占用 ${pct}%，page cache 清理后降至 ${new_pct}%")
    else
        log "内存清理后仍 ${new_pct}% ✗"
        REPAIRS_FAILED+=("内存占用 ${new_pct}%，清理后仍超 90%，可能存在内存泄漏")
    fi
}

# ── 执行所有检查 ────────────────────────────────────────────────────────────
log "=== autorepair 开始 (active=$ACTIVE backend=$ACTIVE_BACKEND frontend=$ACTIVE_FRONTEND) ==="
check_backend
check_frontend
check_memory

# ── 推送结果 ────────────────────────────────────────────────────────────────
if [ "${#REPAIRS_FAILED[@]}" -gt 0 ]; then
    FAILED_LIST=$(printf ' - %s\n' "${REPAIRS_FAILED[@]}")
    DONE_SECTION=""
    if [ "${#REPAIRS_DONE[@]}" -gt 0 ]; then
        DONE_SECTION=$(printf '\n✅ 本轮已修复:\n%s' "$(printf ' - %s\n' "${REPAIRS_DONE[@]}")")
    fi
    BODY="🕐 时间: $TS
❌ 问题: 自动修复尝试失败，需要人工介入
🎯 功能: 服务可用性
📋 失败项:
${FAILED_LIST}${DONE_SECTION}

请检查:
 supervisorctl status
 tail -n 50 /var/log/ssp-backend-${ACTIVE}.err.log
 tail -n 50 /var/log/ssp-autorepair.log"
    bash "$ALERT_SCRIPT" "🆘 SSP 需人工处理" "$BODY" >> "$LOG" 2>&1 || true
    log "已推送 [需人工处理] 告警"

elif [ "${#REPAIRS_DONE[@]}" -gt 0 ]; then
    DONE_LIST=$(printf ' - %s\n' "${REPAIRS_DONE[@]}")
    BODY="🕐 时间: $TS
✅ 问题: 检测到异常，已自动修复
🎯 功能: 服务可用性
📋 修复内容:
${DONE_LIST}

服务已恢复正常，无需人工操作。"
    bash "$ALERT_SCRIPT" "✅ SSP 已自动修复" "$BODY" >> "$LOG" 2>&1 || true
    log "已推送 [已自动修复] 通知"

else
    log "所有服务正常，无需修复"
fi

# 日志限制（保留最近 5000 行）
tail -n 5000 "$LOG" > "${LOG}.tmp" 2>/dev/null && mv "${LOG}.tmp" "$LOG" 2>/dev/null || true
log "=== autorepair 完成 ==="
exit 0
