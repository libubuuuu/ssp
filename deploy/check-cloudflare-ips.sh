#!/usr/bin/env bash
# Cloudflare IP 段差异检查(隐藏雷 #2)
#
# 用途:
#   每周拉一次 https://www.cloudflare.com/ips-{v4,v6},对比 deploy/cloudflare-real-ip.conf
#   有差异写到 /var/log/cf-ips-mismatch.log + 推微信告警
#
# 后果(为何重要):
#   CF 加新 IP 段后,我们 nginx 没更新 → 那批边缘节点的请求被当作公网用户
#   → set_real_ip_from 不生效 → fail2ban / rate limit 用 CF IP 而非真用户 IP
#   → 一个 CF 节点的合法用户被互相牵连 ban
#
# 用法:
#   bash /opt/ssp/deploy/check-cloudflare-ips.sh           # 跑一次
#   crontab: 0 4 * * 1 bash /opt/ssp/deploy/check-cloudflare-ips.sh   # 每周一 04:00
set -uo pipefail

CONF="/opt/ssp/deploy/cloudflare-real-ip.conf"
[[ -f "$CONF" ]] || CONF="/root/ssp/deploy/cloudflare-real-ip.conf"  # dev fallback
LOG="/var/log/cf-ips-mismatch.log"
TS=$(date '+%Y-%m-%d %H:%M:%S')

if [[ ! -f "$CONF" ]]; then
    echo "[$TS] ERROR: snippet 文件不存在: $CONF" | tee -a "$LOG" >&2
    exit 2
fi

# 拉远端 IP 列表(CF 官方,不需要鉴权,curl 失败不退出而是告警)
TMP=$(mktemp)
trap 'rm -f "$TMP" "$TMP.local" "$TMP.remote"' EXIT

V4=$(curl -sS --max-time 10 https://www.cloudflare.com/ips-v4 2>/dev/null || echo "")
V6=$(curl -sS --max-time 10 https://www.cloudflare.com/ips-v6 2>/dev/null || echo "")

if [[ -z "$V4" || -z "$V6" ]]; then
    echo "[$TS] ERROR: 拉 cloudflare.com/ips-v{4,6} 失败,跳过本次检查" | tee -a "$LOG" >&2
    exit 1
fi

# 收集远端期望集合
{
    echo "$V4"
    echo "$V6"
} | grep -E '^[0-9a-f]' | sort -u > "$TMP.remote"

# 收集本地 snippet 里的 set_real_ip_from(剥语法)
grep -E '^[[:space:]]*set_real_ip_from' "$CONF" \
    | sed -E 's/.*set_real_ip_from[[:space:]]+([^;]+);.*/\1/' \
    | sort -u > "$TMP.local"

# 差异检查
ADDED=$(comm -23 "$TMP.remote" "$TMP.local")  # 远端有,本地没有 → 新增,要补
REMOVED=$(comm -13 "$TMP.remote" "$TMP.local") # 本地有,远端没了 → 已废弃,可删

if [[ -z "$ADDED" && -z "$REMOVED" ]]; then
    echo "[$TS] OK: CF IP 段与本地 snippet 一致($(wc -l < "$TMP.remote") 段)" >> "$LOG"
    exit 0
fi

# 有差异 → 写日志 + 推送
{
    echo "[$TS] MISMATCH: CF IP 段差异"
    if [[ -n "$ADDED" ]]; then
        echo "  新增(本地缺): $(echo "$ADDED" | tr '\n' ' ')"
    fi
    if [[ -n "$REMOVED" ]]; then
        echo "  废弃(本地多): $(echo "$REMOVED" | tr '\n' ' ')"
    fi
} | tee -a "$LOG"

# 推微信(如果 push-alert.sh 在)
PUSH=/opt/ssp/deploy/push-alert.sh
[[ -x "$PUSH" ]] || PUSH=/root/ssp/deploy/push-alert.sh
if [[ -x "$PUSH" ]]; then
    BODY="新增: $(echo "$ADDED" | head -10 | tr '\n' ' ')\n废弃: $(echo "$REMOVED" | head -10 | tr '\n' ' ')\n请更新 deploy/cloudflare-real-ip.conf 后重 deploy nginx"
    bash "$PUSH" "🟡 CF IP 段需更新" "$BODY" >> "$LOG" 2>&1 || true
fi

exit 1   # 非 0 让 cron 邮件(若配了 MAILTO)知道有问题
