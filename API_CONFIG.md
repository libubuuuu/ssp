# API 配置文档（2026-05-19 更新）

## 服务器
- 腾讯云新加坡 43.134.71.189
- 蓝绿部署：bash /root/deploy.sh
- 回滚：bash /root/rollback.sh
- 数据库：/opt/ssp-blue/backend/dev.db（活跃库）
- 测试账号：lirunting1a@gmail.com（管理员）

## fal.ai 模型+成本
- Seedance 2.0 Fast r2v: $0.0925/秒（视频生成）
- GPT-Image-2（生成）: $0.02/张
- GPT-Image-2（编辑/九宫格）: $0.24/张（普通），$0.45/张（敏感9帧×$0.05）
- ElevenLabs TTS: ~$0.03/次
- Bytedance Upscaler: 1080p=$0.0072/秒, 2K=$0.0144/秒, 4K=$0.0288/秒
- Kling o3 i2v: 需查fal后台
- FAL_KEY: 从.env.enc读取

## 灵梦API（1189.xin）
- LINGMENG_BASE_URL: https://1189.xin/v1
- LINGMENG_MODEL: gpt-4o
- 成本：100单位=¥79.2，1单位=¥0.792
- gpt-4o每千token: ¥0.256
- 一次正常对话(~3000 tokens): ¥0.77
- SEARCH_API_KEY: gpt-4o-search-preview
- GEMINI_API_KEY: gemini-2.5-flash-all（视频拆解分析）

## 虎皮椒支付
- HUPIJIAO_APPID: 201906180392
- 签名方式: 个人版（直接拼接secret，无&appsecret=前缀）
- 回调: https://ailixiao.com/api/payment/hupijiao/notify
- API: https://api.xunhupay.com/payment/do.html

## 用户定价（50积分=1元）
### 图生视频
- 50积分/秒（5秒=250积分=¥5, 10秒=500积分=¥10）

### AI爆款视频
- 对话: 39积分/次
- 视频生成: max(65, duration×65) + 分辨率附加 + 模特18
- 分辨率附加(按秒): 1080p=3积分/秒, 2K=6积分/秒, 4K=11积分/秒

### 视频拆解
- 分析: 25积分
- 视频生成: max(65, duration×65) + 模特18（不收分辨率费，跳过upscale）

### 分镜复刻
- 分析: 5积分
- 九宫格替换: 普通84积分/张, 敏感168积分/张
- 视频生成: 65积分/秒

### 充值
- 10元=500积分, 50元=2500, 100元=5000, 200元=10000
- 套餐: 月卡¥199/季卡¥499/年卡¥1699

## 关键文件路径
- 后端主逻辑: backend/app/api/video_general.py
- 后端视频生成: backend/app/api/jobs.py
- 后端支付: backend/app/api/payment.py
- 前端AI爆款+视频拆解: frontend/src/app/video/general/page.tsx
- 前端图生视频: frontend/src/app/video/page.tsx
- 前端分镜复刻: frontend/src/app/video/frame-extract/page.tsx
- 前端充值: frontend/src/app/pricing/page.tsx
- 脚本解析: backend/app/services/video_general_script.py
- Gemini客户端: backend/app/services/gemini_client.py
- 计费: backend/app/services/billing.py
- 配置: backend/app/config.py
- 部署脚本: deploy/deploy.sh

## Git
- 仓库: github.com:libubuuuu/ssp.git main分支
- Tags: v1.0-stable, v1.1-stable, v1.2-stable
