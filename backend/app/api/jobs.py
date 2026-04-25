"""全局任务队列 API - 统一管理图片/视频生成，5 并发上限，JSON 持久化"""
import os
import json
import uuid
import time
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.services.fal_service import get_image_service, get_video_service
from app.services.billing import get_task_cost, check_user_credits, deduct_credits, add_credits, create_consumption_record
from app.api.auth import get_current_user

router = APIRouter()

# 路径默认 /root/ssp/jobs_data,测试或多环境通过 JOBS_FILE 覆盖
JOBS_FILE = Path(os.environ.get("JOBS_FILE", "/root/ssp/jobs_data/jobs.json"))
JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
JOBS_DIR = JOBS_FILE.parent

MAX_CONCURRENT = 5
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

def _load_jobs():
    if JOBS_FILE.exists():
        try:
            return json.loads(JOBS_FILE.read_text())
        except:
            return {}
    return {}

def _save_jobs():
    try:
        JOBS_FILE.write_text(json.dumps(JOBS, ensure_ascii=False, indent=2, default=str))
    except Exception as e:
        print(f"save jobs failed: {e}")

JOBS: Dict[str, dict] = _load_jobs()


class SubmitJobRequest(BaseModel):
    type: str
    params: Dict[str, Any]
    title: Optional[str] = None


async def _run_image_job(params: dict):
    service = get_image_service()
    if params.get("reference_images"):
        import fal_client
        result = await fal_client.run_async(
            "fal-ai/nano-banana-2/edit",
            arguments={"prompt": params["prompt"], "image_urls": params["reference_images"]}
        )
        images = result.get("images", [])
        if not images:
            raise Exception("no image generated")
        return {"image_url": images[0].get("url"), "type": "image"}
    else:
        result = await service.generate(
            params["prompt"], params.get("size", "1024x1024"), params.get("model", "nano-banana-2")
        )
        if "error" in result:
            raise Exception(result["error"])
        result["type"] = "image"
        return result


async def _run_video_job(params: dict, job_type: str):
    service = get_video_service()
    if job_type == "video_i2v":
        r = await service.generate_from_image(params["image_url"], params.get("prompt", ""), params.get("tail_image_url"))
    elif job_type == "video_edit":
        r = await service.replace_element(params["video_url"], params["element_image_url"], params["instruction"], params.get("product_image_url"))
    elif job_type == "video_clone":
        r = await service.clone_video(params["reference_video_url"], params["model_image_url"], params.get("product_image_url"))
    else:
        raise Exception(f"unknown video type: {job_type}")
    if r.get("error"):
        raise Exception(r["error"])
    task_id = r.get("task_id")
    endpoint_tag = r.get("endpoint_tag", "edit")
    if not task_id:
        raise Exception("no task_id from fal")
    for _ in range(120):
        await asyncio.sleep(5)
        status = await service.get_task_status(task_id, endpoint_hint=endpoint_tag)
        if status.get("status") == "completed" and status.get("video_url"):
            return {"video_url": status["video_url"], "type": "video"}
        if status.get("status") == "failed":
            raise Exception(status.get("error", "fal task failed"))
    raise Exception("timeout (10 min)")


async def _execute_job(job_id: str):
    async with _semaphore:
        job = JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = time.time()
        _save_jobs()
        try:
            t = job["type"]
            if t == "image":
                result = await _run_image_job(job["params"])
            elif t.startswith("video_"):
                result = await _run_video_job(job["params"], t)
            else:
                raise Exception(f"unknown type: {t}")
            job["status"] = "completed"
            job["result"] = result
            job["finished_at"] = time.time()
            # 写历史记录
            try:
                uid = job.get("user_numeric_id")
                if uid and job.get("cost", 0) > 0:
                    result_data = job.get("result", {})
                    imgs = [result_data["image_url"]] if result_data.get("image_url") else []
                    vids = [result_data["video_url"]] if result_data.get("video_url") else []
                    create_consumption_record(
                        user_id=uid,
                        task_id=job["id"],
                        module=job.get("module", "image/style"),
                        cost=job.get("cost", 0),
                        description=job.get("title", ""),
                        images=imgs,
                        videos=vids,
                    )
            except Exception as hist_err:
                print(f"history write failed: {hist_err}")
        except Exception as e:
            job["status"] = "failed"
            job["error"] = str(e)
            job["finished_at"] = time.time()
            # 退还积分
            try:
                uid = job.get("user_numeric_id")
                if uid and job.get("cost", 0) > 0:
                    add_credits(uid, job.get("cost", 0))
            except:
                pass
        _save_jobs()


def _module_from_type(job_type: str, params: dict) -> str:
    if job_type == "image":
        return "image/multi-reference" if params.get("reference_images") else "image/style"
    if job_type == "video_i2v":
        return "video/image-to-video"
    if job_type == "video_edit":
        return "video/replace/element"
    if job_type == "video_clone":
        return "video/clone"
    return "image/style"


@router.post("/submit")
async def submit_job(req: SubmitJobRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user.get("id") or current_user.get("email", "unknown")
    user_id_str = str(user_id)
    
    module = _module_from_type(req.type, req.params)
    cost = get_task_cost(module)
    
    # 扣费（用 UUID 字符串作为 user_id）
    if cost > 0:
        if not check_user_credits(user_id, cost):
            raise HTTPException(status_code=402, detail=f"积分不足，需要 {cost} 积分")
        if not deduct_credits(user_id, cost):
            raise HTTPException(status_code=500, detail="扣费失败")
    
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id_str,
        "user_numeric_id": user_id,  # 实际是 UUID 字符串
        "type": req.type,
        "title": req.title or req.type,
        "params": req.params,
        "module": module,
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    asyncio.create_task(_execute_job(job_id))
    return {"job_id": job_id, "status": "pending", "cost": cost}


@router.get("/list")
async def list_jobs(current_user: dict = Depends(get_current_user)):
    user_id = str(current_user.get("id") or current_user.get("email", "unknown"))
    mine = [j for j in JOBS.values() if j.get("user_id") == user_id]
    mine.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return {"jobs": mine[:50]}


@router.get("/{job_id}")
async def get_job(job_id: str, current_user: dict = Depends(get_current_user)):
    if job_id not in JOBS:
        raise HTTPException(404, "job not found")
    job = JOBS[job_id]
    uid = str(current_user.get("id") or current_user.get("email", "unknown"))
    if job.get("user_id") != uid:
        raise HTTPException(403, "无权限访问")
    return job


@router.delete("/{job_id}")
async def delete_job(job_id: str, current_user: dict = Depends(get_current_user)):
    if job_id not in JOBS:
        raise HTTPException(404, "job not found")
    job = JOBS[job_id]
    uid = str(current_user.get("id") or current_user.get("email", "unknown"))
    if job.get("user_id") != uid:
        raise HTTPException(403, "无权限删除")
    del JOBS[job_id]
    _save_jobs()
    return {"deleted": job_id}
