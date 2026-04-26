#!/usr/bin/env bash
# 每日加密备份 SSP 关键数据到 GitHub 私有仓库 ssp-backup。
# 备份内容:backend/dev.db + backend/.env.enc + jobs_data/jobs.json(如存在)
# 流程:tar.gz 打包 → AES-256 加密 → push GitHub → 清理本地 7+ 天
# 用法:
#   bash /root/ssp/deploy/backup.sh           # 手动跑
#   见 deploy/backup.cron.example             # cron 调用

set -euo pipefail

# === 全局 ===
TS=$(date +%Y%m%d-%H%M)
LOG="[backup]"
# 2026-04-27 起项目降权到 ssp-app + /opt/ssp,master.key 移到 /etc/ssp/
SSP_ROOT="${SSP_ROOT:-/opt/ssp}"
BACKUP_REPO="${BACKUP_REPO:-/root/ssp-backup-repo}"
MASTER_KEY="${MASTER_KEY:-/etc/ssp/master.key}"
RETENTION_DAYS=7

TMP_DIR=$(mktemp -d -p /dev/shm backup.XXXXXX)
ARCHIVE="${TMP_DIR}/backup-${TS}.tar.gz"
ENCRYPTED_NAME="backup-${TS}.tar.gz.enc"
ENCRYPTED_PATH="${BACKUP_REPO}/${ENCRYPTED_NAME}"

cleanup() {
    if [[ -d "$TMP_DIR" ]]; then
        find "$TMP_DIR" -type f -exec shred -u {} \; 2>/dev/null || true
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT

stage() { echo ""; echo "=== 阶段 $1: $2 ==="; }

# === 阶段 0:前置检查 ===
stage 0 "前置检查"
[[ -f "$MASTER_KEY" ]]               || { echo "$LOG 主密码不存在: $MASTER_KEY" >&2; exit 1; }
[[ -d "$BACKUP_REPO/.git" ]]         || { echo "$LOG 备份镜像仓库未 init: $BACKUP_REPO" >&2; exit 1; }
[[ -f "$SSP_ROOT/backend/dev.db" ]]  || { echo "$LOG dev.db 不存在" >&2; exit 1; }
[[ -f "$SSP_ROOT/backend/.env.enc" ]]|| { echo "$LOG .env.enc 不存在" >&2; exit 1; }
echo "$LOG 时间戳=$TS"

# === 阶段 1:tar.gz 打包到 /dev/shm ===
stage 1 "tar.gz 打包(dev.db + .env.enc + jobs.json 可选)"
TAR_INPUTS=("backend/dev.db" "backend/.env.enc")
if [[ -f "$SSP_ROOT/jobs_data/jobs.json" ]]; then
    TAR_INPUTS+=("jobs_data/jobs.json")
    echo "$LOG 包含 jobs.json"
else
    echo "$LOG 跳过 jobs.json(不存在)"
fi
tar -czf "$ARCHIVE" -C "$SSP_ROOT" "${TAR_INPUTS[@]}"
echo "$LOG 打包大小:$(du -h "$ARCHIVE" | cut -f1)"

# === 阶段 2:AES-256 加密 ===
stage 2 "AES-256-CBC + PBKDF2 加密"
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt \
    -in "$ARCHIVE" -out "$ENCRYPTED_PATH" \
    -pass file:"$MASTER_KEY"
chmod 600 "$ENCRYPTED_PATH"
echo "$LOG 加密产物:$ENCRYPTED_PATH ($(du -h "$ENCRYPTED_PATH" | cut -f1))"

# === 阶段 3:更新 latest.tar.gz.enc 软链 ===
stage 3 "更新 latest.tar.gz.enc → $ENCRYPTED_NAME"
cd "$BACKUP_REPO"
ln -sf "$ENCRYPTED_NAME" latest.tar.gz.enc
echo "$LOG latest 软链已更新"

# === 阶段 4:清理本地 7+ 天 ===
stage 4 "清理本地超过 ${RETENTION_DAYS} 天的备份(GitHub 历史保留所有)"
DELETED=$(find "$BACKUP_REPO" -maxdepth 1 -name 'backup-*.tar.gz.enc' \
    -mtime "+${RETENTION_DAYS}" -print -delete 2>/dev/null | wc -l)
echo "$LOG 本地清理 $DELETED 个旧文件"

# === 阶段 5:git commit + push ===
stage 5 "git push origin main"
cd "$BACKUP_REPO"
git add -A
if git diff --cached --quiet; then
    echo "$LOG 工作区无改动,跳过 push"
else
    git commit -m "backup: $TS" --quiet
    git push -u origin main --quiet
    echo "$LOG push 成功"
fi

# === 阶段 6:总结 ===
stage 6 "完成"
echo "$LOG 备份 $TS 已加密上传 GitHub"
echo "$LOG 本地最近 3 个备份:"
ls -lh "$BACKUP_REPO"/backup-*.tar.gz.enc 2>/dev/null | tail -3 || echo "(无)"
