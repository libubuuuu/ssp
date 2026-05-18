---
name: SSP Pydantic 模型必须 extra="forbid",禁止静默接受未知字段
description: Pydantic 默认 extra="allow" 静默吞未知字段,删字段后老 client 仍传该字段会被静默接受,产品行为 = bug
type: feedback
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---
# 反模式

```python
# ❌ Pydantic 默认行为(extra="allow",静默吞未知字段)
class SegmentPlanItem(BaseModel):
    idx: int
    source_type: Literal["ai", "original"]

# 老 client 传 {"idx": 0, "source_type": "ai", "tier": "economy"} → tier 被静默吞
# 后端按"无 tier"逻辑跑,但用户期望"economy 档" → 行为不一致 → 客诉
```

# Why this is dangerous

1. **删字段后兼容性陷阱**:
   - 删 `tier` 字段 → 旧前端缓存仍带 tier 请求 → API 静默接受 → 用户看到的价格跟实际扣费不符
   - 错误日志无、alert 无、可观测性 0
   - **特征:静默 + 延迟**,deploy 后 6 个月才暴露,那时 db 数据全乱

2. **新增字段时悄悄破坏向后兼容**:
   - 新前端加 `feature_flag` 字段 → 老后端静默吞 → 新功能失效但前端以为生效
   - 同样静默 + 延迟暴露

3. **测试覆盖不到**:
   - 单元测试都用合法字段构造模型,extra 字段静默吞这条路径根本不会被测到
   - 没有 422 错误就没有 trigger,没有 trigger 就没有 alert

# 正确做法

## 核心原则:对内严,对外松

| 类型 | 用法 | 加 forbid 价值 | 决策 |
|------|------|---------------|------|
| Request 模型 | `Request(**user_input)` 解析用户输入 | ⭐⭐⭐ 防废弃字段静默接受 | ✅ 加 forbid |
| Response 模型 | `response.dict()` 输出给前端 | ⭐ forbid 只拦 input,Response 几乎不解析 | ❌ 不加 |

**为什么 Response 不加 forbid**:

未来后端新增字段(比如 `total_credits_charged_with_promo`),Response 模型还没同步:
- **加 forbid**:任何 `Response(**old_dict)` round-trip 解析旧数据都会炸 → **阻断 schema 演化**
- **不加 forbid**:旧字段被忽略,老 Response 模型能解析新数据 → 兼容性好

HTTP API 设计成熟原则,反 Postel 法则:
> Be conservative in what you send(Response 模型精简稳定),
> liberal in what you accept(但接受新字段时不炸)。

**Request 加 forbid + Response 不加 = 对内严防输入污染,对外松留演化空间**。

例外:如果某 Response 类未来需要 round-trip(`Response(**data)` 反解析),那**单独**加 forbid。默认不加。

## 实现方式

**所有 Request 类 BaseModel 必须加 `model_config = {"extra": "forbid"}`**:

```python
# ✅ Request 模型(用户输入)
class CreateRequest(BaseModel):
    model_config = {"extra": "forbid"}  # 拒绝未知字段
    type: Literal["single", "ultimate"]
    segments: List[SegmentPlanItem]
    # ...


# ✅ Request 内部嵌套模型(用户输入的一部分)
class SegmentPlanItem(BaseModel):
    model_config = {"extra": "forbid"}
    idx: int
    source_type: Literal["ai", "original"]


# ❌ Response 模型默认不加(留 schema 演化空间)
class CreateResponse(BaseModel):
    # 不加 model_config,默认 extra="allow"
    job_id: str
    # ...
```

效果:
- 老 client 传废弃字段 → API 直接 422,错误日志可见、可追溯
- 强制前后端同步更新
- 配套加 422 测试 case 锁住"安全网不能被悄悄拆掉"

# 何时 NOT 加 forbid

按"对内严对外松"原则:
- **Response 模型默认不加**(留 schema 演化空间)
- **内部数据 transfer 模型默认不加**(纯 backend 不暴露给前端 / 第三方)

例外:Response 模型未来需要 round-trip(用 Response 类反解析数据)→ 单独加 forbid + 注释说明

# 实战记录

**第 1 次:2026-05-10 commit 3 砍单档,verify 出无 forbid 配置**

场景:删 `SegmentChoice.allowed_tiers` + `SegmentPlanItem.tier` 字段。

我贴改后预览给用户审时,在拍板问题里说"verify 过 Pydantic 不会报错——SegmentPlanItem 没有 extra='forbid' 配置,所以多/少字段都不报"。

用户产品 owner 视角立刻识别这是雷:
- 老前端缓存仍传 `{"tier": "economy"}` → API 静默接受
- 用户期望"经济档 15 积分",后端按单档算 20 积分
- 用户客诉"乱扣钱",追溯极难

修复:commit 3 范围内补丁——12 个 BaseModel 中 **6 个 Request 类加 forbid**(SegmentPlanItem / ImageRef / EstimateRequest / CreateRequest / PreviewSegmentsRequest / CheckDurationRequest),6 个 Response 类不加(SegmentChoice / EstimateResponse / CreateResponse / PreviewSegmentsResponse / CheckDurationResponse / TrimCandidate),加 422 测试 case + 对照测试。

注:SegmentChoice 一开始我误归 Request(凭印象判"前后端 round-trip"),grep 验证后(L149 定义 / L225 PreviewSegmentsResponse 字段 / L476 后端构造,**无任何 SegmentChoice(\*\*data) round-trip**)修正为 Response。Request 用的是 SegmentPlanItem 不是 SegmentChoice — 两个 model 字段不同。

**关键认知 1**:写代码时"verify 过 Pydantic 不会报错"是个假阳性安全感——我以为是 feature(灵活兼容),用户视角是 bug(静默错误)。Pydantic **默认行为不安全**,要显式配置才安全。

**关键认知 2**:第一版我建议"全部 12 个加 forbid",用户产品 owner 视角立刻指出 Response 加 forbid 会**阻断 schema 演化**(round-trip 解析旧数据炸),修正为"对内严对外松"。**一刀切的"默认全加"看似稳健,实际是过度严格反而埋下另一种雷**——用工具时要分清场景。

# How to apply

- code review 看到 Request 类 `class XXXRequest(BaseModel)` → **追问"有没有 extra='forbid'"**(Response 类不必)
- 写新 Request BaseModel 默认加 `model_config = {"extra": "forbid"}`;Response BaseModel 默认不加
- 删字段时 grep 整个项目 Request 类 BaseModel,确认都有 forbid;没有的话**这次 commit 内补齐**(否则就是给 deploy 后的客诉埋雷)
- 配套 422 测试:每个 forbid Request 模型至少 1 个"传未知字段必 422"测试,锁住安全网

跟 `feedback_ssp_dict_get_default_zero_anti_pattern` / `feedback_ssp_verify_before_delete` 配对:
- `dict.get` 默认 0:读字段不写静默默认值
- 删字段 verify:删字段前 verify 所有读者
- Pydantic forbid:接收输入不允许静默多字段
- **三条都属于"静默错误反模式"**,产品级雷
