"""
任务 API - 接入 FAL 真实状态查询，完成时保存历史记录
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from typing import Dict, Set, Optional
from app.services.fal_service import get_video_service
from app.api.auth import get_current_user

router = APIRouter()
active_connections: Dict[str, Set[WebSocket]] = {}


@router.get("/status/{task_id}")
async def get_task_status(task_id: str, endpoint: Optional[str] = None, prompt: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    video_service = get_video_service()
    result = await video_service.get_task_status(task_id, endpoint_hint=endpoint)

    if result.get("status") == "completed" and result.get("video_url"):
        # 保存到历史记录
        try:
            from app.database import get_db
            import uuid, json
            with get_db() as conn:
                cursor = conn.cursor()
                # 检查是否已保存过（避免重复）
                cursor.execute("SELECT id FROM generation_history WHERE id = ?", (task_id,))
                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO generation_history (id, user_id, module, prompt, videos, cost)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        task_id,
                        current_user["id"],
                        "video/image-to-video",
                        prompt or "图生视频",
                        json.dumps([result["video_url"]]),
                        10
                    ))
                    conn.commit()
        except Exception as e:
            print(f"保存历史记录失败: {e}")

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


@router.get("/history")
async def get_history(current_user: dict = Depends(get_current_user)):
    """获取当前用户的生成历史"""
    try:
        from app.database import get_db
        import json
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, module, prompt, images, videos, cost, created_at
                FROM generation_history
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 100
            """, (current_user["id"],))
            rows = cursor.fetchall()
            history = []
            for row in rows:
                videos = []
                images = []
                try:
                    if row[4]: videos = json.loads(row[4])
                except: pass
                try:
                    if row[3]: images = json.loads(row[3])
                except: pass
                history.append({
                    "id": row[0],
                    "module": row[1],
                    "prompt": row[2],
                    "images": images,
                    "videos": videos,
                    "cost": row[5],
                    "created_at": row[6],
                })
            return {"history": history}
    except Exception as e:
        return {"history": [], "error": str(e)}


@router.websocket("/ws/{task_id}")
async def websocket_task_updates(websocket: WebSocket, task_id: str):
    """任务进度推送 WebSocket。

    鉴权:
    - 必须带 ?token=<access_token> query 参数(WebSocket 不支持 Authorization header,只能用 query)
    - 验签 + 用户级吊销(共用 decode_jwt_token,跟 HTTP API 同样规则)
    - 失败时 close code 4401(自定义,通用约定 4xxx 是应用级)

    未来可加(v2):验证 task_id 属于该 user_id(防偷看别人的进度)
    需要 tasks 表加 user_id 字段查询,留下次。
    """
    from app.services.auth import decode_jwt_token

    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=4401, reason="token required")
        return
    payload = decode_jwt_token(token)
    if not payload:
        await websocket.close(code=4401, reason="invalid or expired token")
        return

    await websocket.accept()
    if task_id not in active_connections:
        active_connections[task_id] = set()
    active_connections[task_id].add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_connections[task_id].discard(websocket)
        if not active_connections[task_id]:
            del active_connections[task_id]
