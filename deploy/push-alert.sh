#!/usr/bin/env bash
# 推送告警到外部通道(微信 / 企业微信 / 飞书等)
#
# 用法:bash push-alert.sh "标题" "正文"
#
# 支持的通道(读取 /root/.ssp-watchdog-config 中的环境变量,留空则跳过):
#   PUSHPLUS_TOKEN     - PushPlus(微信,免费,扫码绑定)— 推荐个人用户
#   SERVERCHAN_KEY     - Server 酱(微信,5K/月免费)— 推荐个人用户
#   WECOM_WEBHOOK_URL  - 企业微信群机器人 webhook — 推荐团队
#   FEISHU_WEBHOOK_URL - 飞书机器人 webhook
#
# 配置文件示例 /root/.ssp-watchdog-config(chmod 600):
#   PUSHPLUS_TOKEN=abc123def456
#   SERVERCHAN_KEY=SCT12345xyz

set -uo pipefail

TITLE="${1:-SSP 告警}"
CONTENT="${2:-(无正文)}"

CONFIG_FILE=/root/.ssp-watchdog-config
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
fi

# 防同一告警短时间重复推送(冷却 5 分钟)
COOL_FILE=/tmp/.ssp-push-cooldown
COOL_KEY=$(echo "$TITLE" | md5sum | awk '{print $1}')
if [[ -f "$COOL_FILE" ]] && grep -q "^${COOL_KEY}:" "$COOL_FILE" 2>/dev/null; then
    LAST_TS=$(grep "^${COOL_KEY}:" "$COOL_FILE" | head -1 | cut -d: -f2)
    NOW_TS=$(date +%s)
    if (( NOW_TS - LAST_TS < 300 )); then
        echo "[push-alert] 同标题 5 分钟内已推过,跳过" >&2
        exit 0
    fi
fi

# 记录本次推送时间
echo "${COOL_KEY}:$(date +%s)" >> "$COOL_FILE"
# 保留最近 200 行,防文件无限大
tail -n 200 "$COOL_FILE" > "${COOL_FILE}.tmp" && mv "${COOL_FILE}.tmp" "$COOL_FILE"

PUSHED=0

# === PushPlus(微信)===
if [[ -n "${PUSHPLUS_TOKEN:-}" ]]; then
    curl -s -m 10 -X POST "http://www.pushplus.plus/send" \
        -H "Content-Type: application/json" \
        --data "$(printf '{"token":"%s","title":"%s","content":"%s","template":"txt"}' "$PUSHPLUS_TOKEN" "$TITLE" "$CONTENT")" \
        >/dev/null 2>&1 && PUSHED=$((PUSHED + 1)) || true
fi

# === Server 酱(微信)===
if [[ -n "${SERVERCHAN_KEY:-}" ]]; then
    curl -s -m 10 -X POST "https://sctapi.ftqq.com/${SERVERCHAN_KEY}.send" \
        --data-urlencode "title=$TITLE" \
        --data-urlencode "desp=$CONTENT" \
        >/dev/null 2>&1 && PUSHED=$((PUSHED + 1)) || true
fi

# === 企业微信群机器人 ===
if [[ -n "${WECOM_WEBHOOK_URL:-}" ]]; then
    curl -s -m 10 -X POST "$WECOM_WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        --data "$(printf '{"msgtype":"text","text":{"content":"%s\n\n%s"}}' "$TITLE" "$CONTENT")" \
        >/dev/null 2>&1 && PUSHED=$((PUSHED + 1)) || true
fi

# === 飞书机器人 ===
if [[ -n "${FEISHU_WEBHOOK_URL:-}" ]]; then
    curl -s -m 10 -X POST "$FEISHU_WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        --data "$(printf '{"msg_type":"text","content":{"text":"%s\n\n%s"}}' "$TITLE" "$CONTENT")" \
        >/dev/null 2>&1 && PUSHED=$((PUSHED + 1)) || true
fi

if [[ "$PUSHED" -eq 0 ]]; then
    echo "[push-alert] 没配任何通道,告警未推送(在 $CONFIG_FILE 配 token 启用)" >&2
    exit 1
fi

exit 0
