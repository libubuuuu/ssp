# AI 创意平台

全功能 AI 创意平台，包含图片生成、视频生成、数字人三大核心模块，支持多窗口同步。

## 功能模块

1. **图片生成** - 风格化广告级 / 写实可控
2. **视频生成** - 链接改造 / 文生视频 / 图生视频工作流
3. **数字人 AI** - 口型精准、无多余动作

## 项目结构

```
ai-creative-platform/
├── frontend/              # Next.js 前端
│   ├── src/app/           # 页面与路由
│   │   ├── image/         # 图片生成
│   │   ├── video/         # 视频生成
│   │   ├── digital-human/ # 数字人
│   │   └── tasks/         # 任务状态（WebSocket 多窗口同步）
│   └── .env.example
├── backend/               # FastAPI 后端
│   ├── app/
│   │   ├── api/           # 路由
│   │   ├── services/      # 飞书等服务
│   │   └── config.py
│   └── requirements.txt
├── SPECIFICATION.md
└── README.md
```

## 快速开始

### 1. 后端

```bash
cd backend
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 编辑 .env 填写配置
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

- **backend/.env**: Redis、数据库、S3、飞书 Webhook、AI API Key
- **frontend/.env.local**: `NEXT_PUBLIC_API_URL`、`NEXT_PUBLIC_WS_URL`

## 文档

- [SPECIFICATION.md](./SPECIFICATION.md) - 详细技术规格与开发阶段建议
