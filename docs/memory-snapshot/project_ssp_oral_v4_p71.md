---
name: SSP 口播 V4 P71 最终架构(vace-mask 真分层)
description: 2026-05-04 完成 vace-mask 引擎接入(SAM2 + Wan VACE Fun + qwen-vl 自动分镜),行业唯一近似分层产品
type: project
originSessionId: f98e6643-4495-4bf6-96fa-44085151a76c
---
## 当前架构(2026-05-04 P71 后)

口播带货工作台 dropdown 5 选(从最强到最便宜):

```
🌟 vace-mask         ¥1.40/5s  qwen-vl 视频理解 → 分镜 prompt + SAM2 box mask + Wan VACE Fun 中文 prompt
                                行业唯一近似分层产品(driving 拉起过程保留 + mask 内换产品)
                                限 5s 视频 / mask box 默认胸部 / 模特脸暂不换
                                端点:fal-ai/sam2/video + fal-ai/wan-22-vace-fun-a14b/inpainting
                                + DashScope qwen-vl-max-latest

🏆 catvton-pixverse  ¥1.83/5s  cat-vton VTON + pixverse-swap(整体换人换装)
                                抖音 1k-3k 商家同水平 / NSFW 通过

🆓 aliyun-wan2.7-r2v 免费     阿里通义万相多 reference(配额耗尽要切付费)
                                慢 8min/段

⭐ kling-o3-standard-v2v ¥4.5  Kling O3 多 element(prompt 拗不过分层架构,P64 弃)

📦 pixverse-swap     ¥1.43   单 swap 无 prompt(老兼容)
```

## 工程上的关键

- /root/ssp 源码 + /opt/ssp deploy(rsync /root → /opt)— **Edit 必改 /root**
- deploy 前查 running session(P58 教训)
- VACE Fun 默认输出 5.0625s/81 帧 16fps
- VACE Fun $0.13 固定价(非按秒)
- SAM2 video 输出 binary mask 视频(白=换,黑=保留)
- qwen-vl-max-latest 12K tokens/段 ≈ ¥0.11

## 行业死结(memory)

2026-05-04 现状:**Sora 2 / 即梦 4.0 / 可灵 / Wan 2.5 / Runway / Pika 全市场无端到端"分层时变换装"方案**。
唯一可行 = mask 硬约束(VACE+SAM2)。我们 SaaS 用 vace-mask 是**行业第一档真分层产品**,
抖音 1k-3k 商家做不到这层,我们做到了。

但仍 60-80% 像,不是 100%(行业物理极限)。

## probe verified 真值

- VACE Fun NSFW 内衣类通过 ✓
- SAM2 box_prompts schema:`[{x1,y1,x2,y2,frame_index}]`
- qwen-vl-max-latest 视频理解输出精细分镜 + 关键时刻 + OCR 广告文字
- HappyHorse video-edit 内衣类 partner_validation 硬拒(不可绕)
- 阿里 wan2.7-r2v 免费配额耗尽要切付费

## P71 limit + roadmap

- ⚠️ driving 限 5s(后续拆段)
- ⚠️ mask box 默认胸部(后续前端涂抹 UI)
- ⚠️ 模特脸不换(后续接 InsightFace P46 已装)

## P72-P76 后续迭代(2026-05-04)

- **P72**:Step 2 加 ②‑bis 视频复刻分镜面板,/generate-video-prompt + /update-video-prompt
  endpoints,db 加 auto_video_prompt + user_video_prompt 列
- **P73**:②‑bis 面板任何阶段都显示(asr_done 才可编辑,其他阶段只读)
- **P74**:VACE Fun NSFW 拒"内衣/文胸/拉起"等中文敏感词 → qwen-vl instruction 改输出中性词
  + cn_prompt sanitize NSFW_WORD_MAP + VACE 主体 prompt 改英文(P70 verified)
- **P75**:qwen-vl instruction 升级精细 6 段结构(画面/场景/镜头/模特/产品/分镜/关键时刻),
  输出从 240 字 → 2918 字详细脚本。force=true 参数让用户强制重新生成
- **P76**:加【精准分析原则】6 条,要求分清"分体两件 vs 连体连衣裙",不确定标'(待确认)',
  严禁脑补。关键时刻精度 0.5s → 0.1s

## 当前真实成本(P76)

5s 视频:¥1.40/段
- qwen-vl-max-latest 视频理解:¥0.11
- SAM2 box mask 追踪:¥0.36
- Wan VACE Fun inpainting:¥0.93
- codeformer + 本地 lipsync + thumbnail:¥0.04 + ¥0(本地 InsightFace)
