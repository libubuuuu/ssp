---
name: SSP P221 视频复刻 V2 状态(2026-05-10 收工)
description: P221 上线 + 三个 prod bug 已修已部署 + duration 架构正解未部署的过渡状态
type: project
originSessionId: 0f0e5399-6131-4fec-8f2f-5a8bd385d8cb
---
# 当前生产状态(2026-05-10 收工)

P221 V2 视频复刻已上线 + commit a733e50 已 push origin/feat/auth-email-code-ui。

**三个 prod bug 已部署**(backend pid 1597223 在跑):
1. `/tmp/video_clone_v2_work` owner 修(chown ssp-app)
2. fal r2v 字段名 `image_urls` → `reference_image_urls`
3. 段截短 `-c copy` → libx264 重编码 + yuv420p

# 待 deploy 的改动(明天继续)

`video_clone_v2_pricing.py` + `video_clone_v2_processor.py` 改了但未 deploy:
- `TIER_INPUT_SECONDS` `{economy:2, standard:4}` → `{economy:8, standard:8}`
- `prepare_segment_input` 删截短逻辑,直接用 split 出的整段,只在 < 4s 时 padding 到 4.1s
- 新增 `_fal_duration_for_input(seconds) -> str`:把段实际秒数对齐到 fal 接受枚举 [4,15],call_fal_seedance 用它代替写死的 `FAL_OUTPUT_DURATION=8`

**Why 改这个**:fal probe 实测 input < output 触发 hallucinate,reference 漂移。input=output=8s 全程不漂(用户花 $1.06 实测)。

**还差**:
1. plan_segments_v2 重写 — 切片粒度按 tier 决定;economy 切 2s/段、standard 切 4s/段(用户原 prompt 要求;但 fal 最低 4 秒,economy 2s 走不通,实际方案是 8s/段)
2. processor 适配新切片(可能要 ultimate 一段视频拼成 N 次 fal 调用,而不是 2-3 次)
3. 测试更新(test_video_clone_v2_split.py 涉及 input_seconds 硬编码的)
4. 前端段卡片显示 / 总价计算 / trim 弹窗按新切片(也许不动,数据透传)
5. **价格不动**(用户明令)

# How to apply

明天会话上车:
- 读 `/root/ssp/backend/app/services/video_clone_v2_processor.py` 看当前已改但未部署的状态
- TIER_INPUT_SECONDS / prepare_segment_input / _fal_duration_for_input / call_fal_seedance 已改,plan_segments_v2 / split / 测试还没改
- deploy 前必须查 0 in-flight V2 + chown ssp-app + restart **frontend 的对就是 frontend 的、backend 的就是 backend 的**(memory 已有)
- `/root/ssp/probes/p221_duration_probe.py` 是已知能跑的 probe 模板(用 prod 已成功 job 的 fal storage URL),改改可复用,但要用户授权才能跑

# 残留 orphan job

今天 testing 中卡 processing 的 ac0388d2 / f1e6cb40 已通过 `_refund_full` 等价回收,db 已 failed,余额已退满。pending_refunds 和 credits_ledger 都 idempotent,不重退。`in-flight V2 jobs = 0`,蓝绿安全。

# 🆕 commit 4 hotfix 候补:V2 fal 字段名 + prompt 占位符(2026-05-10 真测发现)

commit 3 deploy 后老板真测踩到产品级 bug:成片完全跟产品图无关。根因不在 commit 3,**在 a733e50(P221 上线)就埋下,今晚正面踩到**。

## 双重 bug

### Bug 1:fal 字段名错(processor.py L244-246)

V2 用 `reference_image_urls`,fal 文档实证字段名是 `image_urls`。fal 静默丢弃未知字段 → 模型完全不看产品图。

### Bug 2:prompt @ 占位符约定不通

V2 build_prompt 拼成 `@产品1替换视频中的裤子(参考素材:@产品1)`,fal 文档实证占位符约定是 `@Image1` / `@Video1` / `@Audio1`。fal 不认 `@产品1` → 当作普通文本 → 只看到"替换视频中的裤子"8 字纯文本生成全新视频。

