import os
"""
管理员 API
- 模型健康状态
- 任务队列状态
- 平台统计数据
"""
from fastapi import UploadFile, File, APIRouter, HTTPException, Depends, Request
from typing import Optional
from ..services.circuit_breaker import get_circuit_breaker
from ..services.task_queue import get_task_queue
from ..database import get_db
from .auth import get_current_user

router = APIRouter()


def _check_admin_role(current_user: dict) -> None:
    """非 Depends 版本,给 17 处 inline check 用"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    # 强制 2FA — 默认关(scaffolding pattern,与 Sentry/CF/Redis 一致):
    # 用户在 /profile/2fa 给 admin 账号 enroll 2FA 后,再 .env.enc 设
    # ADMIN_2FA_REQUIRED=true 重启 supervisor 真启用。详见 docs/ADMIN-2FA.md
    if os.environ.get("ADMIN_2FA_REQUIRED", "false").lower() == "true":
        if not current_user.get("totp_enabled"):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "ADMIN_2FA_REQUIRED",
                    "message": "管理员账号必须启用 2FA 才能访问后台,请先到 /profile/2fa 设置",
                    "redirect": "/profile/2fa",
                },
            )


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """验证管理员权限 + 强制 2FA"""
    _check_admin_role(current_user)
    return current_user


@router.get("/models/status")
async def get_models_status(_admin: dict = Depends(require_admin)):
    """获取所有模型健康状态"""
    circuit_breaker = get_circuit_breaker()
    return {"models": circuit_breaker.get_all_models_status()}


@router.get("/models/{model_name}/status")
async def get_model_status(model_name: str, _admin: dict = Depends(require_admin)):
    """获取指定模型健康状态"""
    circuit_breaker = get_circuit_breaker()
    return circuit_breaker.get_state(model_name)


@router.get("/studio-model-status")
async def get_studio_model_status(_admin: dict = Depends(require_admin)):
    """七十六续:长视频工作台模型切换可观测性。
    返回:
    - config:三个 env 当前值(空 = 未配置,走默认)
    - resolved:每个 mode 实际解析出的 endpoint + source
    - batch_stats:STUDIO_TASKS 内 batch_results 聚合(GC 24h,自然是近 24h 视图)
    - top_errors:失败原因 top 3
    """
    from collections import Counter
    from ..config import get_settings
    from ..services.fal_service import FalVideoService
    from .video_studio import STUDIO_TASKS

    settings = get_settings()
    config = {
        "STUDIO_VIDEO_MODEL_EDIT": settings.STUDIO_VIDEO_MODEL_EDIT,
        "STUDIO_VIDEO_MODEL_EDIT_O3": settings.STUDIO_VIDEO_MODEL_EDIT_O3,
        "STUDIO_VIDEO_MODEL_OVERRIDE": settings.STUDIO_VIDEO_MODEL_OVERRIDE,
    }

    # 直接用类实例,_resolve_endpoint 不依赖 fal_key,避免 get_video_service() 在某些
    # 启动路径(测试 fixture)未 init 时返 None。
    svc = FalVideoService(fal_key=settings.FAL_KEY or "")
    resolved = {}
    for model_key in ("kling/edit", "kling/edit-o3"):
        endpoint, source = svc._resolve_endpoint(model_key)
        resolved[model_key] = {"endpoint": endpoint, "source": source}

    total = completed = failed = other = 0
    err_counter: Counter = Counter()
    for task in STUDIO_TASKS.values():
        for r in (task.get("batch_results") or []):
            total += 1
            st = r.get("status")
            if st == "completed":
                completed += 1
            elif st == "failed":
                failed += 1
                err = (r.get("error") or "unknown")[:120]
                err_counter[err] += 1
            else:
                other += 1
    top_errors = [{"error": e, "count": c} for e, c in err_counter.most_common(3)]

    return {
        "config": config,
        "resolved": resolved,
        "batch_stats": {
            "total_segments": total,
            "completed": completed,
            "failed": failed,
            "pending_or_running": other,
            "success_rate": (round(completed / total, 4) if total else None),
        },
        "top_errors": top_errors,
    }


@router.post("/models/{model_name}/reset")
async def reset_model(model_name: str, request: Request, admin: dict = Depends(require_admin)):
    """重置模型状态（手动恢复）"""
    circuit_breaker = get_circuit_breaker()

    # 重置内存中的状态
    if model_name in circuit_breaker._states:
        circuit_breaker._states[model_name] = {
            "failures": 0,
            "successes": 0,
            "last_failure": None,
            "last_success": None,
            "state": "closed",
        }

    # 重置数据库状态
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE model_health
            SET success_count = 0, failure_count = 0, is_disabled = 0, last_error_at = NULL
            WHERE model_name = ?
        """, (model_name,))
        conn.commit()

    # 审计:系统级状态变更,出问题时方便追溯谁在什么时候重置过
    from app.services.audit import log_admin_action, ACTION_RESET_MODEL
    log_admin_action(
        actor_user_id=admin["id"],
        actor_email=admin.get("email"),
        action=ACTION_RESET_MODEL,
        target_type="model",
        target_id=model_name,
        ip=request.client.host if request.client else None,
    )

    return {"message": f"模型 {model_name} 已重置"}


@router.get("/queue/status")
async def get_queue_status(_admin: dict = Depends(require_admin)):
    """获取全局任务队列状态"""
    task_queue = get_task_queue()
    return task_queue.get_all_queues_status()


@router.get("/stats/overview")
async def get_stats_overview(_admin: dict = Depends(require_admin)):
    """获取平台统计概览"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 用户总数
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]

        # 今日任务数
        cursor.execute("""
            SELECT COUNT(*) FROM tasks
            WHERE DATE(created_at) = DATE('now')
        """)
        today_tasks = cursor.fetchone()[0]

        # 总任务数
        cursor.execute("SELECT COUNT(*) FROM tasks")
        total_tasks = cursor.fetchone()[0]

        # 今日收入（完成的订单）
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) FROM credit_orders
            WHERE status = 'paid' AND DATE(paid_at) = DATE('now')
        """)
        today_revenue = cursor.fetchone()[0]

        # 模型使用统计
        cursor.execute("""
            SELECT model_used, COUNT(*) as count
            FROM tasks
            WHERE model_used IS NOT NULL
            GROUP BY model_used
        """)
        model_usage = [{"model": row[0], "count": row[1]} for row in cursor.fetchall()]

        # 任务状态统计
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM tasks
            GROUP BY status
        """)
        task_status = [{"status": row[0], "count": row[1]} for row in cursor.fetchall()]

        return {
            "total_users": total_users,
            "total_tasks": total_tasks,
            "today_tasks": today_tasks,
            "today_revenue": today_revenue,
            "model_usage": model_usage,
            "task_status": task_status,
        }


@router.get("/tasks/recent")
async def get_recent_tasks(limit: Optional[int] = 20, _admin: dict = Depends(require_admin)):
    """获取最近的任务"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, module, status, model_used, cost_credits, created_at
            FROM tasks
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))

        tasks = []
        for row in cursor.fetchall():
            tasks.append({
                "id": row[0],
                "user_id": row[1],
                "module": row[2],
                "status": row[3],
                "model_used": row[4],
                "cost_credits": row[5],
                "created_at": row[6],
            })

        return {"tasks": tasks}


