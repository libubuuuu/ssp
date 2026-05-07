---
name: pixverse-swap 必须 720p 输出
description: fal pixverse-swap 调用必须强制 720p 输入,不准跑 1080p 让 $0.20→$0.40 翻倍
type: feedback
originSessionId: d8a42b7d-70f3-43b8-93d8-3466f6b2b64f
---
调用 `fal-ai/pixverse/swap` 时,**必须先把输入视频 downscale 到 720p**(等比缩放 + pad 黑边)。绝不允许直接传 1080p 输入。

**Why:** fal pixverse 按输入分辨率计费 — 720p $0.20/5s,1080p $0.40/5s。用户实测发现段视频成本混杂(部分 $0.20,部分 $0.40),原因是参考视频原画 1080p 直接喂给 pixverse。用户铁律:成本必须锁死 $0.20/5s,**不准跑 $0.40 的**。

**How to apply:**
- 任何切段 / 上传视频给 pixverse-swap 之前,必须 ffmpeg `-vf scale=W:H` 强制 720p
- 9:16 → `720:1280` / 16:9 → `1280:720` / 1:1 → `720:720`
- 用 `force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2` 防比例变形
- 检查代码:`/root/ssp/backend/app/api/jobs.py` 的 `_slice_video_by_scenes` 已实现(commit 8f173f5)
- 同样规则适用于 oral.py / 其他 pixverse-swap 调用点 — 任何新增调用前先确保输入 720p
