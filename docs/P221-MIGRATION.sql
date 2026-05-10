-- ============================================================
-- P221 视频复刻 V2 — 数据库 migration v3(对齐 v4 设计文档:4 个产品功能)
-- ============================================================
-- 创建:2026-05-09
-- v2:加 type + segment_tiers + disclaimer_log 表
-- v3:⭐ 加 replacement_mode / image_urls 对象数组 / segments_plan source_type 字段 / prompt_compiled
-- 配套:docs/P221-API-SCHEMA.md(v4)
-- 法务:docs/legal/{terms-of-service.md, video-clone-v2-upload-disclaimer.md}
--
-- 落地方式(项目双轨制 — 生产 SQLite + Phase 2 Postgres+alembic):
--   1) CREATE TABLE / CREATE INDEX 块**复制**进 backend/app/database.py
--      的 init_db() 函数末尾 conn.commit() 前,保持 IF NOT EXISTS 幂等
--   2) 同步 alembic migration:
--        cd /root/ssp/backend
--        venv/bin/alembic revision -m "add_video_clone_v2_tables"
--      把 upgrade() 写成 op.create_table + op.create_index,downgrade() drop
--   3) **不要**直接跑这个 .sql 文件,init_db() 服务启动时自动建
--   4) 测试:venv/bin/pytest -k video_clone_v2(/tmp/ssp_test_*.db 隔离)
--
-- 字段类型(SQLite 兼容):
--   TEXT / INTEGER / REAL / TIMESTAMP(SQLite 实际是 TEXT,DEFAULT CURRENT_TIMESTAMP)
-- ============================================================


