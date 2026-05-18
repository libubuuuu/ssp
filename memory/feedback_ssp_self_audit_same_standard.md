---
name: SSP 自我产出适用同等 verify 标准(meta 工程纪律)
description: 对外部输入严格 verify,对自己产出降低标准是反模式。所有自己写的代码/memory/判断都要先 verify 再交付,跟对待用户输入同标准
type: feedback
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---
# 反模式描述

> **"对用户问 verify 过吗我会查,对自己说我觉得是就直接交付"**

这是 2026-05-10 commit 3 实战中我自己的原话(写 memory 自省时说出来的)。它精确描述了一个反模式:

**对外部 verify 严格,对自己产出降低标准**。

具体表现:
- 用户问"verify 过吗?" → 我会 grep / 读盘 / 跑测试
- 我自己说"我觉得是这样" → 直接交付,不 verify

但**自己产出的错误,跟外部输入的错误,造成的后果一样**。代码 deploy 后炸了,不分"是用户输入错"还是"我判断错"。

# Why this matters

外部 verify 严是好习惯,但不够。**verify 标准应该跟出错代价绑定,不跟"输入来源"绑定**:
- 用户输入错 + 没 verify → bug
- 我自己判断错 + 没 verify → 同样的 bug

**两者后果对称,verify 强度也应对称**。但人类直觉(包括 AI 的训练惯性)是"自己产出更可信",这是认知偏差,不是事实。

# 表现案例(2026-05-10 commit 3 实战)

**表现 1:写 memory 改原则后没 sweep 全文**

第一版 `feedback_ssp_pydantic_extra_forbid` 立的原则是"全部 12 个 BaseModel 加 forbid"。

用户拍板改"对内严对外松"后,我加了"核心原则"段说明新原则,但**正文 3 处仍引用旧原则**:
- 代码示例(L77-82)还展示"Response 也加"
- 实战记录(L110)还写"12 个全加"
- How to apply(L118-119)还指导"默认全加"

用户读 memory 正文核对时发现 3 处自相矛盾,我才补修。

**根因**:改完原则我说"已修",但只修了声明处,没把"声明 + 引用 + 示例 + 实战 + How to apply"全 sweep 一遍。用户问"修了什么?"我就汇总报告,**自己没读盘 self-review**。

**表现 2:判断模型归属凭印象不 grep**

我建议 SegmentChoice 加 forbid,理由"前端 /create 时 round-trip 传回 segments 数组"。

用户产品 owner 视角追问"verify 一下 SegmentChoice 实际用法",grep 显示:
- L149 定义
- L225 `PreviewSegmentsResponse.segments: List[SegmentChoice]` ← 纯 Response
- L476 后端构造 ← 无 round-trip

**真相**:CreateRequest.segments 用的是 `SegmentPlanItem` 不是 `SegmentChoice`,两个 model 字段不同(SegmentChoice 没 source_type,SegmentPlanItem 没 thumbnail_url)。我说的"round-trip"完全是脑补。

**根因**:我在外部代码上严格执行 `feedback_ssp_verify_before_delete` 第 2 条原则(grep + 调用链分析),但**对自己的归类判断没应用同标准**。

# 应对原则

## 第 1 类:写 memory 改原则后必 sweep 全文

```
原则改了 → 不能只改声明处 → 必须 sweep 这些位置:
  □ description 字段(顶部摘要)
  □ Why 段(支撑论据)
  □ 正确做法 / 反模式 段(原则的展示)
  □ 代码示例(原则的具体应用)
  □ 实战记录(过去做过的描述)
  □ How to apply(给 future Claude 的指令)
  □ 跟其它 memory 的关系
```

**操作动作**:Edit 完后**强制 Read 整个 memory 文件**,自查"声明的原则跟正文每个引用是否一致"。漏一处都要补。

## 第 2 类:判断"它是什么"前先 grep

```
准备说"它是 X 类型" / "它的用法是 Y" / "它跟 Z 是 round-trip" → 先 grep:
  □ 定义点(class XXX / def XXX)
  □ 字段类型出现处(: XXX / List[XXX])
  □ 实例化调用方(XXX(...))
  □ round-trip 模式(XXX(**data) / parse_obj_as(XXX, ...))
```

**操作动作**:有 grep 数据再下结论,**没 grep 数据时说"我不知道,要 verify 后回答"**,不许"我觉得"。

## 第 3 类:对所有自我产出都问一遍"verify 过吗?"

