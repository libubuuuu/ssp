---
name: project-ssp-2026-06-12-daily
description: 2026-06-12 凌晨改动汇总：aiview timing归因落日志/V2 watchdog时区修反事件/视频复刻人物句更新，最新 commit 3dfa05c
metadata: 
  node_type: memory
  type: project
  originSessionId: 734a2e74-6025-4262-ae76-40c807d9e22a
---

## 2026-06-12 凌晨改动汇总（接 06-11 晚图片慢调查）

最新 commit: 3dfa05c（全部已推 origin，已部署 blue 槽）

### 1. 图片慢真因钉死 + timing 接入日志（commit bb3908f）
- aiview 文档 v1.4.0 新增 timing 分解字段，已接入 `[AIVIEW-IMG] query completed` 日志
- 真因账目见 [[reference-aiview-timing-fields]]：中转面板"耗时"只统计到响应头；aiview 读响应体 14-18KB/s 是隐藏大头（2MB≈2分钟）；我们端到端 ≤5s
- base64 实锤：归档 PNG 1138KB × 4/3 = 1517KB 与 responseSizeKb 分毫不差；同图我们从 aiview 下载仅 7s vs aiview 从中转读 108s
- 给 aiview 的方案（已给用户话术）：①暴露 output_format=webp（108s→~15s，性价比王）②开 gzip（省25%）③中转就地解码传对象存储返 URL ④修链路。用户拍板发①②

### 2. V2 watchdog 时区修反事件（commit 1e88b50）
- 43111a6 把 watchdog 修反（详见 [[feedback-ssp-timezone-fix-verify-both-sides]]），job feb1e765 创建 3 分钟被误判"超时>30分钟"退 825 积分后又自己跑完 → completed+refunded 双花
- 修复：截止线 gmtime(UTC) + 按 COALESCE(updated_at, created_at) 判最后进展，活任务永不误杀
- 全日志确认仅 feb1e765 一单受害
- **待用户拍板**：feb1e765 的 825 积分补不补扣；要不要清该行 error 字段（现在 completed 但前端因 error_message 显示"生成失败"）

### 3. 视频复刻一键提示词人物句更新（commit 3dfa05c）
- 用户授权修改锁定格式：人物句加"脸部颜色要和身体的皮肤颜色对得上"+"头要小于身体"
- 前后端两份拼接同步改，锁定格式 memory 已更新

### 遗留/待办
- **smoke-test.sh 不存在**：deploy.sh [4.5/5] 冒烟测试一直跳过，靠健康检查兜底，要补回
- **蓝绿部署后旧 tab 假死**：用户实测踩中（01:46 打开的页 02:03 部署后按钮无反应，强刷解决）。建议做 build-id 版本检测"有新版本请刷新"提示条，等用户拍板
- 前端进度提示 + 诚实 ETA（图片 3-7 分钟无反馈），用户已知，等开工指令
- seedream-5.0-lite 的 timing 数据积累 1-2 天后决定是否默认模型分流
- 前后端两份 prompt 拼接逻辑重复，长期收敛为后端单一来源
- semaphore 等待期不释放 / jobs.json 双写覆盖 / 全量 dumps+fsync（06-11 遗留未修）
