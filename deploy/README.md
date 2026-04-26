# Deploy 目录

生产环境部署用的系统配置 + 脚本。换服务器时按 setup-fresh-server.sh 自动恢复。

## 文件说明

| 文件 | 作用 |
|---|---|
| nginx.conf | 生产 nginx 配置(ailixiao.com / admin / monitor 反代、HTTPS、限流) |
| supervisor.conf | supervisor 进程管理(Blue-Green 4 个服务) |
| fail2ban.local | fail2ban 防爆破规则 |
| deploy.sh | 蓝绿零停机部署脚本(/root/deploy.sh symlink 到这里) |
| rollback.sh | 一键回滚脚本(/root/rollback.sh symlink 到这里) |
| setup-fresh-server.sh | (待写)在全新服务器上一键恢复基础设施 |

## 灾备恢复

完整流程见 `docs/DISASTER-RECOVERY.md`(待写)。

简版:
```bash
# 在新服务器(全新 Ubuntu 22.04)上:
git clone git@github.com:libubuuuu/ssp.git /root/ssp
cd /root/ssp
bash deploy/setup-fresh-server.sh    # 一键装系统依赖 + 复制配置 + 起服务

# 然后手动做 3 件事(setup 脚本救不了的):
# 1. 写主密码:
echo "你的主密码" > /root/.ssp_master_key && chmod 400 /root/.ssp_master_key
# 2. 从对象存储拉最新 dev.db 备份
bash deploy/restore-from-backup.sh
# 3. 申请 SSL 证书
certbot --nginx -d ailixiao.com -d admin.ailixiao.com -d monitor.ailixiao.com

# 完成,网站起来
```

## 维护规则

- 任何 nginx / supervisor / fail2ban 改动,**先改 deploy/ 下的版本,然后 cp 到系统目录**,确保 git 跟生产一致
- 任何系统配置改动**必须 commit + push**,否则换服务器恢复时会丢
- 改 deploy.sh / rollback.sh 后跑 `bash -n` 验证语法再 commit
