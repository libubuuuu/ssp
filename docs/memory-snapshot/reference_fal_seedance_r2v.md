---
name: SSP fal seedance fast/r2v 端点参数实测
description: bytedance/seedance-2.0/fast/reference-to-video 字段名 + duration 枚举 + 端点能力定位(2026-05-10 真钱踩 + WebFetch 文档双重 verify)
type: reference
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---

# ⚠️ 2026-05-10 重要修正

本 memory 早期版本声称"字段名是 reference_image_urls",**那是错的**。实测 + fal 官方文档双重 verify,正确字段名是 **`image_urls`**(同 `video_urls` / `audio_urls` 命名规则)。

错误来源:对照 `ad_video_models.py`(端点 `bytedance/seedance-2.0/reference-to-video`,**无 fast/**)的字段名,误以为 fast 版同字段。实际 fast 版字段名跟 V1 `api/jobs.py:3337` 一致是 `image_urls`,跟非 fast 版的 `reference_image_urls` 不同。

**根因**:同 `feedback_ssp_endpoint_capability_mismatch` 第 1 次实战 — 用本项目代码作"已验证"证据。

# 端点

`bytedance/seedance-2.0/fast/reference-to-video`

**端点能力定位(出处:fal 官方文档 verbatim 2026-05-10 WebFetch)**:

> "ByteDance's most advanced reference-to-video model"
> "generates cinematic 720p video with synchronized audio"
> "reference materials serve as **guidance for motion, composition, and style** — **not source material being modified**"
> 示例 prompt: `"A surfer rides a massive wave at golden hour. @Image1 sets the scene."`

→ 这是**参考生成**端点,不是**对象替换**端点。reference video / image 都是"灵感参考",不是"被修改的源"。

→ 想做"保留原视频 + 只换裤子"这种局部替换 → **本端点做不到**,需切 `fal-ai/wan-vace-14b/inpainting`(VACE inpainting + SAM2 mask)。

# 字段名(WebFetch + V1 jobs.py + 真实 V2-FAL 调用三重 verify)

```python
arguments = {
    "video_urls": [video_url],          # ⚠️ 是 video_urls(数组),不是 video_url
    "image_urls": image_urls,           # ⚠️ 不是 reference_image_urls(那是非 fast 版的字段)
    "audio_urls": [],
    "prompt": prompt_compiled,          # 占位符 @Image1 / @Video1 / @Audio1(不是中文 @产品/@人物)
    "resolution": "480p",               # 或 720p
    "duration": "8",                    # str,接受枚举
    "aspect_ratio": "9:16",
    "generate_audio": False,
    "enable_safety_checker": True,
    "seed": 42,
}
```

# prompt 占位符约定(WebFetch 2026-05-10 verbatim)

> "Reference assets in your prompt using `@Image1`, `@Video1`, `@Audio1`, etc."

中文 `@产品` / `@人物` / `@场景` / `@图` fal **不识别**,被当普通文本忽略。

V2 commit 4(2026-05-10):build_prompt 实现"中文 @ → @Image{N} 透明转换",前端 UI 保持中文友好。

# duration 字段(实测枚举)

2026-05-10 真钱 probe 结果:

```
接受值:'auto', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15'
拒绝值:'2', '3'(返 schema error,fal 不计费)
```

**最低 duration = 4 秒**。任何 < 4s 的 input 都需要 padding 到 4.1s+ 才能调。

# input/output 时长行为(2026-05-10 老板真测验证)

- input video duration 跟 output duration 即使一致,**也不保证保留原视频内容** —— 端点本身就是参考生成,不复刻原帧
- 真测 1(2026-05-10 19:29):input 8.033s 720x1280@30fps 241 帧 → output 7.917s 496x864@24fps **190 帧** —— 帧数完全不同,证实是新生成
- 真测 2(2026-05-10 20:23):同 input → 同 output 规格,跟真测 1 像素维度一致

**output 固定**:496x864 @ 24fps,其中 480p 模式下 9:16 比例

# 输出成本(2026-05-10 实测)

- 8s × 9:16 × 720p Fast:**$0.962**(actual_cost_usd 来自 _estimate_cost_usd fallback,fal 实际 cost dashboard 待对账)
- 老板真测 2 次共 $1.924

# 历史误用记录

| 时点 | 误用 | 后果 |
|-----|------|-----|
| commit a733e50(2026-05-10 14:02 V2 上线) | 字段名写 `reference_image_urls` | fal 默默丢图,所有 V2 任务不看产品图 |
| commit 6a085d0(2026-05-10 commit 4) | 字段名修为 `image_urls` ✓ | fal 真看图但端点本身是参考生成,不解决产品定位错配 |
| 老板真测 2 次(2026-05-10 19:29 + 20:23) | 期望"局部替换裤子"但端点是参考生成 | 输出后半段漂回原视频 + ¥39.8 损失(已退) |

# How to apply

- 调用前 verify 字段名:**`image_urls`**(WebFetch + V1 jobs.py:3337 + 真实 V2-FAL log 三重源)
- 占位符:**`@Image1` / `@Video1` / `@Audio1`**,中文 `@产品` 等需后端转换
- duration ≥ 4 字符串
- **不要把这个端点当"对象替换"用** —— 用户期望"保留原视频 + 只换 X"时切 wan-vace-14b inpainting + SAM2

# 跟其它 memory 的关系

- `feedback_ssp_endpoint_capability_mismatch`:本 memory 是该反模式的"实战教训库存"
- `feedback_ssp_self_audit_same_standard` 第 8 次实战:"已验证"标签 link 出处,本 memory 修正过一版错误"已验证"
- `project_ssp_p221_v2` commit 5 候补:基于本 memory 真相,V2 切 VACE inpainting 路线
