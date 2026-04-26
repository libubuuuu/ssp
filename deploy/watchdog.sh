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

# === 1. /health ===
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$PROD_HOST/health" 2>/dev/null || echo "TIMEOUT")
if [[ "$HEALTH" != "200" ]]; then
    alert "[CRIT] /health 返回 $HEALTH(预期 200)"
    crit_count=$((crit_count + 1))
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

# === 总结 ===
if [[ "$crit_count" -gt 0 ]]; then
    log "SUMMARY: CRIT=$crit_count WARN=$warn_count(已写 $ALERTS)"
elif [[ "$warn_count" -gt 0 ]]; then
    log "SUMMARY: WARN=$warn_count(已写 $ALERTS)"
else
    log "OK: health=$HEALTH backend=alive frontend=alive recent_5xx_429<=20 errors<=5 backup_fresh"
fi

exit 0