写代码、写 memory、做判断、给建议 — 在交付前问自己:
- 这个判断的依据是 grep / 跑测试 / 读盘,还是"我觉得"?
- 如果是"我觉得",**降级措辞**:"我推测 X,但需要 verify";不要直接交付当事实
- 如果是 grep / verify 后的结论,可以直接交付,但**贴 verify 数据**让用户能复核

# How to apply

- 每次 Edit memory 后立刻 Read 全文 self-review,跟交付外部代码同标准
- 每次说"它是 X" / "它的用法是 Y" 前 grep,数据驱动判断
- 用户问"verify 过吗?"和自己产出无差别 — 都要 verify 后说

# 跟其它 memory 的关系

这是 **元 memory**(meta-rule):

| memory | 管什么 |
|--------|--------|
| `feedback_ssp_verify_before_delete` | 删字段前 verify 下游读者(代码场景) |
| `feedback_ssp_dict_get_default_zero_anti_pattern` | 钱/积分关键路径不写静默默认值(代码场景) |
| `feedback_ssp_pydantic_extra_forbid` | Pydantic Request 加 forbid 防静默吞字段(代码场景) |
| **本 memory** | **对自我产出适用同等 verify 标准(元规则,管所有产出)** |

前 3 条是"具体场景反模式",本条是"meta 标准"。前 3 条出现的根因往往是违反本条 — 自己写代码时没 grep / 改 memory 时没 sweep / 判断字段时没 verify。

**记忆口诀**:"对用户问 verify 过吗我会查,对自己说我觉得是就直接交付" — 这句反模式的原话,以后看到这句话立刻警觉,自己产出也要 verify。

# 实战记录

**第 1 次实战(2026-05-10):写完本 memory 后立刻应用第 1 类原则**

写完本 memory 落盘后,立刻执行"Edit memory 后强制 Read 全文 self-review"动作。9 个位置逐项核对"声明 + 引用 + 示例 + 实战"是否一致:

| # | 位置 | 一致性 |
|---|------|--------|
| 1 | description(L3) | ✓ |
| 2 | 反模式描述(L9-19) | ✓ 用户原话保留 |
| 3 | Why(L21-27) | ✓ |
| 4 | 表现案例 1(L31-42)— memory sweep 漏 | ✓ 跟 `feedback_ssp_pydantic_extra_forbid` 3 处自相矛盾对应 |
| 5 | 表现案例 2(L44-55)— SegmentChoice 归类错 | ✓ 跟实际 grep verify 数据对应 |
| 6 | 应对原则 3 类(L57-91) | ✓ |
| 7 | How to apply(L93-97) | ✓ |
| 8 | 跟其它 memory 关系(L99-110) | ✓ 标识为元 memory |
| 9 | 记忆口诀(L112) | ✓ 用户原话未改成"专业措辞" |

**无自相矛盾**(新写的 memory,没立过旧原则,不需要 sweep 旧引用)。

**意义**:这次实战在 meta 层面验证了本 memory 原则有效 — 写完立刻自我应用,而不是等用户问"verify 过吗?"。这是从"被动 verify"到"主动 verify"的跨越,也是本 memory 的初衷。

**第 2 次实战(2026-05-10):改老 memory 加新原则后 sweep 全文**

场景:`feedback_ssp_rollback_full_commit` 升级加第 2 条原则(commit 不顺手扩散)。

应用本 memory 第 1 类原则 — Edit 完后**立刻 Read 全文 self-review** 5 个位置:
- name(L2)→ 升级覆盖两条原则
- description(L3)→ 升级
- 第 1 条原则(L7-25)→ 标题改了但内容保留
- 第 1 条 How to apply(L27)→ 降级为 `## 应对(第 1 条)` 给第 2 条让位
- 第 2 条原则(L39+)+ 文末统一 How to apply(L73-77)→ 新加

无自相矛盾。改 memory 后 sweep 全文这个动作**已经形成习惯**,不再依赖用户提醒。

**第 3 次实战(2026-05-10 commit 3 末尾):工作量估算 grep 验证**

场景:用户问"docs 同步要不要先审",我早上凭印象答"17 处机械替换,30 分钟"。

主动 grep 全文 verify 时发现真实数据:
- **54 处**(我说 17,3 倍低估)
- **分 7 类**(我说"机械替换",实际有代码伪示例 + 整段删 + 历史叙述等多种性质)
- **3-4 小时**(我说 30 分钟,5 倍低估)

