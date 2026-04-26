#!/usr/bin/env bash
# SSP 生产 watchdog
# 每 5 分钟跑一次(cron 配),检查关键健康指标,异常时写告警日志
#
# 信号分级:
#   OK       — 写 /var/log/ssp-watchdog.log,周期标记
#   WARN     — 同上 + 写 /var/log/ssp-watchdog-alerts.log(你 tail 这个文件即可看告警)
#   CRITICAL — 同上,标记 [CRIT]
#
# 检查项:
#   1. https://ailixiao.com/health → HTTP 200
#   2. supervisor 至少一组(blue 或 green)backend+frontend 全 RUNNING
#   3. 最近 5 分钟 nginx 5xx/429 出现次数 > 20 → WARN
#   4. 最近 200 行后端 err 日志含 ERROR 行数 > 5 → WARN
#   5. /root/ssp-backup-repo 远端最近 commit 距今 > 26 小时 → WARN(备份失效)

set -uo pipefail
TS=$(date +"%Y-%m-%d %H:%M:%S")
LOG=/var/log/ssp-watchdog.log
ALERTS=/var/log/ssp-watchdog-alerts.log
PROD_HOST="https://ailixiao.com"

log() { echo "[$TS] $1" >> "$LOG"; }
alert() { echo "[$TS] $1" | tee -a "$ALERTS" >> "$LOG"; }

# 触发计数器
warn_count=0
crit_count=0

# === 1. /health(失败时重试 1 次,防 deploy 蓝绿切换窗口期误报) ===
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$PROD_HOST/health" 2>/dev/null || echo "TIMEOUT")
if [[ "$HEALTH" != "200" ]]; then
    sleep 8  # 给 deploy 切换时间
    HEALTH_RETRY=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$PROD_HOST/health" 2>/dev/null || echo "TIMEOUT")
    if [[ "$HEALTH_RETRY" != "200" ]]; then
        alert "[CRIT] /health 第一次返回 $HEALTH,重试仍 $HEALTH_RETRY(已确认非瞬时)"
        crit_count=$((crit_count + 1))
        HEALTH="$HEALTH_RETRY"
    else
        log "[INFO] health 第一次 $HEALTH 但重试 200,判定为瞬时(可能 deploy 切换),不告警"
        HEALTH="200"
    fi
fi

# === 2. supervisor 状态 ===
# 至少需要 backend-{blue|green} 之一 + frontend-{blue|green} 之一在跑
backend_alive=0
frontend_alive=0
if supervisorctl status ssp-backend-blue 2>/dev/null | grep -q "RUNNING"; then backend_alive=1; fi
if supervisorctl status ssp-backend-green 2>/dev/null | grep -q "RUNNING"; then backend_alive=1; fi
if supervisorctl status ssp-frontend-blue 2>/dev/null | grep -q "RUNNING"; then frontend_alive=1; fi
if supervisorctl status ssp-frontend-green 2>/dev/null | grep -q "RUNNING"; then frontend_alive=1; fi

if [[ "$backend_alive" == "0" ]]; then
    alert "[CRIT] 无任何 ssp-backend supervisor 进程 RUNNING"
    crit_count=$((crit_count + 1))
fi
if [[ "$frontend_alive" == "0" ]]; then
    alert "[CRIT] 无任何 ssp-frontend supervisor 进程 RUNNING"
    crit_count=$((crit_count + 1))
fi

