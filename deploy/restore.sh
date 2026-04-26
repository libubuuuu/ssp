#!/usr/bin/env bash
# 从 GitHub ssp-backup 仓库下载并解密备份到 /tmp/restore-XXXXXX/
# 不会覆盖生产数据 — 只解开,提示用户人工 cp
# 用法:
#   bash /root/ssp/deploy/restore.sh                              # 默认拉 latest
#   bash /root/ssp/deploy/restore.sh backup-20260426-1521.tar.gz.enc

set -euo pipefail

LOG="[restore]"
BACKUP_REPO=/root/ssp-backup-repo
MASTER_KEY=/root/.ssp_master_key
TARGET_FILE="${1:-latest.tar.gz.enc}"
RESTORE_DIR=$(mktemp -d /tmp/restore-XXXXXX)

stage() { echo ""; echo "=== 阶段 $1: $2 ==="; }

# === 阶段 0:前置检查 ===
stage 0 "前置检查"
[[ -f "$MASTER_KEY" ]]       || { echo "$LOG 主密码不存在: $MASTER_KEY" >&2; exit 1; }
[[ -d "$BACKUP_REPO/.git" ]] || { echo "$LOG 备份镜像仓库不存在,先重新 git clone:" >&2;
    echo "$LOG   git clone git@github.com:libubuuuu/ssp-backup.git $BACKUP_REPO" >&2; exit 1; }

# === 阶段 1:从 GitHub 拉最新 ===
stage 1 "git pull origin main"
cd "$BACKUP_REPO"
git fetch --quiet origin main
git reset --hard origin/main --quiet
echo "$LOG 已同步到远端 HEAD: $(git log -1 --format='%h %s')"

# === 阶段 2:确认目标文件存在 ===
stage 2 "确认 $TARGET_FILE 存在"
if [[ ! -L "$BACKUP_REPO/$TARGET_FILE" && ! -f "$BACKUP_REPO/$TARGET_FILE" ]]; then
    echo "$LOG 找不到 $TARGET_FILE,远端可用文件:" >&2
    git ls-tree --name-only origin/main | grep -E '^backup-.*\.tar\.gz\.enc$' | tail -5 >&2
    exit 1
fi
SRC_FILE="$BACKUP_REPO/$TARGET_FILE"
# 如果是软链,解到真实文件
if [[ -L "$SRC_FILE" ]]; then
    REAL=$(readlink "$SRC_FILE")
    echo "$LOG $TARGET_FILE → $REAL"
    SRC_FILE="$BACKUP_REPO/$REAL"
fi

# === 阶段 3:AES-256 解密 ===
stage 3 "解密到 $RESTORE_DIR/restored.tar.gz"
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d \
    -in "$SRC_FILE" \
    -out "$RESTORE_DIR/restored.tar.gz" \
    -pass file:"$MASTER_KEY"

# === 阶段 4:tar 解包 ===
stage 4 "tar 解包"
tar -xzf "$RESTORE_DIR/restored.tar.gz" -C "$RESTORE_DIR"
rm -f "$RESTORE_DIR/restored.tar.gz"

# === 阶段 5:打印恢复物 + 操作指南 ===
stage 5 "完成"
echo "$LOG 恢复物已解开到 $RESTORE_DIR"
echo ""
find "$RESTORE_DIR" -type f -exec ls -la {} \;
echo ""
cat <<NEXT
═══════════════════════════════════════════════════════════════════
                 ⚠ 已解开,但未覆盖生产数据 ⚠
═══════════════════════════════════════════════════════════════════
恢复物在: $RESTORE_DIR

要覆盖生产,手动执行(注意先停服务,顺序不可乱):

  supervisorctl stop ssp-backend-blue
  cp $RESTORE_DIR/backend/dev.db        /root/ssp/backend/dev.db
  cp $RESTORE_DIR/backend/.env.enc      /root/ssp/backend/.env.enc
  [[ -f $RESTORE_DIR/jobs_data/jobs.json ]] && \\
    cp $RESTORE_DIR/jobs_data/jobs.json /root/ssp/jobs_data/jobs.json
  chmod 600 /root/ssp/backend/dev.db /root/ssp/backend/.env.enc
  supervisorctl start ssp-backend-blue
  curl https://ailixiao.com/health

确认服务起来后清理:
  rm -rf $RESTORE_DIR
NEXT