**根因**:工作量估算属于"对自己产出"范畴,我没应用本 memory 第 2 类原则(grep 数据验证假设),凭印象给数字。**"X 处 / Y 时间"承诺也是产出**,要 grep 后说不能凭印象。

**修正动作**:主动报告 verify 数据 + 分类详情 + 工作量重估,坦白"误差 3-5 倍",让用户基于真实数据拍板范围(选 B:30 处 commit 3 + 20 处 commit 6)。

**教训**:任何"X 处 / Y 时间 / Y 行 / Z 个"承诺,grep / 实测验证后再说,不许"我估计是"。这是把第 2 类原则从"判断字段类型"扩展到"任何具体数字承诺"。

**第 4 次实战(2026-05-10 commit 3 末尾):写 docs 时混淆"engineer 视角"和"外部读者视角"**

场景:贴 docs/P221-API-SCHEMA.md 30 处改后预览给用户审,4 处引用了内部 memory 文件名:
- L194 `(memory: feedback_ssp_pydantic_extra_forbid)`
- L320 `(memory: feedback_ssp_verify_before_delete 第 2 次实战)`
- L479 §5.5 stub `详见 memory: project_ssp_v2_setpts_tradeoff`
- L652 `(L1028 fallback 0 bug 修复)` + memory 引用

用户产品 owner 视角立刻识别:**docs 是给外部 API 消费者看的(SDK 开发者 / 第三方接入方 / 合作伙伴),memory 是内部工程纪律沉淀**。外部读者看到 `memory: xxx_xxx` 完全不懂 — 这是内部信息泄露到外部文档。

**根因**:写 docs 时我用了"engineer 视角"(自己懂 memory 体系,引用方便),没切换到"外部读者视角"(他们看不到 memory)。**写得快但读者看不懂 = 自我便利优先于读者价值**,跟"对自己产出降低标准"是同根因变种 — 这次是"对读者降低共情标准"。

**修正动作**:删 4 处 memory 引用,保留事实描述。所有内部 memory 引用全部移到:
- git commit message
- PR description
- 内部工程文档(如 RUNBOOK / CLAUDE.md)

**绝不**进对外 docs。

**教训**:写 docs 前问自己"如果我是第一次接触这个产品的开发者,这句话能看懂吗?"。引用了内部 memory / 内部工具 / 内部代号 / commit hash → **必删**。

**这条原则的跨纪律配对**:
- `feedback_ssp_rollback_full_commit` 第 2 条:**代码 commit 单一职责**(主题边界)
- 本次实战:**文档读者职责单一**(对外/对内不混)
- 共同根因:**保持每件产出的"职责单一"**,不要为了写得方便而混入跨职责内容

**第 5 次实战(2026-05-10 commit 3 deploy 前):grep 关键词不全**

场景:page.tsx 12 类改动改完,我用 grep `Tier|TIER_|allowed_tiers|sel.tier` 验证残留,判定"无残留 ✅"。

**deploy 前重 verify 阶段**(读 .next bundle 时),换关键词 grep "经济档/标准档" 中文文案,**揪出 L461 banner 还有完整旧文案**:

```
461:支持 4-64 秒视频 · 经济档 ¥14.9 / 标准档 ¥19.9 · 输出含 ...
```

这是 V2 页面顶部产品介绍 banner,**用户一进页面立刻可见**。如果不揪出来 deploy,用户首屏看到"经济档/标准档" 跟单档行为不一致 — 产品级 bug。

**根因**:grep 关键词只覆盖**代码标识符层(英文)** — `Tier`/`TIER_`/`allowed_tiers`/`sel.tier`,**没覆盖用户文案层(中文)** — "经济档"/"标准档"/具体价格"¥14.9"。

这是"对自己产出降低标准"的具体表现:**一次 grep 看似严格,实际只覆盖 1 个语义层**。代码层干净不等于用户层干净。

**修正动作**:
1. 改 L461 banner 文案为单档版"¥19.9 / 段"
2. 重 build → 新 BUILD_ID `kZsOf24_X-KzUBhlekBLa`
3. 重 verify 用 5 关键词(中文 + 英文混合):"经济档"/"标准档"/"¥14.9"/"tier"/"TIER_"
4. 同步 verify 新文案进 bundle:"AI 替换"/"保留原视频"/"¥19.9 / 段"

