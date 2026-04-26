#!/usr/bin/env bash
# setup-fresh-server.sh
# 在全新 Ubuntu 22.04 上一键搭建 SSP 基础设施。
# 用法:
#   git clone git@github.com:libubuuuu/ssp.git /root/ssp
#   cd /root/ssp
#   sudo bash deploy/setup-fresh-server.sh
#
# 完成后:nginx / supervisor / fail2ban / ufw / swap / 项目依赖全部就位,
# 但后端服务暂未启动(等主密码写入)。结尾打印 4 步手动清单。

set -euo pipefail

# ========== 全局 ==========
TS=$(date +%Y%m%d-%H%M)
REPO_ROOT="${REPO_ROOT:-/root/ssp}"
LOG="[setup]"

stage() {
    echo ""
    echo "=== 阶段 $1: $2 ==="
}

backup_if_exists() {
    local path="$1"
    if [[ -f "$path" ]]; then
        cp -a "$path" "${path}.bak.${TS}"
        echo "$LOG 备份 $path → ${path}.bak.${TS}"
    fi
}

# ========== 阶段 0:前置检查 ==========
stage 0 "前置检查(root + Ubuntu 22.04 + 仓库就位)"

if [[ "${EUID}" -ne 0 ]]; then
    echo "$LOG 必须 root 跑(使用 sudo)" >&2
    exit 1
fi

if [[ ! -f /etc/os-release ]]; then
    echo "$LOG 找不到 /etc/os-release" >&2
    exit 1
fi
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID%%.*}" != "22" ]]; then
    echo "$LOG 仅支持 Ubuntu 22.04,实际:${ID:-?} ${VERSION_ID:-?}" >&2
    exit 1
fi

if [[ ! -d "$REPO_ROOT/deploy" ]]; then
    echo "$LOG $REPO_ROOT/deploy 不存在 — 先 git clone 仓库" >&2
    exit 1
fi

cd "$REPO_ROOT"
echo "$LOG 前置检查通过 — 开始搭建"

# ========== 阶段 1:apt 基础工具 ==========
stage 1 "apt 基础工具(curl/git/nginx/supervisor/fail2ban/ufw/certbot 等)"

export DEBIAN_FRONTEND=noninteractive
apt update
apt install -y \
    curl git ca-certificates gnupg lsb-release software-properties-common \
    build-essential pkg-config \
    ufw fail2ban supervisor nginx \
    certbot python3-certbot-nginx \
    sqlite3

# ========== 阶段 2:Python 3.11 + venv ==========
stage 2 "Python 3.11(deadsnakes 兜底)"

if ! command -v python3.11 >/dev/null 2>&1; then
    if ! apt install -y python3.11 python3.11-venv python3.11-dev 2>/dev/null; then
        echo "$LOG python3.11 不在主源,加 deadsnakes PPA"
        add-apt-repository -y ppa:deadsnakes/ppa
        apt update
        apt install -y python3.11 python3.11-venv python3.11-dev
    fi
fi
python3.11 --version

# ========== 阶段 3:Node 20(NodeSource) ==========
stage 3 "Node 20"

if command -v node >/dev/null 2>&1 && [[ "$(node -v)" == v20* ]]; then
    echo "$LOG node 已就位:$(node -v)"
else
    curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/nodesource_setup.sh
    bash /tmp/nodesource_setup.sh
    apt install -y nodejs
    rm -f /tmp/nodesource_setup.sh
fi
node -v
npm -v

# ========== 阶段 4:swap(2G) ==========
stage 4 "swap"

if swapon --show 2>/dev/null | grep -q '^/swapfile'; then
    echo "$LOG /swapfile 已启用,跳过"
else
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    if ! grep -q '^/swapfile ' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' >> /etc/fstab
    fi
    echo "$LOG /swapfile 2G 已创建并写入 fstab"
fi

# ========== 阶段 5:UFW 防火墙 ==========
stage 5 "UFW 防火墙(22/80/443)"

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status verbose

# ========== 阶段 6:系统配置(nginx / supervisor / fail2ban) ==========
stage 6 "复制 deploy/*.conf 到系统目录(原文件备份)"

backup_if_exists /etc/nginx/sites-enabled/default
cp "$REPO_ROOT/deploy/nginx.conf" /etc/nginx/sites-enabled/default
echo "$LOG nginx 配置已部署"

