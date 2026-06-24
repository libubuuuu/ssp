---
name: project_ssp_aiview_test_cost
description: 我(Claude)产生的 aiview 测试消耗台账，老板会来要这个数对账
metadata: 
  node_type: memory
  type: project
  originSessionId: 7a43c3f4-65db-4859-b684-069499d56911
---

老板要随时能找我要"我跑测试烧的 aiview 成本"。**这是台账，谁来问就报这里的数。**

## 2026-06-24 这场会话我的测试消耗（直连脚本，未走任何用户账号）

| 测试 | 内容 | aiview credits_used |
|---|---|---|
| JPEG 缩图修复验证 | 1 张图(gpt-image-2) | 40 |
| seedance 探测 | 极速版 seedance-2-0-fast 5s 视频 | 550 |
| seedance 探测 | 标准版 seedance-2-0 5s 视频 | 600 |
| seedance 首探被工具超时杀(我操作失误,已提交照样计费) | 极速版 5s 视频(孤儿,没取回) | ~550 |
| 比例探测 | gpt-image-2 @ ratio 9:16 | 40 |
| 比例探测 | gpt-image-2 @ ratio 16:9 | 40 |
| 比例探测 | seedream @ ratio 9:16 | 50 |
| **合计** | 4 图 + 3~4 视频 | **≈ 1870 aiview 积分** |

- 折 RMB ≈ **¥14~16**（按代码成本锚点 seedance 8s@480p≈$0.962 估，精确值取决于老板 aiview 每积分实付单价 → 1870 × 单价 = 真实成本）。
- COS 上传测试：自己的桶、几张几十KB小图，成本≈0，不计。
- 失败的生成 aiview credits_used=0 不计费，未计入。

## 对账口径（老板定的）
"只要不是走真实用户账号的都算测试成本"。已核实：
- generation_history 4544 笔 + V2 jobs 227 个 **全部归属真实用户账号**(9个真实号，老板确认都不是测试号)，0 孤儿/0 删号。
- 所以**我们数据库里没有任何隐藏测试号**；测试消耗 = 不走账号的直连脚本。
- 今天可追溯的 = 我这 ≈1870。**今天以前若有人直连脚本测过 aiview，不进库也不挂账号，只能用** `aiview后台总消耗 − 真实用户aiview消耗 − 1870 = 历史直连测试` 反推。
- aiview 无用量API、网页后台 Claude 进不去，总消耗只有老板后台看得到。

## 2026-06-25 图生视频上线前 probe（直连脚本，未走用户账号）

| 测试 | 内容 | aiview credits_used |
|---|---|---|
| 图生视频 i2v 上线 probe | seedance-2-0-fast 480p 5s 单图 i2v(验证单图 payload 端到端) | 550 |

- 目的：图生视频从 fal kling 切 aiview Seedance 后，验证"单张 image_url 纯图 i2v" payload 能 submit→生成→出片(出片在 openapi/ 公有读前缀)。**通过**。
- 折 RMB ≈ **¥4~5**（口径同上，550 × 老板每积分实付单价）。

相关：[[project_ssp_2026_06_24_daily]]
