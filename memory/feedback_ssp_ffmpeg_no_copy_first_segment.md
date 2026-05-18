---
name: SSP ffmpeg 截短段头不能用 -c copy
description: split 出的段首帧未必是 IDR 关键帧,-c copy 会产出无头损坏文件,fal 解码失败
type: feedback
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---
# 规则

`ffmpeg -i seg.mp4 -ss 0 -t N -c copy out.mp4` 在 split 出的段上**不安全**。

# Why

- `-c copy` 是 stream copy,要求容器首帧是关键帧。
- ffmpeg `split` 生成的段(尤其是视频开头段)首帧未必是 IDR,只是个 P/B 帧。
- 用 `-ss 0 -t N -c copy` 截短:ffmpeg 看不到 IDR 但仍可能输出一个不完整的容器,**退出码 0**(它觉得自己干完了),但产出文件:
  - 大小异常小(33KB 而非正常 ~180KB)
  - 解码器读到坏数据,fal 返 `video_read_error`
- 已踩:SSP V2 P221 上线时段 0(视频开头 [0,8])必现 fal 拒,段 1+ 偶发,定位用了 1 小时。
- 因为 ffmpeg 没报错,Python 这边 `try / except RuntimeError` 永远不触发,损坏文件直接扔下游。
- **2026-05-13 再踩同雷**:V2 `split_input_video` 16s job seg_0 视频流 start_time=3.722s duration=4.4s(应该 0s 起 8s),seg_1 start_time=0.622s duration=7.4s。fal 收到"音频 8s + 视频 3.7s 起"的怪文件,生成结果前几秒视觉接近原视频(用户怒)。修法:`split_input_video` 不再 try `-c copy`,直接强制重编码 + `-avoid_negative_ts make_zero -reset_timestamps 1`。重读这条 memory 前我嘴硬说"已经并行切片送 fal 没问题",事实是切片本身坏的 — 这是 [[feedback_ssp_no_pattern_match]] 的二次违反。

# How to apply

需要从段头截短(或任何怀疑首帧不是 IDR 的段):

```bash
# ❌ 不要
ffmpeg -i seg.mp4 -ss 0 -t 4 -c copy out.mp4

# ✅ 用重编码,每段 ~1s 成本换 100% 输出可解码
ffmpeg -i seg.mp4 -ss 0 -t 4 \
    -c:v libx264 -preset fast -crf 20 -pix_fmt yuv420p \
    -c:a aac out.mp4
```

`-pix_fmt yuv420p` 必加:某些 fal/CDN 解码器不吃 yuv444p / yuv422p 输入。

只有从已知 IDR 边界(比如 `ffmpeg -i src.mp4 -f segment -reset_timestamps 1` 出来的 keyframe-aligned 段)才能 `-c copy`,且仅在不再二次截短的情况下。

# 排查口诀

V2 / 任何视频 pipeline 出 `video_read_error` / fal 拒读 / 段输出大小异常小 → **第一查 ffmpeg 调用链有没有 `-c copy` 截短段头**。
