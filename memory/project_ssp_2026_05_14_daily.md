---
name: ssp-2026-05-14
description: 今日主要变更:口播/长视频删除、真蓝绿修复、全局fal超时、分镜复刻重构
metadata: 
  node_type: memory
  type: project
  originSessionId: 283bbb0f-f2ca-4a06-8e3a-fbca0f28cecc
---

# 2026-05-14 重要变更(commit 45fc15c → a4f4143,共13个commit)

## 1. 口播带货 + 长视频工作台完全删除(commit 3fc123c)

- 后端:`/api/studio` `/api/ad-video` 路由注销,import删除
- 前端:zh.ts/en.ts 删 adVideo/studio/oral 三大块(-712行)
- billing.py 删 ad_video/* 定价条目
- AdminSidebar 删口播任务入口
- **Why**: 用户授权删除，功能不对外

## 2. 真蓝绿架构(commit 2ad6ba9)

已修复,双独立目录:
- `/opt/ssp-blue/backend` + `/opt/ssp-green/backend`
- 共享资源(venv/dev.db/.env.enc)用symlink
- rollback=直接启旧slot,无需恢复archive
- [[SSP 假蓝绿架构 bug]] → 已解决

## 3. 全局fal超时保护(commit a19834a)

`_execute_job`加`asyncio.wait_for`:
- video_general/skill_generate/video_clone: 1200s
- 其余: 600s
- 超时→自动failed+退款

## 4. V2积分即时显示(commit b2b71eb)

提交成功后用`estimate.total_credits`调`adjustLocalUserCredits`,
侧边栏积分数字不再延迟更新。

## 5. 分镜复刻全链路重构

详见[[SSP 分镜复刻(frame-extract)2026-05-14 架构]]

## 6. nginx client_body_timeout 300s→900s

慢网络(50KB/s)上传被截断,改900s解决。

## How to apply

- 下次上车先读 project_ssp_frameextract_2026_05_14.md 了解分镜复刻当前架构
- 口播/长视频已彻底删除,不要再提或重新添加
- 真蓝绿已修,deploy=`bash /root/deploy.sh`,rollback=`bash /root/rollback.sh`
