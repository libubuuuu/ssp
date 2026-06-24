---
name: SSP dict.get 默认 0 / 空字符串反模式(钱/积分关键路径必须 raise)
description: dict.get("key", 0) / dict.get("key", "") 在涉及钱、积分、退款、扣费的 fallback 路径上是反模式 — 用户钱被静默吃掉,必须爆炸式失败
type: feedback
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---
# 反模式

```python
# ❌ 反模式
seg_credits = TIER_CREDITS.get(plan_item.get("tier"), 0)
await _refund_partial(job, idx, seg_credits)  # tier 缺失 → 退 0 → 用户钱被吃

# ❌ 反模式
user_balance = settings.get("balance", 0)
charge_user(user_balance - cost)  # balance 字段不存在 → 按 0 算 → 计费错误

# ❌ 反模式
api_key = config.get("FAL_KEY", "")
fal_client.call(key=api_key)  # key 缺失 → 空字符串调 fal → 失败但说不清原因
```

# Why this is dangerous

`dict.get("key", default)` 的语义是"找不到就用默认值"。但在以下场景默认值掩盖了真问题:

1. **钱 / 积分计算**:fallback 0 = 用户钱被吃 / 多扣 / 少退,**财务损失 + 投诉**
2. **API 鉴权**:fallback 空字符串 = 调用看似成功实际是匿名请求,**安全风险**
3. **业务关键标识**:fallback `"unknown"` 或空 = 错误的归类、错误的路由、错误的退款链路

**核心问题**:`dict.get` 用默认值是"我不在乎这个 key 缺没缺"的语义。**钱和用户体验相关的代码不能"我不在乎"**。

# 应对原则

**第 1 类:确实可能缺失,缺失时业务定义清晰** → 用 `dict.get` + **明确的语义默认值**

```python
# ✅ OK:可选字段,缺失就用 None,后面有 if None 分支
thumbnail = plan_item.get("thumbnail_url")  # None 是合法值
if thumbnail:
    show(thumbnail)
```

**第 2 类:钱 / 积分 / 用户体验关键路径** → 用 `dict[key]` 或 `dict.get(key) or raise` **爆炸式失败**

```python
# ✅ 推荐:KeyError 直接抛
seg_credits = SEGMENT_CREDITS  # 直接用常量,不读 plan
await _refund_partial(job, idx, seg_credits)

# ✅ 也可:显式 raise
tier = plan_item.get("tier")
if tier is None:
    raise ValueError(f"plan_item 缺 tier 字段:idx={idx}")
seg_credits = TIER_CREDITS[tier]  # KeyError 也比静默 0 好
```

**第 3 类:跨版本兼容场景** → 显式分支 + 日志告警

```python
# ✅ 兼容老数据,但不静默
tier = plan_item.get("tier")
if tier:
    seg_credits = LEGACY_TIER_CREDITS[tier]  # 老 job
elif "input_seconds" in plan_item:
    seg_credits = SEGMENT_CREDITS  # 新 job
else:
    log_error(f"plan_item 数据异常:无 tier 也无 input_seconds, idx={idx}")
    raise ValueError("plan_item schema 异常")
```

# 实战记录

**第 1 次:2026-05-10 commit 3 砍单档,L1028 退款 bug**

原代码:
```python
seg_credits = TIER_CREDITS.get(plan_item.get("tier"), 0)
```

砍单档删 plan_item.tier 字段后:
- 老 job(已终态):有 tier,fallback 不触发,行为不变
- 新 job(commit 3 之后):无 tier,**fallback 静默退 0** → 失败段不退款 → 用户钱被吃

verify 时间点:贴 processor.py 8 块改后版本前,主动逐块读代码捋退款语义,发现这条隐性 bug。修复 = 改成 `seg_credits = SEGMENT_CREDITS`。

**意外收获**:commit 3 不仅砍单档,还修了一个"砍单档之前就存在但被 tier 字段掩盖"的早期遗留 bug。如果不主动读 L1028 退款逻辑,deploy 后用户失败任务退 0 投诉时才发现,损失更大。

# How to apply

- code review 看到 `dict.get(..., 0)` / `dict.get(..., "")` / `dict.get(..., None)` 涉及钱、积分、API key、归类标识 → **追问"缺失场景的业务语义"**
- 写代码遇到 fallback 选择 → **先问"缺失时该不该业务异常"**;答"该" → raise / 直接 `dict[key]`;答"不该,缺失就当某值" → 那个值是"业务定义的合法值",不是数字 0
- 跟 `feedback_ssp_verify_before_delete` 配对:**删字段前 verify 读者,读字段时不写静默默认值** — 一对工程纪律,前者防"删了下游炸",后者防"下游静默继续"
