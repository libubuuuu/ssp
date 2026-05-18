---
name: ssp-v2
description: "ENABLE_VIDEO_CLONE_V2=true 不能再关,日预算自动 kill switch 已删,用户授权"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7c096b06-9598-4023-b283-d95e15842516
---

用户 2026-05-13 00:00 明确授权:**视频复刻 V2 开启后永久不能再关**。

# 当前状态

- `.env.enc` 里 `ENABLE_VIDEO_CLONE_V2=true`(备份 `.env.enc.bak.before-v2-on-permanent-20260513-000023`)
- `video_clone_v2_processor.py:_record_daily_spend` 已移除"超日预算→`settings.ENABLE_VIDEO_CLONE_V2=False`"那段,改纯报警 log
- 保留:保险 1(单段成本超限)+ 保险 2(单 job 总额预扣)+ 日预算花销累计写库 + ERROR 报警

# Why

用户不想再因任何技术理由(成本审计未修 / setpts 副作用 / walltime UX 等)反复关 V2。要求永久开,接受单 job 风险,但拒绝整站 disable。

# How to apply

- **不要**因 `project_ssp_v2_fal_cost_audit.md` / `project_ssp_v2_walltime_ux.md` 等开放 bug 把 V2 关回去
- **不要**重新加自动 kill switch
- 如果未来真要应急关 V2(比如严重安全 bug),必须**先问用户**,不要自作主张
- 单 job 跑飞仍由保险 1/2 接住,跟整站 disable 无关
- fal 真实日花销超 `VC2_DAILY_FAL_BUDGET_USD`($100 默认)时,只 log ERROR,不动开关

# 相关

- [[project_ssp_p221_v2]] — V2 上线状态
- [[project_ssp_v2_fal_cost_audit]] — 老的成本对账 bug,**不是**关 V2 的理由
- [[project_ssp_v2_walltime_ux]] — 单段 walltime 体验雷,**不是**关 V2 的理由
- [[project_ssp_v2_setpts_tradeoff]] — setpts 副作用,**不是**关 V2 的理由
