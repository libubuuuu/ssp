#!/usr/bin/env bash
# 创建合成监控测试账号 + 生成随机密码 + 写入 /root/.ssp-watchdog-config
#
# 用法:bash /root/ssp/deploy/create-synthetic-user.sh
#
# 幂等:账号已存在则跳过(并提示去 Server 酱后台 reset 密码或手工查 DB)

set -euo pipefail

CONFIG=/root/.ssp-watchdog-config
EMAIL=synthetic-monitor@ailixiao.com
REPO_ROOT=/root/ssp

if [[ ! -f /root/.ssp_master_key ]]; then
    echo "ERROR: 主密码 /root/.ssp_master_key 不存在,先按 setup 阶段 1 写主密码" >&2
    exit 1
fi

# 先拿环境变量(供 services.auth 用)
ENV_VARS=$(openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d \
    -in "$REPO_ROOT/backend/.env.enc" \
    -pass file:/root/.ssp_master_key 2>/dev/null)
if [[ -z "$ENV_VARS" ]]; then
    echo "ERROR: 解 .env.enc 失败,主密码不对?" >&2
    exit 1
fi

# 生成随机密码
SYNTH_PWD=$(openssl rand -hex 16)

# 用 Python 直接调 services.auth.create_user
cd "$REPO_ROOT/backend"
RESULT=$(eval "$(echo "$ENV_VARS" | grep -v '^#' | sed 's/^/export /')" && \
    venv/bin/python <<PYEOF
import sys
sys.path.insert(0, '.')
from app.services.auth import create_user, get_user_by_email
existing = get_user_by_email("$EMAIL")
if existing:
    print(f"EXISTING:{existing['id']}")
else:
    user = create_user("$EMAIL", "$SYNTH_PWD", "Synthetic Monitor")
    if user:
        print(f"CREATED:{user['id']}")
    else:
        print("FAILED")
PYEOF
)

case "$RESULT" in
    EXISTING:*)
        echo "ℹ️ 账号 $EMAIL 已存在(id ${RESULT#EXISTING:})"
        echo "   如果你忘了密码,需要直接改 DB password_hash 或重新 create_user"
        echo "   配置文件:$CONFIG 里的 SYNTHETIC_TEST_PASSWORD 必须跟 DB 一致"
        ;;
    CREATED:*)
        echo "✅ 账号已创建 $EMAIL (id ${RESULT#CREATED:})"
        # 把密码写入 config(覆盖现有 SYNTHETIC_TEST_PASSWORD 行)
        if grep -q '^SYNTHETIC_TEST_PASSWORD=' "$CONFIG" 2>/dev/null; then
            sed -i "s|^SYNTHETIC_TEST_PASSWORD=.*|SYNTHETIC_TEST_PASSWORD=$SYNTH_PWD|" "$CONFIG"
        else
            echo "SYNTHETIC_TEST_PASSWORD=$SYNTH_PWD" >> "$CONFIG"
        fi
        if ! grep -q '^SYNTHETIC_TEST_EMAIL=' "$CONFIG" 2>/dev/null; then
            echo "SYNTHETIC_TEST_EMAIL=$EMAIL" >> "$CONFIG"
        fi
        chmod 600 "$CONFIG"
        echo "✅ 密码已写入 $CONFIG"
        ;;
    *)
        echo "❌ 创建失败,Python 输出:$RESULT" >&2
        exit 1
        ;;
esac

# 立刻测一下
if [[ -f "$REPO_ROOT/deploy/synthetic-user-test.sh" ]]; then
    echo ""
    echo "=== 立刻试跑合成测试 ==="
    bash "$REPO_ROOT/deploy/synthetic-user-test.sh" && echo "✅ 测试通过" || echo "⚠️ 测试失败,看 /var/log/ssp-synthetic-test.log"
fi
