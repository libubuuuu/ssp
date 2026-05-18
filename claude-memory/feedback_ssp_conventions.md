---
name: SSP 编码约定与禁忌
description: SSP 项目的编码约定(API_BASE 用 ??、i18n 文案、分支策略)与禁止操作(不提交敏感文件、不改 main、生产不跑 dev)
type: feedback
originSessionId: c9d691af-d261-494c-b8cb-27c9d6288ade
---
**编码约定(必须遵守):**
- `API_BASE` 用 `??` 而不是 `||`(避免空字符串/0 被覆盖)
- 文案一律走 i18n,不要硬编码中文
- 新功能开 git 分支,不直接改 main

**禁止操作:**
- 不要把 `.env` / `.env.enc` / `dev.db` 提交到 git
- 不要直接改 main 分支
- 不要在生产服务器上跑 `npm run dev`

**Why:** 用户在 2026-04-25 项目说明里明确列出。`??` vs `||` 是 JS 真值陷阱;硬编码中文会绕过 i18n 体系;生产 `npm run dev` 会占端口/暴露 source map。
**How to apply:** 写前端代码默认 `??`;遇到任何用户可见文案查 i18n 字典;动手前确认当前分支不是 main;commit 前检查 `git status` 排除敏感文件;生产环境只用 `npm run build` + `start` 或部署脚本。
