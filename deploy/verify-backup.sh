#!/usr/bin/env bash
# 验证 ssp-backup 仓库最近一次备份是否就位
# 退出码:
#   0 = 健康(最近备份 ≤ 26 小时)
#   2 = 警告(最近备份 > 26 小时,可能 cron 失败)
#   1 = 错误(仓库不存在 / git fetch 失败 / 无任何备份)
# 用法:bash /root/ssp/deploy/verify-backup.sh

set -e

LOG="[verify]"
BACKUP_REPO=/root/ssp-backup-repo
ALERT_AFTER_HOURS=26

if [[ ! -d "$BACKUP_REPO/.git" ]]; then
    echo "$LOG 备份镜像仓库不存在: $BACKUP_REPO" >&2
    exit 1
fi

cd "$BACKUP_REPO"
git fetch --quiet origin main 2>&1 || { echo "$LOG git fetch 失败 — 网络或权限问题" >&2; exit 1; }

if ! git rev-parse origin/main >/dev/null 2>&1; then
    echo "$LOG 远端 main 分支不存在 — 还从未跑过备份"
    exit 1
fi

LAST_HASH=$(git log -1 --format="%h" origin/main)
LAST_MSG=$(git log -1 --format="%s" origin/main)
LAST_TIME=$(git log -1 --format="%ci" origin/main)
LAST_EPOCH=$(git log -1 --format="%ct" origin/main)
NOW_EPOCH=$(date +%s)
AGE_SECONDS=$(( NOW_EPOCH - LAST_EPOCH ))
AGE_HOURS=$(( AGE_SECONDS / 3600 ))

echo "=== GitHub 远端最近备份 ==="
echo "commit:  $LAST_HASH  $LAST_MSG"
echo "时间:    $LAST_TIME ($AGE_HOURS 小时前)"

echo ""
echo "=== 远端备份文件清单(最近 5 个) ==="
git ls-tree --name-only origin/main 2>/dev/null \
    | grep -E '^backup-.*\.tar\.gz\.enc$' | sort -r | head -5 \
    || echo "(远端无备份文件)"

echo ""
echo "=== 本地最近 5 个备份 ==="
ls -lh "$BACKUP_REPO"/backup-*.tar.gz.enc 2>/dev/null | tail -5 || echo "(本地无)"

echo ""
if (( AGE_HOURS > ALERT_AFTER_HOURS )); then
    echo "⚠ 警告:最近备份距今 ${AGE_HOURS} 小时(阈值 ${ALERT_AFTER_HOURS} 小时)— cron 可能失败" >&2
    exit 2
fi
echo "✓ 健康:最近备份距今 ${AGE_HOURS} 小时"
