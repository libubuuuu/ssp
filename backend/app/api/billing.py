"""积分流水 API — 面向普通用户

GET /api/billing/ledger   分页返回当前用户的 credits_ledger 流水
"""
from fastapi import APIRouter, Depends, Query
from app.api.auth import get_current_user
from app.database import get_db

router = APIRouter()

# reason → 中文标签
_REASON_LABEL = {
    "task_charge":         "扣费",
    "task_refund":         "退款",
    "task_partial_refund": "部分退款",
    "recharge_manual":     "充值",
    "recharge_wx":         "微信充值",
    "recharge_alipay":     "支付宝充值",
    "recharge_hupijiao":   "虎皮椒充值",
    "register_bonus":      "注册赠送",
    "admin_adjust":        "管理员调整",
    "system_compensation": "系统补偿",
    "init_ledger_backfill":     "历史补录",
    "admin_restore_db_corruption_20260523": "系统补偿",
    "ledger_correction_20260524": "账本修正",
}


@router.get("/ledger")
async def get_ledger(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """返回当前用户积分流水，按时间倒序，分页。"""
    user_id = str(current_user["id"])
    offset = (page - 1) * limit

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM credits_ledger WHERE user_id = ?",
            (user_id,),
        )
        total = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT id, delta, balance_after, reason, ref_id, module, created_at
              FROM credits_ledger
             WHERE user_id = ?
             ORDER BY created_at DESC
             LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )
        rows = cursor.fetchall()

    _MODULE_LABEL = {
        "aiview/gpt-image-2":       "AI 图片（标准）",
        "aiview/aiview-pro":         "AI 图片（专业）",
        "aiview/seedance-2-0-fast":  "AI 视频复刻",
        "video/clone-v2":            "AI 视频复刻",
        "payment/hupijiao":          "虎皮椒支付",
        "image/style":               "AI 图片",
        "image/multi-reference":     "AI 图片（多参考）",
        "video/image-to-video":      "AI 视频",
        "video/clone":               "AI 视频复刻",
        "video/general":             "AI 爆款视频",
    }

    records = []
    for row in rows:
        reason = row[3] or ""
        raw_module = row[5] or ""
        module_label = _MODULE_LABEL.get(raw_module) or (raw_module.split("/", 1)[-1] if "/" in raw_module else raw_module) or None
        records.append({
            "id":           row[0],
            "delta":        row[1],
            "balance_after": row[2],
            "reason":       reason,
            "label":        _REASON_LABEL.get(reason, reason),
            "ref_id":       row[4],
            "module":       module_label,
            "created_at":   row[6],   # unix timestamp float
        })

    return {
        "records": records,
        "total":   total,
        "page":    page,
        "pages":   max(1, (total + limit - 1) // limit),
    }