backup_if_exists /etc/supervisor/conf.d/ssp.conf
cp "$REPO_ROOT/deploy/supervisor.conf" /etc/supervisor/conf.d/ssp.conf
echo "$LOG supervisor 配置已部署"

backup_if_exists /etc/fail2ban/jail.local
cp "$REPO_ROOT/deploy/fail2ban.local" /etc/fail2ban/jail.local
echo "$LOG fail2ban 配置已部署"

# limit_req_zone 必须在 http {} 块。检查是否需要补
if ! grep -rq 'limit_req_zone' /etc/nginx/ 2>/dev/null; then
    echo "$LOG [警告] /etc/nginx 内未发现 limit_req_zone 定义,nginx -t 会失败"
    echo "$LOG     请手工把下面两行加到 /etc/nginx/nginx.conf 的 http {} 块:"
    echo "$LOG       limit_req_zone \$binary_remote_addr zone=api_limit:10m rate=20r/s;"
    echo "$LOG       limit_req_zone \$binary_remote_addr zone=login_limit:10m rate=2r/s;"
fi

# ========== 阶段 7:/root symlink ==========
stage 7 "/root/{deploy,rollback}.sh symlink"

[[ -L /root/deploy.sh || -e /root/deploy.sh ]] && rm -f /root/deploy.sh
[[ -L /root/rollback.sh || -e /root/rollback.sh ]] && rm -f /root/rollback.sh
ln -s "$REPO_ROOT/deploy/deploy.sh" /root/deploy.sh
ln -s "$REPO_ROOT/deploy/rollback.sh" /root/rollback.sh
chmod +x "$REPO_ROOT/deploy/deploy.sh" "$REPO_ROOT/deploy/rollback.sh"
ls -la /root/deploy.sh /root/rollback.sh

# ========== 阶段 8:后端依赖 ==========
stage 8 "后端 venv + pip install"

cd "$REPO_ROOT/backend"
if [[ ! -d venv ]]; then
    python3.11 -m venv venv
    echo "$LOG 创建 backend/venv"
else
    echo "$LOG backend/venv 已存在,复用"
fi
venv/bin/pip install --upgrade pip
if [[ -f requirements.txt ]]; then
    venv/bin/pip install -r requirements.txt
else
    echo "$LOG [警告] backend/requirements.txt 不存在 — 跳过 pip install"
fi
cd "$REPO_ROOT"

# ========== 阶段 9:前端依赖 + 构建 ==========
stage 9 "前端 npm install + npm run build"

cd "$REPO_ROOT/frontend"
if [[ -f package-lock.json ]]; then
    npm ci || npm install
else
    npm install
fi
# 构建可能因为 NEXT_PUBLIC_* env 缺失给警告,但不应该 fail
npm run build
cd "$REPO_ROOT"

# ========== 阶段 10:启用服务(不加载 env) ==========
stage 10 "启用 nginx / fail2ban / supervisor"

nginx -t

systemctl enable nginx fail2ban supervisor
systemctl restart nginx
systemctl restart fail2ban
systemctl restart supervisor

supervisorctl reread
supervisorctl update

# blue 服务 autostart=true,但此刻没有 .ssp_master_key,后端会解 env 失败一直崩。
# 主动停下,等用户写完主密码再人工 start。
supervisorctl stop ssp-backend-blue ssp-backend-green ssp-frontend-blue ssp-frontend-green 2>/dev/null || true
echo "$LOG 应用进程已 stop — 等待主密码写入"

# ========== 阶段 11:watchdog 配置 + 日志文件 ==========
stage 11 "watchdog 配置文件 + 日志路径"

if [[ ! -f /root/.ssp-watchdog-config ]]; then
    cp "$REPO_ROOT/deploy/watchdog-config.example" /root/.ssp-watchdog-config
    chmod 600 /root/.ssp-watchdog-config
    echo "$LOG 创建 /root/.ssp-watchdog-config(从 example,需手工填推送 token + 合成账号密码)"
else
    echo "$LOG /root/.ssp-watchdog-config 已存在,保留不动"
fi

# 创建日志文件(后续 cron 任务写)
for f in /var/log/ssp-watchdog.log \
         /var/log/ssp-watchdog-alerts.log \
         /var/log/ssp-synthetic-test.log \
         /var/log/ssp-backup.log; do
    touch "$f"
    chmod 640 "$f"
