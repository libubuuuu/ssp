"""全局任务队列 API - 统一管理图片/视频生成，5 并发上限，JSON 持久化"""
import os
import json
import uuid
import time
import asyncio
import fcntl
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.services.fal_service import get_image_service, get_video_service, fal_upload_with_retry
from app.services.billing import get_task_cost, check_user_credits, deduct_credits, add_credits, create_consumption_record
from app.services.logger import log_info, log_warning  # P101: ad_video TTS+lipsync 日志
from app.api.auth import get_current_user

router = APIRouter()

# 路径默认项目根/jobs_data/jobs.json,测试或多环境通过 JOBS_FILE 覆盖
_DEFAULT_JOBS_FILE = Path(__file__).resolve().parents[3] / "jobs_data" / "jobs.json"
JOBS_FILE = Path(os.environ.get("JOBS_FILE", str(_DEFAULT_JOBS_FILE)))
JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
JOBS_DIR = JOBS_FILE.parent

MAX_CONCURRENT = 5
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

def _load_jobs():
    """读取 jobs.json,加共享锁(LOCK_SH)避免读到正在写的半量"""
    if not JOBS_FILE.exists():
        return {}
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.loads(f.read() or "{}")
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        print(f"load jobs failed: {e}")
        return {}


def _save_jobs():
    """写 jobs.json,加排他锁(LOCK_EX)防止多 worker 并发覆盖损坏文件

    Phase 2 迁 RQ/Celery + Redis 后退役。当前文件型队列单进程多协程是安全的,
    多进程(uvicorn workers)/cron 并发场景下没锁会撞数据丢失。
    """
    try:
        # mode w 会 truncate,要在 flock 之前 open;flock 跨 close 不传播,
        # 用 with open 保证锁只在写入期间持有
        with open(JOBS_FILE, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(JOBS, ensure_ascii=False, indent=2, default=str))
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
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
        # 八十四续 P6:nano-banana-2/edit 是 Google 系列对内衣/塑身/紧身衣等
        # NSFW 拦截极严(实测豹纹比基尼直接拒,和 prompt 无关)。
        # 切字节 Seedream 4 edit:国产对带货类宽容,实测同图能成,且支持多图合成。
        import fal_client
        result = await fal_client.run_async(
            "fal-ai/bytedance/seedream/v4/edit",
            arguments={
                "prompt": params["prompt"],
                "image_urls": params["reference_images"],
                "image_size": "square_hd",
            }
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


async def _run_ad_video_job(params: dict):
    """AI 带货视频 — Seedance 2.0 异步任务

    单段(<=15s):走老路,所有 scenes 拼成一个 prompt 跑一次 Seedance
    多段(>15s,P31 2026-05-01):每个 scene 独立调用 Seedance,
                           Semaphore(5) 并发 + ffmpeg concat 拼接,
                           沿用口播 V3 P28 长视频分段模板

    参数:
      - image_url: 首帧图(可以是 /preview 输出,也可以是直接上传的)
      - script: 完整脚本 dict {overall_setting, model_description, scenes:[...]}
      - duration: 总时长 5-300 秒
      - aspect_ratio / resolution / enable_audio
    """
    from app.services import ad_video_models
    from app.services.vlm_service import split_segments

    duration = int(params.get("duration", 15))
    script = params.get("script") or {}
    scenes = script.get("scenes") or []

    overall = script.get("overall_setting", "")
    model_desc = script.get("model_description", "")
    aspect_ratio = params.get("aspect_ratio", "9:16")
    resolution = params.get("resolution", "720p")

    # P39 (2026-05-01):回 i2v 架构 + Flux Kontext 合首帧。
    # 痛点:ref2vid 改产品(用户实测产品被改) → 切 image edit SOTA 锁产品。
    # 路线:产品图 → Flux Kontext 17s 合"模特+产品+背景"首帧 → Seedance v1.5/pro
    # i2v 70s 出视频。5s 视频总用时 ~90 秒,产品保真度高,真人质感强。

    # 第 1 步:用产品图 + 第 1 段 visual 调 Flux Kontext 合共享 base 首帧
    first_scene = scenes[0] if scenes else {}
    base_result = await ad_video_models.compose_first_frame(
        product_image_url=params.get("product_image_url") or params.get("image_url"),
        background_image_url=params.get("background_image_url"),
        model_description=model_desc,
        scene_visual_prompt=first_scene.get("visual_prompt", ""),
        product_back_image_url=params.get("product_back_image_url"),
    )
    if "error" in base_result or not base_result.get("image_url"):
        raise Exception(f"首帧合成失败: {base_result.get('error', '?')}")
    base_image_url = base_result["image_url"]

    # ---------- 单段模式(<=12s) ----------
    # P104(2026-05-05):去掉 Seedance i2v + 后置 lipsync 两步浪费钱方案,
    # 改用 omnihuman talking head 一步到位(image + audio → 模特对口型说音频内容)
    # omnihuman 设计就是 talking,嘴型 + 表情比 i2v + 后置 lipsync 自然得多
    # 流程:TTS speech → audio_url → omnihuman(first_frame + audio)→ talking video
    if duration <= 12 or len(scenes) <= 1:
        speech_text = (first_scene.get("speech") or "").strip() if first_scene else ""
        if not speech_text:
            raise Exception("scene speech 为空,无法生成 talking video")

        import fal_client as _fc
        import re as _re

        # P112(2026-05-05):VLM 偶尔不听话写超 duration 的 speech,导致 TTS 念出
        # 比 duration 长得多的音频,omnihuman 用音频驱动出"5s 选项 → 13s 视频"。
        # 双保险:按 duration 算 max_chars 截断,防 VLM 写超。
        # elevenlabs multilingual-v2 实测速率:中文 ~5 字/秒,英文 ~14 字符/秒。
        _has_cn = bool(_re.search(r"[一-鿿]", speech_text))
        _max_chars = int(duration * (5 if _has_cn else 14))
        if len(speech_text) > _max_chars:
            log_warning(
                f"ad_video P112 speech 超长 {len(speech_text)} > {_max_chars} "
                f"(duration={duration}s, lang={'CN' if _has_cn else 'EN'}),截断"
            )
            speech_text = speech_text[:_max_chars]

        # Step 1: TTS speech → audio
        log_info(f"ad_video P104 TTS speech_len={len(speech_text)}")
        tts_result = await _fc.run_async(
            "fal-ai/elevenlabs/tts/multilingual-v2",
            arguments={"text": speech_text[:500]},
        )
        audio_obj = tts_result.get("audio") if isinstance(tts_result.get("audio"), dict) else None
        audio_url = audio_obj.get("url") if audio_obj else tts_result.get("audio_url")
        if not audio_url:
            raise Exception("TTS 未返 audio_url")

        # Step 2: talking head(image + audio → 对口型视频)
        # P114(2026-05-05):默认改 Kling Avatar v2 Standard($0.056/秒,省 60%),
        # schema 完全兼容(image_url+audio_url)。用户前端可改回 omnihuman/Pro 等。
        # Kling 多支持 optional prompt 引导动作 — 把 visual_prompt 透传过去更精细
        omnihuman_endpoint = params.get("talking_head_endpoint", "fal-ai/kling-video/ai-avatar/v2/standard")
        log_info(f"ad_video P104 talking_head endpoint={omnihuman_endpoint}")
        _args = {
            "image_url": base_image_url,
            "audio_url": audio_url,
        }
        # Kling Avatar 支持 prompt 字段引导动作 — 拼 visual_prompt 增强 P113 镜头公式落地
        if "kling" in omnihuman_endpoint and first_scene:
            _vp = (first_scene.get("visual_prompt") or "").strip()
            if _vp:
                _args["prompt"] = _vp[:500]
        h = await _fc.submit_async(
            omnihuman_endpoint,
            arguments=_args,
        )
        task_id = h.request_id
        for _ in range(120):  # 20 min cap
            await asyncio.sleep(5)
            try:
                s = await _fc.status_async(omnihuman_endpoint, task_id)
            except Exception:
                continue
            if type(s).__name__ == "Completed":
                res = await _fc.result_async(omnihuman_endpoint, task_id)
                v = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else res.get("video_url")
                if not v:
                    raise Exception("omnihuman 未返 video_url")
                log_info(f"ad_video P104 omnihuman OK url={v[:80]}")
                return {"video_url": v, "type": "video"}
        raise Exception("AI 带货视频生成超时(20 分钟)")

    # ---------- 多段模式(>15s):N 段独立首帧 + N 段 i2v 并发 + ffmpeg concat ----------
    seg_durs = split_segments(duration)
    n = len(seg_durs)
    n_actual = min(n, len(scenes))
    scenes_to_run = scenes[:n_actual]

    sem = asyncio.Semaphore(5)

    async def _run_scene(idx: int, scene: dict) -> str:
        async with sem:
            # Step A: 每段在共享 base 上调 Flux Kontext 合本段独立首帧(锁模特身份+按本段 visual)
            scene_frame_url = base_image_url  # fallback
            try:
                fr = await ad_video_models.compose_first_frame_for_scene(
                    base_image_url=base_image_url,
                    scene=scene,
                    model_description=model_desc,
                    overall_setting=overall,
                )
                if fr.get("image_url"):
                    scene_frame_url = fr["image_url"]
                else:
                    from app.services.logger import log_warning
                    log_warning(
                        f"ad_video scene {idx+1}/{n_actual} 首帧合成失败,"
                        f"回退共享 base: {fr.get('error')}"
                    )
            except Exception as fe:
                from app.services.logger import log_warning
                log_warning(
                    f"ad_video scene {idx+1}/{n_actual} 首帧合成异常,回退共享 base: {fe}"
                )

            # Step B: 用本段独立首帧调 Seedance v1.5/pro i2v
            single_script = {
                "overall_setting": overall,
                "model_description": model_desc,
                "scenes": [scene],
            }
            sub = await ad_video_models.submit_seedance_video(
                image_url=scene_frame_url,
                script=single_script,
                duration=seg_durs[idx],
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                enable_audio=False,  # 多段拼接禁原生音频(段间换音轨会跳)
            )
            if sub.get("error"):
                raise Exception(f"段 {idx+1}/{n_actual}: {sub['error']}")
            tid = sub.get("task_id")
            if not tid:
                raise Exception(f"段 {idx+1}/{n_actual}: Seedance 未返 task_id")
            for _ in range(180):
                await asyncio.sleep(5)
                st = await ad_video_models.poll_seedance_status(tid)
                if st.get("status") == "completed" and st.get("video_url"):
                    return st["video_url"]
                if st.get("status") == "failed":
                    raise Exception(f"段 {idx+1}/{n_actual}: {st.get('error')}")
            raise Exception(f"段 {idx+1}/{n_actual}: 超时(15 min)")

    seg_urls = await asyncio.gather(
        *[_run_scene(i, s) for i, s in enumerate(scenes_to_run)]
    )

    # ---------- 下载 + ffmpeg concat ----------
    import tempfile, shutil, subprocess
    import httpx
    import fal_client as _fc

    seg_root = Path(tempfile.mkdtemp(prefix="ad_video_segs_"))
    try:
        local_paths = []
        async with httpx.AsyncClient(timeout=180.0) as client:
            for i, url in enumerate(seg_urls):
                out = seg_root / f"out_{i:02d}.mp4"
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(out, "wb") as fp:
                        async for chunk in resp.aiter_bytes(64 * 1024):
                            fp.write(chunk)
                local_paths.append(out)

        concat_list = seg_root / "concat.txt"
        with open(concat_list, "w") as fp:
            for p in local_paths:
                fp.write(f"file '{p}'\n")

        merged = seg_root / "final.mp4"
        # 优先 stream copy(快+无损),失败再 re-encode 兜底
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list), "-c", "copy", str(merged)],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0 or not merged.exists() or merged.stat().st_size == 0:
            # re-encode fallback(各段编码参数不一致时走这条)
            r2 = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(concat_list),
                 "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an",
                 str(merged)],
                capture_output=True, text=True, timeout=300,
            )
            if r2.returncode != 0 or not merged.exists():
                raise Exception(f"ffmpeg concat 失败: {r2.stderr[-500:]}")

        # 上传到 fal storage 拿可访问 URL(沿用现有归档/分发模式)
        final_url = await fal_upload_with_retry(str(merged))
        return {"video_url": final_url, "type": "video"}
    finally:
        shutil.rmtree(seg_root, ignore_errors=True)


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
            elif t == "ad_video":
                result = await _run_ad_video_job(job["params"])
            else:
                raise Exception(f"unknown type: {t}")

            # BUG-2: 归档 fal URL → 本地 /uploads(防 fal.media 7-30 天过期)
            try:
                from app.services.media_archiver import archive_url
                uid = job.get("user_numeric_id") or job.get("user_id") or "anon"
                if result.get("image_url"):
                    result["image_url"] = await archive_url(result["image_url"], uid, "image")
                if result.get("video_url"):
                    result["video_url"] = await archive_url(result["video_url"], uid, "video")
            except Exception as arch_err:
                print(f"archive failed (continuing with fal URL): {arch_err}")

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
    if job_type == "ad_video":
        return "ad_video/generate"
    return "image/style"


