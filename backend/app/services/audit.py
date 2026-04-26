"""审计日志服务

写入管理员操作不可变记录。设计原则:
- 写失败**不阻塞业务流程**(只 log_error,不抛异常)
- 不提供 update / delete 接口,只增不改
- details 用 JSON 存放任意结构,扩展性好
"""
import json
import uuid
from typing import Optional, Dict, Any

from ..database import get_db


# 已知 action 枚举(扩展时直接加,不强制校验)
ACTION_ADJUST_CREDITS = "adjust_credits"
ACTION_SET_ROLE = "set_role"
ACTION_LOGIN_AS = "login_as"
ACTION_DELETE_USER = "delete_user"
ACTION_FORCE_LOGOUT = "force_logout"
ACTION_CONFIRM_ORDER = "confirm_order"
ACTION_RESET_MODEL = "reset_model"


def log_admin_action(
    actor_user_id: str,
    actor_email: Optional[str],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    ip: Optional[str] = None,
) -> bool:
    """记录管理员操作。返回是否写入成功;**失败不抛异常**,业务流程继续。"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO audit_log
                (id, actor_user_id, actor_email, action, target_type, target_id, details, ip)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(uuid.uuid4()),
                actor_user_id,
                actor_email,
                action,
                target_type,
                target_id,
                json.dumps(details, ensure_ascii=False) if details else None,
                ip,
            ))
            conn.commit()
        return True
    except Exception as e:
        try:
            from .logger import log_error
            log_error(f"审计日志写失败 action={action}", error=str(e))
        except Exception:
            pass
        return False


def list_audit_log(
    limit: int = 100,
    actor_user_id: Optional[str] = None,
    action: Optional[str] = None,
):
    """读审计日志(管理员查询用,业务侧不直接暴露)"""
    sql = "SELECT id, actor_user_id, actor_email, action, target_type, target_id, details, ip, created_at FROM audit_log WHERE 1=1"
    params = []
    if actor_user_id:
        sql += " AND actor_user_id = ?"
        params.append(actor_user_id)
    if action:
        sql += " AND action = ?"
        params.append(action)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    return [
        {
            "id": r[0],
            "actor_user_id": r[1],
            "actor_email": r[2],
            "action": r[3],
            "target_type": r[4],
            "target_id": r[5],
            "details": json.loads(r[6]) if r[6] else None,
            "ip": r[7],
            "created_at": r[8],
        }
        for r in rows
    ]
