---
name: SSP 任何 fal 调用必须先经用户同意
description: 跑 probe / 真任务调 fal 端点前必须先问用户 + 报预估成本,不擅自扣 KEY 余额
type: feedback
originSessionId: cb94cb1f-0cce-4b66-9a3e-76eef2cc4683
---
跑任何会调 fal API 的操作前(probe 实测 / 触发用户测一遍真任务 / 我自己跑 fal_client 调试)
必须先告诉用户:
1. 要调哪个端点
2. 预估成本(单段 ~$0.5 / 5 段并发 ~$5 / 完整 ad-video 链路 ~$10 等)
3. 等用户明确点头("可以"/"跑"/"OK")才执行

**Why**:用户实际付 fal KEY 的钱,probe 看着是技术验证但全是真扣余额。我之前一轮 ad-video
重构跑了 6+ 个 probe(Seedance 2.0 单段 + 5 段 / Kling v3 / omnihuman / ref2vid /
GPT-Image 2 5 段),累计十几刀。用户怒"生成视频要问过我的同意,而不是浪费我的钱"。
2026-05-05 当天事件。

**How to apply**:
- 即使按 memory `fal_probe_first` 教训"切端点前必 probe",probe **本身**也要先问用户
- 鼓动用户在前端测真任务前(generate 按钮一点就扣 fal),要预先告知"这次会跑什么 + 大概多少钱",
  用户按下去才不会事后骂浪费
- 例外:health check / 单纯路由 401 验证(curl 不带 token)— 这些不调 fal,免询问
- 写 probe 脚本本身没问题(代码静态),**真跑** probe 才需要用户同意
