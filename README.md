# AI 创意平台

企业级 AI 商业内容创作平台，提供图片生成、视频生成、数字人、语音克隆等一体化 AI 创作能力。

## 核心功能

### 图片生成
- **文生图**：风格化广告级 / 写实可控，支持多种风格预设
- **多参考图**：拖拽排序决定权重，融合多图特征生成
- **图生图**：上传参考图片，智能理解并生成

### 视频生成
- **图生视频**：上传首帧图片，AI 生成动态视频
- **元素替换**：一键替换视频中的商品/人物/背景
- **翻拍复刻**：提取爆款视频的运镜和节奏，换模特和产品翻拍
- **视频剪辑台**：分镜解析、多语言改写、时间轴重组

### 数字人与语音
- **数字人**：上传人物图片 + 音频，生成精准口型同步视频
- **语音克隆**：5-10 秒提取音色，生成专属配音
- **TTS**：文本转语音，支持多种预设音色

### 企业级特性
- **模型熔断**：连续失败 3 次自动切换备用模型
- **并发控制**：单用户最多 5 个并发任务
- **额度系统**：积分计价、失败返还、支付订单
- **限流防刷**：IP/用户双维度限流
- **多窗口同步**：WebSocket 实时推送任务状态
- **安全认证**：JWT 密钥强制环境变量配置，CORS 白名单
- **管理员后台**：模型监控、熔断告警、平台统计

## 项目结构

```
ai-creative-platform/
├── backend/                 # FastAPI 后端 (Python)
│   ├── app/
│   │   ├── api/            # API 路由 (auth, image, video, avatar, admin, payment...)
│   │   ├── services/       # 业务服务 (熔断器, 限流器, 额度, 日志...)
│   │   ├── config.py       # 配置管理
│   │   ├── database.py     # SQLite 数据库
│   │   └── main.py         # 应用入口
│   ├── requirements.txt    # Python 依赖
│   └── .env.example        # 环境变量模板
│
├── frontend/               # Next.js 前端 (React + TypeScript)
│   ├── src/app/            # 页面与路由
│   │   ├── image/          # 图片生成
│   │   ├── video/          # 视频生成 & 剪辑台
│   │   ├── avatar/         # 数字人
│   │   ├── voice-clone/    # 语音克隆
│   │   ├── auth/           # 认证 (登录/注册/密码找回)
│   │   ├── profile/        # 个人中心
│   │   ├── pricing/        # 充值中心
│   │   └── admin/          # 管理员后台
│   ├── src/lib/            # 通用工具 & Hooks
│   └── .env.example        # 环境变量模板
│
├── README.md               # 项目说明
├── SPECIFICATION.md        # 技术规格文档
└── DEPLOYMENT_GUIDE.md     # 部署指南
```

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Next.js 15 + React 19 + TypeScript + Tailwind CSS |
| 后端 | FastAPI (Python) |
| 数据库 | SQLite (可迁移至 PostgreSQL) |
| 认证 | JWT (PyJWT + bcrypt) |
| AI 服务 | FAL AI API |
| 基础设施 | 自定义熔断器、限流器、任务队列 |

## 快速开始

### 1. 后端

```bash
cd backend
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 编辑 .env 填写 FAL_KEY 和 JWT_SECRET
uvicorn app.main:app --reload
```

API 文档: http://localhost:8000/docs

### 2. 前端

```bash
cd frontend
npm install
cp .env.example .env.local   # 可选，默认连 localhost:8000
npm run dev
```

访问: http://localhost:3000

### 3. 环境变量

**backend/.env**（必须配置）:
```bash
FAL_KEY=your_fal_key
JWT_SECRET=your-random-secret-key
```

**backend/.env**（可选）:
```bash
REDIS_URL=redis://localhost:6379/0
ALLOWED_ORIGINS=http://localhost:3000,https://yourdomain.com
S3_ENDPOINT=
FEISHU_WEBHOOK_URL=
ALIYUN_ACCESS_KEY_ID=
ALIYUN_ACCESS_KEY_SECRET=
ALIYUN_SMS_TEMPLATE_CODE=
DEVELOPER_PHONE=
```

**frontend/.env.local**:
```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

## 文档

- [SPECIFICATION.md](./SPECIFICATION.md) - 详细技术规格
- [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md) - 部署指南
- [PROJECT_FINAL_SUMMARY.md](./PROJECT_FINAL_SUMMARY.md) - 项目总览

## 定价

| 功能 | 价格 |
|---|---|
| 文生图 | 2 积分/张 |
| 多参考图生图 | 5 积分/张 |
| 图生视频 | 10 积分/次 |
| 元素替换 | 15 积分/次 |
| 翻拍复刻 | 20 积分/次 |
| 数字人生成 | 10 积分/次 |

### 套餐

| 套餐 | 积分 | 价格 | 折扣 |
|---|---|---|---|
| 月卡 | 500 | ¥199 | 8 折 |
| 季卡 | 1500 | ¥499 | 7 折 |
| 年卡 | 6000 | ¥1699 | 6 折 |

## License

MIT
