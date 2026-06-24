---
name: SSP fal 端点切换前必须先 probe
description: 改 fal 端点 / 引擎前用真 KEY 实测 submit + result + NSFW,不要 deploy 后碰运气打地鼠
type: feedback
originSessionId: c9fa48e7-3011-420b-bf5c-0b9b4cc66943
---
**规则:** 改 oral / video 工作台的 fal 端点(切引擎、换 slug、改 args schema)前,
**必须**先用 backend 的 FAL_KEY 在临时 python 脚本里 submit + 等 result + 验证 NSFW 容忍度,
然后再 deploy。

**Why:** 2026-04-30 八十四续 P11-P15 连续 4 次 deploy 打地鼠,每次都被新 fal 错卡:
- P11 ltx 端点:slug 对了 但 hasattr poll bug 让 session 死循环
- P12 seedance/v2/pro:slug 已废,fal 返 "Path not found"
- P13 seedance-2.0/fast:slug 对了但 1080p resolution 不接(只接 480p/720p)
- P14 720p:slug + resolution 都对了,但被 NSFW 拒(content_policy_violation,内衣硬拒)
- P15 kling/o3/standard:实测 5 端点 NSFW 后才选对

每次 deploy 中断用户、退款、返工。**probe 一次 2-5 分钟,deploy 打地鼠每轮 10-30 分钟**。

**How to apply:** 改 oral.py / fal_service.py 的端点配置前,先跑这种脚本:
```python
import os, asyncio, fal_client
# FAL_KEY 从 supervisor backend 进程拉:
# PID=$(supervisorctl pid ssp-backend-blue/green)
# FAL_KEY=$(cat /proc/$PID/environ | tr '\0' '\n' | grep '^FAL_KEY=' | cut -d= -f2-)
async def probe(ep, args):
    h = await fal_client.submit_async(ep, arguments=args)
    rid = h.request_id
    for _ in range(180):  # 30 min cap
        await asyncio.sleep(10)
        s = await fal_client.status_async(ep, rid)
        if type(s).__name__ == 'Completed':
            return await fal_client.result_async(ep, rid)
asyncio.run(probe(...))
```

特别检查项:
1. **slug 路径**:fal 偶尔不带 `fal-ai/` 前缀(如 `bytedance/seedance-2.0/fast/...`),换端点必须 submit 试
2. **args schema**:duration / resolution / aspect_ratio 各 fal 端点接受集不同,422 schema 错就改
3. **NSFW 容忍**:用真用户的 vton 图(不是 demo 图)测,带货场景图常被 partner_validation_failed
4. **fal Status 判定**:`type(s).__name__ == 'Completed'`,不要 `hasattr(s,'status')`(永远 False)
5. **fake completed**:错的 slug submit 可能假成功返 request_id,但 result 阶段才暴露 path not found。**必须等到 result 才算 OK**
6. **⚠️ 必须看视频实际质量,不只是"submit OK + 出 video URL"**:2026-05-05 P115 踩坑 — Kling Avatar v2 通过 Flux Kontext reframe 调通,probe 显示 submit OK + 拿到 video URL,我立刻上线。但**实际视频质量被 reframe 弄坏**:产品弱化、背景被换工作室、嘴型夸张。用户骂死。
   probe 通过的标准必须是:**视频实际质量 ≥ 现状**。要 ffprobe + 抽帧 + 人眼对比关键画面(产品、背景、嘴型自然度)。光看 fal 返 200 OK 不算 probe 通过。