## 对照证据(2026-05-10 verify)

- **fal 官方文档**(WebSearch 2026-05-10):字段名 `image_urls` / `video_urls` / `audio_urls`,占位符 `@Image1` / `@Video1` / `@Audio1`
- **V1 jobs.py L3334**(api/jobs.py,P216 dc64082)同端点 `bytedance/seedance-2.0/fast/reference-to-video` 用 `image_urls` 字段名 + `@Image1` / `@Video1` 占位符,跟 fal 文档一致
- **V2 注释 L241-243** 自标"已验证 ad_video_models.py:949"是错对照 — ad_video 调的是 `bytedance/seedance-2.0/reference-to-video`(无 fast/),跟 V2 用的 `.../fast/reference-to-video` 不是同一端点

## 实测受影响 job

- 老板真测 job_id `59fb3e90-0723-41cd-8366-69802a143f25`(2026-05-10 19:29:16)
- 扣 20 积分 / fal 计费 $0.962 已花
- 成片是 fal 视频(curl 200,2.21MB,video/mp4),但内容跟参考图无关

## fal 退款机制无路径

fal API 调用成功 → 计费已扣 → 无 refund 路径。**用户层面 20 积分由项目方加回**(commit 4 一起做,留 transaction record)。

## commit 4 工作清单

1. processor.py L246:`reference_image_urls` → `image_urls`(必改)
2. build_prompt:@ 占位符规则改 — `@Image1` 还是另起约定(产品决策,老板拍板)
3. 前端 prompt 模板同步(UI 文案 — 用户能看到产品/人物/场景的中文标签,但拼到后端时映射成 fal 约定)
4. 测试用例更新(单测验证字段名 + 占位符)
5. 老板 ¥19.9 客户服务积分加回(credits_ledger transaction record 留痕,reason `task_partial_refund` 或 `customer_service_credit`)
6. processor.py L241-243 注释删 / 改成 link 到 fal 文档 URL + V1 jobs.py L3334 line ref

## 不做 rollback 的原因

commit 3 是砍单档,跟这两个 bug 无关。bug 在 a733e50(P221 V2 上线)就埋下,commit 3 deploy 前就在跑。rollback 到 b01886b 还是踩同样 bug。**只能往前修,不能 rollback**。

# 🆕 commit 4 已 deploy 但产品定位错(2026-05-10 22:00 真测发现)

commit 4 (6a085d0) 已 deploy 上线。技术执行成功 — fal 真看图了 / 字段名对了 / @ 占位符对了。但**老板真测 2 次依然不符合预期**。

## 真测证据(老板 2 次 ¥19.9 已全额退,余额 692)

**第 1 次**:job_id `59fb3e90-0723-41cd-8366-69802a143f25`(commit 3 deploy 后,commit 4 前)
- prompt: `@产品1替换视频中的裤子(参考素材:@产品1)`(中文 @ fal 不识别 → 当纯文本 → 等于 prompt='替换视频中的裤子')
- 字段名 `reference_image_urls`(写错被 fal 默默丢弃 → 模型完全不看图)
- 输出:跟参考图无关的视频

**第 2 次**:job_id `6bc9c44d-5eb6-4ae4-a2ce-a88dde9ba36a`(commit 4 deploy 后)
- prompt: `@Image1 替换视频中的裤子(参考素材:@Image1)`(转换成功,fal 能识别)
- 字段名 `image_urls`(对了,fal 真看图)
- 输出:fal 真看图了,但视频后半段(约 5-8 秒)漂回原视频画面 / 没换产品到位

## 根因(WebFetch fal 文档 verbatim 实证)

> "reference materials serve as **guidance for motion, composition, and style** — **NOT source material being modified**"

`bytedance/seedance-2.0/fast/reference-to-video` **是参考生成端点,不是对象替换端点**。reference video 给"运动/构图/风格"做参考,**不是被修改的源**。

