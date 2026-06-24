---
name: project-ssp-2026-06-08-daily
description: 2026-06-08 改动汇总：图片生成时序优化/aiview错误透传/QR支付/账单标签/归档重试
metadata: 
  node_type: memory
  type: project
  originSessionId: 972c4844-f1d7-4f98-9683-4ae0221e9d96
---

## 2026-06-08 改动汇总

最新 commit: 6e35b94

### 改动列表

1. **aiview 错误透传**（commit 89f2280）
   - `aiview_service.py` query 返回路径加 `d.get("error_message")` 三重 fallback
   - 人脸生成失败原因现在能正确显示给用户

2. **定价页 QR 支付**（commit 486722a）
   - 安装 `qrcode.react`，支付弹窗改为二维码展示
   - 用户可截图后在微信/支付宝相册扫码，不再跳新标签页

3. **账单流水标签补全**（commit 288a2c7）
   - `billing.py` `_REASON_LABEL` 补全所有缺失 key（recharge_hupijiao/register_bonus 等）
   - 新增 `_MODULE_LABEL` 映射技术路径为中文名

4. **图片生成轮询提速**（commit 71b6bea）
   - 后端 aiview 轮询 5s→2s，平均节省 2.5s

5. **图片生成计时日志**（commits aa9e246, aa2a435）
   - T1=提交→aiview返回图片，T2=提交→用户可见
   - 实测：aiview 生成约 77~156s（负载波动），我们无额外耗时

6. **图片归档异步化**（commit aa2a435）
   - 归档从同步改为异步，消除原来 69s 阻塞
   - 用户拿到 aiview URL 立刻可见（T1=T2）

7. **图片归档重试保证**（commit 6e35b94）
   - `_bg_archive` 加 3 次重试（立即/30s/90s）
   - 全败才 `log_error` 告警，保证 aiview 临时 URL 被替换为永久链接

### 图片生成完整时间线（当前状态）
```
0s    用户点生成
84s   aiview 返回 URL → 后端立刻标 completed（T1=T2）
84+5s 前端轮询到 completed → 用户看到图（aiview 临时 URL）
87s   后台归档完成 → 替换为永久链接（用户无感知）
```

**Why:** 用户等待时间 = aiview 生成时间 + 最多 5s 前端轮询，后端无额外耗时
**How to apply:** 前端轮询间隔 5s 仍可优化（改 1~2s 可再减等待）