@router.get("/orders")
async def admin_list_orders(status: str = "all", current_user: dict = Depends(get_current_user)):
    """管理员：查所有订单（status=pending/paid/all）"""
    _check_admin_role(current_user)
    
    with get_db() as conn:
        cursor = conn.cursor()
        if status == "all":
            cursor.execute("""
                SELECT o.id, o.user_id, u.email, o.amount, o.price, o.status, o.created_at, o.paid_at
                FROM credit_orders o LEFT JOIN users u ON o.user_id = u.id
                ORDER BY o.created_at DESC LIMIT 200
            """)
        else:
            cursor.execute("""
                SELECT o.id, o.user_id, u.email, o.amount, o.price, o.status, o.created_at, o.paid_at
                FROM credit_orders o LEFT JOIN users u ON o.user_id = u.id
                WHERE o.status = ?
                ORDER BY o.created_at DESC LIMIT 200
            """, (status,))
        rows = cursor.fetchall()
    
    orders = [{
        "id": r[0], "user_id": r[1], "user_email": r[2],
        "credits": r[3], "price": r[4], "status": r[5],
        "created_at": r[6], "paid_at": r[7],
    } for r in rows]
    return {"orders": orders, "total": len(orders)}


@router.get("/users-list")
async def admin_list_users(current_user: dict = Depends(get_current_user)):
    """管理员：列出所有用户"""
    _check_admin_role(current_user)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, email, name, role, credits, created_at
            FROM users ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
    
    users = [{"id": r[0], "email": r[1], "name": r[2], "role": r[3], "credits": r[4], "created_at": r[5]} for r in rows]
    return {"users": users}


@router.post("/users/{user_id}/adjust-credits")
async def admin_adjust_credits(user_id: str, delta: int, request: Request, current_user: dict = Depends(get_current_user)):
    """管理员：手动加/减用户积分（delta 可正可负）

    P156(2026-05-06):改原子 UPDATE 修 race condition。
    之前用 SELECT + SET 两步,并发时可能覆盖用户的扣费操作。
    现在用 UPDATE credits = MAX(0, credits + ?) 一步原子完成,
    SQLite 在数据库层算 + floor,绝不会读到中间值。
    """
    _check_admin_role(current_user)

    with get_db() as conn:
        cursor = conn.cursor()
        # 先读旧余额，计算实际 delta（MAX(0,...) 可能让实扣小于请求值）
        cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        old_credits = row[0]
        cursor.execute(
            "UPDATE users SET credits = MAX(0, credits + ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (delta, user_id),
        )
        cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
        new_credits = cursor.fetchone()[0]
        actual_delta = new_credits - old_credits  # 真实变动量，对账准确
        # ledger 与积分变动同一事务，两者原子提交
        from app.services.billing import _ledger_in_tx
        _ledger_in_tx(cursor, user_id, actual_delta, new_credits,
                      "admin_adjust", current_user.get("id"), "admin/adjust-credits")
        conn.commit()

    # 审计日志(失败不阻塞业务)
    from app.services.audit import log_admin_action, ACTION_ADJUST_CREDITS
    log_admin_action(
        actor_user_id=current_user["id"],
        actor_email=current_user.get("email"),
        action=ACTION_ADJUST_CREDITS,
        target_type="user",
        target_id=user_id,
        details={"delta": actual_delta, "requested_delta": delta, "new_credits": new_credits},
        ip=request.client.host if request.client else None,
    )

    return {"success": True, "user_id": user_id, "new_credits": new_credits,
            "delta": actual_delta, "requested_delta": delta}


@router.get("/diagnose-history")
async def admin_diagnose_history(current_user: dict = Depends(get_current_user)):
    """列出 watchdog 告警时自动冻结的诊断快照(最近 100 份)"""
    _check_admin_role(current_user)
    import os
    SNAPSHOT_DIR = "/var/log/ssp-diagnose"
    if not os.path.isdir(SNAPSHOT_DIR):
        return {"snapshots": []}
    files = []
    try:
        for fn in sorted(os.listdir(SNAPSHOT_DIR), reverse=True):
            if not fn.endswith(".json"):
                continue
            full = os.path.join(SNAPSHOT_DIR, fn)
            stat = os.stat(full)
            # 文件名格式: 20260426-210501-CRIT.json
            level = "WARN"
            if "-CRIT" in fn:
                level = "CRIT"
            files.append({
                "filename": fn,
                "level": level,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
            })
    except Exception as e:
        return {"snapshots": [], "error": str(e)}
    return {"snapshots": files[:100]}


@router.get("/diagnose-snapshot/{filename}")
async def admin_diagnose_snapshot(filename: str, current_user: dict = Depends(get_current_user)):
    """读取单份快照内容"""
    _check_admin_role(current_user)
    import os, re, json
    # 严格校验 filename 格式,防路径穿越
    if not re.fullmatch(r"\d{8}-\d{6}-(CRIT|WARN)\.json", filename):
        raise HTTPException(400, "invalid filename")
    full = os.path.join("/var/log/ssp-diagnose", filename)
    if not os.path.isfile(full):
        raise HTTPException(404, "snapshot not found")
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        # 尝试解析 JSON 给前端友好渲染;失败返原文
        try:
            data = json.loads(content)
            return {"filename": filename, "data": data}
        except Exception:
            return {"filename": filename, "raw": content}
    except Exception as e:
        raise HTTPException(500, f"read failed: {e}")


