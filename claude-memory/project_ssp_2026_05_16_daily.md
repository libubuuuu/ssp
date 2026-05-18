---
name: project-ssp-2026-05-16-daily
description: 2026-05-16 当天大改动汇总——AI爆款视频全链路、视频复刻灰度、系统改进，最新 commit 2f81e95
metadata: 
  node_type: memory
  type: project
  originSessionId: 6d5efa5d-ceba-473a-9be0-7388217a105a
---

# 2026-05-16 大改动汇总（commit a439cff）

## 核心新功能：AI爆款视频（/video/general 标签B）

**路径**：`/video/general` → 标签"AI爆款视频"

**完整流程**：
1. 上传产品图（正/反/侧/场景） → fal storage URL
2. 选模特来源（AI自动/上传图/上传视频）
3. 选参数（时长/市场/剧情or直接带货模式/用户想法）
4. 点"生成创意脚本（35积分）" → POST /api/video/general/script → Gemini gpt-4o → 格式化脚本展示
5. 点"确认脚本，生成视频" → POST /api/video/general/script-to-video → 解析分镜→Seedance批处理→ffmpeg拼接→视频播放

**关键文件**：
- `backend/app/services/video_general_script.py` — 两套prompt(_PROMPT_DIRECT/_PROMPT_STORY) + _COMMON_RULES + parse_script() + generate_script()
- `backend/app/api/video_general.py` — /script 和 /script-to-video 两个新端点（不动原有analyze/storyboard/generate）
- `backend/app/api/jobs.py` — script_to_video job类型 + _run_script_to_video_job() worker
- `frontend/src/app/video/general/page.tsx` — 全新页面，原始页面备份在 page.backup-original.tsx.bak
- `backend/app/services/gemini_client.py` — 灵梦API(gpt-4o)客户端，OpenAI兼容接口

**Why:** 灵梦配置的 gemini-2.5-pro-all 在平台不存在，改用 gpt-4o，效果良好。

## 入口A：视频复刻（灰度，commit 2f81e95）

**白名单**：`lirunting1a@gmail.com`（前端硬编码，`/api/auth/me` 判断）

**流程**：上传参考视频 → 上传产品图 → 选模特/参数 → POST /api/video/general/video-analyze → 展示脚本 → 确认脚本→生成视频（复用 script-to-video）

**关键文件**：
- `gemini_client.py`：新增 `image_urls: list` 参数（多产品图URL），视频URL优先放最前
- `video_general_script.py`：`_PROMPT_VIDEO_ANALYZE` + `analyze_video()` — 让gpt-4o看视频+产品图，输出替换后脚本
- `video_general.py`：`POST /video-analyze`，35积分，失败退
- `page.tsx`：`isWhitelisted` state，非白名单仍"开发中"；`scriptA`/`script`独立；`generateVideo(scriptOverride?)` 支持两个入口共享生成流程

## 系统改进

**积分/退款体系修复**：
- 管理员入账/微信回调绕过add_credits()的漏洞已修复→现在写ledger流水
- refund_tracker TTL 30分钟限制已删除→任务失败随时退款
- 管理员确认订单改为原子UPDATE WHERE status='pending'
- credits_ledger历史差额已补平（lirunting1a@gmail.com差+2930，测试账号差+200）

**新功能**：
- /credits 积分明细页（分页）
- /contact 微信客服页 + 全局悬浮按钮（WechatSupport.tsx）
- JobPanel 可拖拽，吸附左右，localStorage持久化
- db_backup.py 每日备份+对账（/opt/ssp/backups/）
- order_gc.py pending订单30分钟超时取消
- app_config DB表（batch_max_duration=8，batch_whitelist）

**并发升级**：所有 Semaphore(3) 已升到 Semaphore(5)，除TTS那个不动

**分镜复刻**：8s批处理全量开放，移除白名单灰度

## 灵梦API（Lingmeng/Gemini）
- .env.enc 里：LINGMENG_BASE_URL=https://1189.xin/v1，LINGMENG_MODEL=gpt-4o，LINGMENG_API_KEY=sk-i9of...
- 平台只有 gemini-2.5-flash-image 和 gemini-2.5-flash-image-preview（经常429）→ 实用gpt-4o

**How to apply:** 后续扩展AI功能时，直接从 gemini_client.ask_gemini() 调用，支持文字/图片/视频URL。
