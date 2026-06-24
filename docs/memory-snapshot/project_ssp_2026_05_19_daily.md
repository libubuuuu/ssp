---
name: project-ssp-2026-05-19-daily
description: SSP 2026-05-19 改动汇总 — 虎皮椒支付修复/安全加固/UI积分显示/分镜复刻品类定价
metadata: 
  node_type: memory
  type: project
  originSessionId: 7b926571-d78b-40ba-a7f0-9a16270f6c30
---

## 2026-05-19 主要改动（最新 commit a01165a）

### 虎皮椒支付全链修复
- **签名格式**：个人版签名是 `sorted_params + secret`（无 `&appsecret=` 前缀），企业版才加前缀。实测5种格式，v2（直接拼接）唯一通过（commit ca0de05）
- **secret 更新**：`HUPIJIAO_SECRET` 改为 `2388b8c639fe4a2c6ed7ee4c16062eaa`，三个 `.env.enc` 同步
- **套餐支付**：package 类型也走虎皮椒（原来只有 credit），commit 679f63b
- **旧扫码流程删除**：前端只保留虎皮椒 UI，无 payment_url 时报错"支付通道暂时不可用"（commit 8b6472d）

### 支付安全加固（commit f2e90a3）
- 回调加金额校验：`abs(paid_fee - order_price) > 0.01` → 返 fail，防少付触发积分发放
- 同一事务：`UPDATE credit_orders` + `UPDATE users.credits` 同一 `conn.commit()`，消除竞态窗口

### 积分显示 UI
- 图生视频按钮：`生成视频（扣N积分）`，N = duration × 50（commit dab6848）
- 分镜复刻视频生成按钮：`生成完整视频（扣N积分）`，N = 合并后总秒数 × 65，与分镜列表逻辑一致（commit a01165a）

### 分镜复刻九宫格替换品类定价（commits 0c5e958 / 789b379）
- 新增品类下拉：普通类目 84积分/张，内衣/泳装类目 168积分/张
- 前端传 `sensitive` 字段，后端按 168/84 扣费（原来是 10/20，已更新）
- 计费按张数：`cost = credits_per_grid × len(grid_urls)`

### fal 成本实证汇总（本次调研，非代码改动）
- Seedance Fast r2v：$0.0925/s（billing CSV 实证）
- Kolors VTON：$0.05/次（固定，billing CSV 实证）
- GPT-Image-2 edit（九宫格替换）：~¥1.5-2/张（代码注释实测）
- Bytedance Upscaler：~$0.05/次（fal_service.py 注释实证）
- AI爆款视频 10s 全链 fal 成本：~$1.10 ≈ ¥7.52，用户付 650积分=¥13，毛利约 42%

### 灵梦 API 调用次数（本次调研）
- AI爆款视频最少 4 次调用（搜索+审稿员+林久+文案师），最多 6 次（+语言重写+场景重写）
- 灵梦单价代码内无记录，需登录 1189.xin 后台查询

**Why:** 虎皮椒修复使充值功能首次真正可用；安全加固防止少付或竞态发积分；积分显示让用户付费前知晓成本。
**How to apply:** 虎皮椒签名格式已锁定为个人版，不要改回企业版格式。分镜复刻定价 84/168 已是新基准。
