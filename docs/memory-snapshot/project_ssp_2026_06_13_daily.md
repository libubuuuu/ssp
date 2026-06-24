---
name: project-ssp-2026-06-13-daily
description: SSP 2026-06-13 充值中心调价上线，commit d1d3ee6
metadata: 
  node_type: memory
  type: project
  originSessionId: b634f160-325e-4cc6-a950-f6088f520cc5
---

# SSP 2026-06-13 充值中心调价

commit `d1d3ee6`，已 push + deploy（当前激活 green）。

## 新套餐（payment.py PACKAGES，credits 含赠送=实际到账数）

| 套餐 | 价格 | 到账积分 | 显示 |
|---|---|---|---|
| 基础版 monthly | ¥200 | 10000 | 无折扣标签 |
| 标准版 quarterly | ¥500 | 26780（25000+送1780） | 9.5 折 |
| 高级版 yearly | ¥1000 | 55000（50000+送5000） | 9 折 |

- 基准价 50积分=1元 没变（[[project-ssp-pricing-locked]] 仍有效）
- 前端 pricing/page.tsx：discount 为空时折扣徽章隐藏（条件渲染）
- i18n zh/en 的 rule3 + pkg_*Desc 同步更新
- 对老用户/挂单零影响：订单创建时快照 credits+price，回调按订单行校验
- 已知口径偏差：高级版实际 9.09 折显示"9 折"略夸大（真9折应送5556），用户知情按此上线

**Why:** 运营调价，价格回归整数 + 赠送积分模式。
**How to apply:** 再调套餐只改 payment.py PACKAGES + i18n 三处；credits 字段语义=到账总数（含赠送），别按面值写。
