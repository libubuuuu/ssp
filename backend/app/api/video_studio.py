"""
长视频工作台 API
上传长视频 → 拆分 → 批量翻拍 → 拼接
"""
import os
import json
import re
import shutil
import uuid
import tempfile
import subprocess
import asyncio
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from pydantic import BaseModel
from app.services.fal_service import get_video_service
from app.services.billing import (
    get_task_cost,
    check_user_credits,
    deduct_credits,
    add_credits,
    create_consumption_record,
)
from app.api.auth import get_current_user

router = APIRouter()

# 工作区目录:默认项目根/studio_workspace,环境变量 SSP_STUDIO_DIR 可覆盖
# 路径策略:从本文件 (app/api/video_studio.py) 上溯 4 级 = backend/app/api → backend/app → backend → 项目根
_DEFAULT_STUDIO = Path(__file__).resolve().parents[3] / "studio_workspace"
STUDIO_DIR = Path(os.environ.get("SSP_STUDIO_DIR", str(_DEFAULT_STUDIO)))
STUDIO_DIR.mkdir(parents=True, exist_ok=True)

# 分片上传临时目录(完成后立刻清理)
UPLOAD_TMP_DIR = STUDIO_DIR / "_uploading"
UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)

# 持久化存储
SESSIONS_FILE = STUDIO_DIR / "sessions.json"

def _load_tasks():
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text())
        except:
            return {}
    return {}