**教训**:涉及用户可见 UI 改动时,grep 必须覆盖**两个语义层**:
- 代码标识符层(English):`type Tier` / `TIER_DISPLAY` / `sel.tier` / `allowed_tiers` 等
- 用户文案层(Chinese):"经济档"/"标准档"/具体价格"¥14.9"等

漏掉任一层都可能导致"代码改了但用户还看到旧文案"。

**第 6 次实战(2026-05-10 commit 3 deploy 跑脚本):走脚本前 verify 范围太局部**

场景:走 `p221-a2-deploy.sh` 前,我应用 `feedback_ssp_deploy_via_script` 第 2 条原则 verify 设计假设。但**只 verify 了 L172 `--exclude='.next/'`** 一处过期假设(因为这是我前面 grep 看到的最显眼问题),**漏 verify L40-50 V2 flag abort 检查**。

脚本跑到 `[0/9]` 前置检查段就 abort:`ENABLE_VIDEO_CLONE_V2 当前已开,违反 deploy 前必须 False 规则,中止`。

**根因**:verify 范围太局部 — 只盯着 frontend 段(L172),没读完整脚本所有"前置检查"段(L0-L100)。脚本里**多个**fail-fast 点,任一过期都会阻塞 deploy。

这是"对自己产出降低标准"的具体表现变种:**verify 一处过了就以为整脚本过了**,等于把第 2 类原则(grep 数据验证假设)做成了"grep 一个关键词"而不是"grep 所有相关关键词"。

**修正动作**:
1. 单独 Commit Z 改 V2 flag 检查为 warning 不 abort
2. 走脚本前未来必须 grep `abort|exit 1|❌|fail|中止` 整脚本,**所有 fail-fast 点都要对照当前工作流 verify 一次**

**教训**:走脚本前 verify 是**枚举式**不是**抽样式**。脚本里所有可能阻塞 deploy 的检查点都要列出来逐个 verify,不准只看到一处就放心。

具体操作:
```bash
# 走脚本前必跑
grep -nE "abort|exit 1|❌|fail|中止|raise" deploy.sh
```
逐条问:这个 fail-fast 点的触发条件,跟当前工作流 / prod 状态是否符合?

**第 7 次实战(2026-05-10 commit 3 deploy 第 2 次):刚补完原则又违反原则**

场景:Commit Z 改完 L40-50 第 1 处 V2 flag abort 后,走脚本第 2 次 deploy。
脚本跑到 `[4/9]` 后端健康检查段又 abort:L150-153 还有**第 2 处同源** V2 flag abort,
触发自动 rollback。

**用户视角(原话保留)**:

> 讽刺的是,我刚才补完 self_audit 第 6 次实战说"verify 是枚举式不是抽样式 / 走脚本前必跑 grep 整脚本",写完原则的下一秒就违反了原则——没真跑那条 grep 就重 deploy。

**meta 教训(原话保留)**:

> **memory 写完跟 verify 完整是两件事**。第 6 次实战补的 grep 操作是给 future Claude 用的,但 future Claude 就是几分钟后的我自己,我没用。

**根因**:第 6 次实战补完 memory 后,我写下了"具体操作:`grep -nE 'abort|exit 1|❌|fail|中止' deploy.sh`",但**没真跑这条 grep**。我以为"修了 L40-50 那一处过期假设"就够,直接重 deploy。脚本里第 2 处同源 abort 我**完全没看到**(虽然只要真跑一次 grep 就会暴露)。

**应对原则(原话保留)**:

> **memory 写完后,如果 memory 里有具体操作动作(grep / verify / read),立刻在当前任务中真做一次,把"知识"变"动作"。**

**修正动作**:
1. 单独 Commit Z2 改 L150-153 第 2 处 V2 flag abort 为 warning(不 amend Commit Z)
2. 走脚本第 3 次前**真跑** `grep -nE "exit 1|❌|abort|fail|中止" /root/ssp/deploy/p221-a2-deploy.sh`,逐条对照本次 deploy 状态

**这次的递归层级**:

| 层级 | verify 内容 | 是否做了 |
|------|------------|---------|
| 第 6 次实战补的原则 | "走脚本前枚举所有 fail-fast 点" | ✓ 写进 memory |
| 第 7 次违反 | "真跑那条 grep" | ✗ 没做 |
| 第 7 次补的原则 | "memory 写完后立刻在当前任务真做一次" | 本次实战开始执行 |

