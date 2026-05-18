---
name: SSP 端点能力 verify 必须 link 到官方文档(本项目代码不可作"已验证"二手证据)
description: 对照"同项目代码"verify 不等于对照"官方 API 文档"verify。本项目自己的 docstring/注释/调用代码可能继承误解,作为"已验证"证据视为无效
type: feedback
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---
# 反模式

> "对照同项目其他模块代码 verify 不等于对照官方文档 verify。"

涉及外部第三方 API(fal / 阿里云 / 腾讯云 / OpenAI)的能力判断,如果"已验证"标签 link 到的是**本项目内部代码**(其他模块的 docstring / 注释 / 调用例),不是 link 到**第三方官方文档/官方 API spec/真实测试输出**,这条 verify 视为无效。

## Why

外部第三方 API 的"能力定位"是**事实信息**,事实只有官方源是权威的。本项目内部代码记录的是**项目作者当时对 API 的理解** —— 这个理解可能从写代码那天就是错的,而错误会被后续读代码的人继承(因为大家都信任"项目内已经在用,应该没问题")。

跟 `feedback_ssp_self_audit_same_standard` 第 8 次实战的"已验证标签必须 link 到具体证据"是同源原则,但本条更具体:**指定哪些证据有效**。

| 证据类型 | 是否可作"已验证"link | 原因 |
|---------|---------------------|-----|
| 官方文档 URL + verbatim 引用 | ✓ | 第一手权威 |
| 官方 GitHub repo 的 example code | ✓ | 第一手权威 |
| 真实 probe 输出(自己跑 + 真钱) | ✓ | 第一手实测 |
| 第三方 API 错误返回 verbatim | ✓ | 第一手实测 |
| **本项目内部代码 / 注释 / docstring** | **✗** | 二手,可能继承误解 |
| **同事/AI 回忆** | **✗** | 二手,可能记忆漂移 |
| WebSearch summary(无原文 quote) | ⚠️ | 仅作探路,不能作终验 |

# 第 1 次实战(2026-05-10):commit 3+4 全工作建立在 V1 docstring 误解之上

## 时间线 + 损失

- 2026-05-08 20:23 (commit dc64082):libubuuuu 写 V1 video_clone.py docstring 与 commit message:
  - `feat(video_clone): P216 接入 Seedance 2.0 r2v Fast — 真复刻视频镜头/动作`
  - `r2v Fast 直接 video-to-video,真复刻原视频动作/构图,只换场景/人物/产品`
  - `输出:替换场景/人物/产品后的视频(动作/构图复刻原视频)`
- 2026-05-10 14:02 (commit a733e50):V2 上线时复用 V1 端点假设
- 2026-05-10 19:00-22:00:commit 3(砍单档)+ commit 4(fal 字段名)deploy
- 2026-05-10 老板真测 2 次,2 次都"输出跟原视频构图无关 / 后半段漂回原视频画面"
- 2026-05-10 22:00 老板抓出根因:fal 文档 verbatim "reference materials = guidance for motion/composition/style, **NOT source material being modified**" — 完全跟 V1 docstring 反向

## 损失数据

- 老板真钱 ¥39.8(2 × 20 积分,已全额退)
- 老板时间 9+ 小时(commit 3+4 整夜)
- AI 时间 28 轮 / 9 小时
- ¥1.92 fal 计费 + 工程师误工

## 根因(对照本反模式)

commit 3+4 期间的 verify 全部 link 到本项目代码,**没有一处 link 到 fal 文档**:

| 时点 | "已验证"声明 | link 到的证据 | 实际是否官方源 |
|-----|------------|-------------|--------------|
| commit 4 前 V2 processor.py L243 | `已验证此字段名(同 r2v 端点系)` | ad_video_models.py:949 | ✗ 本项目代码 |
| commit 4 修复后 | `字段名 image_urls 对齐 V1 jobs.py:3337` | V1 jobs.py:3337 | ✗ 本项目代码(V1 同样基于 docstring 假设) |
| 整个 commit 3 砍单档讨论 | "V2 是视频复刻产品" | V1 docstring + 前端文案 | ✗ 全是项目内部叙述 |

