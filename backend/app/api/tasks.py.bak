"""
任务 API
- 查询状态
- WebSocket 实时推送 (多窗口同步)
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Set

router = APIRouter()

# 多窗口：task_id -> 连接的 WebSocket 集合
active_connections: Dict[str, Set[WebSocket]] = {}


@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """查询任务状态"""
    # TODO: 从 Redis/DB 查询
    return {
        "task_id": task_id,
        "status": "pending",  # pending | processing | completed | failed
        "progress": 0,
        "result_url": None,
        "error": None,
    }


@router.websocket("/ws/{task_id}")
async def websocket_task_updates(websocket: WebSocket, task_id: str):
    """WebSocket: 任务状态实时推送 (多窗口同步)"""
    await websocket.accept()
    
    if task_id not in active_connections:
        active_connections[task_id] = set()
    active_connections[task_id].add(websocket)
    
    try:
        while True:
            # 保持连接，等待服务端推送
            data = await websocket.receive_text()
            # 可处理客户端心跳等
    except WebSocketDisconnect:
        active_connections[task_id].discard(websocket)
        if not active_connections[task_id]:
            del active_connections[task_id]
