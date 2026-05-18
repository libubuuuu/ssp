# API 配置文档（2026-05-19）

## fal.ai
- Seedance 2.0 Fast r2v: `fal-ai/seedance-2/fast/reference-to-video`
- GPT-Image-2: `fal-ai/gpt-image-2`（场景图+产品图生成）
- ElevenLabs TTS: `fal-ai/elevenlabs/tts/multilingual-v2`
- Bytedance Upscaler: `fal-ai/bytedance-upscaler/upscale/video`
- FAL_KEY: 从 .env.enc 读取

## Seedance 参数
- resolution: 480p（生成）→ upscale到1080p/2K/4K
- duration: 4-15秒（API限制）
- aspect_ratio: 9:16 / 16:9
- generate_audio: True（环境音）
- audio_urls: TTS音频URL（口播）
- MAX_DUR: 15（AI爆款视频）/ 14（视频拆解）

## 灵梦API（1189.xin）
- LINGMENG_BASE_URL: https://1189.xin/v1
- LINGMENG_MODEL: gpt-4o（AI爆款视频的林久/审稿员/文案师）
- LINGMENG_API_KEY: 从 .env.enc 读取
- SEARCH_API_KEY: gpt-4o-search-preview（趋势搜索专用）
- GEMINI_API_KEY: gemini-2.5-flash-all（视频拆解分析专用）

## 虎皮椒支付
- HUPIJIAO_APPID: 201906180392
- HUPIJIAO_SECRET: 从 .env.enc 读取
- API: https://api.xunhupay.com/payment/do.html
- 签名方式: 个人版（直接拼接secret，无&appsecret=前缀）
- 回调: https://ailixiao.com/api/payment/hupijiao/notify

## 积分规则
- 50积分 = 1元
- 视频生成: cost = max(65, target_duration * 65) + 分辨率附加费
- 1080p免费，2K+20积分，4K+50积分

## 部署
- 服务器: 腾讯云新加坡 43.134.71.189
- 蓝绿部署: bash /root/deploy.sh
- 回滚: bash /root/rollback.sh
- deploy.sh会等待活跃任务完成（最多15分钟）再切换

## .env.enc 当前所有 KEY 名
FAL_KEY
JWT_SECRET
ALLOWED_ORIGINS
RESEND_API_KEY
FROM_EMAIL
ORAL_BYPASS_VOICE_CLONE
STORAGE_DIRECT_UPLOAD_ENABLED
STORAGE_BUCKET
STORAGE_REGION
STORAGE_SECRET_ID
STORAGE_SECRET_KEY
ORAL_STEP_B_ENGINE
DASHSCOPE_API_KEY
ENABLE_VIDEO_CLONE_V2
DEEPSEEK_API_KEY
SENTRY_DSN
LINGMENG_BASE_URL
LINGMENG_MODEL
LINGMENG_API_KEY
SEARCH_BASE_URL
SEARCH_API_KEY
GEMINI_API_KEY
HUPIJIAO_SECRET
