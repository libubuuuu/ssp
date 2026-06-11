"""
数据库模块
直接使用 sqlite3，跳过 Prisma Python
"""
import os
import sqlite3
from contextlib import contextmanager
from typing import Optional
import json
from datetime import datetime

# 路径默认 ./dev.db,测试或多环境通过 DATABASE_PATH 覆盖
DATABASE_PATH = os.environ.get("DATABASE_PATH", "./dev.db")


@contextmanager
def get_db():
    """获取数据库连接

    PRAGMA 说明:
    - journal_mode=WAL: 写不阻塞读,生产并发写场景必开;一次设置文件级生效持久
    - synchronous=NORMAL: 配合 WAL 平衡耐久性和性能(满 fsync 太慢,WAL 自带保障)
    - busy_timeout=30000: 撞锁等 30 秒再放弃，Python connect(timeout=30) 与 PRAGMA 对齐
    """
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)  # Python 层 30s，与 PRAGMA 对齐
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _patch_users_columns(cursor):
    """幂等地补齐 users 表的后加列。
    Phase 2 迁 PostgreSQL + Alembic 后由真正的迁移系统接管。
    """
    patches = [
        ("totp_secret", "ALTER TABLE users ADD COLUMN totp_secret TEXT DEFAULT NULL"),
        ("totp_enabled", "ALTER TABLE users ADD COLUMN totp_enabled INTEGER DEFAULT 0"),
        # tokens_invalid_before:Unix 时间戳。token.iat 早于此值则该 token 失效。
        # 改密码 / 主动登出所有设备 / 管理员强制踢人时,把当前时间戳写进来。
        ("tokens_invalid_before", "ALTER TABLE users ADD COLUMN tokens_invalid_before INTEGER DEFAULT 0"),
    ]
    for col_name, sql in patches:
        try:
            cursor.execute(sql)
        except sqlite3.OperationalError as e:
            # SQLite 已有该列时报 "duplicate column name: xxx",此情况是幂等成功
            if "duplicate column" not in str(e).lower():
                raise