**直到老板让我 WebFetch fal 文档才第一次拿到第一手证据**,而此时已经踩了 2 次真钱。

# 应对原则

## 第 1 类:涉及第三方 API 能力的"已验证"必须 link 第一手源

每一条"已验证"注释/commit message/code review pass,如果对象是第三方 API 的能力 / 字段名 / 参数行为,**必须**:

```
✓ # 出处:https://fal.ai/models/.../api(verbatim quote: "...")
✓ # 出处:probe 真测 2026-05-XX 输出 verbatim: "..."
✗ # 出处:本项目 path/to/file.py:line(无效)
✗ # 出处:同项目 X 模块已经在用(无效)
```

## 第 2 类:第三方 API 切换/选型必须先 WebFetch 文档原文

切换 fal 端点 / 阿里云模型 / 任何外部 API 之前,**强制**:

1. WebFetch 官方文档,读 description / use cases / input schema / output schema **verbatim**
2. 把 verbatim quote 直接放进 commit message 或 design doc
3. 用本项目 docstring 概括这个端点能力,**必须** quote 一段官方文档原文作脚注

## 第 3 类:本项目 docstring 的端点能力描述要带"出处"标注

写 V1/V2/V3 docstring 描述 fal 端点能力时,**强制**:

```python
"""V2 视频复刻 (Seedance 2.0 r2v Fast)

端点能力(出处:fal 官方文档 https://fal.ai/models/.../ verbatim 2026-05-10):
> "ByteDance's most advanced reference-to-video model"
> "reference materials serve as guidance for motion, composition, and style — 
>  not source material being modified"

→ 我们的产品定位:利用此端点的"参考生成"能力做 X
"""
```

不带"出处"标注的能力描述等于无效叙述。读者(包括 future Claude)看到这种描述要主动追问"出处呢?",而不是默认接受。

## 第 4 类:发现项目内 docstring 跟官方文档冲突 → 立即修

如果 verify 时发现项目内 docstring 跟官方文档冲突:

1. **不要**改官方文档(它是权威)
2. **不要**让项目代码继续用错误描述(其他人会继承)
3. **立即**单独 commit 修 docstring(类似 commit Z2 模式),link 官方原文
4. 顺手 grep 项目内**所有**引用同一端点的位置,sweep 一遍是否都基于错误描述

# How to apply

- 每次写 / 看 / review 涉及第三方 API 的"已验证"标签,验证 link 是不是第一手源
- 切换/选型 fal 端点 → WebFetch 文档原文 → 写进 commit message → 再写代码
- docstring 描述端点能力 → 必须 quote 官方文档原文,标"出处:URL verbatim YYYY-MM-DD"
- 发现项目内 docstring 跟官方文档冲突 → 单独 commit 修 + sweep 全项目同端点引用

# 跟其它 memory 的关系

- `feedback_ssp_self_audit_same_standard` 第 8 次实战:"已验证"标签必须 link 到具体证据 — 本 memory 是它的**子约束**(指定哪些证据有效)
- `feedback_ssp_fal_probe_first` :改 fal endpoint 前必须 probe — 本 memory 补"probe 之前还要先 WebFetch 文档"
- `feedback_ssp_no_pattern_match` :不脑补 — 本 memory 补"对'同项目其他模块这么写'也不能脑补它对"

# 根本认知

**项目内部代码的存在 ≠ 它对**。代码长期能跑、commit 已落地、有人在用 — 这些都不是"它对"的证据,只是"还没踩到"的证据。第一次有人按它的描述去做产品决策(比如 V2 产品定位),错误就会被放大成产品级 bug,然后真钱真用户真客诉。