@router.get("/diagnose")
async def admin_diagnose(current_user: dict = Depends(get_current_user)):
    """一键诊断快照 — 出问题时点一下就有完整报告,发给我精准定位。

    包含:
    - 时间戳 + 服务器健康
    - supervisor 4 服务状态
    - nginx 最近错误 + 最近请求统计(429/5xx/4xx 数)
    - 后端 ERROR 日志最近 30 行
    - watchdog 最近 10 条告警
    - 当前蓝绿状态
    - 数据库基础统计
    """
    _check_admin_role(current_user)

    import subprocess
    import os
    from datetime import datetime, timedelta

    def run(cmd: str, timeout: int = 5) -> str:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            return (r.stdout + r.stderr).strip()[:8000]  # 截 8KB 防爆
        except Exception as e:
            return f"(err: {e})"

    def tail(path: str, n: int = 30) -> list:
        if not os.path.exists(path):
            return [f"(file not found: {path})"]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return [ln.rstrip() for ln in f.readlines()[-n:]]
        except Exception as e:
            return [f"(read err: {e})"]

    # supervisor 状态
    sup = run("supervisorctl status")

    # nginx error 最近(过滤无关内容)
    nginx_err = tail("/var/log/nginx/error.log", 30)

    # 后端 ERROR 日志(blue + green)
    be_blue_err = tail("/var/log/ssp-backend-blue.err.log", 20)
    be_green_err = tail("/var/log/ssp-backend-green.err.log", 20)

    # nginx access 最近 5 分钟统计
    five_min_ago = (datetime.now() - timedelta(minutes=5)).strftime("%d/%b/%Y:%H:%M")
    access_stats = run(
        f"awk -v since='{five_min_ago}' "
        "'{ match($0, /\\[[^]]+\\]/); ts=substr($0, RSTART+1, 17); "
        "if (ts >= since && match($0, /\" ([0-9]{3}) /, m)) c[m[1]]++ } "
        "END { for (s in c) print s, c[s] }' "
        "/var/log/nginx/access.log 2>/dev/null | sort -rn -k 2"
    )

    # 当前 active 蓝绿(看 nginx proxy_pass 端口)
    active_port = run("grep -oP 'proxy_pass http://127.0.0.1:\\K[0-9]+' /etc/nginx/sites-enabled/default | head -1")
    active = "blue" if active_port == "8000" else "green" if active_port == "8001" else "unknown"

    # health
    health_code = run("curl -s -o /dev/null -w '%{http_code}' --max-time 5 https://ailixiao.com/health")

    # watchdog 最近告警
    watchdog_alerts = tail("/var/log/ssp-watchdog-alerts.log", 10)
    watchdog_log = tail("/var/log/ssp-watchdog.log", 5)

    # 数据库基础统计
    db_stats = {}
    try:
        with get_db() as conn:
            c = conn.cursor()
            for table in ("users", "tasks", "credit_orders", "audit_log"):
                try:
                    c.execute(f"SELECT COUNT(*) FROM {table}")
                    db_stats[table] = c.fetchone()[0]
                except Exception:
                    db_stats[table] = "?"
    except Exception as e:
        db_stats = {"error": str(e)}

    # 磁盘 + 内存
    disk_usage = run("df -h /root | tail -1 | awk '{print $5\" used (\"$3\"/\"$2\")\"}'")
    mem_usage = run("free -h | grep Mem | awk '{print $3\"/\"$2\" used\"}'")

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "health": health_code,
            "active_bluegreen": active,
            "active_port": active_port,
            "disk": disk_usage,
            "memory": mem_usage,
        },
        "supervisor": sup,
        "nginx_error_tail": nginx_err,
        "nginx_access_5min_stats": access_stats,
        "backend_blue_err_tail": be_blue_err,
        "backend_green_err_tail": be_green_err,
        "watchdog_alerts_tail": watchdog_alerts,
        "watchdog_recent_runs": watchdog_log,
        "db_stats": db_stats,
        "_usage_hint": "出问题时把这份 JSON 全部复制粘贴给 Claude,30 秒精准定位",
    }


@router.get("/watchdog")
async def admin_get_watchdog_status(current_user: dict = Depends(get_current_user)):
    """读 watchdog 最近报告 + 告警列表(供 admin dashboard 卡片用)"""
    _check_admin_role(current_user)

    import os
    LOG_PATH = "/var/log/ssp-watchdog.log"
    ALERTS_PATH = "/var/log/ssp-watchdog-alerts.log"

    def tail_lines(path: str, n: int) -> list:
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return [ln.rstrip() for ln in lines[-n:]]
        except Exception:
            return []

    log_recent = tail_lines(LOG_PATH, 30)
    alerts_recent = tail_lines(ALERTS_PATH, 50)

    # 最近 1 小时告警数(粗略统计 — 看时间戳)
    import re
    from datetime import datetime, timedelta
    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)
    recent_alerts_count = 0
    for ln in alerts_recent:
        m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", ln)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            if ts >= one_hour_ago:
                recent_alerts_count += 1
        except ValueError:
            continue

    # 最近一次 watchdog 跑的时间戳(从 log 末行抓)
    last_run = None
    if log_recent:
        m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", log_recent[-1])
        if m:
            last_run = m.group(1)

    # 整体状态判断
    last_log = log_recent[-1] if log_recent else ""
    if "[CRIT]" in last_log or "CRIT=" in last_log:
        overall = "critical"
    elif "WARN=" in last_log and "WARN=0" not in last_log:
        overall = "warn"
    elif last_log.startswith("") and "OK:" in last_log:
        overall = "ok"
    else:
        overall = "unknown"

    return {
        "overall": overall,
        "last_run": last_run,
        "recent_alerts_1h": recent_alerts_count,
        "log_tail": log_recent,
        "alerts_tail": alerts_recent,
    }


@router.get("/audit-log")
async def admin_list_audit_log(
    action: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    limit: int = 100,
    current_user: dict = Depends(get_current_user),
):
    """管理员查询审计日志。
    支持按 action / actor_user_id 过滤,按 created_at DESC,默认 100 条上限 500。
    """
    _check_admin_role(current_user)
    if limit > 500:
        limit = 500
    from app.services.audit import list_audit_log
    rows = list_audit_log(limit=limit, actor_user_id=actor_user_id, action=action)
    return {"total": len(rows), "logs": rows}


