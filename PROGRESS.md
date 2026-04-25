# 进度日志

## 2026-04-25
✅ 装好 Claude Code(在服务器上)
✅ 把 handoff.md 复制成 CLAUDE.md
✅ git init + 第一次 commit
🔄 正在做:邮箱验证码登录前端 UI
   - 改 src/app/auth/page.tsx 加 tab
   - 新建 src/app/auth/forgot-password/page.tsx

## 2026-04-26

延续昨天的"正在做"。今日在分支 `feat/auth-email-code-ui` 上 4 个 commit:

| commit | 说明 |
|---|---|
| `8fec010 fix(auth)` | 修复 page.tsx 44-56 行登录后 admin 跳转的 3 层嵌套复制粘贴块,合并成干净的 if/else |
| `b07e3ff feat(auth)` | 加邮箱验证码登录 tab(密码/验证码/注册三选一),接 `/api/auth/send-code` + `/api/auth/login-by-code`,60s 倒计时,i18n 字典补 8 个新 key(zh/en),ESLint+tsc 对修改文件全绿 |
| `ff1c71f chore(deploy)` | `/root/{deploy,rollback}.sh` 游离仓库外 → 迁进 `ssp/scripts/` + `/root/` 下 symlink 兼容(cron/文档零影响)|
| `6c2b674 fix(deploy)` | rollback.sh 蓝绿回滚原本只切 backend 端口,会让前后端版本错位 → 补 frontend 端口 sed 替换,与 deploy.sh 对齐 |
| `e0ac929 feat(auth)` | forgot-password 从 stub(setTimeout 假装成功)改为真实接 `/api/auth/send-code`(purpose=reset)+ `/api/auth/reset-password-by-code`,三步流程 request→verify→success,60s 倒计时,18 个新 i18n key(`auth.forgot.*`,zh/en 对齐),修掉旧文件 line 24 用未导入 `t()` 的 tsc 错误。tsc/eslint 对修改文件全绿。未浏览器实测(生产禁 dev + frontend 仍 death-loop) |

⏳ **未推:** 5 commit 留在本地。push 阻塞两件事:
1. `.git/config` 明文 PAT 未轮换(CLAUDE.md 已记)
2. `main` 上还压着 18 个旧 commit 同样未推

### 已知 TODO(下次会话挑)

- [x] **forgot-password 是 stub** — 已闭环(commit `e0ac929`):接 `/api/auth/send-code`(purpose=reset)+ `/api/auth/reset-password-by-code`,三步流程 + i18n 化。tsc/eslint 对修改文件全绿。**未浏览器实测**(生产禁 dev + :3000 仍 death-loop)
- [ ] **auth/page.tsx 旧文案** — 部分仍走旧的 `lang==="en"?X:Y` inline ternary,新加的全走 `t()`。等做专项 i18n 重构统一收口
- [ ] **tsc 既有错误未清** — `npx tsc --noEmit` 报 14+ 个既有错误:forgot-password / digital-human / multi-reference / merchant/products/new / video/clone / video/editor / video/replace 等多个 page 使用 `t` 但未 import;`i18n/{zh,en}.ts` 第 337 行起 profile 块的 `topupCredits` / `saveChanges` / `confirmChange` 重复 3 次。CI 估计没跑 tsc 或没失败门禁。下次:`fix: i18n 重复键 + 前端组件 t 导入修齐`
- [ ] **ESLint 全仓库扫描** — 本次只扫了 auth + i18n 三个文件。全仓库 sweep 待办
- [ ] **`ssp-frontend.service` 死循环 11 万次** — PID 2764261 是手动起的 next-server,占着 :3000,systemd 起不来,journal 在飞速写。需破坏性 op 决策(kill PID + systemctl restart)。属运维操作不属代码改动
- [ ] **GitHub PAT 轮换 + push** — `.git/config` 内含明文 PAT,且 `/root/ssp.bak.*` 三份历史快照里也有,需要先在 GitHub 撤销旧 PAT、改成 deploy key (SSH) 后再 push,否则任何 push 都把 PAT 留在远端日志里

## 决策记录

- 2026-04-25:决定继续在生产服务器开发(短期方案,中期改本地+git)
- 2026-04-25:决定用 Resend 而非 SMTP。理由:免费额度够、国际送达稳
- 2026-04-26:`/root/{deploy,rollback}.sh` 迁进 `ssp/scripts/` + symlink 兼容。理由:游离脚本无 git 跟踪 = 无审计、无回滚、无 review,与企业级标准冲突;symlink 保证 cron / 手敲命令的旧路径不变,零运维风险
- 2026-04-26:auth/page.tsx 中**新加**的文案全部走 `t()`,**旧**的 `lang==="en"?X:Y` 不动。理由:在不扩大 commit 范围的前提下,新代码符合 i18n 约定;旧代码改造留专项重构 commit 一次性收口,避免本次 feat commit 被 i18n refactor 噪音淹没
- 2026-04-26:fix(auth) 重复粘贴块单独成 commit 不混进 feat。理由:bug 修复与新功能解耦,git blame / revert / cherry-pick 都干净