**输入/输出帧数对比(2026-05-10 ffprobe 实测)**:
- 原视频 input:720x1280 @ 30fps, 8.033s, 241 帧
- fal 输出:496x864 @ 24fps, 7.917s, 190 帧
- 帧数完全不同 → 证实 fal 是从零生成新视频,不是基于原帧修改

## 团队历史误用(C2 调查)

V1 docstring 写错最早源头:**commit dc64082**(libubuuuu, 2026-05-08 20:23 +0800):
- commit message: `feat(video_clone): P216 接入 Seedance 2.0 r2v Fast — 真复刻视频镜头/动作`
- video_clone.py docstring: `r2v Fast 直接 video-to-video,真复刻原视频镜头/动作`

V1 docstring "动作复刻"误解活了 2 天没踩到(没用户用产品定位真测),V2 上线后第一次真测就踩 ¥39.8 + 9 小时损失。

# 🆕 commit 5 候补:切端点 — wan-vace-14b/inpainting + SAM2 mask

## 老板正式锁定的产品定位

V2 真正产品定位 = **完美复刻 + 局部替换**:

- 用户上传:原视频 + 产品图 / 人物图 / 场景图
- 输出:**保留**原视频的镜头/动作/构图/时长(每一帧都跟原视频一致)
- **只替换**:用户指定的对象(产品 / 人物 / 场景)
- **每一帧都要替换到位** — 不允许"前 5 秒换了后 3 秒漂回去"
- **帧级公平计费** — 漏帧的成本不能让用户承担

## 技术路线候补(2 选 1 + 测试路线)

**候补 A:VACE inpainting + SAM2 mask(完整方案,口播 V4 已有代码栈)**

依赖项目已有的 3 个 fal 服务(fal_service.py 已有 + probe verified 2026-05-04):

1. `FalSAM2VideoService`(`fal-ai/sam2/video`):video_url + box_prompts → 输出黑白 mask 视频
2. `FalInpaintingService`(`fal-ai/wan-vace-14b/inpainting`):video_url + mask_image_url + ref_image_urls + prompt → 保留 mask 外区域 + 只换 mask 内
   - pricing: 480p $0.04/秒 / 580p $0.06/秒 / 720p $0.08/秒(按 16fps)
   - num_frames: 81-241(8s × 16fps = 128 帧 ✓)
3. `FalVaceFunInpaintingService`(`fal-ai/wan-22-vace-fun-a14b/inpainting`):video_url + mask_video_url(逐帧 mask)+ ref + prompt
   - 输出固定 720p / 5.0625s / 81 帧 / $0.13 视频(超 5s 要分段)

8s 视频成本估算(720p):8s × $0.08 = $0.64 + SAM2 segmentation(待 probe 估)≈ $0.7-1.0,跟 fal r2v $0.962 同档。

**候补 B:Lucy Realtime 2(`decart/lucy-realtime-2/realtime`)** — $0.02/秒
- 文档警告:依然是 diffusion 生成式("synthesizes outcomes rather than modifying source video structure"),需先 probe 确认是否真"保留原视频结构"
- 流式实时,3 秒视频 = $0.06,适合先低成本探路

**测试路线**:先 ¥0.5 跑 Lucy 探路 → 不行就走候补 A(完整移植 SAM2 + VACE 栈)

## commit 5 工作清单(待老板拍板设计方案)

1. 切端点(processor.py 的 call_fal_seedance → 新 call_fal_vace_inpaint)
2. 接入 SAM2 video segmentation(用户在某帧画 box → 自动跨帧追踪 → mask 视频)
3. 前端加 "选 mask 区域" 步骤(用户在视频帧上画框圈定要替换的对象)
4. 帧级质量校验(替换后视频跟原视频对比 — 非 mask 区域帧像素差应该极小)
5. 部分退款机制(如果有"漂回原视频"的帧 → 按帧比例退款)
6. memory 更新 + 测试用例

## 不做 rollback 的原因

跟 commit 4 同源:bug 不在 commit 3+4,在 a733e50 V2 上线就用错端点。rollback 到任何 commit 都踩同样产品定位错配。**只能往前修(commit 5 切端点),不能 rollback**。