def _save_tasks():
    try:
        SESSIONS_FILE.write_text(json.dumps(STUDIO_TASKS, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"save tasks failed: {e}")

STUDIO_TASKS = _load_tasks()


class ElementConfig(BaseModel):
    """元素配置"""
    name: str  # "模特A"、"产品1"
    main_image_url: str  # 主图URL (fal上传后的https URL)
    reference_image_urls: List[str] = []  # 参考图（0-3张）


class BatchGenerateRequest(BaseModel):
    """批量生成请求"""
    session_id: str  # 拆分时返回的session_id
    segments: List[dict]  # [{index, video_url, prompt}]
    elements: List[ElementConfig]  # 所有段落共用的元素（最多4个）
    mode: str = "o3"  # "o1" 或 "o3"


def _run_ffmpeg(cmd: List[str]) -> tuple:
    """执行 ffmpeg 命令"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return result.returncode == 0, result.stderr
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timeout"


def _get_video_duration(path: str) -> float:
    """获取视频时长（秒）"""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except:
        return 0.0


@router.post("/upload")
async def upload_video(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """上传长视频到工作区"""
    session_id = str(uuid.uuid4())[:8]
    session_dir = STUDIO_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # 保存原视频(流式 1MB 块写入,不一次性读全到内存)
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    video_path = session_dir / f"source{ext}"
    chunk_size = 1024 * 1024  # 1 MB
    with open(video_path, "wb") as f:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)

    size_bytes = video_path.stat().st_size
    duration = _get_video_duration(str(video_path))

    STUDIO_TASKS[session_id] = {
        "session_id": session_id,
        "user_id": str(current_user.get("id", "unknown")),
        "video_path": str(video_path),
        "duration": duration,
        "segments": [],
        "status": "uploaded",
    }
    _save_tasks()

    return {
        "session_id": session_id,
        "duration": round(duration, 2),
        "size_mb": round(size_bytes / 1024 / 1024, 2),
    }


@router.post("/upload-chunk")
async def upload_chunk(
    chunk: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_idx: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    """分片上传(YouTube/OSS 标配模式):
    - 前端把视频切 5MB 块,顺序调本端点
    - 单 chunk 远小于 nginx client_max_body_size,任意大小视频都能传
    - 最后一片到达时合并 + 创建 session,返回 session_id
    """
    # 验证 upload_id(只允许 hex 字符,长度 16,防路径穿越)
    if not re.fullmatch(r"[a-f0-9]{16}", upload_id):
        raise HTTPException(400, "invalid upload_id")
    if chunk_idx < 0 or total_chunks < 1 or chunk_idx >= total_chunks:
        raise HTTPException(400, "invalid chunk_idx/total_chunks")
    if total_chunks > 10000:  # 50GB 上限(每片 5MB × 10000)
        raise HTTPException(400, "too many chunks")

    user_id = str(current_user.get("id", "unknown"))
    upload_dir = UPLOAD_TMP_DIR / f"{user_id}_{upload_id}"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 流式写入这一片(1MB sub-chunk 节内存)
    chunk_path = upload_dir / f"{chunk_idx:06d}"
    with open(chunk_path, "wb") as f:
        while True:
            data = await chunk.read(1024 * 1024)
            if not data:
                break
            f.write(data)

    # 不是最后一片:回执
    if chunk_idx + 1 < total_chunks:
        return {"status": "chunk_received", "chunk_idx": chunk_idx, "received_bytes": chunk_path.stat().st_size}

    # 最后一片到达:校验所有 chunks 都在,合并 + 创建 session
    missing = [i for i in range(total_chunks) if not (upload_dir / f"{i:06d}").exists()]
    if missing:
        # 缺片(可能某片上传失败前端没补传)— 报错让前端补传
        raise HTTPException(400, f"missing chunks: {missing[:5]}{'...' if len(missing) > 5 else ''}")

    session_id = str(uuid.uuid4())[:8]
    session_dir = STUDIO_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # 安全的 ext(只取最后一个字符到末尾,防注入)
    raw_ext = os.path.splitext(filename)[1] or ".mp4"
    ext = re.sub(r"[^a-zA-Z0-9.]", "", raw_ext)[:8] or ".mp4"
    video_path = session_dir / f"source{ext}"

    # 顺序合并所有 chunks
    with open(video_path, "wb") as out:
        for i in range(total_chunks):
            cp = upload_dir / f"{i:06d}"
            with open(cp, "rb") as f:
                shutil.copyfileobj(f, out, 1024 * 1024)

    # 清理临时分片目录
    shutil.rmtree(upload_dir, ignore_errors=True)

    size_bytes = video_path.stat().st_size
    duration = _get_video_duration(str(video_path))

    STUDIO_TASKS[session_id] = {
        "session_id": session_id,
        "user_id": user_id,
        "video_path": str(video_path),
        "duration": duration,
        "segments": [],
        "status": "uploaded",
    }
    _save_tasks()

    return {
        "status": "completed",
        "session_id": session_id,
        "duration": round(duration, 2),
        "size_mb": round(size_bytes / 1024 / 1024, 2),
    }


@router.post("/split")
async def split_video(
    session_id: str = Form(...),
    segment_duration: int = Form(8),  # 每段几秒
    current_user: dict = Depends(get_current_user)
):
    """按时长拆分视频"""
    if session_id not in STUDIO_TASKS:
        raise HTTPException(404, "session not found")

    task = STUDIO_TASKS[session_id]
    if task.get("user_id") != str(current_user.get("id", "unknown")):
        raise HTTPException(403, "无权限访问")
    video_path = task["video_path"]
    duration = task["duration"]
    session_dir = STUDIO_DIR / session_id

    # 计算需要拆几段（每段最长 segment_duration 秒，但保证不小于 3 秒）
    total_segments = max(1, int(duration // segment_duration))
    if duration % segment_duration >= 3:
        total_segments += 1

    # 用 ffmpeg 切分，强制 re-encode 保证切点精准
    segments = []
    for i in range(total_segments):
        start = i * segment_duration
        output = session_dir / f"segment_{i:03d}.mp4"
        cmd = [
            "ffmpeg", "-y", "-ss", str(start), "-i", video_path,
            "-t", str(segment_duration),
            "-c:v", "libx264", "-c:a", "aac",
            "-preset", "fast", str(output)
        ]
        ok, err = _run_ffmpeg(cmd)
        if ok and output.exists() and output.stat().st_size > 1000:
            # 上传到 fal
            import fal_client
            try:
                fal_url = await fal_client.upload_file_async(str(output))
                segments.append({
                    "index": i,
                    "start": start,
                    "duration": min(segment_duration, duration - start),
                    "local_path": str(output),
                    "fal_url": fal_url,
                })
            except Exception as e:
                print(f"fal upload failed for segment {i}: {e}")

    task["segments"] = segments
    task["status"] = "split"
    _save_tasks()

    return {
        "session_id": session_id,
        "total_segments": len(segments),
        "segments": [{"index": s["index"], "start": s["start"], "duration": s["duration"], "url": s["fal_url"]} for s in segments],
    }


@router.post("/batch-generate")
async def batch_generate(
    req: BatchGenerateRequest,
    current_user: dict = Depends(get_current_user)
):
    """批量翻拍所有片段"""
    if req.session_id not in STUDIO_TASKS:
        raise HTTPException(404, "session not found")

    task = STUDIO_TASKS[req.session_id]
    if task.get("user_id") != str(current_user.get("id", "unknown")):
        raise HTTPException(403, "无权限访问")
    segments = task["segments"]
    if not segments:
        raise HTTPException(400, "no segments to generate")

    # 构建元素数据
    elements = []
    prompt_parts = []
    for i, elem in enumerate(req.elements[:4], 1):
        el_dict = {
            "frontal_image_url": elem.main_image_url,
            "reference_image_urls": elem.reference_image_urls or [elem.main_image_url]
        }
        elements.append(el_dict)
        prompt_parts.append(f"@Element{i} ({elem.name})")

    # 自动生成 prompt
    if len(elements) == 1:
        prompt_template = f"Replace the character/product in the video with {prompt_parts[0]}, maintaining the same movements and camera angles."
    else:
        joined = ", ".join(prompt_parts)
        prompt_template = f"Replace elements in the video with: {joined}. Maintain the same movements and camera angles."

    # 选模型
    model_key = "kling/edit-o3" if req.mode == "o3" else "kling/edit"

    # 计费:N 段 × video/replace/element 单价。先预扣全额,提交失败按段返还(镜像 ad_video.py + jobs.py 的 fail-refund 模式)
    user_id = current_user["id"]
    per_seg_cost = get_task_cost("video/replace/element")
    n = len(segments)
    total_cost = per_seg_cost * n

    if total_cost > 0:
        if not check_user_credits(user_id, total_cost):
            raise HTTPException(
                status_code=402,
                detail=f"积分不足,{n} 段需 {total_cost} 积分",
            )
        if not deduct_credits(user_id, total_cost):
            raise HTTPException(status_code=500, detail="扣费失败,请重试")

    # 批量提交
    service = get_video_service()
    batch_results = []
    submit_failed = 0
    for seg in segments:
        args = {
            "video_url": seg["fal_url"],
            "prompt": prompt_template,
            "elements": elements,
            "keep_audio": True,
        }
        result = await service._generate_video(model_key, args)
        status = result.get("status", "failed")
        # submit 失败 → 这段会在循环结束 add_credits 退款,标记 refunded 防止 /batch-status 双退
        is_submit_fail = status != "pending"
        if is_submit_fail:
            submit_failed += 1
        batch_results.append({
            "segment_index": seg["index"],
            "task_id": result.get("task_id"),
            "endpoint_tag": result.get("endpoint_tag"),
            "status": status,
            "error": result.get("error"),
            "cost": per_seg_cost,
            "refunded": is_submit_fail,
        })

    # 失败段返还(只覆盖 fal submit 失败;async 失败由 batch-status / merge 阶段后续补)
    refund = submit_failed * per_seg_cost
    if refund > 0:
        add_credits(user_id, refund)
    actual_cost = total_cost - refund

    # 写消费记录(实扣金额,用户在 /tasks/history 能看到)
    if actual_cost > 0:
        create_consumption_record(
            user_id=user_id,
            task_id=req.session_id,
            module="video/replace/element",
            cost=actual_cost,
            description=f"长视频翻拍 · {n - submit_failed}/{n} 段提交成功",
        )

    task["batch_results"] = batch_results
    task["batch_model"] = model_key
    task["batch_cost"] = actual_cost  # 留个记录,merge 阶段以后补 async 失败返还时要查
    task["status"] = "generating"
    _save_tasks()

    return {
        "session_id": req.session_id,
        "total": len(batch_results),
        "tasks": batch_results,
        "cost": actual_cost,  # 让前端 sidebar adjustLocalUserCredits(-cost)
        "submit_failed": submit_failed,
    }


@router.get("/batch-status/{session_id}")
async def batch_status(
    session_id: str,
    current_user: dict = Depends(get_current_user)
):
    """查询批量任务进度"""
    if session_id not in STUDIO_TASKS:
        raise HTTPException(404, "session not found")

    task = STUDIO_TASKS[session_id]
    if task.get("user_id") != str(current_user.get("id", "unknown")):
        raise HTTPException(403, "无权限访问")
    if "batch_results" not in task:
        raise HTTPException(400, "no batch task")

    service = get_video_service()
    completed = 0
    failed = 0
    results = []
    refunded_this_call = 0  # 本次 poll 新退的钱(给前端 sidebar adjustLocalUserCredits(+))
    user_id = current_user["id"]

    def _refund_if_needed(seg: dict) -> int:
        """async 失败时退该段(幂等:refunded 标记防止双退)。返回本次实退积分"""
        if seg.get("refunded"):
            return 0
        seg_cost = seg.get("cost", 0)
        if seg_cost > 0:
            add_credits(user_id, seg_cost)
        seg["refunded"] = True
        return seg_cost

    for r in task["batch_results"]:
        if r.get("status") == "completed" and r.get("video_url"):
            completed += 1
            results.append(r)
            continue
        if r.get("status") == "failed":
            # 历史失败 — submit 时已退或上轮 poll 已退,refunded 标记保证不重复
            refunded_this_call += _refund_if_needed(r)
            failed += 1
            results.append(r)
            continue
        # 查询 fal
        if r.get("task_id"):
            status = await service.get_task_status(r["task_id"], endpoint_hint=r.get("endpoint_tag"))
            r["status"] = status.get("status", "processing")
            if status.get("video_url"):
                r["video_url"] = status["video_url"]
                completed += 1
            elif status.get("status") == "failed":
                # **新发现的 async 失败 — fal 接了任务但跑挂,这里退款**
                refunded_this_call += _refund_if_needed(r)
                failed += 1
        results.append(r)

    total = len(task["batch_results"])
    if completed + failed == total:
        task["status"] = "done"
    _save_tasks()

    return {
        "session_id": session_id,
        "total": total,
        "completed": completed,
        "failed": failed,
        "processing": total - completed - failed,
        "tasks": results,
        "refunded_this_call": refunded_this_call,  # 前端拿这个 +adjustLocalUserCredits 让 sidebar 涨回去
    }


@router.post("/merge/{session_id}")
async def merge_segments(
    session_id: str,
    current_user: dict = Depends(get_current_user)
):
    """拼接所有完成的片段"""
    if session_id not in STUDIO_TASKS:
        raise HTTPException(404, "session not found")

    task = STUDIO_TASKS[session_id]
    if task.get("user_id") != str(current_user.get("id", "unknown")):
        raise HTTPException(403, "无权限访问")
    
    # 幂等：已经拼接过直接返回
    if task.get("status") == "finished" and task.get("final_url"):
        completed = sum(1 for r in task.get("batch_results", []) if r.get("status") == "completed")
        return {
            "session_id": session_id,
            "final_url": task["final_url"],
            "segments_merged": completed,
            "cached": True,
        }
    
    batch_results = task.get("batch_results", [])
    session_dir = STUDIO_DIR / session_id

    # 下载所有成片到本地
    import httpx
    local_files = []
    async with httpx.AsyncClient() as client:
        for r in sorted(batch_results, key=lambda x: x["segment_index"]):
            if r.get("status") == "completed" and r.get("video_url"):
                local_path = session_dir / f"result_{r['segment_index']:03d}.mp4"
                resp = await client.get(r["video_url"], follow_redirects=True, timeout=120)
                with open(local_path, "wb") as f:
                    f.write(resp.content)
                local_files.append(str(local_path))

    if not local_files:
        raise HTTPException(400, "no completed segments")

    # 用 concat 方式合并
    concat_file = session_dir / "concat.txt"
    with open(concat_file, "w") as f:
        for p in local_files:
            f.write(f"file '{p}'\n")

    output = session_dir / "final.mp4"
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
           "-c:v", "libx264", "-c:a", "aac", "-preset", "fast", str(output)]
    ok, err = _run_ffmpeg(cmd)

    if not ok:
        raise HTTPException(500, f"merge failed: {err[:200]}")

    # 上传最终成品到 fal
    import fal_client
    final_url = await fal_client.upload_file_async(str(output))

    task["final_url"] = final_url
    task["status"] = "finished"
    _save_tasks()

    return {
        "session_id": session_id,
        "final_url": final_url,
        "segments_merged": len(local_files),
    }


@router.get("/list")
async def list_sessions(current_user: dict = Depends(get_current_user)):
    """列出当前用户的历史 session，按时间倒序"""
    import os
    uid = str(current_user.get("id", "unknown"))
    items = []
    for sid, task in STUDIO_TASKS.items():
        if task.get("user_id") != uid:
            continue  # 严格：没 user_id 的也跳过
        # 计算保留剩余天数（按7天算）
        try:
            session_dir = STUDIO_DIR / sid
            if session_dir.exists():
                mtime = session_dir.stat().st_mtime
                import time
                age_days = (time.time() - mtime) / 86400
                remaining = max(0, 7 - int(age_days))
            else:
                remaining = 0
                mtime = 0
        except:
            remaining = 0
            mtime = 0
        
        # 统计
        segments = task.get("segments", [])
        batch_results = task.get("batch_results", [])
        completed = sum(1 for r in batch_results if r.get("status") == "completed")
        
        items.append({
            "session_id": sid,
            "status": task.get("status", "unknown"),
            "duration": task.get("duration", 0),
            "total_segments": len(segments),
            "completed_segments": completed,
            "final_url": task.get("final_url"),
            "created_at": mtime,
            "remaining_days": remaining,
        })
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return {"total": len(items), "sessions": items}


@router.get("/session/{session_id}")
async def get_session(session_id: str, current_user: dict = Depends(get_current_user)):
    """获取单个 session 详情"""
    if session_id not in STUDIO_TASKS:
        raise HTTPException(404, "session not found")
    task = STUDIO_TASKS[session_id]
    uid = str(current_user.get("id", "unknown"))
    if task.get("user_id") != uid:
        raise HTTPException(403, "无权限访问")
    return task


@router.delete("/session/{session_id}")
async def delete_session(session_id: str, current_user: dict = Depends(get_current_user)):
    """删除一个 session"""
    if session_id not in STUDIO_TASKS:
        raise HTTPException(404, "session not found")
    task = STUDIO_TASKS[session_id]
    uid = str(current_user.get("id", "unknown"))
    if task.get("user_id") != uid:
        raise HTTPException(403, "无权限删除")
    import shutil
    session_dir = STUDIO_DIR / session_id
    if session_dir.exists():
        shutil.rmtree(session_dir)
    del STUDIO_TASKS[session_id]
    _save_tasks()
    return {"deleted": session_id}
