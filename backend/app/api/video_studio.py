"""
长视频工作台 API
上传长视频 → 拆分 → 批量翻拍 → 拼接
"""
import os
import json
import uuid
import tempfile
import subprocess
import asyncio
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from pydantic import BaseModel
from app.services.fal_service import get_video_service
from app.api.auth import get_current_user

router = APIRouter()

# 工作区目录
STUDIO_DIR = Path("/root/ssp/studio_workspace")
STUDIO_DIR.mkdir(parents=True, exist_ok=True)

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

    # 保存原视频
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    video_path = session_dir / f"source{ext}"
    contents = await file.read()
    with open(video_path, "wb") as f:
        f.write(contents)

    duration = _get_video_duration(str(video_path))

    STUDIO_TASKS[session_id] = {
        "session_id": session_id,
        "video_path": str(video_path),
        "duration": duration,
        "segments": [],
        "status": "uploaded",
    }
    _save_tasks()

    return {
        "session_id": session_id,
        "duration": round(duration, 2),
        "size_mb": round(len(contents) / 1024 / 1024, 2),
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

    # 批量提交
    service = get_video_service()
    batch_results = []
    for seg in segments:
        args = {
            "video_url": seg["fal_url"],
            "prompt": prompt_template,
            "elements": elements,
            "keep_audio": True,
        }
        result = await service._generate_video(model_key, args)
        batch_results.append({
            "segment_index": seg["index"],
            "task_id": result.get("task_id"),
            "endpoint_tag": result.get("endpoint_tag"),
            "status": result.get("status", "failed"),
            "error": result.get("error"),
        })

    task["batch_results"] = batch_results
    task["batch_model"] = model_key
    task["status"] = "generating"
    _save_tasks()

    return {
        "session_id": req.session_id,
        "total": len(batch_results),
        "tasks": batch_results,
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
    if "batch_results" not in task:
        raise HTTPException(400, "no batch task")

    service = get_video_service()
    completed = 0
    failed = 0
    results = []

    for r in task["batch_results"]:
        if r.get("status") == "completed" and r.get("video_url"):
            completed += 1
            results.append(r)
            continue
        if r.get("status") == "failed":
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
