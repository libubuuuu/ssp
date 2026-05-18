---
name: SSP 字段/常量/函数删除前必须 verify 下游所有读者
description: 先删再补 = 生产 bug 温床。删任何符号前必须 grep 全代码库,verify 所有读者,再决定删/保留/改值
type: feedback
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---
# 规则

删任何**字段、常量、函数、表列**前,**必须**先做这 3 件事:

1. **grep 全代码库**找出所有读者(后端 services / api / 测试 / 前端 / 文档 / SQL / log 解析)
2. **逐一 verify** 每个读者真的不依赖这个符号(读了就抛?读了赋默认值?读了用于关键计算?)
3. **拿不准就保留 + 改值,而不是删**:字段保留风险 = 0、工作量 = 0;真没人读再下个 commit 删

# Why

- "先删再补"是生产 bug 温床:下游真依赖,deploy 之后任务炸,debug 反向追溯成本远高于多保留一个字段
- 已踩(2026-05-10 commit 3 计划阶段):我打算删 segments_plan 里的 `input_seconds` 字段,自己也说"可能要保留"——意味着没 verify 完 processor.py 是否还在读。用户拦下,改成"保留字段名 + 动态算值"
- 同性质教训:memory `feedback_ssp_rollback_full_commit` — 单边 rollback / 单边删字段都是"小步快跑"心态导致的隐性失误

# How to apply

**正确流程**(用 commit 3 块 6 举例):

```bash
# 第 1 步:grep 找读者
grep -rn "input_seconds" backend/app/ frontend/src/ backend/tests/ docs/

# 第 2 步:逐一看每处怎么读
# - processor.py:读了用来算 fal duration → 真依赖
# - 测试:assert input_seconds == X → 测试挂在这字段上
# - 前端:展示给用户 → UI 依赖

# 第 3 步:做决定
# - 真依赖 → 字段保留,值改成新逻辑(本次:input_seconds = back["duration"])
# - 全员可改 → 改 + 同 commit 内更新所有读者
# - 全员不读 → 安全删
```

**两类常见判断错误**:

1. **"我自己写的,我知道没人读"**:错。半年后的自己 / AI 都不是同一个,grep 比记忆可信
2. **"删了再补就行"**:错。生产环境部署后再补,中间窗口期 = 生产 bug。删字段必须保证 deploy 那一刻所有读者已迁好

**适用范围**:
- Python 函数 / 常量 / 类属性
- Pydantic 字段
- DB 表列(更严:涉及 alembic migration,删错要写 downgrade)
- API response 字段(前端可能依赖)
- 配置项 / 环境变量(运维可能依赖)

# 例外

只有以下两种情况可以"删了再补":
1. 完全私有的内部辅助函数,grep 全代码库 0 处引用
2. 立即同 commit 内把所有读者也改了(如本 commit 内同时删读者代码)

# 实战记录

**第 1 次实战胜利:2026-05-10 commit 3 砍单档**

场景:计划删 `SegmentChoice` Pydantic 模型的 `allowed_tiers` 字段(L154)。

应用本原则前我已经贴出 6 块改动给用户审。**写完本 memory 后立刻应用原则**,在贴 split.py 之前 grep 全代码库 verify `allowed_tiers` 所有读者,揪出 `video_clone_v2.py:L479`:

```python
segments = [
    SegmentChoice(
        idx=p["idx"], start=p["start"], duration=p["duration"],
        thumbnail_url=None,
        allowed_tiers=p["allowed_tiers"],  # ← 漏块!
    ) for p in plan
]
```

L479 不是"删除点",是**透传点** — 把后端 plan 的 allowed_tiers 字段塞进 Pydantic 模型。块 1 删模型字段后,这处透传不删的后果:Pydantic ValidationError → preview-segments 端点 500 → 用户上传视频后预览瘫痪。

补进同 commit 后救一次 deploy 灾难。**原则验证有效**:
- 不 grep 单靠"我自己写的我知道"会漏(我自己也漏)
- 透传 / 重新打包数据 这种"非删除"读法 grep 不到字段名以外关键词
- "字段名" + "字段值的源" 双向 grep 才能覆盖

**第 2 次实战记录:2026-05-10 commit 3 verify `input_seconds` 字段**

场景:打算删 segments_plan 里的 `input_seconds` 字段(块 6)。

应用本原则 grep + 调用链分析:
- `processor.py:L192` 有 `def _fal_duration_for_input(input_seconds: float)` — 参数名巧合
- 但真实调用方 `L235 _fal_duration_for_input(input_duration_sec)`,实参 `input_duration_sec` 来自 `L309 prepared_dur = await _ffprobe_duration(prepared)` — **ffprobe 实测,不是从 plan dict 取**
- 全代码库无人读 `plan_item["input_seconds"]`,字段在 backend 是真死字段

**判断结果**:verify 完确认能删,但**仍选择保留**。

理由不是"工作量大",而是**commit 边界纪律**:
- commit 3 是"砍单档",不是"清理死字段"
- 混入额外清理 → 模糊 commit 边界 → 出 bug 时回退范围大、review 难度高
- 跟 `feedback_ssp_rollback_full_commit` 同性质:小步快跑 vs 一次大改的工程纪律取舍
- 留给 commit 6: cleanup 专项,跟 docs 死字段一起清

**核心原则更新**:**verify 完不一定要立刻删,verify 是为了"知道能不能删";删的时机要服务 commit 边界**。

两次实战的差异:
- 第 1 次(allowed_tiers 透传):**必删** — 不删本 commit deploy 直接 500
- 第 2 次(input_seconds 字段):**可留** — 留也不影响功能,服从边界纪律