@router.post("/submit")
async def submit_job(req: SubmitJobRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user.get("id") or current_user.get("email", "unknown")
    user_id_str = str(user_id)
    
    module = _module_from_type(req.type, req.params)
    cost = get_task_cost(module)
    
    # 扣费(原子:SQL 层 WHERE credits >= ?,无竞态)
    if cost > 0:
        if not deduct_credits(user_id, cost):
            raise HTTPException(status_code=402, detail=f"积分不足,需要 {cost} 积分")
    
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


def _studio_sessions_as_virtual_jobs(user_id: str) -> list[dict]:
    """七十五续:把当前用户的 long-video session 转成虚拟 job 给 My Tasks 显示。

    只展示有 batch_results 的 session(纯上传/拆分但没生成的不展示,避免噪音)。
    每 session 1 条聚合 job,标题"长视频翻拍 X/Y 完成",点击跳转 /video/studio/{sid}。

    不修 STUDIO_TASKS 真实结构,只在返回时合并视图。
    Status 映射:
      final_url 存在 → completed(merge 完成)
      batch_results 全 failed → failed
      任意 in pending/running → running
      全 completed 但没 final_url → running(等待 merge)
    """
    try:
        from app.api.video_studio import STUDIO_TASKS, STUDIO_DIR
    except Exception:
        return []

    out = []
    for sid, task in STUDIO_TASKS.items():
        if task.get("user_id") != user_id:
            continue
        batch_results = task.get("batch_results")
        if not batch_results:
            continue  # 没 generate 过,不展示

        n = len(batch_results)
        completed = sum(1 for r in batch_results if r.get("status") == "completed" and r.get("video_url"))
        failed = sum(1 for r in batch_results if r.get("status") == "failed")
        pending = n - completed - failed
        final_url = task.get("final_url")

        # status 推导
        if final_url:
            v_status = "completed"
        elif failed == n:
            v_status = "failed"
        else:
            v_status = "running"  # 含等待 merge / 部分完成 / 仍在跑

        # 标题:状态 + 进度
        if final_url:
            title = f"长视频翻拍 · 全部完成({n} 段)"
        elif pending > 0:
            title = f"长视频翻拍 · {completed}/{n} 完成,{pending} 生成中"
        else:
            title = f"长视频翻拍 · {n} 段已完成,等待合并"

        # created_at 用 session_dir mtime(STUDIO_TASKS 无 created_at 字段)
        try:
            mtime = (STUDIO_DIR / sid).stat().st_mtime
        except (OSError, ValueError):
            mtime = 0.0

        out.append({
            "id": f"studio_{sid}",
            "user_id": user_id,
            "user_numeric_id": user_id,
            "type": "long_video",                     # 新类型,前端识别可加图标
            "title": title,
            "params": {
                "session_id": sid,
                "segments_total": n,
                "segments_completed": completed,
                "segments_failed": failed,
                "segments_pending": pending,
            },
            "module": "video/replace/element",
            "cost": task.get("batch_cost", 0),
            "status": v_status,
            "created_at": mtime,
            "result": {"video_url": final_url} if final_url else None,
            # 给前端标识 + 跳转用
            "_long_video": True,
            "_session_id": sid,
            "_route": f"/video/studio/{sid}",
        })
    return out


@router.get("/list")
async def list_jobs(current_user: dict = Depends(get_current_user)):
    """七十五续:My Tasks 列表合并 long-video sessions(虚拟 job 视图)"""
    user_id = str(current_user.get("id") or current_user.get("email", "unknown"))
    mine = [j for j in JOBS.values() if j.get("user_id") == user_id]
    # 追加 long-video 虚拟 jobs
    mine.extend(_studio_sessions_as_virtual_jobs(user_id))
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
