---
name: project-ssp-video-general-pipeline-locked
description: SSP AI爆款视频 _run_script_to_video_job 当前稳定架构锁定，任意一处回退都会破坏音频或视频质量
metadata: 
  node_type: memory
  type: project
  originSessionId: d2d8c3c9-760a-4cdb-b6f9-be30f908d3bb
---

# AI爆款视频生成 Pipeline 锁定（2026-05-18）

**最新 commit: `bc45254`，已验证可用。以下任何一处不能回退。**

## 锁定架构：6 个关键点

### 1. TTS 必须串行先跑（不能改回 create_task 并行）
```python
# ✅ 正确：await 串行，拿到 audio_url 再继续
_tts_audio_url: str | None = None
if enable_voice:
    _r = await _fal.run_async("fal-ai/elevenlabs/tts/multilingual-v2", ...)
    _tts_audio_url = ...

# ❌ 错误：create_task 并行，audio_url 还没有就提交了 Seedance
_tts_concurrent_task = asyncio.create_task(_concurrent_tts())
```

### 2. Seedance `generate_audio: True`（不能改回 False）
```python
args = {
    ...
    "generate_audio": True,   # ✅ 环境音 + TTS口播
    # "generate_audio": False,  ❌ 无声，需要后期 ffmpeg 合并
}
```

### 3. TTS audio_url 传给 Seedance `audio_urls`（不能删）
```python
if tts_audio_url:
    args["audio_urls"] = [tts_audio_url]   # ✅ Seedance 原生口播
```

### 4. 无 ffmpeg 音频合并块（不能加回来）
ffmpeg 下载视频+音频 → 合并 → 再上传的整块代码已删除。
Seedance 直接出带声音的视频，不需要后期合并。

### 5. Seedance prompt 动作描述置顶（不能改回图片优先）
```python
# ✅ 正确：动作描述第一句
prompt = (
    f"Generate a {req_dur}-second continuous video with the following actions: "
    f"{_scene_descriptions}. "
    f"{model_line}{portrait_line}"
    f"@Image1 is the reference for the model's appearance (face, body type). "
    ...
)

# ❌ 错误：图片锁定优先
# "@Image1 defines the EXACT visual appearance... CRITICAL: Strictly match..."
# "Ignore any color words in the description..."
```

### 6. task 拆分纯时长（不能加回场景边界判断）
```python
# ✅ 正确：只按 MAX_DUR 拆
elif cur["total_dur"] + dur > _batch_max:

# ❌ 错误：加了 _scene_changed 导致"公园跑道"≠"同一跑道"过度拆分
# elif _scene_changed or cur["total_dur"] + dur > _batch_max:
```

## Pipeline 完整顺序
```
TTS (await) → audio_url
    ↓
场景图并发生成（与portrait同时）
    ↓
Seedance(image_urls + audio_urls + generate_audio=True) × N tasks 串行
    ↓
ffmpeg concat（多 task 拼接，保留音轨）
    ↓
fal_upload → video_url
    ↓
upscale（bytedance-upscaler，target_resolution）
    ↓
最终 video_url_out
```

**Why:** 2026-05-18 反复踩坑后锁定。TTS并行→ffmpeg合并的旧架构已废弃，Seedance原生口播质量更好且省一次下载/合并/上传。

**How to apply:** 每次改 `_run_script_to_video_job` 前先读本条目，确认 6 个关键点没有被回退。
