# AI 创意平台 - 升级实施计划

**项目路径**: `C:\Users\Administrator\ai-creative-platform`
**创建时间**: 2026-04-12
**目标**: 对照企业级蓝图，分阶段完成功能升级

---

## Phase 1: 基础架构升级 + 核心修复 (预计 3-5 天)

### 1.1 数据库与用户系统

**新增文件**:
- `backend/app/db/session.py` - 数据库会话管理
- `backend/app/models/user.py` - 用户模型
- `backend/app/models/task.py` - 任务模型
- `backend/app/models/model_health.py` - 模型健康监控模型
- `backend/prisma/schema.prisma` - 数据库 Schema (如果用 Prisma)

**改动文件**:
- `backend/app/config.py` - 添加数据库配置
- `backend/requirements.txt` - 添加数据库依赖

**核心功能**:
```python
# 用户模型
class User(Base):
    id: str (PK)
    email: str (unique)
    role: str (USER/ADMIN/DEVELOPER)
    credits: int (默认 100)
    created_at: datetime

# 任务模型
class Task(Base):
    id: str (PK)
    user_id: str (FK)
    module: str (IMAGE_GEN/VIDEO_REPLACE/...)
    status: str (PENDING/PROCESSING/COMPLETED/FAILED)
    input: JSON
    output: JSON
    model_used: str
    cost_credits: int
    retry_count: int
    created_at: datetime
    completed_at: datetime
```

### 1.2 模型熔断与告警系统

**新增文件**:
- `backend/app/services/circuit_breaker.py` - 熔断器服务
- `backend/app/services/alert.py` - 告警服务 (阿里云短信)

**改动文件**:
- `backend/app/main.py` - 初始化熔断器
- `backend/app/services/fal_service.py` - 集成熔断检查

**核心逻辑**:
```python
# 熔断器
class CircuitBreaker:
    failure_threshold = 3
    reset_timeout = 60000  # 1 分钟
    
    async def record_success(self, model_name: str)
    async def record_failure(self, model_name: str)
    async def is_available(self, model_name: str) -> bool

# 告警
class AlertService:
    async def send_sms(phone: str, message: str)
    async def notify_model_failure(model_name: str, failure_count: int)
```

### 1.3 并发任务池控制

**新增文件**:
- `backend/app/services/task_queue.py` - 任务队列服务

**核心逻辑**:
```python
USER_CONCURRENCY_LIMIT = 5

async def enqueue_task(user_id: str, task: Task) -> dict:
    running = await get_running_tasks(user_id)
    if running >= USER_CONCURRENCY_LIMIT:
        await add_to_queue(task)
        return {status: "queued", position: queue_position}
    await process_task(task)
    return {status: "processing"}
```

### 1.4 前端 UI 修复与优化

**改动文件**:
- `frontend/src/app/image/page.tsx` - 添加多参考图上传 + 权重排序
- `frontend/src/app/tasks/page.tsx` - 显示排队进度
- `frontend/src/components/` - 新增通用组件

---

## Phase 2: 核心视频功能 (预计 5-7 天)

### 2.1 视频元素替换 (Kling O1 Edit)

**新增文件**:
- `backend/app/api/video_replace.py`
- `backend/app/services/video_replace.py`

**API**:
```python
@router.post("/replace/element")
async def replace_video_element(
    video_url: str,
    element_image_url: str,
    instruction: str  # "把视频里的水杯替换成我的产品"
) -> dict:
    # Kling O1 Edit
```

**前端**:
- `frontend/src/app/video/replace/page.tsx`

### 2.2 视频翻拍复刻

**新增文件**:
- `backend/app/api/video_clone.py`

**API**:
```python
@router.post("/clone")
async def clone_video(
    reference_video_url: str,
    model_image_url: str,
    product_image_url: str
) -> dict:
    # Kling O1 Edit - 提取运镜/节奏/动作，替换主体
```

**前端**:
- `frontend/src/app/video/clone/page.tsx`

### 2.3 高保真图生视频

**改动文件**:
- `backend/app/services/fal_service.py` - 添加 Kling O1 Reference
- `frontend/src/app/video/generate/page.tsx` - 更新 UI

---

## Phase 3: 开发者后台 (预计 3-5 天)

### 3.1 监控大屏

**新增文件**:
- `backend/app/api/admin/stats.py`
- `frontend/src/app/admin/dashboard/page.tsx`

**指标**:
- 各模型调用量占比 (饼图)
- 成功率趋势 (折线图)
- API 消耗成本 (柱状图)
- 用户额度消耗排行 (TOP10)
- 平台流水 (今日/本周/本月)

### 3.2 熔断管理后台

**前端**:
- `frontend/src/app/admin/models/page.tsx` - 模型健康状态
- `frontend/src/app/admin/alerts/page.tsx` - 告警记录

### 3.3 任务池管理

**前端**:
- `frontend/src/app/admin/tasks/page.tsx` - 全局任务队列监控

---

## Phase 4: 高级功能 (预计 7-10 天)

### 4.1 Web 端视频剪辑台

**新增文件**:
- `backend/app/api/video_editor.py`
- `backend/app/services/video_parser.py` - 视频语义解析
- `frontend/src/app/video/editor/page.tsx` - 时间轴剪辑 UI

**流程**:
1. LLaVA 抽帧分析 → 分镜描述
2. Whisper → 音频转文字
3. 输出分镜时间轴 JSON
4. 前端分镜卡片编辑 + 翻译
5. 时间轴重组 + 导出

### 4.2 数字人 (克制型)

**新增文件**:
- `backend/app/api/avatar.py`
- `backend/app/services/avatar.py` - Hunyuan Avatar

**约束**:
```python
AVATAR_CONFIG = {
    "allow_gesture": False,
    "allow_body_movement": False,
    "lip_sync_only": True,
    "facial_expression": "minimal",
}
```

### 4.3 语音克隆

**新增文件**:
- `backend/app/api/tts.py`
- `backend/app/services/voice_clone.py` - Qwen3 TTS / MiniMax

---

## Phase 5: 商业化与防刷 (预计 2-3 天)

### 5.1 支付与订单

**新增文件**:
- `backend/app/models/order.py`
- `backend/app/api/payment.py`

### 5.2 限流防刷

**中间件**:
```python
RATE_LIMIT = {
    "per_ip": 10,  # 每分钟
    "per_user": 20,
    "daily_limit": 100,
}
```

---

## 技术债务清理

| 问题 | 优先级 | 解决方案 |
|------|--------|----------|
| 环境变量管理混乱 | P0 | 统一.env 配置，区分 dev/prod |
| 错误处理不完整 | P0 | 全局异常处理器 + 用户友好提示 |
| 前端状态管理分散 | P1 | 统一使用 Zustand |
| 无单元测试 | P2 | pytest + react-testing-library |

---

## 立即执行清单

- [ ] 1. 初始化数据库 (PostgreSQL/SQLite)
- [ ] 2. 创建用户/任务/模型健康模型
- [ ] 3. 实现熔断器服务
- [ ] 4. 实现告警服务 (阿里云短信)
- [ ] 5. 实现任务队列控制
- [ ] 6. 前端添加多参考图上传 UI
- [ ] 7. 前端显示排队进度

---

**版本**: v4.0
**状态**: 执行中
