# SSP 灾备演练剧本

> **没演练过的灾备方案 = 假灾备。** 这份剧本必须真跑过一次。
>
> 第一次演练目标:在 50 元/月测试服务器上,从 0 恢复到网站能登录,**< 1 小时**。

---

## 演练前提清单(必须齐全)

| 你必须有 | 来源 | 校验 |
|---|---|---|
| 测试服务器 SSH 访问 | 腾讯云轻量 / 阿里云轻量,2C/4G,Ubuntu 22.04 | `ssh root@<新IP>` 能进 |
| GitHub SSH key | 当前服务器 `/root/.ssh/id_ed25519_github` | 已备份到密码管理器(第 4 步要做的) |
| 主密码 | 你脑子 / 密码管理器 / 纸质 | 用它能解 `.env.enc`(下面要测) |
| 一个测试域名(不必是 ailixiao.com) | 任意域名(可临时申请) | 演练只测恢复流程,不要影响生产 |

**演练原则:** 在测试服务器跑,**绝不动生产**。

---

## 演练流程(每步预计耗时)

### 阶段 0:开测试服务器(10 分钟)

1. 腾讯云控制台 → 轻量应用服务器 → 立即购买
2. 配置:**Ubuntu 22.04** + 2C/4G/40G + 任意地域 + 50 元档
3. 设 root 密码 + 创建实例
4. SSH 上去:`ssh root@<新IP>`
5. 验证:`uname -a` 看到 Ubuntu 22.04

---

### 阶段 1:配 SSH key 让能 clone(5 分钟)

```bash
# 在新服务器上生成 SSH key
ssh-keygen -t ed25519 -C "ssp-drill" -N "" -f /root/.ssh/id_ed25519_github

# 显示公钥
cat /root/.ssh/id_ed25519_github.pub
```

**复制公钥** → 打开 https://github.com/settings/keys → New SSH key → 粘贴 → 命名 "ssp-drill-test"

验证:
```bash
ssh -i /root/.ssh/id_ed25519_github -T git@github.com
# 应回应 "Hi libubuuuu!"
```

---

### 阶段 2:clone + setup-fresh-server.sh(20-30 分钟)

```bash
# clone 主仓
git clone git@github.com:libubuuuu/ssp.git /root/ssp
cd /root/ssp

# 一键搭基础设施
sudo bash deploy/setup-fresh-server.sh
```

**预期看到:**
- 阶段 0:前置检查通过
- 阶段 1-10:依次装 apt 工具、Python、Node、swap、防火墙、配置、symlink、依赖、build、启服务
- 阶段 11:watchdog 配置 + 日志路径
- 阶段 11.5:**memory 自动恢复**(关键 — 看到 "memory 已恢复到 ..." 即成功)
- 阶段 12:cron 自动装
- 阶段 13:打印 5 步手动清单

**任何阶段失败 → 复制错误粘给我修脚本**,直到全过。

---

### 阶段 3:写主密码 + 验证能解(2 分钟)

```bash
# 把你保管的主密码写入(替换那串)
echo "你保管的主密码原文" > /root/.ssp_master_key
chmod 400 /root/.ssp_master_key

# 验证能解 .env.enc
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d \
  -in /root/ssp/backend/.env.enc \
  -pass file:/root/.ssp_master_key | head -3
```

**预期看到 `RESEND_API_KEY=re_xxx`、`FAL_KEY=fal_xxx` 之类。**
**报错 "bad decrypt"** = 主密码不对,从密码管理器/纸条核对再写。

---

### 阶段 4:从 GitHub 拉数据库恢复(5 分钟)

```bash
# 先 clone 备份仓
git clone git@github.com:libubuuuu/ssp-backup.git /root/ssp-backup-repo
cd /root/ssp-backup-repo

# 跑恢复脚本
bash /root/ssp/deploy/restore.sh
```

**预期:解密成功,文件解到 `/tmp/restore-XXXXXX/`,显示 dev.db / .env.enc / jobs.json**

```bash
# 复制恢复物到生产位置
RESTORE_DIR=$(ls -d /tmp/restore-* | tail -1)
cp $RESTORE_DIR/backend/dev.db /root/ssp/backend/dev.db
chmod 600 /root/ssp/backend/dev.db

# 验证 DB 能读
sqlite3 /root/ssp/backend/dev.db "SELECT COUNT(*) FROM users;"
# 应看到正数(跟生产用户数一致)
```

---

### 阶段 5:启动服务 + 健康检查(2 分钟)

```bash
supervisorctl start ssp-backend-blue ssp-frontend-blue
sleep 10
supervisorctl status

# 健康
curl http://127.0.0.1:8000/health
# 期望:{"status":"ok"}
```

**任何失败:** 查 `/var/log/ssp-backend-blue.err.log` 看异常。

---

### 阶段 6:配监控 + 跑合成测试(5 分钟)

```bash
# 创建合成监控账号
bash /root/ssp/deploy/create-synthetic-user.sh

# 配 Server 酱(你已有 SCKEY,粘贴):
sed -i 's/^SERVERCHAN_KEY=.*/SERVERCHAN_KEY=你的SCKEY/' /root/.ssp-watchdog-config

# 测推送通道
bash /root/ssp/deploy/push-alert.sh "测试" "演练通了"
# 你微信应该收到一条
```

---

### 阶段 7:Claude 项目记忆验证(2 分钟)

```bash
# 看 memory 是否恢复
ls /root/.claude/projects/-root/memory/
# 应看到 4 个 .md 文件

# 启 Claude Code,看它是否记得项目
cat /root/start-claude.txt
claude
```

**预期 Claude 进来读 PROGRESS.md/CLAUDE.md/memory,10 句话总结项目状态。**

---

### 阶段 8:演练总结 + 销毁(2 分钟)

如果上面 1-7 都通过:

```bash
# 在 PROGRESS.md 加一行
cd /root/ssp
echo "" >> PROGRESS.md
echo "## $(date +%Y-%m-%d) 灾备演练 ✅" >> PROGRESS.md
echo "在测试服务器 <新IP> 跑通完整恢复流程,耗时 $XX 分钟,无重大问题。" >> PROGRESS.md
git add PROGRESS.md
git commit -m "docs(drill): $(date +%Y-%m-%d) 灾备演练成功"
git push origin main
```

**然后:**
- 关掉测试服务器(腾讯云控制台释放),不要再花钱
- 去 GitHub Settings → SSH keys 删掉刚加的 "ssp-drill-test" key

---

## 演练失败时怎么办

**任何一步失败,把错误信息粘给 Claude(我)**,我会:
1. 定位是脚本 bug 还是环境差异
2. 立刻修脚本 + push
3. 让你重跑那一步

**第一次演练通常会失败 1-3 个地方** — 这就是演练的意义,**暴露真实问题在生产挂之前**。

---

## 演练频率

- **第一次:** 部署上线后 1 周内(本周做)
- **后续:** 每季度一次,验证文档没腐烂

---

## 验收标准

演练**算成功**的硬指标:
- [ ] 全部 7 阶段通过(允许任何阶段重跑修复)
- [ ] dev.db 真有用户数据(跟生产 user 数对得上)
- [ ] 主密码能解 .env.enc
- [ ] 后端 /health 返 200
- [ ] supervisor 4 服务 RUNNING
- [ ] watchdog 跑一次 OK
- [ ] 合成监控账号能登录
- [ ] 微信收到推送测试
- [ ] 新启 claude 后能读 memory(说出"用户偏好直接、严格、不 sugar-coat")
- [ ] 总耗时 < 90 分钟

**全打勾 = 你这套灾备真能用。** 这才是值得收藏的承诺。
