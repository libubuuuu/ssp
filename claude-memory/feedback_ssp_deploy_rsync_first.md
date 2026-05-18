---
name: ssp-deploy-rsync-first
description: "SSP deploy.sh frontend 不 rsync,改完 /root/ssp 必须先 rsync 到 /opt/ssp 再跑 deploy.sh,否则 build 的是 /opt 的旧代码"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 7c096b06-9598-4023-b283-d95e15842516
---

`/root/deploy.sh frontend` 只做 `cd /opt/ssp/frontend && rm -rf .next && npm run build` 然后蓝绿切换 — **不**做 `/root/ssp` → `/opt/ssp` 的 rsync。

所以改完前端代码后,正确顺序是:

1. Edit 改 `/root/ssp/frontend/...`(不是 /opt,见 [[ssp-edit-root-not-opt]])
2. `cd /root/ssp/frontend && npm run build`(本地 verify,见 [[ssp-frontend-verify]])
3. `git add + commit`(/root/ssp 是 git 工作树)
4. **`rsync -av /root/ssp/frontend/src/path/to/file /opt/ssp/frontend/src/path/to/file`**(单文件或单目录 source,见 [[ssp-rsync-safety]])
5. **`chown ssp-app:ssp-app /opt/ssp/frontend/src/path/to/file`**(见 [[ssp-deploy-chown]])
6. `bash /root/deploy.sh frontend`(此时 /opt build 才是新代码)
7. 按 nginx frontend 端口验证 active program(见 [[ssp-deploy-frontend-program]])

**Why**:2026-05-12 改卡片 CSS 时第二轮编辑只动 /root,deploy.sh 跑了完整蓝绿切换,但 build 的还是 /opt 的旧 .tsx,playwright metric 反查发现 cell 尺寸没变才意识到。第一轮碰巧 /opt 已被前序会话同步过所以没暴露。

**How to apply**:任何前端代码改动 → commit 后立刻 rsync /root→/opt + chown,然后才 deploy.sh。或者改 deploy.sh 把 rsync 步骤加进去(更彻底,但要等用户同意改部署脚本)。

**自查**:deploy 后 `diff /root/ssp/frontend/src/<file> /opt/ssp/frontend/src/<file>` 应为空。若有差异 = rsync 漏了。
