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
    """管理员：手动加/减用户积分（delta 可正可负）"""
    _check_admin_role(current_user)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        old_credits = row[0]
        new_credits = max(0, old_credits + delta)
        cursor.execute("UPDATE users SET credits = ? WHERE id = ?", (new_credits, user_id))
        conn.commit()

    # 审计日志(失败不阻塞业务)
    from app.services.audit import log_admin_action, ACTION_ADJUST_CREDITS
    log_admin_action(
        actor_user_id=current_user["id"],
        actor_email=current_user.get("email"),
        action=ACTION_ADJUST_CREDITS,
        target_type="user",
        target_id=user_id,
        details={"delta": delta, "old_credits": old_credits, "new_credits": new_credits},
        ip=request.client.host if request.client else None,
    )

    return {"success": True, "user_id": user_id, "new_credits": new_credits, "delta": delta}


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
