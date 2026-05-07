---
name: pixverse person 模式特写镜头 mask 失败要降级
description: pixverse-swap mode="person" 在产品超近景/极特写镜头里因没清晰人脸会报 "Could not generate mask",不能让整 job 失败,要降级用上一步未换脸的视频
type: feedback
originSessionId: bf5e5bd4-8e8a-4d86-89e7-4176ad18cde7
---
`fal-ai/pixverse/swap` 的 `mode="person"` 在视频里**没清晰人脸 / 人脸太小 / 主体是产品特写**时也会报相同错误:
```
Could not generate mask for swap.
input_value_error
```

注意:这跟 `mode="object"` + 穿戴类是**不同 root cause**。穿戴类是物体边界融合;person 模式是脸找不到。

**Why:** /video/replicate 的 5 段镜头里通常有 1-2 段是产品 macro / extreme close-up(突出商品质感),那些段视频里没脸,pixverse 没东西可以贴。

**实证(2026-05-07,job 12e084e3 + ee449b63):**
- pixverse-2step,seg 2 完整出片 ✅
- seg 0/1 Step B mask 失败 → P173 降级用 Step A 视频 ✅
- seg 3 Step A object mask 失败 → P173 没兜 Step A,整 job 死 ❌

**修复(P173 → P177,2026-05-07,3 次失败才扫干净):**
- 教训:replicate 有 4 个独立 engine 路径(pixverse-swap / pixverse-2step / seedance-lite-i2v / catvton-pixverse → 委托 swap)。每条路独立 gather + raise。
- P173 只兜 pixverse-2step Step B mask;P174 加 Step A mask + ffmpeg 静态视频;P175 扩展到 2step 所有错误 + return_exceptions;P176 单步 swap 同样兜底;P177 seedance 同样兜底 + 把 `_replicate_frame_to_static_video` 提到 module level 共享
- 现在所有 engine 都是 4 层降级:正常 → 任何错降级到 GPT 帧静态视频 → ref 视频段(pixverse 才有)→ gather return_exceptions=True 保险
- **下次同类 bug 排查方法:先 grep `^async def _gen_videos_` + `gather` 看有几条独立路径,一次性扫所有,别只修撞到的那一条**

**How to apply:**
- 不要把"用户产品是穿戴类 → 别用 2step"和"特写镜头 mask 失败"混为一谈,前者用 pixverse-swap 单步,后者要走降级
- pixverse Step A / Step B 都可能报 `Could not generate mask` / `input_value_error`,**两层都要兜**
- 任何对 pixverse swap 的并发调度,gather 都得评估单段失败是否要拖死整体 — 默认应该 `return_exceptions=True` 或 try/except 内化
- 降级质量梯度:Step A+B 都成 > Step B 失败用 Step A > Step A 失败用静态帧 > 整 job 死
- 出片质量预期:Step A 失败那段会是静态画面(GPT 帧),用户能接受(总比无视频好);Step B 失败那段会保留 Step A 的脸(可能不是用户选的模特)
