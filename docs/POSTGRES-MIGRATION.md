# Phase 2 — Postgres 迁移路径(脚手架已就位)

> 当前状态:**alembic 脚手架在位,业务仍跑 SQLite WAL**。本文档说明从今天到切 Postgres 之间的每一步。
> 最后更新:2026-04-28(四十七续)

## 现状(已搭好的)

| 项 | 路径 | 状态 |
|---|---|---|
| alembic 配置 | `backend/alembic.ini` | ✅ |
| alembic env.py | `backend/alembic/env.py` | ✅ 自动从 `DATABASE_URL` 或 `DATABASE_PATH` 取连接串 |
| 初始 migration | `backend/alembic/versions/24bf7cbb36fb_initial_schema_mirror.py` | ✅ 镜像 `app/database.py:init_db()` |
| dev.db / 生产 dev.db | `alembic_version='24bf7cbb36fb'` | ✅ 已 stamp head |
| `requirements.txt` | `alembic==1.18.4` + `SQLAlchemy==2.0.49` | ✅ |

## 业务代码不动

- `app/database.py` 继续直接用 sqlite3 连接,不引入 SQLAlchemy ORM
- 测试 `tests/conftest.py` 仍用 `init_db()` 在 tmp DB 上建表(快、隔离)
- `alembic` 只管 schema 演进,不参与运行时 query

## 日常工作流(改 schema 时)

### 1. 改 `app/database.py:init_db()` 加新列 / 表

```python
# 例:users 表加 last_login_at
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    ...
    last_login_at TEXT
)
""")
```

### 2. 写对应 alembic migration

```bash
cd /root/ssp/backend
venv/bin/alembic revision -m "add users.last_login_at"
# 编辑 alembic/versions/<id>_add_users_last_login_at.py:
#   def upgrade():
#       op.add_column("users", sa.Column("last_login_at", sa.Text()))
#   def downgrade():
#       op.drop_column("users", "last_login_at")
```

### 3. 本地 + 生产 dev.db 跑 upgrade

```bash
DATABASE_PATH=/root/ssp/backend/dev.db venv/bin/alembic upgrade head
DATABASE_PATH=/opt/ssp/backend/dev.db venv/bin/alembic upgrade head
```

> ⚠ **生产先备份再 upgrade**(`bash /root/backup_daily.sh` 立即跑一次 + 异地)

## 切 Postgres 时的步骤(未来某天)

### 0. 用户操作:开 Postgres 实例 + 装 psycopg2

```bash
# 1. 腾讯云 / RDS 开 Postgres 14+,拿到连接串:
#    postgresql+psycopg2://<user>:<pwd>@<host>:5432/<db>

# 2. backend/.env 加:
#    DATABASE_URL=postgresql+psycopg2://...

# 3. 装驱动
cd /opt/ssp/backend && venv/bin/pip install psycopg2-binary
```

### 1. 在 Postgres 上跑 alembic upgrade(新建 schema)

```bash
cd /opt/ssp/backend
DATABASE_URL=postgresql+psycopg2://... venv/bin/alembic upgrade head
# 验证表全部建好:psql -c "\dt"
```

### 2. 数据迁移(SQLite → Postgres)— ✅ 脚本已就位

`backend/scripts/sqlite_to_postgres.py`(六十一续):

```bash
cd /opt/ssp/backend

# Step 1: dry-run 验证(只 count + 前 5 行 sample,不写)
venv/bin/python scripts/sqlite_to_postgres.py \
  --sqlite-path /opt/ssp/backend/dev.db \
  --postgres-url postgresql+psycopg2://USER:PWD@HOST:5432/ssp \
  --dry-run

# Step 2: 真迁移(业务停机或切只读模式后跑,防数据漂移)
venv/bin/python scripts/sqlite_to_postgres.py \
  --sqlite-path /opt/ssp/backend/dev.db \
  --postgres-url postgresql+psycopg2://USER:PWD@HOST:5432/ssp

# Step 3: 失败时单表重迁(前面已迁的表已 commit)
venv/bin/python scripts/sqlite_to_postgres.py \
  --sqlite-path ... --postgres-url ... --tables credit_orders
```

设计要点:
- SQLAlchemy Core 跨方言通用,反射目标库 schema(无需手维护字段映射)
- 迁移顺序按 FK 依赖(users → merchants → products,等)
- 每表 row count 校验,源 != 目标 → raise
- 单表事务隔离,失败仅 rollback 该表
- 测试:`tests/test_sqlite_to_postgres.py` 6 case(SQLite→SQLite round-trip 验证逻辑)

### 3. 切 `app/database.py` 到 SQLAlchemy

业务代码改动:
- `get_db()` 返回 SQLAlchemy 连接而非 sqlite3
- 所有 raw SQL 用 `text()` 包装(Postgres ? → :name 占位符)
- `INTEGER PRIMARY KEY AUTOINCREMENT` → `Integer, primary_key=True`(Postgres 自动 SERIAL)
- `BOOLEAN DEFAULT 0` → `BOOLEAN DEFAULT FALSE`(Postgres 严格类型)

> 这一步是体力活,**不在脚手架范围**。预估 1-2 天。

### 4. 测试 + 灰度

- pytest 全跑 against Postgres test DB(改 conftest fixtures)
- 蓝绿部署一边切 Postgres 灰度,另一边留 SQLite 兜底
- 观察 24h,无异常切全量

## 常见问题

**Q: 现在 stamp 了 head,如果 init_db() 再加表会怎样?**
A: alembic 不知道,upgrade 也不会触发(已在 head 了)。**始终先写 alembic migration,再改 init_db,两者保持同步**。或者中长期把 init_db 改成调用 `alembic upgrade head`。

**Q: 测试要不要切 alembic?**
A: 不切。tests/conftest.py 的 `init_db()` 直接建表更快(每个测试 truncate,不重建),alembic 是给生产 schema 演进用的。

**Q: 如果 alembic migration 和 init_db schema 漂移了怎么办?**
A: 跑这个对比脚本(可加 CI):
```python
# 对比 alembic upgrade head 后的 schema vs init_db() 后的 schema
# 见 commit 四十七续的 staging 测试方法
```

## 应急回滚

```bash
# 回退 1 步:alembic downgrade -1
# 回退到指定:alembic downgrade <revision_id>
# 完全清空(测试用,生产慎用):alembic downgrade base
```

## TODO(切 Postgres 前要做)

- [ ] init_db() 改成调用 `alembic upgrade head`(消除两份 schema 维护)
- [ ] tests 仍用 init_db 还是 alembic?(性能 vs 一致性的取舍)
- [x] 写 SQLite → Postgres 数据迁移脚本 — ✅ 六十一续就位 + 6 测试
- [ ] backend/app/database.py 切 SQLAlchemy(体力活,1-2 天)
- [ ] 跑全量 pytest against Postgres(改 conftest fixtures 切 PG test DB)
