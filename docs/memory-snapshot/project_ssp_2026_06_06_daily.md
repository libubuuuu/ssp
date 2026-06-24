---
name: project-ssp-2026-06-06-daily
description: 2026-06-06 大改动汇总：图片/视频上传分离、全站切COS、视频复刻修复与UI优化、任务限制、磁盘清理
metadata: 
  node_type: memory
  type: project
  originSessionId: c40c09b4-8a0b-4e34-ade9-40ab9ca6991a
---

## 2026-06-06 改动汇总（最新 commit: 610e386）

### 1. 图片/视频上传分离
- 图片上传端点从 `video.py` 迁移到 `image.py`
- 新端点：`POST /api/image/upload/cos`（原来有 /upload/fal，已删除）
- video/page.tsx、image/page.tsx 前端同步更新

### 2. 全站文件上传切换腾讯 COS（不再走 fal storage）
- 涉及：video.py / ad_video.py / video_general.py / video_frame_extract.py / content.py / video_clone.py / replicate.py / video_studio.py / jobs.py / fal_service.py / ad_video_models.py / video_general_script.py
- 统一用 `await asyncio.to_thread(upload_to_cos, path)`
- `/upload/fal` 端点已删除，`fal_upload_with_retry` 调用全部替换

### 3. 视频复刻 V2 失败修复（永久）
- 网络抖动：download_input 加 3 次重试（5s/15s/40s）
- COS URL 有效期 24h → 7 天（cos_upload.py Expired=604800）
- 下载 403 自动重签 COS URL（regenerate_cos_url）
- 调 aiview 前统一刷新参考图 URL
- /tmp/video_clone_v2_work 启动时自动建好（防重启后 root 占用导致 PermissionError）
- error_message 改为有意义的 "ExceptionType: msg" 格式

### 4. 视频复刻 UI 优化（video-clone-v2/page.tsx）
- Step 1：删掉"效果说明"那句话
- Step 1：更换视频按钮真正弹文件选择器 + 加叉叉删除视频
- Step 2：删掉内衣/泳装警告
- 步骤编号修复：single 模式 1→2→3→4，ultimate 模式 1→2→3→4→5

### 5. 任务数量限制
- 视频复刻 V2 + jobs.py 两个系统合计最多 5 个 pending/processing 任务
- 超限返 429，积分不扣
- `count_user_active_jobs(user_id)` 函数在 jobs.py 导出

### 6. 磁盘清理
- systemd journal 从 2.1GB 压缩到 200MB（释放 1.8GB）
- 删除旧轮转日志（/var/log/*.1, *.gz 等）
- 删除 uploads 7天前的媒体文件（释放 3.7GB，数据库记录全保留）
- 磁盘使用率从 91% 降到 81%

### 7. 历史记录页提示
- tasks/history/page.tsx 加黄色提示：生成内容保存 7 天，积分账单永久保留

### 8. 其他小修
- nginx 手动切槽时前端端口（3002→3000）也要同步改，曾漏改导致 502

**Why:** 用户要求图片视频上传分离、全站不走 fal storage、视频复刻失败永久修复、任务数限制 5 个
**How to apply:** 下次涉及上传相关改动，统一用 cos_upload.py 的 upload_to_cos；任务提交前检查合计数 ≤5