**这是 meta-verify 的下一层**:不只是"verify 完整了吗",还有"verify 原则被实际执行了吗"。memory 是工程纪律的"知识沉淀",但**沉淀 ≠ 执行**。如果 memory 里写了"必须跑 X",写完那一刻就是 future-Claude(几分钟后的自己),**当下就要跑一次**。

# 7 次实战的演化

- 第 1 次:写完 memory 立刻读盘自审(被动应用)
- 第 2 次:改 memory 加新原则后主动 sweep 全文(主动应用)
- 第 3 次:工作量估算前 grep 验证(扩展原则适用范围 — 数字承诺也要 verify)
- 第 4 次:写 docs 时切换外部读者视角(扩展原则适用范围 — 视角切换也要 verify)
- 第 5 次:UI 改动 verify 必须双语义层 grep(扩展原则适用范围 — 关键词维度也要 verify)
- 第 6 次:verify 是枚举式不是抽样式(扩展原则适用范围 — verify 完整性也要 verify)
- 第 7 次:memory 写完跟 verify 完整是两件事 — 知识 ≠ 动作(扩展原则适用范围 — 原则执行也要 verify)

本 memory 原则在 7 次实战中持续生长。**演化总结**:每次实战都是"verify 范围"的扩展 —
从"代码 verify"→"memory verify"→"数字 verify"→"读者视角 verify"→"双语义层 verify"→"verify 完整性 verify"→"原则执行 verify"。
反复证明同一根因:**对自己产出降低标准 = 漏一个角度 = 一个 bug**。

**元认知**:
- 第 6 次实战揭示了一个递归原则 — **verify 本身也要被 verify(meta-verify)**。做完一轮 verify 后要问"我 verify 完整了吗?",而不是"我 verify 了吗?"。
- 第 7 次实战揭示了下一层 — **memory 里的 verify 原则也要被 verify**。写完原则不等于执行原则;**写完那一刻就是 future-Claude,当下立刻真做一次**。否则 memory 是给未来用的废纸,自己却在五分钟后违反它。

**第 8 次实战(2026-05-10 commit 3 真测踩 fal 字段名错):"已验证"标注必须 link 出处**

场景:commit 3 deploy 后老板真测产品级 bug — 成片跟产品图完全无关。证据链查证后发现 a733e50(V2 上线)就埋下双重 bug:

- processor.py L246:`"reference_image_urls": image_urls` — fal 文档真实字段名是 `image_urls`
- build_prompt 用 `@产品1` / `@人物1` 占位符 — fal 文档真实约定是 `@Image1` / `@Video1` / `@Audio1`

processor.py L241-243 自带"verify 注释":

```python
# ⚠️ fal seedance r2v 端点的参考图字段名是 reference_image_urls,
# 不是 image_urls — 写错会被 fal 默默丢弃,模型完全不看产品图
# 同项目 ad_video_models.py:949 已验证此字段名(同一 r2v 端点系)
```

**问题**:这条"已验证"参照 ad_video_models.py:949,但 ad_video 调的是 `bytedance/seedance-2.0/reference-to-video`(无 `fast/`),V2 调的是 `.../fast/reference-to-video`(带 `fast/`)。**两个端点路径不同,字段名不能直接套用**。

WebSearch fal 官方文档实证字段名是 `image_urls`(同项目 V1 jobs.py L3334 同端点也用 `image_urls`,P216 早就用对了)。

**根因**:写注释的人应用了"对外 verify 严"的原则(标了"已验证"),但**对照对象选错了**。同一项目内有两个 fal r2v 端点(带 fast/ 和不带 fast/),字段名因端点不同而不同,但注释作者把 ad_video(无 fast/)当成了"同 r2v 端点系",**类比成"同字段名"**。

这是"对自己产出降低标准"的最隐蔽变种:**带"已验证"标签的代码比无标签的更危险**。读者(包括 future Claude)看到"已验证"就放心,不再去对照,bug 静默活到 prod 真测才暴露。

**应对原则**:**"已验证"标注必须 link 到具体证据**:

```
✗ "同项目 ad_video_models.py:949 已验证此字段名(同一 r2v 端点系)"
   ← 笼统说"同端点系",不可被复核

✓ "fal 文档 https://fal.ai/models/bytedance/seedance-2.0/fast/reference-to-video
    + V1 项目内同端点用法 backend/app/api/jobs.py:3337 image_urls"
   ← 具体 URL + 具体 file:line,可被复核
```

