"""
任务 API - 接入 FAL 真实状态查询，完成时保存历史记录
"""
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from typing import Dict, Set, Optional
from app.services.fal_service import get_video_service
from app.api.auth import get_current_user

router = APIRouter()
active_connections: Dict[str, Set[WebSocket]] = {}

# 每个 task_id 一个共享的 polling task,所有订阅者复用同一次 FAL 查询
_polling_tasks: Dict[str, asyncio.Task] = {}
# polling 间隔与超时(测试可 monkeypatch)
POLL_INTERVAL_SEC: float = 3.0
POLL_MAX_ITERATIONS: int = 240  # 12 分钟兜底,FAL 任务最长约 10 分


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


async def _broadcast(task_id: str, payload: dict) -> None:
    """把 payload 推给所有订阅 task_id 的 WS,失败的连接顺手摘掉。"""
    conns = active_connections.get(task_id)
    if not conns:
        return
    dead = []
    for ws in list(conns):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        conns.discard(ws)
    if not conns:
        active_connections.pop(task_id, None)


async def _poll_fal_task(task_id: str, endpoint_hint: Optional[str]) -> None:
    """后台轮询 FAL 任务状态并 broadcast 进度。

    - 终态(completed/failed)推完 final 后关所有连接 + 清理归属注册表
    - 12 分钟超时兜底,推 timeout + 关连接
    - 轮询过程中所有订阅者都掉线了就停
    """
    from app.services import task_ownership

    service = get_video_service()
    try:
        for _ in range(POLL_MAX_ITERATIONS):
            await asyncio.sleep(POLL_INTERVAL_SEC)

            if not active_connections.get(task_id):
                # 没人订阅了,提前停
                return

            try:
                result = await service.get_task_status(task_id, endpoint_hint=endpoint_hint)
            except Exception as e:
                # FAL 接口抖动:推个 processing 占位,下一轮再试
                result = {"status": "processing", "error": f"poll error: {e}"}

            status = result.get("status", "processing")
            payload = {
                "task_id": task_id,
                "status": status,
                "result_url": result.get("video_url"),
                "error": result.get("error"),
            }
            await _broadcast(task_id, payload)

            if status in ("completed", "failed"):
                conns = active_connections.pop(task_id, set())
                for ws in list(conns):
                    try:
                        await ws.close(code=1000, reason="task done")
                    except Exception:
                        pass
                task_ownership.unregister(task_id)
                return

        # 跑到这说明超时
        await _broadcast(task_id, {
            "task_id": task_id,
            "status": "failed",
            "result_url": None,
            "error": "polling timeout",
        })
        conns = active_connections.pop(task_id, set())
        for ws in list(conns):
            try:
                await ws.close(code=1000, reason="timeout")
            except Exception:
                pass
        task_ownership.unregister(task_id)
    finally:
        _polling_tasks.pop(task_id, None)


@router.websocket("/ws/{task_id}")
async def websocket_task_updates(websocket: WebSocket, task_id: str):
    """任务进度推送 WebSocket。

    鉴权(分两层,失败均不暴露差异):
    - 4401:token 缺失/无效/过期/吊销/类型错(refresh 不能调业务)
    - 4403:token 有效但当前用户不是该 task 的 owner(或 task 未注册/已过期)
    - 4xxx 是应用级 close code 约定

    归属注册由各 FAL 提交端点(video / avatar)调 task_ownership.register 完成。

    推送:接到首个连接时启动后台 polling task(共享给同 task 的所有订阅者),
    定期查 FAL 状态 → broadcast。可选 ?endpoint=<edit|edit-o3|reference|i2v>
    告诉后端去哪个 FAL 端点查(对应提交时返回的 endpoint_tag,不传则默认 i2v)。
    """
    from app.services.auth import decode_jwt_token
    from app.services import task_ownership

    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=4401, reason="token required")
        return
    payload = decode_jwt_token(token)
    if not payload:
        await websocket.close(code=4401, reason="invalid or expired token")
        return

    user_id = payload.get("user_id") or payload.get("sub")
    if not user_id or not task_ownership.verify(task_id, user_id):
        await websocket.close(code=4403, reason="not your task")
        return

    endpoint_hint = websocket.query_params.get("endpoint")

    await websocket.accept()
    if task_id not in active_connections:
        active_connections[task_id] = set()
    active_connections[task_id].add(websocket)

    # 同 task_id 的多客户端共享同一个 polling
    if task_id not in _polling_tasks:
        _polling_tasks[task_id] = asyncio.create_task(_poll_fal_task(task_id, endpoint_hint))

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        conns = active_connections.get(task_id)
        if conns is not None:
            conns.discard(websocket)
            if not conns:
                active_connections.pop(task_id, None)
                # 没订阅者了,polling 下次循环开头会自然退出