-- ----------------------------------------------------------------
-- 1) video_clone_v2_jobs:V2 主任务表
-- ----------------------------------------------------------------
-- type:single(单段两档) / ultimate(全能档段位独立选择)
-- 全局两档(用户最终决议):economy / standard
-- replacement_mode:partial(局部替换,默认 original 段)/ full(全方位替换,默认 ai 段)
--                  仅是前端 UI 状态记录,后端逻辑由 segments_plan 各段 source_type 驱动
-- tier / segment_tiers:历史字段,新版本不再使用(每段 tier 已内嵌 segments_plan)
--                      留着保持表结构稳定,新代码不写,旧 admin 查询兼容
CREATE TABLE IF NOT EXISTS video_clone_v2_jobs (
    id                       TEXT PRIMARY KEY,
    user_id                  TEXT NOT NULL,

    -- 计费模型 + 替换模式
    type                     TEXT NOT NULL,                       -- single | ultimate
    replacement_mode         TEXT NOT NULL,                       -- ⭐ 功能 1: partial | full
    tier                     TEXT,                                -- (历史字段,新代码不写)
    segment_tiers            TEXT,                                -- (历史字段,新代码不写)

    -- 输入
    input_video_url          TEXT NOT NULL,                       -- fal storage URL
    input_video_local_path   TEXT,                                -- 切片用,服务器本地缓存
    input_video_duration_sec REAL NOT NULL,                       -- ffprobe 实测
    input_video_sha256       TEXT,                                -- 法务举证关联
    -- ⭐ 功能 3:image_urls 改对象数组 JSON
    -- 旧:["url1","url2"]
    -- 新:[{"url":"...","role":"product"},{"url":"...","role":"person"},...]
    -- role ∈ {product, person, scene, reference}
    image_urls               TEXT NOT NULL,
    prompt                   TEXT NOT NULL,                       -- 用户原始输入(审计 / 重新生成用)
    -- ⭐ 功能 3:后端 build_prompt 拼好的最终 prompt(带 @产品1 @人物1 @场景1 等)
    -- 调 fal 时用这个,不用 prompt 字段
    prompt_compiled          TEXT,

    -- 切片计划(create 时算好,跑过程不变)
    -- segments_plan JSON: [
    --   {"idx":0,"start":0.0,"duration":8.0,
    --    "source_type":"ai",       -- ⭐ 功能 1+2: ai | original
    --    "tier":"standard",        -- ai 段必填(economy/standard);original 段为 null
    --    "input_seconds":4,        -- ai 段:按 tier 算;original 段:null
    --    "thumbnail_url":"https://ailixiao.com/uploads/.../thumb_0.jpg"},
    --   {"idx":1,"start":8.0,"duration":8.0,
    --    "source_type":"original","tier":null,"input_seconds":null,
    --    "thumbnail_url":"..."},
    --   ...
    -- ]
    segments_plan            TEXT NOT NULL,
    segments_count           INTEGER NOT NULL,

    -- 段执行结果(processor 边跑边更新)
    -- segments_results JSON: [
    --   -- ai 段:跑 fal
    --   {"idx":0,"source_type":"ai","fal_request_id":"...","status":"completed",
    --    "output_url":"...","retry_count":0,"actual_cost_usd":0.65,"error":null},
    --   -- original 段:本地 ffmpeg 切原视频对应秒数,不调 fal
    --   {"idx":1,"source_type":"original","status":"ready",
    --    "local_path":"/opt/ssp/uploads/.../seg_1.mp4","actual_cost_usd":0.0},
    --   -- ai 段失败(重试 1 次仍败):⭐ 直接跳过,不补原视频
    --   {"idx":2,"source_type":"ai","status":"failed_skipped","retry_count":1,
    --    "error":"fal NSFW rejected","actual_cost_usd":0.0},
    --   ...
    -- ]
    segments_results         TEXT NOT NULL DEFAULT '[]',

    -- 成片
    final_video_url          TEXT,                                -- 归档后 ailixiao.com 地址
    final_video_local_path   TEXT,                                -- /opt/ssp/uploads/video_clone_v2/{id}/...

    -- 计费(单位:积分,1 积分 ≈ ¥1)
    total_credits_charged    INTEGER NOT NULL,
    total_credits_refunded   INTEGER NOT NULL DEFAULT 0,
    fal_cost_total_usd       REAL NOT NULL DEFAULT 0,             -- 审计用

    -- 状态机:pending/processing/concatenating/completed/failed/refunded/cancelled
    status                   TEXT NOT NULL DEFAULT 'pending',
    error_step               TEXT,                                -- split/fal/concat/archive
    error_message            TEXT,

    -- 时间戳
    created_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at             TIMESTAMP,
    archived_at              TIMESTAMP,                           -- GC 后清本地文件,row 保留

    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_vc2_user_time
    ON video_clone_v2_jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vc2_status
    ON video_clone_v2_jobs(status);
CREATE INDEX IF NOT EXISTS idx_vc2_archive
    ON video_clone_v2_jobs(archived_at, completed_at);
CREATE INDEX IF NOT EXISTS idx_vc2_type
    ON video_clone_v2_jobs(type);


-- ----------------------------------------------------------------
-- 2) video_clone_v2_daily_budget:每日 fal 总花销看板(保险 3)
-- ----------------------------------------------------------------
-- date:YYYY-MM-DD(UTC+8 中国时区)
-- spent_usd:当日累计 fal 实扣 USD
-- locked:超阈值后置 1,API 入口检查后拒收新订单(跨重启持久化)
CREATE TABLE IF NOT EXISTS video_clone_v2_daily_budget (
    date        TEXT PRIMARY KEY,
    spent_usd   REAL NOT NULL DEFAULT 0,
    locked      INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- ----------------------------------------------------------------
-- 3) video_clone_v2_disclaimer_log:上传弹窗勾选留痕(⭐ 新增,法务举证)
-- ----------------------------------------------------------------
-- 用户最终决议:不做技术审核 → 强力法务托底
-- 法务 §4.4.4:平台收到投诉/协查可"无须事先通知地披露"用户信息
-- 这表是该条款的事后举证基础 — 必须能证明每次提交时用户都勾了 disclaimer
--
-- 字段:
--   id            自增主键
--   user_id       谁勾的
--   job_id        关联 video_clone_v2_jobs.id(create 写入时同步)
--   ip            勾选时的客户端 IP(走 X-Forwarded-For 中间件)
--   user_agent    浏览器 UA(辅助辨识)
--   video_sha256  视频文件指纹(防"换了视频但举不出当时勾的版本"抗辩)
--   disclaimer_version  声明书版本号(v1 = 当前;改条款时升版本)
--   acknowledged_at     勾选时间(精确到毫秒)
CREATE TABLE IF NOT EXISTS video_clone_v2_disclaimer_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT NOT NULL,
    job_id              TEXT NOT NULL,
    ip                  TEXT,
    user_agent          TEXT,
    video_sha256        TEXT,
    disclaimer_version  TEXT NOT NULL DEFAULT 'v1',
    acknowledged_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (job_id) REFERENCES video_clone_v2_jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_vc2_disclaimer_user
    ON video_clone_v2_disclaimer_log(user_id, acknowledged_at DESC);
CREATE INDEX IF NOT EXISTS idx_vc2_disclaimer_job
    ON video_clone_v2_disclaimer_log(job_id);


-- ============================================================
-- 集成完成检查清单
-- ============================================================
-- [ ] database.py init_db() 末尾 commit 前嵌入三块 CREATE
-- [ ] backend/alembic/versions/<hash>_add_video_clone_v2_tables.py 镜像 schema
-- [ ] 重启后端 → 检查日志 "Database initialized successfully!" 无 OperationalError
-- [ ] sqlite3 /opt/ssp/backend/dev.db ".schema video_clone_v2_jobs" 输出表结构
-- [ ] PRAGMA index_list('video_clone_v2_jobs') 四个 index 全在
-- [ ] 跑一次空 INSERT 测约束(NOT NULL 字段都报错才算装好)
-- [ ] 加 cron:每日 00:05 UTC+8 INSERT OR IGNORE 当日 budget 行 + 检查前一天 locked 触发短信
-- [ ] disclaimer_log 表有 INSERT 路径(create 端点入口)+ 有 admin 查询路径(投诉响应用)