@router.post("/users/{user_id}/force-logout")
async def admin_force_logout(user_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    """管理员强制踢人:把目标用户在所有设备的 token 一次性失效"""
    _check_admin_role(current_user)

    # 验证目标用户存在
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        target_email = row[0]

    from app.services.auth import invalidate_user_tokens
    # P8 后 invalidate_user_tokens 返 int 时间戳(原 bool);> 0 即成功
    invalidate_ts = invalidate_user_tokens(user_id)

    # 写审计
    from app.services.audit import log_admin_action
    log_admin_action(
        actor_user_id=current_user["id"],
        actor_email=current_user.get("email"),
        action="force_logout",
        target_type="user",
        target_id=user_id,
        details={"target_email": target_email, "invalidate_ts": invalidate_ts},
        ip=request.client.host if request.client else None,
    )

    return {"success": True, "user_id": user_id, "message": "该用户所有 token 已失效"}


@router.post("/upload-qr")
async def admin_upload_qr(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """管理员上传收款码图片"""
    _check_admin_role(current_user)
    
    # 保存到 frontend/public/qr-payment.png(项目根的相对路径,与部署位置解耦)
    from pathlib import Path
    _project_root = Path(__file__).resolve().parents[3]
    target = str(_project_root / "frontend" / "public" / "qr-payment.png")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    
    contents = await file.read()
    # 简单校验：必须是图片
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="必须上传图片")
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片超过 5MB")
    
    with open(target, "wb") as f:
        f.write(contents)

    # 加个时间戳避免浏览器缓存
    import time
    return {"success": True, "url": f"/qr-payment.png?v={int(time.time())}", "size": len(contents)}


# ==================== 七十七续 P7:口播任务运营 / 巡检 ====================


@router.get("/oral-tasks")
async def admin_oral_tasks(
    status: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    _admin: dict = Depends(require_admin),
):
    """口播任务总览 + 列表 + 失败 top 原因。

    summary:总数 / 各 status 计数 / 平均时长 / 平均净扣积分 / 总净扣
    failure_top:失败 top 5(error_step + error_message + count)
    items:列表(每条含 user_email / step_progress / credits_net / error_message)
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    with get_db() as conn:
        cursor = conn.cursor()

        # 1) 各 status 计数
        cursor.execute("SELECT status, COUNT(*) FROM oral_sessions GROUP BY status")
        status_counts = {r[0]: r[1] for r in cursor.fetchall()}

        # 2) 总览聚合
        cursor.execute(
            """SELECT COUNT(*),
                      AVG(duration_seconds),
                      AVG(credits_charged - credits_refunded),
                      SUM(credits_charged - credits_refunded)
               FROM oral_sessions"""
        )
        agg = cursor.fetchone() or (0, 0, 0, 0)

        # 3) 失败 top 5
        cursor.execute(
            """SELECT error_step, SUBSTR(COALESCE(error_message, ''), 1, 120) AS msg, COUNT(*) AS c
               FROM oral_sessions
               WHERE status LIKE 'failed_%' AND error_message IS NOT NULL
               GROUP BY error_step, msg
               ORDER BY c DESC
               LIMIT 5"""
        )
        failure_top = [
            {"step": r[0], "message": r[1], "count": r[2]}
            for r in cursor.fetchall()
        ]

        # 4) 列表
        sql = (
            "SELECT s.id, s.user_id, u.email, s.tier, s.status, "
            "s.duration_seconds, s.credits_charged, s.credits_refunded, "
            "s.error_step, s.error_message, s.final_video_url, "
            "s.created_at, s.completed_at, "
            "(s.asr_transcript IS NOT NULL), (s.edited_transcript IS NOT NULL), "
            "(s.new_audio_url IS NOT NULL), (s.swapped_video_url IS NOT NULL), "
            "(s.final_video_url IS NOT NULL) "
            "FROM oral_sessions s LEFT JOIN users u ON u.id = s.user_id WHERE 1=1"
        )
        params: list = []
        if status:
            sql += " AND s.status = ?"
            params.append(status)
        if tier:
            sql += " AND s.tier = ?"
            params.append(tier)
        sql += " ORDER BY s.created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor.execute(sql, params)

        items = []
        for r in cursor.fetchall():
            items.append({
                "id": r[0],
                "user_id": r[1],
                "user_email": r[2],
                "tier": r[3],
                "status": r[4],
                "duration_seconds": r[5],
                "credits_charged": r[6],
                "credits_refunded": r[7],
                "credits_net": (r[6] or 0) - (r[7] or 0),
                "error_step": r[8],
                "error_message": (r[9] or "")[:200] if r[9] else None,
                "final_video_url": r[10],
                "created_at": r[11],
                "completed_at": r[12],
                "step_progress": {
                    "step1_asr": bool(r[13]),
                    "step2_edit": bool(r[14]),
                    "step3_audio": bool(r[15]),
                    "step4_swap": bool(r[16]),
                    "step5_final": bool(r[17]),
                },
            })

    return {
        "summary": {
            "total": agg[0] or 0,
            "avg_duration_seconds": round(agg[1] or 0, 1),
            "avg_net_credits": round(agg[2] or 0, 1),
            "total_net_credits": agg[3] or 0,
            "status_counts": status_counts,
        },
        "failure_top": failure_top,
        "items": items,
    }


@router.get("/oral-tasks/{session_id}")
async def admin_oral_task_detail(session_id: str, _admin: dict = Depends(require_admin)):
    """单条 oral session 完整字段(运营 drill-down)。

    用途:用户报"这条结果不对"时,看 ASR 听对没 / 编辑文案 / 中间产物 URL /
    fal request_id,定位是哪一步出问题。

    selected_models / selected_products 后端 json.loads 解析后返,前端直接渲染。
    """
    import json as _json

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """SELECT s.*, u.email AS user_email
               FROM oral_sessions s LEFT JOIN users u ON u.id = s.user_id
               WHERE s.id = ?""",
            (session_id,),
        )
        row = c.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="oral session 不存在")

    d = dict(row)

    # JSON 字段解析(失败时保留原 string,前端能看到坏数据)
    for k in ("selected_models", "selected_products", "asr_word_timestamps"):
        v = d.get(k)
        if v:
            try:
                d[k] = _json.loads(v)
            except Exception:
                pass  # 保留原 string

    # 派生字段:净扣 + 已耗时
    d["credits_net"] = (d.get("credits_charged") or 0) - (d.get("credits_refunded") or 0)

    # original_video_url 派生(同 /api/oral/status 逻辑)
    orig_path = d.get("original_video_path") or ""
    if orig_path.startswith("/opt/ssp/uploads/oral/"):
        d["original_video_url"] = orig_path.replace("/opt/ssp/uploads", "/uploads")
    else:
        d["original_video_url"] = None

    return d


@router.get("/v2-cost-report")
async def admin_v2_cost_report(_admin: dict = Depends(require_admin)):
    """V2 视频复刻 fal 成本对账报告（估算值，以 fal dashboard 账单为准）。

    对账方法：
    1. 登录 fal.ai → Billing → Export CSV（选对应月份）
    2. 用下方 estimated_usd_by_month 对应月份数字做对比
    3. 差额 > 10% 则排查（可能是估算倍率偏差或新段长分布变化）
    """
    with get_db() as conn:
        # 按月汇总 completed job 的估算成本
        monthly = conn.execute(
            """SELECT strftime('%Y-%m', completed_at) AS month,
                      COUNT(*) AS jobs,
                      ROUND(SUM(fal_cost_total_usd), 4) AS estimated_usd,
                      SUM(CASE WHEN type='single' THEN 1 ELSE 0 END) AS single_jobs,
                      SUM(CASE WHEN type='ultimate' THEN 1 ELSE 0 END) AS ultimate_jobs
               FROM video_clone_v2_jobs
               WHERE status IN ('completed', 'partial_completed')
                 AND completed_at IS NOT NULL
               GROUP BY month
               ORDER BY month DESC
               LIMIT 12""",
        ).fetchall()

        # 每日预算累计表（也是估算）
        daily = conn.execute(
            """SELECT date, ROUND(spent_usd, 4) AS spent_usd
               FROM video_clone_v2_daily_budget
               ORDER BY date DESC
               LIMIT 30""",
        ).fetchall()

        # 全时段合计
        totals = conn.execute(
            """SELECT COUNT(*) AS total_jobs,
                      ROUND(SUM(fal_cost_total_usd), 4) AS total_estimated_usd
               FROM video_clone_v2_jobs
               WHERE status IN ('completed', 'partial_completed')""",
        ).fetchone()

    return {
        "note": "以下均为估算值（实际段长×$0.0925×1.3），以 fal.ai Billing 账单为准",
        "reconcile_instructions": [
            "1. 登录 fal.ai → Billing → Export CSV",
            "2. 筛选 endpoint 含 seedance 的行，SUM(amount)",
            "3. 与下方 estimated_usd_by_month 对比，差额 > 10% 需排查",
        ],
        "total_jobs": totals["total_jobs"] if totals else 0,
        "total_estimated_usd": totals["total_estimated_usd"] if totals else 0,
        "estimated_usd_by_month": [dict(r) for r in monthly],
        "daily_spend_last_30d": [dict(r) for r in daily],
    }


@router.get("/model-usage")
async def admin_model_usage(_admin: dict = Depends(require_admin)):
    """对账单：jobs.json 模型用量 + video_clone_v2 分层 + credits_ledger 全量汇总。

    全程只读，不触碰计费/积分/表结构。
    """
    import json as _json
    from pathlib import Path as _Path
    from collections import defaultdict as _defaultdict

    # ── 1. jobs.json 读取与聚合 ──────────────────────────────────────────
    _jobs_file = _Path(os.environ.get(
        "JOBS_FILE",
        str(_Path(__file__).resolve().parents[3] / "jobs_data" / "jobs.json"),
    ))
    jobs_raw: dict = {}
    if _jobs_file.exists():
        try:
            import fcntl as _fcntl
            with open(_jobs_file, "r", encoding="utf-8") as _f:
                _fcntl.flock(_f.fileno(), _fcntl.LOCK_SH)
                try:
                    jobs_raw = _json.loads(_f.read() or "{}")
                finally:
                    _fcntl.flock(_f.fileno(), _fcntl.LOCK_UN)
        except Exception as _e:
            print(f"admin model-usage: load jobs failed: {_e}")

    def _model_label(job: dict) -> str:
        jtype = job.get("type", "unknown")
        if jtype == "image":
            p = job.get("params") or {}
            m = (p.get("model") or "").strip() if isinstance(p, dict) else ""
            return m if m else "image/未标注"
        if jtype == "video_i2v":
            return "图生视频/未标注"
        if jtype in ("script_to_video", "video_general"):
            return "AI爆款视频"
        if jtype == "video_general_analyze":
            return "AI爆款/分析"
        if jtype == "video_general_storyboard":
            return "AI爆款/分镜"
        if jtype in ("replicate_analyze", "skill_analyze"):
            return "分镜复刻/分析"
        if jtype == "skill_replace":
            return "分镜复刻/替换"
        if jtype == "skill_generate":
            return "分镜复刻/生成"
        if jtype in ("replicate", "video_clone"):
            return "视频复刻V1"
        if jtype == "ad_video":
            return "广告视频"
        return job.get("module") or jtype

    groups: dict = _defaultdict(lambda: {
        "count": 0, "success": 0, "failed": 0, "cost_credits": 0, "users": set(),
    })
    for _job in jobs_raw.values():
        if not isinstance(_job, dict):
            continue
        _label = _model_label(_job)
        _g = groups[_label]
        _g["count"] += 1
        _st = _job.get("status", "")
        if _st == "completed":
            _g["success"] += 1
        elif _st in ("failed", "error"):
            _g["failed"] += 1
        _g["cost_credits"] += int(_job.get("cost") or 0)
        _uid = _job.get("user_id") or ""
        if _uid:
            _g["users"].add(_uid)

    jobs_usage = [
        {
            "model_label": _lbl,
            "count": _g["count"],
            "success": _g["success"],
            "failed": _g["failed"],
            "cost_credits": _g["cost_credits"],
            "unique_users": len(_g["users"]),
        }
        for _lbl, _g in sorted(groups.items(), key=lambda x: -x[1]["count"])
    ]

    # ── 2. video_clone_v2 + credits_ledger ──────────────────────────────
    with get_db() as conn:
        vc2_rows = conn.execute("""
            SELECT
                COALESCE(video_model, 'seedance-2-0-fast') AS video_model,
                COUNT(*) AS count,
                SUM(CASE WHEN status IN ('completed','partial_completed') THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                SUM(total_credits_charged)  AS credits_charged,
                SUM(total_credits_refunded) AS credits_refunded,
                ROUND(SUM(fal_cost_total_usd), 4) AS fal_usd,
                COUNT(DISTINCT user_id) AS unique_users
            FROM video_clone_v2_jobs
            GROUP BY video_model
            ORDER BY count DESC
        """).fetchall()

        ledger_rows = conn.execute("""
            SELECT
                cl.user_id,
                u.email   AS user_email,
                u.name    AS user_name,
                u.credits AS current_balance,
                SUM(CASE WHEN cl.delta < 0 THEN cl.delta ELSE 0 END) AS consumed,
                SUM(CASE WHEN cl.reason LIKE 'task_refund%' THEN cl.delta ELSE 0 END) AS refunded,
                SUM(CASE WHEN cl.reason LIKE 'recharge%'   THEN cl.delta ELSE 0 END) AS recharged,
                SUM(CASE WHEN cl.delta > 0
                         AND cl.reason NOT LIKE 'task_refund%'
                         AND cl.reason NOT LIKE 'recharge%'
                    THEN cl.delta ELSE 0 END) AS admin_adjusted,
                SUM(cl.delta) AS net,
                COUNT(*)      AS tx_count
            FROM credits_ledger cl
            LEFT JOIN users u ON u.id = cl.user_id
            GROUP BY cl.user_id
            ORDER BY ABS(SUM(cl.delta)) DESC
        """).fetchall()

    _vc2_labels = {
        "seedance-2-0-fast":     "极速",
        "seedance-2-0-standard": "标准",
        "seedance-1-0-fast":     "极速(v1)",
        "seedance-1-0-standard": "标准(v1)",
    }
    vc2_usage = [
        {
            "video_model":       r["video_model"],
            "display_name":      _vc2_labels.get(r["video_model"], r["video_model"]),
            "count":             r["count"],
            "completed":         r["completed"],
            "failed":            r["failed"],
            "credits_charged":   r["credits_charged"] or 0,
            "credits_refunded":  r["credits_refunded"] or 0,
            "fal_estimated_usd": r["fal_usd"] or 0,
            "unique_users":      r["unique_users"],
        }
        for r in vc2_rows
    ]

    ledger_summary = [
        {
            "user_id":        r["user_id"],
            "user_email":     r["user_email"] or "—",
            "user_name":      r["user_name"] or "—",
            "current_balance": r["current_balance"] or 0,
            "consumed":       r["consumed"] or 0,
            "refunded":       r["refunded"] or 0,
            "recharged":      r["recharged"] or 0,
            "admin_adjusted": r["admin_adjusted"] or 0,
            "net":            r["net"] or 0,
            "tx_count":       r["tx_count"],
        }
        for r in ledger_rows
    ]

    # ── 3. 全局汇总 ─────────────────────────────────────────────────────
    return {
        "jobs_usage":  jobs_usage,
        "vc2_usage":   vc2_usage,
        "ledger_summary": ledger_summary,
        "totals": {
            "total_jobs":              len(jobs_raw),
            "jobs_cost_credits":       sum(_g["cost_credits"] for _g in groups.values()),
            "vc2_net_cost_credits":    sum(r["credits_charged"] - r["credits_refunded"] for r in vc2_usage),
            "total_recharged_credits": sum(r["recharged"] for r in ledger_summary),
            "total_consumed_credits":  sum(abs(r["consumed"]) for r in ledger_summary),
            "total_refunded_credits":  sum(r["refunded"] for r in ledger_summary),
        },
    }


@router.get("/billing-detail")
async def admin_billing_detail(
    type: str,
    model_label: Optional[str] = None,
    user_id: Optional[str] = None,
    video_model: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    _admin: dict = Depends(require_admin),
):
    """对账单明细：按 type 返回单条记录列表（只读）。

    type=jobs   → jobs.json 中该 model_label 的所有任务
    type=ledger → credits_ledger 中该 user_id 的所有流水
    type=vc2    → video_clone_v2_jobs 中该 video_model 的所有任务
    """
    import json as _json, datetime as _dt
    from pathlib import Path as _Path

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    def _ts(v) -> str:
        if v is None:
            return "—"
        try:
            return _dt.datetime.fromtimestamp(float(v)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(v)[:16]

    # ── type=jobs ────────────────────────────────────────────────────────
    if type == "jobs":
        if not model_label:
            raise HTTPException(400, "model_label required for type=jobs")

        _jobs_file = _Path(os.environ.get(
            "JOBS_FILE",
            str(_Path(__file__).resolve().parents[3] / "jobs_data" / "jobs.json"),
        ))
        jobs_raw: dict = {}
        if _jobs_file.exists():
            try:
                import fcntl as _fcntl
                with open(_jobs_file, "r", encoding="utf-8") as _f:
                    _fcntl.flock(_f.fileno(), _fcntl.LOCK_SH)
                    try:
                        jobs_raw = _json.loads(_f.read() or "{}")
                    finally:
                        _fcntl.flock(_f.fileno(), _fcntl.LOCK_UN)
            except Exception:
                pass

        def _label(job: dict) -> str:
            jtype = job.get("type", "unknown")
            if jtype == "image":
                p = job.get("params") or {}
                m = (p.get("model") or "").strip() if isinstance(p, dict) else ""
                return m if m else "image/未标注"
            if jtype == "video_i2v":    return "图生视频/未标注"
            if jtype in ("script_to_video", "video_general"): return "AI爆款视频"
            if jtype == "video_general_analyze":  return "AI爆款/分析"
            if jtype == "video_general_storyboard": return "AI爆款/分镜"
            if jtype in ("replicate_analyze", "skill_analyze"): return "分镜复刻/分析"
            if jtype == "skill_replace":  return "分镜复刻/替换"
            if jtype == "skill_generate": return "分镜复刻/生成"
            if jtype in ("replicate", "video_clone"): return "视频复刻V1"
            if jtype == "ad_video":       return "广告视频"
            return job.get("module") or jtype

        matched = [
            j for j in jobs_raw.values()
            if isinstance(j, dict) and _label(j) == model_label
        ]
        matched.sort(key=lambda j: j.get("created_at") or 0, reverse=True)
        total = len(matched)
        page_items = matched[offset: offset + limit]

        # 批量查用户信息（一次 IN 查询）
        uids = list({j.get("user_id", "") for j in page_items if j.get("user_id")})
        user_map: dict = {}
        if uids:
            with get_db() as conn:
                ph = ",".join("?" * len(uids))
                rows = conn.execute(
                    f"SELECT id, email, name FROM users WHERE id IN ({ph})", uids
                ).fetchall()
                user_map = {r["id"]: {"email": r["email"] or "—", "name": r["name"] or "—"} for r in rows}

        result_rows = []
        for j in page_items:
            uid = j.get("user_id", "")
            u = user_map.get(uid, {})
            dur = None
            if j.get("finished_at") and j.get("started_at"):
                try:
                    dur = round(float(j["finished_at"]) - float(j["started_at"]))
                except Exception:
                    pass
            result_rows.append({
                "id":         j.get("id", "")[:8],
                "user_email": u.get("email", uid[:8] + "…"),
                "user_name":  u.get("name", "—"),
                "title":      (j.get("title") or "")[:40],
                "type":       j.get("type", ""),
                "module":     j.get("module", ""),
                "cost":       j.get("cost") or 0,
                "status":     j.get("status", ""),
                "created_at": _ts(j.get("created_at")),
                "duration_sec": dur,
            })
        return {"rows": result_rows, "total": total, "limit": limit, "offset": offset}

    # ── type=ledger ──────────────────────────────────────────────────────
    elif type == "ledger":
        if not user_id:
            raise HTTPException(400, "user_id required for type=ledger")

        def _reason_label(reason: str) -> str:
            if not reason:
                return "—"
            if reason == "task_charge":       return "任务扣费"
            if reason.startswith("task_refund"): return "任务退款"
            if reason.startswith("recharge"):  return "用户充值"
            if reason.startswith("admin"):    return "管理员调整"
            if reason == "init_ledger_backfill": return "初始化补录"
            if reason.startswith("ledger_correction"): return "账本修正"
            return reason

        with get_db() as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM credits_ledger WHERE user_id = ?", (user_id,)
            ).fetchone()
            total = total_row["cnt"] if total_row else 0
            rows = conn.execute(
                """SELECT id, delta, balance_after, reason, ref_id, module, created_at
                   FROM credits_ledger WHERE user_id = ?
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (user_id, limit, offset),
            ).fetchall()
            # 充值记录单独全量返回（通常极少，不分页）
            recharge_rows = conn.execute(
                """SELECT id, delta, balance_after, reason, ref_id, created_at
                   FROM credits_ledger WHERE user_id = ? AND reason LIKE 'recharge%'
                   ORDER BY created_at DESC""",
                (user_id,),
            ).fetchall()
        return {
            "rows": [
                {
                    "id":            r["id"][:8],
                    "delta":         r["delta"],
                    "balance_after": r["balance_after"],
                    "reason":        r["reason"],
                    "reason_label":  _reason_label(r["reason"]),
                    "ref_id":        (r["ref_id"] or "")[:12],
                    "module":        r["module"] or "—",
                    "created_at":    _ts(r["created_at"]),
                }
                for r in rows
            ],
            "recharges": [
                {
                    "id":            r["id"][:8],
                    "delta":         r["delta"],
                    "balance_after": r["balance_after"],
                    "reason":        r["reason"],
                    "ref_id":        (r["ref_id"] or "")[:20],
                    "created_at":    _ts(r["created_at"]),
                }
                for r in recharge_rows
            ],
            "total": total, "limit": limit, "offset": offset,
        }

    # ── type=vc2 ─────────────────────────────────────────────────────────
    elif type == "vc2":
        vm = video_model or "seedance-2-0-fast"
        with get_db() as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM video_clone_v2_jobs WHERE COALESCE(video_model,'seedance-2-0-fast')=?",
                (vm,),
            ).fetchone()
            total = total_row["cnt"] if total_row else 0
            rows = conn.execute(
                """SELECT id, user_id, type, replacement_mode, status,
                          total_credits_charged, total_credits_refunded,
                          fal_cost_total_usd, error_message,
                          created_at, completed_at
                   FROM video_clone_v2_jobs
                   WHERE COALESCE(video_model,'seedance-2-0-fast')=?
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (vm, limit, offset),
            ).fetchall()

            uids = list({r["user_id"] for r in rows if r["user_id"]})
            user_map2: dict = {}
            if uids:
                ph = ",".join("?" * len(uids))
                urs = conn.execute(
                    f"SELECT id, email, name FROM users WHERE id IN ({ph})", uids
                ).fetchall()
                user_map2 = {u["id"]: u["email"] or u["name"] or u["id"][:8] for u in urs}

        def _dur2(row) -> Optional[int]:
            try:
                if row["completed_at"] and row["created_at"]:
                    from datetime import datetime as _DT
                    fmt = "%Y-%m-%d %H:%M:%S"
                    a = _DT.strptime(str(row["created_at"])[:19], fmt)
                    b = _DT.strptime(str(row["completed_at"])[:19], fmt)
                    return int((b - a).total_seconds())
            except Exception:
                pass
            return None

        return {
            "rows": [
                {
                    "id":               r["id"][:8],
                    "user_email":       user_map2.get(r["user_id"], r["user_id"][:8] + "…"),
                    "type":             r["type"],
                    "replacement_mode": r["replacement_mode"],
                    "status":           r["status"],
                    "credits_charged":  r["total_credits_charged"],
                    "credits_refunded": r["total_credits_refunded"],
                    "fal_usd":          round(r["fal_cost_total_usd"] or 0, 3),
                    "error":            (r["error_message"] or "")[:60],
                    "created_at":       str(r["created_at"])[:16],
                    "duration_sec":     _dur2(r),
                }
                for r in rows
            ],
            "total": total, "limit": limit, "offset": offset,
        }

    else:
        raise HTTPException(400, f"unknown type: {type}")


@router.get("/billing-consumption")
async def admin_billing_consumption(
    page: int = 0,
    limit: int = 100,
    user_id: Optional[str] = None,
    export: bool = False,
    _admin: dict = Depends(require_admin),
):
    """积分消耗明细：task_charge 流水，含用户、接口、模型信息。export=true 返回全量。"""
    import datetime as _dt

    def _ts(v) -> str:
        try:
            return _dt.datetime.fromtimestamp(float(v)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(v)[:16]

    where = "cl.reason = 'task_charge'"
    params: list = []
    if user_id:
        where += " AND cl.user_id = ?"
        params.append(user_id)

    def _provider(module: str) -> str:
        if not module or module == "—": return "—"
        if module.startswith("aiview/"): return "aiview"
        if module.startswith("fal/"): return "fal"
        if module.startswith("image/"): return "fal"
        if "clone-v2" in module: return "aiview"
        if module.startswith("video/"): return "fal"
        if module == "register": return "system"
        return "—"

    with get_db() as conn:
        # 只统计净消耗 > 0 的（排除全额退款的失败任务）
        total = conn.execute(f"""
            SELECT COUNT(*) FROM credits_ledger cl
            WHERE {where}
            AND (
                cl.ref_id IS NULL
                OR ABS(cl.delta) - COALESCE(
                    (SELECT SUM(r.delta) FROM credits_ledger r
                     WHERE r.ref_id = cl.ref_id AND r.reason = 'task_refund'), 0
                ) > 0
            )
        """, params).fetchone()[0]

        actual_limit = 9999 if export else max(1, min(limit, 200))
        actual_offset = 0 if export else page * actual_limit

        rows = conn.execute(f"""
            SELECT cl.delta, cl.ref_id, cl.module, cl.created_at,
                   u.email, u.name,
                   v.video_model, v.type AS vc2_type,
                   COALESCE(
                       (SELECT SUM(r.delta) FROM credits_ledger r
                        WHERE r.ref_id = cl.ref_id AND r.reason = 'task_refund'), 0
                   ) AS total_refund
            FROM credits_ledger cl
            LEFT JOIN users u ON u.id = cl.user_id
            LEFT JOIN video_clone_v2_jobs v ON v.id = cl.ref_id
            WHERE {where}
            AND (
                cl.ref_id IS NULL
                OR ABS(cl.delta) - COALESCE(
                    (SELECT SUM(r.delta) FROM credits_ledger r
                     WHERE r.ref_id = cl.ref_id AND r.reason = 'task_refund'), 0
                ) > 0
            )
            ORDER BY cl.created_at DESC
            LIMIT ? OFFSET ?
        """, params + [actual_limit, actual_offset]).fetchall()

    def _model(r) -> str:
        if r["video_model"]:
            return f"{r['video_model']}({r['vc2_type'] or ''})"
        m = r["module"] or ""
        if "/" in m:
            return m.split("/", 1)[1]
        return m or "—"

    return {
        "rows": [
            {
                "时间":    _ts(r["created_at"]),
                "用户":    r["email"] or "—",
                "供应商":   _provider(r["module"] or ""),
                "模型/接口": _model(r),
                "消耗积分":  max(0, abs(int(r["delta"])) - int(r["total_refund"])),
                "任务ID":   (r["ref_id"] or "")[:20],
            }
            for r in rows
        ],
        "total": total,
        "page": page,
    }


@router.get("/billing-recharges")
async def admin_billing_recharges(
    export: bool = False,
    _admin: dict = Depends(require_admin),
):
    """充值入账：credit_orders 中 status=paid 的订单，与虎皮椒账单对应。"""
    import datetime as _dt

    def _ts(v) -> str:
        if not v:
            return "—"
        try:
            return _dt.datetime.fromtimestamp(float(v)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(v)[:16]

    with get_db() as conn:
        rows = conn.execute("""
            SELECT co.id, co.amount, co.price, co.paid_at, co.created_at,
                   u.email, u.name
            FROM credit_orders co
            LEFT JOIN users u ON u.id = co.user_id
            WHERE co.status = 'paid'
            ORDER BY co.created_at DESC
        """).fetchall()

    result = [
        {
            "时间":   _ts(r["paid_at"] or r["created_at"]),
            "用户":   r["email"] or "—",
            "充值积分": r["amount"],
            "金额(元)": r["price"],
            "订单号":  r["id"],
        }
        for r in rows
    ]
    return {
        "rows": result,
        "total": len(result),
        "total_amount": round(sum(r["金额(元)"] or 0 for r in result), 2),
        "total_credits": sum(r["充值积分"] or 0 for r in result),
    }


@router.get("/billing-gifts")
async def admin_billing_gifts(
    export: bool = False,
    _admin: dict = Depends(require_admin),
):
    """赠送积分：注册赠送(register_bonus) + 历史补录(init_ledger_backfill)。"""
    import datetime as _dt

    def _ts(v) -> str:
        try:
            return _dt.datetime.fromtimestamp(float(v)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(v)[:16]

    with get_db() as conn:
        rows = conn.execute("""
            SELECT cl.delta, cl.reason, cl.created_at,
                   u.email, u.name
            FROM credits_ledger cl
            LEFT JOIN users u ON u.id = cl.user_id
            WHERE cl.reason IN ('register_bonus', 'init_ledger_backfill')
            ORDER BY cl.created_at DESC
        """).fetchall()

    result = [
        {
            "时间":   _ts(r["created_at"]),
            "用户":   r["email"] or "—",
            "赠送积分": int(r["delta"]),
            "类型":   "注册赠送" if r["reason"] == "register_bonus" else "历史补录",
        }
        for r in rows
    ]
    return {
        "rows": result,
        "total": len(result),
        "total_credits": sum(r["赠送积分"] for r in result),
    }


@router.get("/billing-users")
async def admin_billing_users(_admin: dict = Depends(require_admin)):
    """用户消耗汇总列表：每个用户的成功/失败次数、净消耗积分。"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                cl.user_id,
                u.email,
                u.name,
                u.credits AS current_balance,
                COUNT(*) AS total_charges,
                SUM(CASE WHEN EXISTS(
                    SELECT 1 FROM credits_ledger r
                    WHERE r.ref_id = cl.ref_id AND r.reason='task_refund' AND cl.ref_id IS NOT NULL
                ) THEN 1 ELSE 0 END) AS failed_count,
                SUM(ABS(cl.delta)) AS gross_credits,
                SUM(COALESCE((
                    SELECT SUM(r.delta) FROM credits_ledger r
                    WHERE r.ref_id = cl.ref_id AND r.reason='task_refund'
                ), 0)) AS refunded_credits
            FROM credits_ledger cl
            LEFT JOIN users u ON u.id = cl.user_id
            WHERE cl.reason = 'task_charge'
            GROUP BY cl.user_id
            ORDER BY gross_credits DESC
        """).fetchall()
    return {
        "users": [
            {
                "user_id":        r["user_id"],
                "email":          r["email"] or "—",
                "name":           r["name"] or "—",
                "current_balance": r["current_balance"] or 0,
                "total_charges":  r["total_charges"],
                "failed_count":   r["failed_count"],
                "success_count":  r["total_charges"] - r["failed_count"],
                "gross_credits":  r["gross_credits"] or 0,
                "refunded_credits": abs(int(r["refunded_credits"] or 0)),
                "net_credits":    (r["gross_credits"] or 0) - abs(int(r["refunded_credits"] or 0)),
            }
            for r in rows
        ]
    }


@router.get("/billing-user-detail")
async def admin_billing_user_detail(
    user_id: str,
    page: int = 0,
    limit: int = 100,
    export: bool = False,
    _admin: dict = Depends(require_admin),
):
    """单用户消耗明细：成功+失败全显示，含模型/接口信息。"""
    import datetime as _dt

    def _ts(v) -> str:
        try:
            return _dt.datetime.fromtimestamp(float(v)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(v)[:16]

    def _provider(module: str) -> str:
        if not module: return "—"
        if module.startswith("aiview/"): return "aiview"
        if "clone-v2" in module: return "aiview"
        if module.startswith("fal/") or module.startswith("image/") or module.startswith("video/"): return "fal"
        return "—"

    actual_limit = 9999 if export else max(1, min(limit, 200))
    actual_offset = 0 if export else page * actual_limit

    with get_db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM credits_ledger WHERE reason='task_charge' AND user_id=?",
            (user_id,)
        ).fetchone()[0]

        rows = conn.execute("""
            SELECT cl.delta, cl.ref_id, cl.module, cl.created_at,
                   v.video_model, v.type AS vc2_type,
                   COALESCE((
                       SELECT SUM(r.delta) FROM credits_ledger r
                       WHERE r.ref_id = cl.ref_id AND r.reason = 'task_refund'
                   ), 0) AS total_refund
            FROM credits_ledger cl
            LEFT JOIN video_clone_v2_jobs v ON v.id = cl.ref_id
            WHERE cl.reason = 'task_charge' AND cl.user_id = ?
            ORDER BY cl.created_at DESC
            LIMIT ? OFFSET ?
        """, (user_id, actual_limit, actual_offset)).fetchall()

    def _model(r) -> str:
        if r["video_model"]:
            return f"{r['video_model']}({r['vc2_type'] or ''})"
        m = r["module"] or ""
        if "/" in m:
            return m.split("/", 1)[1]
        return m or "—"

    result = []
    for r in rows:
        gross = abs(int(r["delta"]))
        refund = int(r["total_refund"])
        net = gross - refund
        result.append({
            "时间":    _ts(r["created_at"]),
            "状态":    "失败" if net == 0 and refund > 0 else "成功",
            "供应商":  _provider(r["module"] or ""),
            "模型/接口": _model(r),
            "扣积分":  gross,
            "退积分":  refund,
            "净消耗":  net,
        })
    return {"rows": result, "total": total, "page": page}


@router.post("/update-trends")
async def manual_update_trends(_admin: dict = Depends(require_admin)):
    """手动触发每日趋势更新（不用等凌晨3点）。"""
    try:
        from app.services.trend_updater import run_trend_update
        success = await run_trend_update()
        return {"status": "ok", "updated": success}
    except Exception as e:
        raise HTTPException(500, f"趋势更新失败: {str(e)[:200]}")
