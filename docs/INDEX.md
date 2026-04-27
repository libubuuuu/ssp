# 项目文档索引

> 新会话上车先读这个,看哪些跟当前任务相关再展开。

## 顶级(必读)

- [`../CLAUDE.md`](../CLAUDE.md) — 项目长期档案 / 技术栈 / 路线图 / 协作约定
- [`../RUNBOOK.md`](../RUNBOOK.md) — 故障应急手册(supervisor / 蓝绿 / 回滚 / 备份恢复 / 密钥轮换 / 常见故障)
- [`../PROGRESS.md`](../PROGRESS.md) — 进度日记(只留最近 5 续,完整历史见下方 archive)
- [`PROGRESS-archive/2026-04.md`](PROGRESS-archive/2026-04.md) — 2026-04 归档(P0-P9 + BUG-1/2 + 隐藏雷 1-3 + P8 阶段 1-2 + 通宵交付 + 灾备 + AIOps,1440 行)

## 用户操作 SOP(他要做的事)

| 文档 | 用途 | 用户工作量 |
|---|---|---|
| [SENTRY-SETUP.md](SENTRY-SETUP.md) | 错误监控接入(后端代码已就绪等 DSN) | 5 分钟 |
| [CLOUDFLARE-SETUP.md](CLOUDFLARE-SETUP.md) | CDN + 真用户 IP 透传 | 15 分钟 + 24h DNS |
| [REDIS-SETUP.md](REDIS-SETUP.md) | 限流 Redis 后端启用(可选,多 worker 时必要) | 15 分钟 |
| [DISASTER-RECOVERY.md](DISASTER-RECOVERY.md) | 全新服务器 30-60 分钟恢复手册 | 灾备时按步骤跑 |

## 工程内部参考

| 文档 | 主题 |
|---|---|
| [P8-COOKIE-MIGRATION.md](P8-COOKIE-MIGRATION.md) | httpOnly Cookie 阶段 3 清理路线图(30 天后) |
| [COVERAGE-2026-04-27.md](COVERAGE-2026-04-27.md) | 测试覆盖率快照 + 后续补齐建议(整体 46%,核心 auth/billing 89-91%) |

## 按场景导航

### "我要查 bug"
- 故障应急 → `RUNBOOK.md`
- 历史决策 / 修过的洞 → `PROGRESS.md` grep 关键词
- 当前已知差距 → `CLAUDE.md` 的"已知差距"章节

### "我要做新功能"
- 路线图 → `CLAUDE.md` 的 Phase 1-5
- 编码约定 → `CLAUDE.md` 的"协作约定"
- 跑测试 → `CLAUDE.md` 的"怎么跑测试"

### "服务器出问题了"
- `RUNBOOK.md` → 按章节定位:服务进程 / 蓝绿部署 / 回滚 / 数据库 / 证书 / 密钥 / 常见故障
- `monitor.ailixiao.com` 看面板;watchdog 推微信看告警

### "用户报了具体 bug"
- 启动咒语 `/root/start-claude.txt`
- AIOps 闭环:admin Banner 🩺 一键复制 JSON → 粘贴给我精准定位
- 历史诊断快照 → `https://admin.ailixiao.com/admin/diagnose`

### "新接外部服务"
- Sentry / Cloudflare / Redis 各自 SOP 文档(见上"用户操作 SOP")
- 模板:`docs/<NAME>-SETUP.md` 写 5-7 步 + 验证 + 不要做的事

## 文档维护约定

- 用户操作 SOP 必须含"不要做的事"清单(防误操作)
- 每份 SOP 必须含验证步骤("怎么知道接通了")
- 凭据 / 密钥永远不写在 docs(只描述获取方法)
- 更新主索引(本文件)时把新增 docs 也加进表
