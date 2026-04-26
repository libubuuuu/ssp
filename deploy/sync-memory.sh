#!/usr/bin/env bash
# 同步 Claude Code 的项目记忆到 git 仓库(docs/memory-snapshot/)
#
# 为什么需要这个:
#   ~/.claude/projects/<proj>/memory/ 是 Claude Code 客户端本地数据,**不在 git**
#   里面存"用户偏好 / feedback / 项目上下文",换服务器就丢
#   定期同步到 docs/memory-snapshot/ 让 git 带着走
#
# 用法:
#   bash /root/ssp/deploy/sync-memory.sh   # 单次同步
#
# 双向:
#   - 同步到 git(本服务器 → docs/memory-snapshot/)
#   - 灾备恢复时反向 cp(docs/memory-snapshot/ → ~/.claude/projects/.../memory/)
#     setup-fresh-server.sh 末尾会引导这一步

set -uo pipefail

LOCAL_MEMORY=/root/.claude/projects/-root/memory
REPO_SNAPSHOT=/root/ssp/docs/memory-snapshot

if [[ ! -d "$LOCAL_MEMORY" ]]; then
    echo "[sync-memory] $LOCAL_MEMORY 不存在,跳过(可能 Claude Code 还没初始化)"
    exit 0
fi

mkdir -p "$REPO_SNAPSHOT"

# 同步本地 → 仓库副本(覆盖)
rsync -a --delete "$LOCAL_MEMORY/" "$REPO_SNAPSHOT/"

# 看是否有变更
cd /root/ssp
if git diff --quiet docs/memory-snapshot/ 2>/dev/null; then
    echo "[sync-memory] 无变更,跳过 commit"
    exit 0
fi

echo "[sync-memory] 检测到 memory 变更,提示 commit:"
git status docs/memory-snapshot/ | sed 's/^/    /'
echo ""
echo "执行以下命令 commit + push:"
echo "    cd /root/ssp"
echo "    git add docs/memory-snapshot/"
echo "    git commit -m 'docs(memory): 同步 Claude Code 项目记忆'"
echo "    git push origin main"