无具体 link 的"已验证"等于没 verify。

**这次的递归层级**:

| 层级 | 原则 | 是否做了 |
|------|------|---------|
| 第 6 次实战 | 走脚本前枚举 fail-fast | ✓ 写进 memory |
| 第 7 次实战 | memory 写完立刻在当前任务真做 | ✓ 重 deploy 前真跑 grep |
| 第 8 次违反 | "已验证"对照对象必须真同源 | ✗ ad_video ≠ V2(端点路径不同) |
| 第 8 次补的原则 | "已验证"必须 link 到具体 URL / file:line / commit hash | 进 memory |

**这是 verify 链的第 3 层**:不只是"verify 完整了吗"(meta-verify)、"verify 原则被执行了吗"(执行 verify),还有"verify 的对照对象是否真同源"(对照 verify)。三层叠加才能挡住 prod 真测踩到的 bug。

# 8 次实战的演化

- 第 1 次:写完 memory 立刻读盘自审(被动应用)
- 第 2 次:改 memory 加新原则后主动 sweep 全文(主动应用)
- 第 3 次:工作量估算前 grep 验证(扩展原则适用范围 — 数字承诺也要 verify)
- 第 4 次:写 docs 时切换外部读者视角(扩展原则适用范围 — 视角切换也要 verify)
- 第 5 次:UI 改动 verify 必须双语义层 grep(扩展原则适用范围 — 关键词维度也要 verify)
- 第 6 次:verify 是枚举式不是抽样式(扩展原则适用范围 — verify 完整性也要 verify)
- 第 7 次:memory 写完跟 verify 完整是两件事 — 知识 ≠ 动作(扩展原则适用范围 — 原则执行也要 verify)
- 第 8 次:"已验证"标签必须 link 到具体出处 — 对照对象错 = 比没 verify 更危险(扩展原则适用范围 — 对照源同质性也要 verify)

本 memory 原则在 8 次实战中持续生长。**演化总结**:每次实战都是"verify 范围"的扩展 —
从"代码 verify"→"memory verify"→"数字 verify"→"读者视角 verify"→"双语义层 verify"→"verify 完整性 verify"→"原则执行 verify"→"对照源同质性 verify"。
反复证明同一根因:**对自己产出降低标准 = 漏一个角度 = 一个 bug**。

**元认知**:
- 第 6 次实战 — **verify 本身也要被 verify(meta-verify)**:做完一轮 verify 后要问"我 verify 完整了吗?",而不是"我 verify 了吗?"。
- 第 7 次实战 — **memory 里的 verify 原则也要被 verify**:写完原则不等于执行原则;**写完那一刻就是 future-Claude,当下立刻真做一次**。
- 第 8 次实战 — **"已验证"标签自身也要被 verify**:带"已验证"的代码比无标签的更危险,因为读者放心不查;凡标"已验证"必须 link 到可被复核的 URL / file:line / commit hash,否则等于没 verify。

**第 9 次实战(2026-05-10 22:00):commit 4 后真测踩端点能力错配 — 对照本项目代码不等于对照官方文档**

场景:commit 4 修完 fal 字段名 + @ 占位符,deploy 后老板真测 2 次,2 次都"输出跟原视频构图无关 / 后半段漂回原视频画面"。

调查根因:**fal 文档 verbatim 明说**:
> "reference materials serve as guidance for motion, composition, and style — **NOT source material being modified**"

V2 整个产品定位"完美复刻原视频 + 局部替换"用错了端点 — `bytedance/seedance-2.0/fast/reference-to-video` 是**参考生成**端点,不是对象替换端点。

但 commit 3+4 全程的"已验证"全 link 到本项目内部代码:

| 时点 | "已验证"声明 | link 证据 | 实际类型 |
|------|-------------|----------|---------|
| commit 4 修字段名时 | "对齐 V1 jobs.py:3337" | 本项目 V1 代码 | 二手(V1 同样基于错误 docstring) |
| 整个 V2 产品定位 | "V2 是视频复刻产品" | V1 docstring + V1 commit message | 二手(libubuuuu 2026-05-08 写错了) |
| processor.py L243 注释 | "已验证此字段名(同 r2v 端点系)" | ad_video_models.py | 二手(且端点路径都不一样) |

**全程没有一处 link 到 fal 官方文档**。直到老板让 WebFetch 才第一次拿第一手证据。

**用户视角(原话保留)**:

