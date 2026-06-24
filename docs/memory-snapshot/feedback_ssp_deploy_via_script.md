---
name: SSP deploy 规范脚本两条原则(走脚本 + 走前 verify 设计假设)
description: ① 不准手动 rsync(防 archive 钩子失效);② 走脚本前 verify 脚本设计假设是否符合当前工作流(脚本是历史决策的产物,工作流变了脚本可能过时)
type: feedback
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---
# 第 1 条原则:deploy 一律走规范脚本,不准手动 rsync

deploy 一律走 `/root/ssp/deploy/p221-a2-deploy.sh`(或同级 deploy 脚本),**不准为了快手动 rsync /root → /opt**。

# Why

- 规范 deploy 脚本里有自动 archive 钩子(`cp -a /opt/ssp /root/.p221-a2-prod-archive-{TS}`)
- 手动 rsync 跳过 archive → 配套 rollback 脚本(`p221-a2-deploy.sh.rollback`)依赖 archive 目录,**没 archive 就没法用**
- 已踩(2026-05-10 commit fcb0789):为了快手动 rsync backend + frontend,出事时发现只能走 `git checkout HEAD~1 -- file` + 重 build 凑合 rollback,规范路径用不了
- "今天就这一次手动"的心态,因为产品 prod 出事是低概率事件,真出事时 archive 不存在等于裸奔

## 应对(第 1 条)

deploy 流程默认:

```
bash /root/ssp/deploy/p221-a2-deploy.sh
# 该脚本应该负责:
# 1. pre-flight (0 in-flight V2 jobs / git status clean)
# 2. archive /opt → /root/.p221-a2-prod-archive-{TS}
# 3. rsync /root → /opt + chown ssp-app
# 4. frontend npm run build (如有前端改动)
# 5. restart 正确的 program (memory: 用 nginx port 判 active)
# 6. health check
# 7. 输出 rollback 命令(包含 archive 路径)
```

**例外**:确实是单文件紧急热修(< 5 行)+ 已经做过同等改动 deploy + 用户明确同意省 archive,才能手动 rsync。否则一律走脚本。

如果脚本现状不全(比如缺 archive 钩子),先补脚本再 deploy,**不要绕过**。

# 第 2 条原则:走脚本前先 verify 脚本设计假设是否符合当前工作流

脚本是**历史决策的产物**。脚本设计时假设的工作流可能跟今天不一样。**走脚本前必须 verify 脚本里的关键决策(exclude / include / 跳过步骤)是否符合当前工作流**,不一致就先补脚本(单独 commit),再 deploy。

## Why

- 脚本写了之后随项目演化会过时:`exclude` 列表可能跟不上新加的目录、`build` 步骤假设可能跟不上新工具链、`chown` 用户可能跟不上权限调整
- 脚本默认通过(set -e 不报错)≠ 行为正确,可能"成功 deploy 了一个错的版本"
- 这种 bug **静默 + 延迟**:deploy 显示成功,prod 跑的实际是旧代码 / 半新半旧,跟 `feedback_ssp_pydantic_extra_forbid` 的"静默错误"反模式同性质

## 如何 verify 设计假设

走脚本前**至少 grep 这些关键词**,确认每条都符合当前工作流:

```bash
grep -nE "exclude=|include=|skip|跳过|不带" deploy.sh
```

逐条问:
- **`--exclude=X`**:这次 deploy 的工作流真的不需要同步 X 吗?(eg `.next/` exclude 当年是"prod 不 build",今天可能"prod 端 build")
- **跳过步骤注释**:这次工作流的前置条件 / 数据状态跟脚本假设一致吗?
- **写死的路径 / 用户名**:迁移后还对吗?
- **chown 用户**:权限模型变了没?

任一不一致 → **单独 commit 补脚本 + dry-run 验证 + 用户审 + 跑**。不准在 deploy 当下临时改。

## 实战记录

**第 1 次实战(2026-05-10):commit 3 deploy 时揪出 .next/ exclude 盲点**

场景:commit 3 改完 backend + frontend + docs,准备走 `p221-a2-deploy.sh`。

走脚本前我读了脚本完整内容,发现 L172 主动 `--exclude='.next/'`,注释写"prod .next 是用户离线 build 好的,保留"。

**对照当前工作流**:今晚我在 prod 服务器 root 用户跑了 `npm run build`,新 `.next` 在 `/root/ssp/frontend/.next`(BUILD_ID `V68NCAAC7rrUh0ySJ-24e`)。如果按脚本 exclude 跑:
- `frontend/src` 同步过去
- `.next` 不带 → /opt 还是 8 小时前的旧 BUILD_ID `eX3RisqvGGKF9OqC7wWwi`
- restart frontend 跑的还是旧代码 → **commit 3 的 12 类前端改动等于白改**

报告用户后,用户拍板:
- 单独 Commit X 修脚本(删 `--exclude='.next/'`)
- 然后 Commit Y commit 3 主体

**关键认知**:**脚本通过(exit 0)不等于行为正确**。今晚我差点直接 `bash p221-a2-deploy.sh`,脚本会成功完成,但 prod 跑的是错的 frontend。揪出这点是因为强制读了脚本 + 对照当前工作流,不是因为脚本本身报错。

# How to apply 总结

- deploy 前两步:**先验脚本(第 2 条)→ 不一致就先补脚本 + 单独 commit → 再走脚本(第 1 条)**
- 不准为了"今天就这一次"绕过任一条原则
- 两条原则配对:第 1 条防 deploy 工程纪律破坏,第 2 条防"走对脚本但走错版本"
