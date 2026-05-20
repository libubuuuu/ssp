"""视频拆帧 storyboard 提取 API(2026-05-12,基于 skills.video_frame_extraction)

流程:
  1. /upload/video      上传任意视频 → fal storage URL
  2. /analyze           skill 拆帧 + qwen-vl 看九宫格 → 出 N 段分镜 + wizper 口播
  3. /analyze/status/{job_id}   异步轮询

跟 /api/video/replicate/{upload,analyze,...} 输入/输出 schema 完全一致,前端粘 ad-video 直接复用。
区别:走 PySceneDetect 切镜头 + 九宫格 image API,不走 qwen-vl-video。
"""
from __future__ import annotations

import asyncio
import tempfile
import time
import uuid

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File

from app.api.auth import get_current_user
from app.services.billing import deduct_credits, add_credits
from app.services.fal_service import fal_upload_with_retry
from app.services.upload_guard import read_bounded, IMAGE_MIMES, _check_mime
from app.services.logger import log_info

router = APIRouter()

VIDEO_MIMES = ("video/mp4", "video/quicktime", "video/webm", "video/x-msvideo")
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_IMAGE_SIZE = 10 * 1024 * 1024   # 10 MB


# ================= 端点 1:上传视频 =================

@router.post("/upload/video")
async def upload_video(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """视频上传:流式落盘 → fal storage URL。支持慢速网络。"""
    import os
    from app.services.upload_guard import stream_bounded_to_path
    from pathlib import Path
    _check_mime(file, VIDEO_MIMES, "参考视频")
    suffix = ".mp4"
    if file.filename and "." in file.filename:
        suffix = "." + file.filename.rsplit(".", 1)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
    try:
        await stream_bounded_to_path(file, Path(tmp_path), MAX_VIDEO_SIZE, VIDEO_MIMES, "参考视频")
        url = await fal_upload_with_retry(tmp_path)
    finally:
        try: os.unlink(tmp_path)
        except Exception: pass
    return {"video_url": url}


# ================= 分镜分析 prompt =================
# 关键点:让 qwen-vl 把九宫格当 N 个独立镜头看,不当成一张拼贴图

def _build_skill_instruction(scenes_info: list[dict], n_grids: int = 1) -> str:
    """根据 PySceneDetect 切出的 scene 列表生成 qwen-vl prompt。

    多九宫格场景(n_grids > 1):告诉 qwen-vl 你看到的是 N 张连续九宫格,
    所有 scene id 全局编号 1..total,按图序 + 格序展开。
    """
    n = len(scenes_info)
    per_grid = 9
    lines = []
    for i, s in enumerate(scenes_info):
        grid_idx = i // per_grid + 1
        cell_idx = i % per_grid + 1
        lines.append(
            f"  图{grid_idx} 第{cell_idx}格 (id={i+1}): {s['start_seconds']:.1f}-{s['end_seconds']:.1f}s "
            f"(时长{s['duration_seconds']:.1f}秒)"
        )
    range_block = "\n".join(lines)

    grid_intro = (
        f"你看到的是 {n_grids} 张分镜九宫格 storyboard 图(每张 3×3=9 格),按时间顺序排列:"
        f"图 1 是前 9 个镜头,图 2 是接下来的 9 个,以此类推。"
        if n_grids > 1
        else "你看到的是 1 张分镜九宫格 storyboard 图(3×3=9 格)"
    ) + f",每张图内格子顺序左→右、上→下,每格左上角有黑底白字编号水印(1-9)。原视频共 {n} 个独立镜头。"

    return f"""{grid_intro}

每个镜头对应的原视频时间范围(scene id = 全局序号 1..{n}):
{range_block}

**请只看每一格独立画面**(每格 = 1 个独立镜头),把全部 {n} 个镜头解读成 {n} 个 scene 对象,输出严格 JSON,不要任何 markdown 围栏 / 注释 / 说明文字。

JSON 格式:
{{
  "scenes": [
    {{
      "id": <全局 1..{n}>,
      "time_range": "<对应格的时间范围,例如 '0.0-3.7s'>",
      "duration_sec": <数字>,
      "shot": "<景别:close-up | medium-shot | wide-shot | medium close-up | extreme close-up>",
      "action": "<这一镜里发生的动作,15 字内中文>",
      "framing": "<构图:正面/侧面/背面/俯拍/仰拍/特写/...>",
      "visual_prompt": "<150 字内英文 prompt,可直接喂给视频生成模型,描述场景、灯光、镜头语言、主体动作,不写台词、不写画面里的文字>"
    }},
    ...严格 {n} 个全局对象,id 跨多张图连续递增
  ],
  "model_identity": "<出现的主要人物的英文描述,150-300 字符,覆盖 race/gender/age/face/skin/hair/build/clothing。没出镜就空字符串>",
  "product_category": "<以下之一: 服装/上衣, 服装/下装, 服装/连衣裙, 服装/外套, 服装/内衣, 服装/塑身衣, 服装/泳装, 服装/睡衣, 鞋子, 包/箱包, 配饰/帽子围巾手套, 配饰/首饰, 数码/电子, 美妆/护肤, 家居, 食品, 日用, 其他>",
  "total_duration_seconds": <数字,= 所有 scene duration_sec 之和>
}}

要求:
1. scenes 数组**严格 {n} 个对象**,id 从 1 到 {n},不多不少
2. time_range 必须精确填我上面给的范围,**不要自己估**
3. visual_prompt 必须英文 + 具象,**不要写"如视频所示"这种废话**,不要写画面里出现什么"文字"
4. visual_prompt **绝对禁用词**(违反 fal content_checker):
   - 服装: bra, lingerie, underwear, panties, bikini, balconette, push-up, padded, underwire, shapewear
   - 身体: chest, breast, bust, waist, hips, thigh, butt, cleavage
   改用 the garment / the apparel / the item / upper outfit / lower outfit / torso
5. 整体输出必须是合法 JSON,顶层就是 {{...}}
"""


# ================= 端点 2:推 JOBS 队列 =================

@router.post("/analyze")
async def analyze_submit(
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    """异步:推 JOBS 队列 type=skill_analyze 后立刻返 analyze_job_id,前端轮询。"""
    video_url = body.get("video_url")
    if not video_url:
        raise HTTPException(400, "video_url 必填")
    user_id = str(current_user["id"])
    cost = 25
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    from app.api.jobs import JOBS, _save_jobs, _execute_job, create_tracked_task
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "skill_analyze",
        "title": "视频拆帧 storyboard",
        "params": {"video_url": video_url, "_user_id": user_id},
        "module": "video/frame-extract/analyze",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    create_tracked_task(_execute_job(job_id))
    log_info(f"frame-extract/analyze submitted job={job_id} user={user_id}")
    return {"analyze_job_id": job_id, "status": "pending"}


@router.get("/analyze/status/{job_id}")
async def analyze_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """前端轮询:返 pending/running/completed/failed + 完成时附 scenes."""
    from app.api.jobs import JOBS
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    uid = str(current_user.get("id"))
    if job.get("user_id") != uid:
        raise HTTPException(403, "无权限")
    out = {"status": job.get("status", "pending")}
    if job.get("status") == "completed":
        out.update(job.get("result") or {})
    if job.get("status") == "failed":
        out["error"] = job.get("error", "unknown")
    return out


# ================= 端点 4:上传素材图(产品 / 人物 / 场景) =================

@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """跟 /api/video/replicate/upload/image 同形:上传图 → fal storage URL。"""
    import io
    import os
    import tempfile
    from PIL import Image

    contents = await read_bounded(file, MAX_IMAGE_SIZE, IMAGE_MIMES, "素材图")
    try:
        img = Image.open(io.BytesIO(contents))
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode in ("RGBA", "LA"):
                bg.paste(img, mask=img.split()[-1])
            else:
                bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            img.save(tmp.name, "JPEG", quality=90, optimize=True)
            tmp_path = tmp.name
        try:
            url = await fal_upload_with_retry(tmp_path)
        finally:
            try: os.unlink(tmp_path)
            except Exception: pass
    except Exception as e:
        raise HTTPException(500, f"图片处理失败: {str(e)[:200]}")
    return {"image_url": url}


# ================= 端点 5:九宫格替换(GPT-Image 2 edit) =================

@router.post("/replace")
async def replace_submit(
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    """异步:推 JOBS type=skill_replace,GPT-Image 2 edit 把每张九宫格里的人/物/景换成用户上传的。

    接 grid_urls (list):N 张九宫格(长视频自动分页)→ 并发 N 次 GPT-2 edit → 返 replaced_grid_urls (list)。
    """
    grid_urls = body.get("grid_urls") or []
    if not grid_urls and body.get("grid_url"):
        # 兼容旧客户端单 grid_url
        grid_urls = [body["grid_url"]]
    product_image_url = body.get("product_image_url")
    model_image_url = body.get("model_image_url")
    scene_image_url = body.get("scene_image_url")  # 可选
    model_identity = body.get("model_identity") or ""
    product_category = body.get("product_category") or "其他"
    scenes = body.get("scenes") or []  # P237:前端传当前 scenes,worker 替换后用 qwen-vl 重写其中的 visual_prompt

    if not grid_urls:
        raise HTTPException(400, "grid_urls 必填")
    # 产品 / 人物 / 场景 至少传 1 张
    if not any([product_image_url, model_image_url, scene_image_url]):
        raise HTTPException(400, "产品图 / 人物图 / 场景图 至少上传 1 张")

    user_id = str(current_user["id"])
    credits_per_grid = 90
    cost = max(credits_per_grid, credits_per_grid * len(grid_urls))
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    from app.api.jobs import JOBS, _save_jobs, _execute_job, create_tracked_task
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "skill_replace",
        "title": f"九宫格 storyboard 替换 ({len(grid_urls)} 张)",
        "params": {
            "grid_urls": grid_urls,
            "product_image_url": product_image_url,
            "model_image_url": model_image_url,
            "scene_image_url": scene_image_url,
            "model_identity": model_identity,
            "product_category": product_category,
            "scenes": scenes,  # P237 透传给 worker 重写 visual_prompt
            "_user_id": user_id,
        },
        "module": "video/frame-extract/replace",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    create_tracked_task(_execute_job(job_id))
    log_info(f"frame-extract/replace submitted job={job_id} user={user_id} n_grids={len(grid_urls)} cost={cost}")
    return {"replace_job_id": job_id, "status": "pending", "cost": cost}


@router.get("/replace/status/{job_id}")
async def replace_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    from app.api.jobs import JOBS
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    uid = str(current_user.get("id"))
    if job.get("user_id") != uid:
        raise HTTPException(403, "无权限")
    out = {"status": job.get("status", "pending")}
    if job.get("status") == "completed":
        out.update(job.get("result") or {})
    if job.get("status") == "failed":
        out["error"] = job.get("error", "unknown")
    return out


# ================= 端点 6:视频生成(Seedance r2v 多段并发 + concat) =================

@router.post("/generate")
async def generate_submit(
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    """异步:推 JOBS type=skill_generate。从 N 张替换后九宫格切回 total_scenes 张帧 → 并发 r2v → ffmpeg concat。"""
    replaced_grid_urls = body.get("replaced_grid_urls") or []
    if not replaced_grid_urls and body.get("replaced_grid_url"):
        replaced_grid_urls = [body["replaced_grid_url"]]  # 兼容旧 client
    scenes = body.get("scenes") or []
    product_image_url = body.get("product_image_url")
    model_image_url = body.get("model_image_url")
    scene_image_url = body.get("scene_image_url")
    user_prompt = (body.get("user_prompt") or "").strip()
    aspect_ratio = body.get("aspect_ratio") or "9:16"
    original_video_url = body.get("video_url")  # P238:原视频 URL,用于叠加音轨到新视频
    skipped_scene_ids = body.get("skipped_scene_ids") or []  # 用户选择跳过的分镜 id

    if not replaced_grid_urls:
        raise HTTPException(400, "replaced_grid_urls 必填(先调 /replace)")
    if not scenes:
        raise HTTPException(400, "scenes 必填")
    if not any([product_image_url, model_image_url, scene_image_url]):
        raise HTTPException(400, "产品图 / 人物图 / 场景图 至少上传 1 张")

    user_id = str(current_user["id"])
    # 计费:按"相同图片 + 总时长≤15s"分组后每组调一次 API,每次 max(3,ceil(组时长)) 秒
    active_scenes = [s for s in scenes if s.get("id") not in set(skipped_scene_ids)]
    import math as _math

    def _preview_group_tasks(scs, max_dur=15.0, min_dur=3):
        tasks, cur_dur = [], 0.0
        for s in scs:
            dur = float(s.get("duration_sec") or 4)
            if cur_dur + dur <= max_dur:
                cur_dur += dur
            else:
                tasks.append(cur_dur)
                cur_dur = dur
        if cur_dur > 0:
            tasks.append(cur_dur)
        return [max(min_dur, _math.ceil(d)) for d in tasks]

    task_durs = _preview_group_tasks(active_scenes)
    total_duration_sec = sum(task_durs)
    cost = max(55, total_duration_sec * 55)
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    from app.api.jobs import JOBS, _save_jobs, _execute_job, create_tracked_task
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "skill_generate",
        "title": f"视频生成 ({len(scenes)} 段 r2v 拼合 / {len(replaced_grid_urls)} 张九宫格源)",
        "params": {
            "replaced_grid_urls": replaced_grid_urls,
            "scenes": scenes,
            "skipped_scene_ids": skipped_scene_ids,
            "product_image_url": product_image_url,
            "model_image_url": model_image_url,
            "scene_image_url": scene_image_url,
            "user_prompt": user_prompt,
            "aspect_ratio": aspect_ratio,
            "original_video_url": original_video_url,  # P238 用于叠加音轨
            "_user_id": user_id,
            "_user_email": current_user.get("email", ""),
        },
        "module": "video/frame-extract/generate",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    create_tracked_task(_execute_job(job_id))
    log_info(f"frame-extract/generate submitted job={job_id} user={user_id} scenes={len(scenes)} grids={len(replaced_grid_urls)} cost={cost}")
    return {"generate_job_id": job_id, "status": "pending", "cost": cost}


@router.get("/generate/status/{job_id}")
async def generate_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    from app.api.jobs import JOBS
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    uid = str(current_user.get("id"))
    if job.get("user_id") != uid:
        raise HTTPException(403, "无权限")
    out = {"status": job.get("status", "pending")}
    if job.get("status") == "completed":
        out.update(job.get("result") or {})
    if job.get("status") == "failed":
        out["error"] = job.get("error", "unknown")
    return out