def _patch_video_clone_v2_columns(cursor):
    """幂等地补齐 video_clone_v2_jobs 表后加列(P221 A2 智能切片提示)。

    新加列 trimmed_seconds / trim_start / trim_end 用于记录用户主动丢弃的段。
    """
    patches = [
        ("trimmed_seconds", "ALTER TABLE video_clone_v2_jobs ADD COLUMN trimmed_seconds REAL DEFAULT 0"),
        ("trim_start",      "ALTER TABLE video_clone_v2_jobs ADD COLUMN trim_start REAL"),
        ("trim_end",        "ALTER TABLE video_clone_v2_jobs ADD COLUMN trim_end REAL"),
        # B 阶段:多段 drop 的 JSON([[s1,e1],[s2,e2],...]) processor 实际读这个字段
        # legacy trim_start/end 仅作单段 drop 兼容(/create 自动转成 1-elem JSON)
        ("trim_drop_ranges_json", "ALTER TABLE video_clone_v2_jobs ADD COLUMN trim_drop_ranges_json TEXT"),
        # aiview 切换:记录本单使用的视频模型(fast / 标准版 2.0),用于按模型费率退款
        ("video_model", "ALTER TABLE video_clone_v2_jobs ADD COLUMN video_model TEXT DEFAULT 'seedance-2-0-fast'"),
    ]
    for col_name, sql in patches:
        try:
            cursor.execute(sql)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def _patch_oral_columns(cursor):
    """幂等地补齐 oral_sessions 表的后加列(七十七续 P9b 双 mask 双轮 inpaint)。

    legacy `mask_image_path` 字段保留(读老数据兼容)。新写入走 person/product
    分列。第二轮可选,无 product mask 时 swap1_video_url 直接写到 swapped_video_url。
    """
    patches = [
        ("person_mask_image_path", "ALTER TABLE oral_sessions ADD COLUMN person_mask_image_path TEXT"),
        ("product_mask_image_path", "ALTER TABLE oral_sessions ADD COLUMN product_mask_image_path TEXT"),
        ("swap1_video_url", "ALTER TABLE oral_sessions ADD COLUMN swap1_video_url TEXT"),
        ("swap1_fal_request_id", "ALTER TABLE oral_sessions ADD COLUMN swap1_fal_request_id TEXT"),
        # 七十七续 P12 — oral_sessions GC,60 天后清目录,DB row 保留(账单/审计),archived_at 标记已清
        ("archived_at", "ALTER TABLE oral_sessions ADD COLUMN archived_at TIMESTAMP"),
        # 八十四续 V3 — VTON 静态试穿产物("模特穿产品"合成图,Step A 输出,Step B reference 输入)
        ("vton_image_url", "ALTER TABLE oral_sessions ADD COLUMN vton_image_url TEXT"),
        # 八十四续 P16 — 用户在 /start 选择成片比例(9:16 / 16:9 / 1:1 / auto)
        ("aspect_ratio", "ALTER TABLE oral_sessions ADD COLUMN aspect_ratio TEXT"),
        # P41 — 用户在 /start 选择 Step B 引擎(白名单见 oral.py _STEP_B_ENGINES)
        ("step_b_engine", "ALTER TABLE oral_sessions ADD COLUMN step_b_engine TEXT"),
        # P43-2 — 用户选是否过 fal Topaz 超分到 1440p(720p×2),+$0.02/秒
        ("use_topaz_upscale", "ALTER TABLE oral_sessions ADD COLUMN use_topaz_upscale INTEGER NOT NULL DEFAULT 0"),
        # P44 — demucs 音轨分离(Step 1 ASR 内异步触发,失败降级 elevenlabs/原音轨)
        # vocals_path:本地 mp3,Step 5 lipsync 用作干净人声输入(替代原音轨)
        # bgm_path  :本地 mp3,Step 5 lipsync 后 ffmpeg amix 混回成片(实现"保留 BGM")
        ("vocals_path", "ALTER TABLE oral_sessions ADD COLUMN vocals_path TEXT"),
        ("bgm_path", "ALTER TABLE oral_sessions ADD COLUMN bgm_path TEXT"),
        # P44 — Step B 实际跑通的引擎(可能是 fallback 后的,跟 step_b_engine 用户选项不同)
        ("step_b_engine_used", "ALTER TABLE oral_sessions ADD COLUMN step_b_engine_used TEXT"),
        # P45 — codeformer 增强后的模特图(预处理产物);use_face_enhance 用户开关(默认 1 开启)
        ("enhanced_model_url", "ALTER TABLE oral_sessions ADD COLUMN enhanced_model_url TEXT"),
        ("use_face_enhance", "ALTER TABLE oral_sessions ADD COLUMN use_face_enhance INTEGER NOT NULL DEFAULT 1"),
        # P46-L2 — 本地 InsightFace inswapper 生成的视频缩略图(免费,前端列表/历史页用)
        ("thumbnail_url", "ALTER TABLE oral_sessions ADD COLUMN thumbnail_url TEXT"),
        # P72 — qwen-vl 视频理解自动生成的分镜 prompt(展示给用户看)+ 用户编辑后的版本
        ("auto_video_prompt", "ALTER TABLE oral_sessions ADD COLUMN auto_video_prompt TEXT"),
        ("user_video_prompt", "ALTER TABLE oral_sessions ADD COLUMN user_video_prompt TEXT"),
        # P81 — vace-mask 手动按段生成模式
        # segments_json 存每段 metadata(JSON):[{idx,start_s,end_s,prompt,driving_url,mask_url,fal_url,status}]
        # vace_full_mask_url 存 SAM2 整段 mask 视频(段间共享)
        ("segments_json", "ALTER TABLE oral_sessions ADD COLUMN segments_json TEXT"),
        ("vace_full_mask_url", "ALTER TABLE oral_sessions ADD COLUMN vace_full_mask_url TEXT"),
    ]
    for col_name, sql in patches:
        try:
            cursor.execute(sql)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def init_db():
    """初始化数据库表

    ⚠ 与 alembic 的关系:
    - 测试 / fresh 部署直接调本函数(快、隔离)
    - 生产 schema 演进走 alembic upgrade head(`backend/alembic/versions/`)
    - 改 schema 时**两边都要改**,保持等价。详见 docs/POSTGRES-MIGRATION.md
    - 现有 dev.db 已 stamp 到 head 24bf7cbb36fb;新加列 → 写新 alembic migration + 同步本函数
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # 用户表（含 2FA 列;迁移管理见下方 _patch_users_columns）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            avatar_url TEXT,
            role TEXT DEFAULT 'user',
            credits INTEGER DEFAULT 100,
            phone TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            totp_secret TEXT DEFAULT NULL,
            totp_enabled INTEGER DEFAULT 0,
            tokens_invalid_before INTEGER DEFAULT 0
        )
        """)
        _patch_users_columns(cursor)

        # 身材数据表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS body_measurements (
            id TEXT PRIMARY KEY,
            user_id TEXT UNIQUE NOT NULL,
            height REAL,
            weight REAL,
            chest REAL,
            waist REAL,
            hips REAL,
            shoulder REAL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)

        # 商家表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            id TEXT PRIMARY KEY,
            user_id TEXT UNIQUE NOT NULL,
            shop_name TEXT,
            shop_desc TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)

        # 产品表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            category TEXT NOT NULL,
            gender TEXT NOT NULL,
            price REAL NOT NULL,
            images TEXT,
            model_3d_url TEXT,
            thumbnail_url TEXT,
            sizes TEXT,
            stock INTEGER DEFAULT 0,
            is_published BOOLEAN DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (merchant_id) REFERENCES merchants(id)
        )
        """)

        # 订单表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            total_amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)

        # 订单明细表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            size TEXT NOT NULL,
            customization TEXT,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
        )
        """)

        # 3D 人体模型表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS body_models (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            source_image_url TEXT NOT NULL,
            model_3d_url TEXT NOT NULL,
            thumbnail_url TEXT,
            measurements TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 任务表（AI 生成任务）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            module TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            input TEXT,
            output TEXT,
            model_used TEXT,
            cost_credits INTEGER DEFAULT 0,
            retry_count INTEGER DEFAULT 0,
            queue_position INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)

        # 模型健康监控表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS model_health (
            id TEXT PRIMARY KEY,
            model_name TEXT UNIQUE NOT NULL,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            last_error_at TEXT,
            is_disabled BOOLEAN DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 生成历史表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS generation_history (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            module TEXT NOT NULL,
            prompt TEXT,
            images TEXT,
            videos TEXT,
            cost INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)

        # 订单表（额度充值）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS credit_orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            price REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            paid_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)

        # 审计日志表(管理员操作不可变记录)
        # 不提供 UPDATE / DELETE 接口,只增不改
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            actor_user_id TEXT NOT NULL,
            actor_email TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            details TEXT,
            ip TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # P3-3:注册 IP 日志(反羊毛党 — 同 IP 24h 限 3 次成功注册)
        # registered_at_ts 用 unix 时间戳便于窗口比较
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS register_ip_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            registered_at_ts REAL NOT NULL
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_register_ip_log_ip ON register_ip_log(ip)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_register_ip_log_ts ON register_ip_log(registered_at_ts)")

        # BUG-1:注册失败 IP 日志(反脚本爆破 — 同 IP 24h 失败 >=10 次 → 429)
        # 上一轮 P3-3 只对"成功"计数,脚本反复试错 code 无配额,本表补这个洞
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS register_ip_failure_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            attempted_at_ts REAL NOT NULL,
            reason TEXT
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_register_ip_failure_log_ip ON register_ip_failure_log(ip)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_register_ip_failure_log_ts ON register_ip_failure_log(attempted_at_ts)")

        # 五十一续:异步任务失败退款追踪(refund_tracker 持久化)
        # 进程重启不丢退款记录;UPDATE refunded=1 WHERE refunded=0 SQL 原子保证只退一次
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_refunds (
            task_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            cost INTEGER NOT NULL,
            registered_at REAL NOT NULL,
            refunded INTEGER NOT NULL DEFAULT 0
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_refunds_registered_at ON pending_refunds(registered_at)")

        # 七十七续:口播带货工作台 oral_sessions 表(MVP 经济档先行)
        # 详见 docs/ORAL-BROADCAST-PLAN.md §4.1。SQLite hand-written,Phase 2 切 PG 时统一走 alembic。
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS oral_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tier TEXT NOT NULL,
            status TEXT NOT NULL,
            original_video_path TEXT NOT NULL,
            duration_seconds REAL NOT NULL,
            selected_models TEXT,
            selected_products TEXT,
            mask_image_path TEXT,
            person_mask_image_path TEXT,
            product_mask_image_path TEXT,
            extracted_audio_path TEXT,
            voice_ref_audio_path TEXT,
            asr_transcript TEXT,
            asr_word_timestamps TEXT,
            edited_transcript TEXT,
            voice_provider TEXT,
            voice_id TEXT,
            voice_id_created_at TEXT,
            new_audio_url TEXT,
            swap1_video_url TEXT,
            swap1_fal_request_id TEXT,
            swap_fal_request_id TEXT,
            swapped_video_url TEXT,
            lipsync_fal_request_id TEXT,
            final_video_url TEXT,
            final_video_archived TEXT,
            credits_charged INTEGER NOT NULL,
            credits_refunded INTEGER NOT NULL DEFAULT 0,
            error_step TEXT,
            error_message TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_oral_user ON oral_sessions(user_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_oral_status ON oral_sessions(status)")
        _patch_oral_columns(cursor)

        # P42 — 多素材编排子表(导演级工作流)
        # role:asset 在生成中的语义角色;type:媒体类型;alias:用户起的引用名(prompt 用 @alias)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS oral_session_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            type TEXT NOT NULL,
            url TEXT NOT NULL,
            alias TEXT,
            ord INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES oral_sessions(id) ON DELETE CASCADE
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_oral_assets_session ON oral_session_assets(session_id, ord)")

        # P110: ad-video 爆款话术库(替代 vlm_service.py 硬编码 prompt 素材)
        # kind: hook(钩子) / selling(卖点 3 件套) / cta(促单) / example(品类完整示例)
        # category: 服装/塑身/内衣/裤子/鞋/箱包/美妆/护肤/母婴/食品/家电/数码/家居/健身/宠物/办公/旅行 等;NULL=通用
        # region: CN(国内中文) / GLOBAL(海外英文)
        # text 加 UNIQUE 防同句重复 INSERT
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS viral_scripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            region TEXT NOT NULL,
            kind TEXT NOT NULL,
            category TEXT,
            text TEXT NOT NULL UNIQUE,
            source_url TEXT,
            scraped_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_viral_scripts_region_kind ON viral_scripts(region, kind)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_viral_scripts_category ON viral_scripts(category)")

        # P157(2026-05-06)credits_ledger 流水表(单一真相源,所有积分变化都记)
        # 每笔扣费/退款/admin 调账/充值入一条,balance_after 字段用于对账校验
        # SUM(delta) WHERE user_id=? 必须等于 users.credits(cron 每天对账)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS credits_ledger (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            delta INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            reason TEXT NOT NULL,
            ref_id TEXT,
            module TEXT,
            created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ledger_user_time ON credits_ledger(user_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ledger_ref ON credits_ledger(ref_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ledger_reason ON credits_ledger(reason)")

        # P221(2026-05-09) 视频复刻 V2 三张表(详见 docs/P221-API-SCHEMA.md / docs/P221-MIGRATION.sql)
        # type=single|ultimate;replacement_mode=partial|full
        # 全局两档:economy(input 2s, 15 积分) / standard(input 4s, 20 积分)
        # 双版本下载:final_video_url_watermarked(合规版 30% 不透明度) + final_video_url_raw(无标识版)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_clone_v2_jobs (
            id                       TEXT PRIMARY KEY,
            user_id                  TEXT NOT NULL,
            type                     TEXT NOT NULL,
            replacement_mode         TEXT NOT NULL,
            tier                     TEXT,
            segment_tiers            TEXT,
            input_video_url          TEXT NOT NULL,
            input_video_local_path   TEXT,
            input_video_duration_sec REAL NOT NULL,
            input_video_sha256       TEXT,
            image_urls               TEXT NOT NULL,
            prompt                   TEXT NOT NULL,
            prompt_compiled          TEXT,
            segments_plan            TEXT NOT NULL,
            segments_count           INTEGER NOT NULL,
            segments_results         TEXT NOT NULL DEFAULT '[]',
            final_video_url          TEXT,
            final_video_url_watermarked TEXT,
            final_video_url_raw      TEXT,
            final_video_local_path   TEXT,
            total_credits_charged    INTEGER NOT NULL,
            total_credits_refunded   INTEGER NOT NULL DEFAULT 0,
            fal_cost_total_usd       REAL NOT NULL DEFAULT 0,
            status                   TEXT NOT NULL DEFAULT 'pending',
            error_step               TEXT,
            error_message            TEXT,
            created_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at             TIMESTAMP,
            archived_at              TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vc2_user_time ON video_clone_v2_jobs(user_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vc2_status ON video_clone_v2_jobs(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vc2_archive ON video_clone_v2_jobs(archived_at, completed_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vc2_type ON video_clone_v2_jobs(type)")
        _patch_video_clone_v2_columns(cursor)

        # 每日 fal 总花销看板(保险 3,持久化跨重启)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_clone_v2_daily_budget (
            date        TEXT PRIMARY KEY,
            spent_usd   REAL NOT NULL DEFAULT 0,
            locked      INTEGER NOT NULL DEFAULT 0,
            updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 上传弹窗勾选 + 双版本下载选择留痕(法务 §4.4.4 / §4.4.6 事后举证用)
        # disclaimer_version: v1 = 当前 C1-C7 + A1-A5;改条款时升版本
        # watermark_choice: 用户在下载页选 raw(无标识) | watermarked(合规),null = 还没下载
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_clone_v2_disclaimer_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             TEXT NOT NULL,
            job_id              TEXT NOT NULL,
            ip                  TEXT,
            user_agent          TEXT,
            video_sha256        TEXT,
            disclaimer_version  TEXT NOT NULL DEFAULT 'v1',
            acknowledged_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            watermark_choice    TEXT,
            watermark_chosen_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (job_id) REFERENCES video_clone_v2_jobs(id)
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vc2_disclaimer_user ON video_clone_v2_disclaimer_log(user_id, acknowledged_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vc2_disclaimer_job ON video_clone_v2_disclaimer_log(job_id)")

        # 运营配置表(key-value,运行时可改,重启不丢)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_config (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        # 默认值:仅 INSERT OR IGNORE,不覆盖已有配置
        cursor.execute(
            "INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)",
            ("batch_max_duration", "8"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)",
            ("batch_whitelist", '["lirunting1a@gmail.com"]'),
        )

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trend_cache (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            platform   TEXT NOT NULL,
            category   TEXT NOT NULL,
            content    TEXT NOT NULL,
            source     TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 第三方 API 轮询队列：提交后立即入表，全局 harvester 统一轮询
        # 进程重启后 status=polling 的孤儿记录在启动时标 failed + 退积分
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS polling_queue (
            id           TEXT PRIMARY KEY,
            job_id       TEXT NOT NULL,
            request_id   TEXT NOT NULL,
            query_type   TEXT NOT NULL,
            endpoint_hint TEXT,
            user_id      TEXT,
            status       TEXT DEFAULT 'polling',
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            last_polled_at TEXT,
            poll_count   INTEGER DEFAULT 0,
            result_json  TEXT,
            error_text   TEXT
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_polling_queue_status ON polling_queue(status)")

        # 创建索引优化查询性能
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_generation_history_user_id ON generation_history(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_credit_orders_user_id ON credit_orders(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor_user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at)")

        conn.commit()
        print("Database initialized successfully!")


def get_app_config(key: str, default: str = "") -> str:
    """读取 app_config 表中的配置项;键不存在时返回 default。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_app_config(key: str, value: str) -> None:
    """写入或更新 app_config 配置项。"""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value),
        )
        conn.commit()


if __name__ == "__main__":
    init_db()
