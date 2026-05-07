---
name: pixverse object 模式不支持穿戴类产品
description: pixverse-swap mode="object" 只能 swap 独立物体(手机/包/道具),不能处理穿戴在身上的服装/塑身衣/胸罩/内衣 — 会报 "Could not generate mask for swap"
type: feedback
originSessionId: b9112ba0-66ed-4dfa-ba4d-391805539fe7
---
`fal-ai/pixverse/swap` 的 `mode="object"` 只对**独立可识别物体**有效:smartphone, prop, handheld item, mug 等。

**不支持**:
- 穿戴类(束腰/塑身衣/胸罩/内衣/T恤/裤子)
- 配饰(项链/耳环 — 太小)
- 跟身体融合的物品

**实证(2026-05-07,job 7608dd56):**
用户测试塑身衣类产品,4 段全 fail:
```
Could not generate mask for swap.
input_value_error
```

**Why:** pixverse 内部用语义分割检测物体边界,服装跟身体的边界模糊,生成不了独立 mask。

**How to apply:**
- 不要给穿戴类/服装类用户提议"2 步 pixverse(object → person)"方案
- 这类产品只能用 mode="person"(把人+衣服整体替换)
- 凭空触碰问题靠"GPT-2 把产品放在和 driving 同位置"路线 — 不可靠但唯一选择
- 如果用户产品是手持类(包/手机/水杯),才考虑 object 模式

**Update 2026-05-07:** 用户敲"两个模型都要可以用,我测试一下哪个好",前端 /video/replicate 现保留 3 个引擎选项:
- `pixverse-swap`(单步 mode=person,推荐穿戴类)
- `pixverse-2step`(object→person,**穿戴类必失败**,但用户要保留作为非穿戴类时的选项)
- `kling-3-pro-i2v`(¥2.5/5s,不复刻动作,GPT-Image 2 出图)
对穿戴类用户仍要劝阻"别选 pixverse-2step",但不要从 UI 删除。
