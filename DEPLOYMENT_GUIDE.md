# AI 创意平台 - 部署指南

**最后更新**: 2026-04-12  
**项目状态**: 可运行、可部署

---

## 一、快速启动（开发环境）

### 1. 后端启动

```bash
cd C:\Users\Administrator\ai-creative-platform\backend

# 激活虚拟环境
venv\Scripts\activate

# 启动服务（热重载）
uvicorn app.main:app --reload --port 8000
```

**访问**:
- API 文档：http://localhost:8000/docs
- 管理员后台 API: http://localhost:8000/api/admin/*

### 2. 前端启动

```bash
cd C:\Users\Administrator\ai-creative-platform\frontend

# 安装依赖（首次启动时）
npm install

# 启动开发服务器
npm run dev
```

**访问**:
- 前台：http://localhost:3000
- 管理员后台：http://localhost:3000/admin/dashboard

---

## 二、生产环境部署

### 1. 环境变量配置

创建 `backend/.env` 文件：

```bash
# FAL AI
FAL_KEY=your_fal_key

# JWT 认证
JWT_SECRET=change-this-secret-in-production-2026

# 阿里云短信（可选）
ALIYUN_ACCESS_KEY_ID=
ALIYUN_ACCESS_KEY_SECRET=
ALIYUN_SMS_TEMPLATE_CODE=
DEVELOPER_PHONE=

# 数据库
DATABASE_URL=sqlite+aiosqlite:///./dev.db
```

### 2. 后端部署

```bash
cd backend

# 使用虚拟环境
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 启动生产服务
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 3. 前端部署

```bash
cd frontend

# 安装依赖
npm install

# 构建生产版本
npm run build

# 启动生产服务
npm start
```

### 4. Nginx 反向代理配置

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 前端
    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }

    # 后端 API
    location /api/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 5. HTTPS 配置（Let's Encrypt）

```bash
# 安装 Certbot
sudo apt install certbot python3-certbot-nginx

# 获取证书
sudo certbot --nginx -d your-domain.com

# 自动续期
sudo certbot renew --dry-run
```

---

## 三、Docker 部署（可选）

### 1. 创建 Dockerfile

**backend/Dockerfile**:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

**frontend/Dockerfile**:
```dockerfile
FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]
```

### 2. docker-compose.yml

```yaml
version: '3.8'

services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      - FAL_KEY=${FAL_KEY}
      - JWT_SECRET=${JWT_SECRET}
    volumes:
      - ./backend/dev.db:/app/dev.db

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    depends_on:
      - backend

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - frontend
      - backend
```

### 3. 启动容器

```bash
docker-compose up -d
```

---

## 四、数据库迁移

项目使用 SQLite，数据库文件位于 `backend/dev.db`。

### 备份数据库

```bash
cp backend/dev.db backend/dev.db.backup
```

### 重置数据库

```bash
rm backend/dev.db
# 重启后端服务，数据库会自动创建
```

---

## 五、监控与日志

### 1. 查看后端日志

```bash
# 如果使用 systemd 服务
sudo journalctl -u ai-creative-backend -f

# 如果直接运行
# 日志会输出到终端
```

### 2. 查看前端日志

```bash
# PM2 管理
pm2 logs ai-creative-frontend

# 直接运行
# 日志会输出到终端
```

### 3. 管理员后台监控

访问 http://your-domain.com/admin/dashboard 查看：
- 模型健康状态
- 任务队列状态
- 平台统计概览

---

## 六、常见问题

### Q: 端口被占用怎么办？

**A**: 修改启动端口：
```bash
# 后端
uvicorn app.main:app --port 8001

# 前端
PORT=3001 npm run dev
```

### Q: 如何添加新用户？

**A**: 用户可以通过 `/auth` 页面自行注册，或手动插入数据库：
```sql
INSERT INTO users (id, email, password_hash, credits)
VALUES ('user_xxx', 'user@example.com', 'hashed_password', 100);
```

### Q: 额度不足如何充值？

**A**: 
1. 通过 `/pricing` 页面购买套餐或充值包
2. 创建订单
3. 支付订单（当前为模拟支付）
4. 额度自动到账

### Q: 如何查看 API 密钥？

**A**: 检查 `backend/.env` 文件：
```bash
cat backend/.env
```

---

## 七、性能优化建议

### 1. 启用 Redis 缓存

```bash
# 安装 Redis
sudo apt install redis-server

# 修改配置
# 在 backend/app/config.py 中添加 Redis 配置
```

### 2. 数据库索引

```sql
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_orders_user_id ON credit_orders(user_id);
CREATE INDEX idx_tasks_user_id ON tasks(user_id);
```

### 3. CDN 加速

将静态资源（图片、视频）上传到 CDN：
- 阿里云 OSS
- 腾讯云 COS
- AWS S3 + CloudFront

---

## 八、安全建议

### 1. 修改 JWT 密钥

```bash
# 生成随机密钥
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 2. 配置防火墙

```bash
# 仅允许必要端口
sudo ufw allow 80
sudo ufw allow 443
sudo ufw allow 22  # SSH
sudo ufw enable
```

### 3. 定期备份

```bash
# 创建备份脚本
#!/bin/bash
cp backend/dev.db /backup/dev.db.$(date +%Y%m%d)
```

---

## 九、联系支持

- 项目文档：`PROJECT_FINAL_SUMMARY.md`
- Phase 报告：`PHASE1-4_COMPLETE_REPORT.md`
- 架构设计：`IMPLEMENTATION_PLAN.md`

---

**部署状态检查清单**:
- [ ] 环境变量已配置
- [ ] 数据库已初始化
- [ ] 后端服务已启动
- [ ] 前端服务已启动
- [ ] Nginx 反向代理已配置
- [ ] HTTPS 证书已安装
- [ ] 防火墙已配置
- [ ] 监控告警已设置