done
mkdir -p /var/log/ssp-diagnose
chmod 750 /var/log/ssp-diagnose

# ========== 阶段 11.5:恢复 Claude 项目记忆 + 启动咒语 ==========
stage 11.5 "恢复 Claude 项目记忆(memory + start-claude.txt)"

# 把 git 仓库里的 memory 副本恢复到本地(让未来 Claude 继承协作约定 + 项目背景)
if [[ -d "$REPO_ROOT/docs/memory-snapshot" ]]; then
    mkdir -p /root/.claude/projects/-root/memory
    # 只复制 .md 文件,跳过 README.md(那是说明,不是记忆)
    for f in "$REPO_ROOT"/docs/memory-snapshot/*.md; do
        bn=$(basename "$f")
        [[ "$bn" == "README.md" ]] && continue
        cp "$f" /root/.claude/projects/-root/memory/
    done
    echo "$LOG memory 已恢复到 /root/.claude/projects/-root/memory/"
fi

# 启动咒语(根 cat 文件让用户能立刻用)
if [[ -f "$REPO_ROOT/start-claude.txt" ]]; then
    cp "$REPO_ROOT/start-claude.txt" /root/start-claude.txt
    echo "$LOG /root/start-claude.txt 已就位 — 新会话跑 cat /root/start-claude.txt 引导 Claude"
fi

# ========== 阶段 12:安装 cron 任务 ==========
stage 12 "cron 任务(watchdog + 合成监控 + 备份)"

# 备份现有 crontab
crontab -l > /tmp/cron.bak 2>/dev/null || true

# 合并现有 + 仓库 example,去重排序
cat /tmp/cron.bak "$REPO_ROOT/deploy/cron.example" \
    | grep -vE '^\s*#' \
    | grep -vE '^\s*$' \
    | sort -u \
    | crontab -

echo "$LOG cron 当前任务:"
crontab -l | grep -v '^#' | grep -v '^$' | sed 's/^/    /'

# ========== 阶段 13:打印手动清单 ==========
stage 13 "完成 — 5 步手动清单"

cat <<'NEXT'

╔══════════════════════════════════════════════════════════════════╗
║          基础设施就绪。完成下面 5 步才能让站点上线                 ║
╚══════════════════════════════════════════════════════════════════╝

[1/5] 写主密码(解锁 .env.enc):
  echo "你保管的主密码原文" > /root/.ssp_master_key
  chmod 400 /root/.ssp_master_key

  验证密码对(不真解出来,只看头几行):
  openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d \
    -in /root/ssp/backend/.env.enc \
    -pass file:/root/.ssp_master_key | head -3

[2/5] 恢复数据库:
  # 从 GitHub libubuuuu/ssp-backup 拉最近备份(已加密)
  bash /root/ssp/deploy/restore.sh
  # 然后把 /tmp/restore-XXX/backend/dev.db cp 到 /root/ssp/backend/dev.db
  chmod 600 /root/ssp/backend/dev.db

[3/5] 改 DNS A 记录到本机 IP,等生效后申请 SSL:
  dig +short ailixiao.com   # 应返回本机 IP
  certbot --nginx \
    -d ailixiao.com -d www.ailixiao.com \
    -d admin.ailixiao.com -d monitor.ailixiao.com \
    --agree-tos --redirect

[4/5] 启动应用:
  supervisorctl start ssp-backend-blue ssp-frontend-blue
  supervisorctl status
  systemctl reload nginx
  curl https://ailixiao.com/health   # 应 200

[5/5] 配置监控告警通道(2 步):
  # 5a. 创建合成监控账号(自动生成密码 + 写入 watchdog-config + 注册 user)
  cd /root/ssp/backend && bash /root/ssp/deploy/create-synthetic-user.sh
  # 5b. 配推送 token(任选一个,Server 酱 推荐):
  #   去 https://sct.ftqq.com 微信扫码拿 SCKEY,然后:
  vim /root/.ssp-watchdog-config   # 把 SERVERCHAN_KEY= 那行填上
  bash /root/ssp/deploy/push-alert.sh "测试" "通了"   # 验证

诊断:
  curl https://ailixiao.com/health
  tail -f /var/log/ssp-backend-blue.err.log
  tail -f /var/log/ssp-watchdog-alerts.log

完整流程见 docs/DISASTER-RECOVERY.md
NEXT
