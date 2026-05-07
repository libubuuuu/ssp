---
name: AI 带货视频时长必须严格等于用户选择
description: /ad-video 用户选 10s 必须出 10s,不能 7+7=14s。VLM 写超字时 worker 必须按 scene.duration_sec 截断
type: feedback
originSessionId: b9112ba0-66ed-4dfa-ba4d-391805539fe7
---
`/ad-video` 用户选时长 = 实际输出时长(用户铁律,曾因不准发火)。

**Why:** 用户在前端明确选 5/8/10/12/30/60... 期望严格相等。VLM 不听字数限制是常态,
worker 必须 enforce。之前选 10s 出 14s,选 8s 出 12s,被用户多次抱怨。

**How to apply:**
- P149 多段路径(`jobs.py` seg_speeches 构建)必须按 `scene.duration_sec × 字符速率` 截断 speech
  - 中文 elevenlabs multilingual-v2 速率 ≈ 5 字/秒
  - 英文 ≈ 14 字符/秒
- P118 单段路径已截断,别动
- Seedance 多段路径(>12s)不需要截断(Seedance 严格按 duration 参数出片)
- 截断要 log_warning 报警,以后能看哪些段超字便于优化 VLM prompt
