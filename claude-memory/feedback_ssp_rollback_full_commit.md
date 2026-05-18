---
name: SSP commit 边界纪律(rollback 整 commit + 不顺手扩散)
description: 两条 commit 边界纪律。① rollback 默认整 commit 回退不单边(防前后端版本错位);② commit 内不"顺手"做主题外改动(防边界模糊)。同根因:commit 单一职责
type: feedback
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---
# 第 1 条原则:rollback 默认走整 commit 回退,不准单边

deploy 出事要 rollback,**默认走"整 commit 回退"路径**:

```bash
git revert <bad_commit_sha> --no-edit
# 然后 deploy 一次新 commit (前后端同步回到上一个稳定版本)
```

**不准只 rollback backend 单文件 / 只 rollback frontend 单文件**,即使技术上行得通。

# Why

- 前后端版本不对齐 = 工程纪律破坏
- 当前版本工作时偶然没事(新老 backend 行为接近),但下次 deploy 时:
  - git history 看 `fcb0789` 已上线,但 prod 跑的实际是 backend=`a733e50`、frontend=`fcb0789` 的混搭
  - 新 deploy 要 diff 哪一版来生成 changelog → 错;改动 review 范围错;前后端 contract 漂移检测错
- "功能层面没问题"是临时假象,工程上是埋雷
- 紧急止血时单边 rollback 可作为"保命备选",**但事后必须立刻补整 commit 回退**,不能就这么留着混搭跑

## 应对(第 1 条)

- 用户报 bug 要 rollback → 先问 / 默认走整 commit revert(路径 3),不要主动建议单边 rollback(路径 1/2)
- 如果用户明确说"只回 backend / 只回 frontend",照做但要警告"这是临时止血,事后必须整 commit 同步"
- 整 commit revert 流程:
  ```
  git revert <bad_sha> --no-edit
  # 走规范 deploy 脚本(memory: feedback_ssp_deploy_via_script)
  # backend + frontend 同步部署
  ```
- 已踩教训背景:2026-05-10 deploy fcb0789 时,我列 rollback 预案"路径 1 / 路径 2 / 路径 3" 让用户挑,用户纠正:路径 1/2 只是紧急备选,默认必走路径 3

# 第 2 条原则:commit 内不"顺手"做主题外改动

工程师常见诱惑:**"反正都改这文件了,顺手把别的也改了"**。

这种"顺手"是 commit 边界破坏的开始。即使改动本身正确,也会让:
- **review 失焦**:reviewer 要在"主题改动"和"顺手改动"间切换语境,容易漏看
- **rollback 复杂**:出 bug 不知道是主题改动炸的还是顺手改动炸的,rollback 全掀掉损失大,部分 rollback 又违反第 1 条
- **问题归因模糊**:git blame 后追溯"为什么这行这么改"时,commit message 不会提"顺手"那部分,后人看不懂

## 应对原则

每次想"顺手"前问自己:**"这事跟当前 commit 主题是同一性质吗?"**

| 情况 | 决策 |
|------|------|
| 同性质(如改 pricing.py 同时清里面的 dead import) | ✅ 可以顺手 |
| 跨性质(如砍单档 commit 顺手调 UX 步骤编号) | ❌ 单独 commit |

判断"同性质"的标准:**改动是否服务于本 commit 的核心目标**。砍单档的核心目标是"删 tier 字段一条线",Step 编号重排是 UX 微调,跟 tier 无关 → 不同性质。

## 实战记录

**第 1 次:2026-05-10 commit 3 砍单档结尾**

我贴前端 page.tsx 改动时,在拍板问题里说"single 模式 Step 编号重排,要不要顺手把 Step 编号(Section title)调一下?"。

用户拒绝:
- commit 3 主体"砍单档"
- Step 编号是 UX 微调,不同性质
- 混合 commit = 模糊边界
- 创建 commit 3.5 / commit 4 专项做 UX 调整

**关键认知**:写代码 / 改架构时容易陷入"flow 状态",觉得"反正在改这块,多改一点不费事"。但 commit 是给 review 和 rollback 用的,不是给写代码的人方便用的。**写代码的便利性 < commit 可读性 + 可回退性**。

# How to apply

- 用户报 bug 要 rollback → 默认走整 commit revert(第 1 条)
- 写代码想"顺手改一下别的" → 停一下问"同性质吗?",不同性质就单独 commit(第 2 条)
- 两条都属于"commit 单一职责"的具体应用 — 一个管出问题怎么撤,一个管做完别越界
