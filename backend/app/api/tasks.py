"""
任务 API - 接入 FAL 真实状态查询
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from typing import Dict, Set, Optional
from app.services.fal_service import get_video_service
from app.api.auth import get_current_user

router = APIRouter()
active_connections: Dict[str, Set[WebSocket]] = {}


@router.get("/status/{task_id}")
async def get_task_status(task_id: str, endpoint: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    video_service = get_video_service()
    result = await video_service.get_task_status(task_id, endpoint_hint=endpoint)

    if result.get("status") == "failed":
        try:
            from app.database import get_db
            from app.services.billing import add_credits
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT cost FROM generation_history WHERE id = ? AND user_id = ?", (task_id, current_user["id"]))
                row = cursor.fetchone()
                if row:
                    add_credits(current_user["id"], row[0])
                    result["refunded"] = row[0]
        except Exception:
            pass

    return {
        "task_id": task_id,
        "status": result.get("status", "processing"),
        "result_url": result.get("video_url"),
        "thumbnail_url": result.get("thumbnail_url"),
        "error": result.get("error"),
        "refunded": result.get("refunded"),
    }


@router.websocket("/ws/{task_id}")
async def websocket_task_updates(websocket: WebSocket, task_id: str):
    await websocket.accept()
    if task_id not in active_connections:
        active_connections[task_id] = set()
    active_connections[task_id].add(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        active_connections[task_id].discard(websocket)
        if not active_connections[task_id]:
            del active_connections[task_id]