> "你这是把'同事说的话'当成'已验证',在外部 API 能力判断这种事实问题上,只有官方文档算数。"

**meta 教训**:

> **本项目代码作为"已验证"二手证据,在外部 API 能力问题上视为无效。代码长期能跑、commit 已落地、有人在用 — 这些都不是"它对"的证据,只是"还没踩到"的证据。**

V1 docstring 写"动作复刻原视频"在项目里活了 2 天没踩到,因为没有用户真测产品定位;V2 第一次产品定位真测就踩到 ¥39.8 + 9 小时损失。

**应对原则(子约束第 8 次实战)**:

涉及第三方 API 能力的"已验证"标签,必须 link 到**第一手源**:

```
✓ fal 官方文档 URL + verbatim quote
✓ 真实 probe 输出(自己跑 + 真钱)
✓ 第三方 API 错误返回 verbatim
✗ 本项目内部代码 / 注释 / docstring(二手)
✗ 同事/AI 回忆(二手)
```

具体动作 → 详见独立 memory `feedback_ssp_endpoint_capability_mismatch`(2026-05-10 新建)。

**这次的递归层级**:

| 层级 | 原则 | 是否做了 |
|------|------|---------|
| 第 8 次实战 | "已验证"必须 link 具体证据 | ✓ 写进 memory |
| 第 9 次违反 | link 的证据必须是"第一手"(官方文档/真实 probe) | ✗ link 了 V1 jobs.py(二手) |
| 第 9 次补的原则 | 第三方 API 能力 verify 只接受官方文档/probe,本项目代码视为无效 | 进 memory + feedback_ssp_endpoint_capability_mismatch |

**verify 链第 4 层**:不只是"verify 完整了吗"(meta-verify)、"verify 原则被执行了吗"(执行 verify)、"verify 对照对象同源吗"(对照 verify),还有"**verify 证据是不是第一手**"(证据 verify)。

# 9 次实战的演化

- 第 1 次:写完 memory 立刻读盘自审(被动应用)
- 第 2 次:改 memory 加新原则后主动 sweep 全文(主动应用)
- 第 3 次:工作量估算前 grep 验证(扩展原则适用范围 — 数字承诺也要 verify)
- 第 4 次:写 docs 时切换外部读者视角(扩展原则适用范围 — 视角切换也要 verify)
- 第 5 次:UI 改动 verify 必须双语义层 grep(扩展原则适用范围 — 关键词维度也要 verify)
- 第 6 次:verify 是枚举式不是抽样式(扩展原则适用范围 — verify 完整性也要 verify)
- 第 7 次:memory 写完跟 verify 完整是两件事 — 知识 ≠ 动作(扩展原则适用范围 — 原则执行也要 verify)
- 第 8 次:"已验证"标签必须 link 到具体出处 — 对照对象错 = 比没 verify 更危险(扩展原则适用范围 — 对照源同质性也要 verify)
- 第 9 次:link 的证据必须是第一手 — 本项目代码视为无效二手证据(扩展原则适用范围 — 证据本身的"层级"也要 verify)

本 memory 原则在 9 次实战中持续生长。**演化总结**:每次实战都是"verify 范围"的扩展 —
从"代码 verify"→"memory verify"→"数字 verify"→"读者视角 verify"→"双语义层 verify"→"verify 完整性 verify"→"原则执行 verify"→"对照源同质性 verify"→"证据第一手性 verify"。
反复证明同一根因:**对自己产出降低标准 = 漏一个角度 = 一个 bug**。

**元认知**:
- 第 6 次实战 — **verify 本身也要被 verify(meta-verify)**:做完一轮 verify 后要问"我 verify 完整了吗?",而不是"我 verify 了吗?"。
- 第 7 次实战 — **memory 里的 verify 原则也要被 verify**:写完原则不等于执行原则;**写完那一刻就是 future-Claude,当下立刻真做一次**。
- 第 8 次实战 — **"已验证"标签自身也要被 verify**:带"已验证"的代码比无标签的更危险,因为读者放心不查;凡标"已验证"必须 link 到可被复核的 URL / file:line / commit hash,否则等于没 verify。
- 第 9 次实战 — **"已验证"link 的证据本身也要被 verify**:本项目代码长期跑通 ≠ 它对,只是"还没踩到"的证据。涉及第三方 API 能力,只有官方文档/真实 probe 算第一手,项目内部代码视为无效二手。