# === 3. nginx 5xx/429 频次(最近 5 分钟) ===
# nginx access.log 时间格式 [26/Apr/2026:20:31:16 +0800]
if [[ -r /var/log/nginx/access.log ]]; then
    SINCE_MIN=$(date -d '5 minutes ago' +'%d/%b/%Y:%H:%M' 2>/dev/null || echo "")
    if [[ -n "$SINCE_MIN" ]]; then
        # 取最近 2000 行(性能保护),过滤时间 >= SINCE_MIN 且状态码是 5xx 或 429
        ERR_COUNT=$(tail -n 2000 /var/log/nginx/access.log 2>/dev/null | \
            awk -v since="$SINCE_MIN" '
                {
                    # 提取 [26/Apr/2026:20:31:16
                    match($0, /\[[^]]+\]/);
                    ts = substr($0, RSTART+1, 17);
                    # 提取 status code(行格式: ...HTTP/1.1" XXX ...)
                    if (match($0, /" (5[0-9][0-9]|429) /)) {
                        if (ts >= since) print
                    }
                }
            ' | wc -l)
        if [[ "$ERR_COUNT" -gt 20 ]]; then
            alert "[WARN] 最近 5 分钟 nginx 5xx/429 共 $ERR_COUNT 次(阈值 20)"
            warn_count=$((warn_count + 1))
        fi
    fi
fi

# === 4. 后端 err 日志最近 ERROR 行数 ===
RECENT_ERR=0
for f in /var/log/ssp-backend-blue.err.log /var/log/ssp-backend-green.err.log; do
    if [[ -r "$f" ]]; then
        # 只看最近 200 行,统计 ERROR 出现次数(grep -c 命中 0 时返 1,但仍输出 0,用 || true 容错)
        n=$(tail -n 200 "$f" 2>/dev/null | grep -cE "(ERROR|Traceback|Exception)" 2>/dev/null || true)
        n=${n:-0}
        RECENT_ERR=$((RECENT_ERR + n))
    fi
done
if [[ "$RECENT_ERR" -gt 5 ]]; then
    alert "[WARN] 后端最近 200 行 err 日志含 ERROR/Traceback/Exception 共 $RECENT_ERR 行"
    warn_count=$((warn_count + 1))
fi

# === 5. ssp-backup 远端备份新鲜度 ===
if [[ -d /root/ssp-backup-repo/.git ]]; then
    cd /root/ssp-backup-repo 2>/dev/null && git fetch --quiet origin main 2>/dev/null || true
    if git rev-parse origin/main >/dev/null 2>&1; then
        last_epoch=$(git log -1 --format="%ct" origin/main 2>/dev/null || echo 0)
        now_epoch=$(date +%s)
        age_hours=$(( (now_epoch - last_epoch) / 3600 ))
        if [[ "$age_hours" -gt 26 ]]; then
            alert "[WARN] ssp-backup 最近备份距今 $age_hours 小时(阈值 26 小时,可能 cron 失败)"
            warn_count=$((warn_count + 1))
        fi
    fi
    cd / 2>/dev/null || true
fi

# === 告警时:自动冻结完整诊断快照(给 Claude 事后复盘用)===
SNAPSHOT_DIR=/var/log/ssp-diagnose
mkdir -p "$SNAPSHOT_DIR"
if [[ "$crit_count" -gt 0 || "$warn_count" -gt 0 ]]; then
    # 文件名带时间戳 + 等级,防覆盖
    LEVEL=$([[ "$crit_count" -gt 0 ]] && echo "CRIT" || echo "WARN")
    SNAP_FILE="$SNAPSHOT_DIR/$(date +%Y%m%d-%H%M%S)-${LEVEL}.json"

    # 先把 disk / memory 计算好,避免 shell 引号嵌套问题
    DISK_PCT=$(df -h /root | tail -1 | awk '{print $5}')
    DISK_USED=$(df -h /root | tail -1 | awk '{print $3}')
    DISK_TOTAL=$(df -h /root | tail -1 | awk '{print $2}')
    DISK_STR="${DISK_PCT} used (${DISK_USED}/${DISK_TOTAL})"
    MEM_USED=$(free -h | awk '/^Mem:/{print $3}')
    MEM_TOTAL=$(free -h | awk '/^Mem:/{print $2}')
    MEM_STR="${MEM_USED}/${MEM_TOTAL} used"

    # 收集快照(纯 shell + 现有日志,无需调认证 API)
    {
        echo "{"
        echo "  \"timestamp\": \"$TS\","
        echo "  \"level\": \"$LEVEL\","
        echo "  \"warn_count\": $warn_count,"
        echo "  \"crit_count\": $crit_count,"
        echo "  \"health\": \"$HEALTH\","
        echo "  \"backend_alive\": $backend_alive,"
        echo "  \"frontend_alive\": $frontend_alive,"
        echo "  \"supervisor\": $(supervisorctl status 2>&1 | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'),"
        echo "  \"nginx_error_tail\": $(tail -n 30 /var/log/nginx/error.log 2>/dev/null | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'),"
        echo "  \"backend_blue_err_tail\": $(tail -n 20 /var/log/ssp-backend-blue.err.log 2>/dev/null | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'),"
        echo "  \"backend_green_err_tail\": $(tail -n 20 /var/log/ssp-backend-green.err.log 2>/dev/null | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'),"
        echo "  \"watchdog_alerts_tail\": $(tail -n 10 \"$ALERTS\" 2>/dev/null | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'),"
        echo "  \"disk\": $(printf '%s' "$DISK_STR" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'),"
        echo "  \"memory\": $(printf '%s' "$MEM_STR" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')"
        echo "}"
    } > "$SNAP_FILE" 2>/dev/null

    # 限定快照数量(保留最近 100 份,旧的删)
    ls -t "$SNAPSHOT_DIR"/*.json 2>/dev/null | tail -n +101 | xargs -r rm -f
fi

# === 总结 ===
if [[ "$crit_count" -gt 0 ]]; then
    log "SUMMARY: CRIT=$crit_count WARN=$warn_count(已写 $ALERTS,快照 $SNAPSHOT_DIR/)"
elif [[ "$warn_count" -gt 0 ]]; then
    log "SUMMARY: WARN=$warn_count(已写 $ALERTS,快照 $SNAPSHOT_DIR/)"
else
    log "OK: health=$HEALTH backend=alive frontend=alive recent_5xx_429<=20 errors<=5 backup_fresh"
fi

exit 0
