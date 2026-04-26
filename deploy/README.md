# Deploy 目录

生产环境部署用的系统配置 + 脚本。**换服务器时跑 setup-fresh-server.sh 即可恢复一切**。

## 文件清单

### 系统配置(cp 到 /etc/)
| 文件 | 作用 |
|---|---|
| nginx.conf | 生产 nginx 配置(主站 + admin + monitor 三个 vhost,含限流、JSON error_page、proxy timeout) |
| supervisor.conf | supervisor 进程管理(Blue-Green 4 个服务) |
| fail2ban.local | fail2ban 防爆破规则 |

### 部署 / 回滚 / 备份
| 文件 | 作用 |
|---|---|
| setup-fresh-server.sh | 全新 Ubuntu 22.04 一键恢复基础设施 + 监控告警 + cron |
| deploy.sh | 蓝绿零停机部署(symlink: `/root/deploy.sh`) |
| rollback.sh | 部署失败一键回滚(symlink: `/root/rollback.sh`) |
| backup.sh | 每日加密备份 dev.db + .env.enc + jobs.json → GitHub libubuuuu/ssp-backup |
| restore.sh | 从 GitHub 拉最新备份解密到 /tmp/(不自动覆盖生产) |
| verify-backup.sh | 验证最近备份是否就位(退出码:0 健康 / 2 过期 / 1 错误) |

### 监控告警(AIOps 体系)
| 文件 | 作用 |
|---|---|
| watchdog.sh | 每 5 分钟自动巡检(health / supervisor / nginx 5xx / 后端 ERROR / 备份新鲜度 + 4 端点合成监控)。告警时冻结诊断快照 + 推送 |
| synthetic-user-test.sh | 每 30 分钟模拟真实用户旅程(login → /me → /jobs/list → /packages),任何步骤失败立刻告警 |
| push-alert.sh | 推送告警到外部通道(PushPlus / Server 酱 / 企业微信 / 飞书),5 分钟同标题冷却防刷屏 |
| create-synthetic-user.sh | 自动建合成监控账号 + 生成密码 + 写 watchdog-config |

### 模板(setup-fresh-server.sh 用)
| 文件 | 作用 |
|---|---|
| cron.example | 完整 cron 任务清单(watchdog 5min / 合成 30min / 备份 03:15) |
| watchdog-config.example | /root/.ssp-watchdog-config 模板(推送 token + 合成账号占位) |
| backup.cron.example | 仅备份 cron 行(老模板,被 cron.example 取代,保留兼容) |

---

## 换服务器灾备恢复(简版)

```bash
# 1. 全新 Ubuntu 22.04 上 git clone
git clone git@github.com:libubuuuu/ssp.git /root/ssp
cd /root/ssp

# 2. 一键搭基础设施 + 监控 + cron
sudo bash deploy/setup-fresh-server.sh

# 3. 跑完后按打印的 5 步手动清单做(写主密码 / 恢复 DB / SSL / 起服务 / 配监控)
```

完整流程 + 故障排查见 [docs/DISASTER-RECOVERY.md](../docs/DISASTER-RECOVERY.md)。

---

## 维护规则

- 任何 nginx / supervisor / fail2ban 配置改动,**先改 deploy/ 下的版本,然后 cp 到 /etc/**,确保 git 跟生产一致
- 任何系统配置改动**必须 commit + push**,否则换服务器恢复时会丢
- cron 任务改了,改 `cron.example` 让 setup-fresh-server.sh 能装上
- 改 *.sh 后跑 `bash -n` 验证语法再 commit
