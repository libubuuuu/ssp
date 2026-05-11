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
from app.services.fal_service import fal_upload_with_retry, AliyunQwenVLVideoService
from app.services.upload_guard import read_bounded, IMAGE_MIMES
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
    """跟 /api/video/replicate/upload/video 同形:上传视频文件 → fal storage URL。"""
    import os
    contents = await read_bounded(file, MAX_VIDEO_SIZE, VIDEO_MIMES, "参考视频")
    suffix = ".mp4"
    if file.filename and "." in file.filename:
        suffix = "." + file.filename.rsplit(".", 1)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    try:
        url = await fal_upload_with_retry(tmp_path)
    finally:
        try: os.unlink(tmp_path)
        except Exception: pass
    return {"video_url": url}


# ================= 分镜分析 prompt =================
# 关键点:让 qwen-vl 把九宫格当 N 个独立镜头看,不当成一张拼贴图

def _build_skill_instruction(scenes_info: list[dict]) -> str:
    """根据 PySceneDetect 切出的 scene 列表生成 qwen-vl prompt。"""
    n = len(scenes_info)
    lines = []
    for i, s in enumerate(scenes_info):
        lines.append(
            f"  第{i+1}格(id={i+1}): {s['start_seconds']:.1f}-{s['end_seconds']:.1f}s "
            f"(时长{s['duration_seconds']:.1f}秒)"
        )
    range_block = "\n".join(lines)

    return f"""你看到的是一张分镜九宫格 storyboard 图,由 {n} 张关键帧按时间顺序左→右、上→下拼接而成。每格左上角有 1..{n} 编号水印。

每格对应的原视频时间范围:
{range_block}

**请只看每一格独立画面**(不要把它当一张拼贴图整体分析),把每格解读成 1 个独立的镜头(scene),输出严格 JSON,不要任何 markdown 围栏 / 注释 / 说明文字。

JSON 格式:
{{
  "scenes": [
    {{
      "id": <1..{n}>,
      "time_range": "<对应格的时间范围,例如 '0.0-3.7s'>",
      "duration_sec": <数字>,
      "shot": "<景别:close-up | medium-shot | wide-shot | medium close-up | extreme close-up>",
      "action": "<这一镜里发生的动作,15 字内中文>",
      "framing": "<构图:正面/侧面/背面/俯拍/仰拍/特写/...>",
      "visual_prompt": "<150 字内英文 prompt,可直接喂给视频生成模型,描述场景、灯光、镜头语言、主体动作,不写台词、不写画面里的文字>"
    }},
    ...严格 {n} 个
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
    cost = 1
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    # fail-fast:qwen-vl 服务可用性
    try:
        svc = AliyunQwenVLVideoService()
        if not svc.is_available():
            add_credits(user_id, cost, reason="task_refund")
            raise HTTPException(503, "qwen-vl 视觉服务不可用(DASHSCOPE_API_KEY 未配置)")
    except HTTPException:
        raise
    except Exception as e:
        add_credits(user_id, cost, reason="task_refund")
        raise HTTPException(503, f"qwen-vl 初始化失败: {str(e)[:200]}")

    from app.api.jobs import JOBS, _save_jobs, _execute_job
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
    asyncio.create_task(_execute_job(job_id))
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
    """异步:推 JOBS type=skill_replace,GPT-Image 2 edit 把九宫格里的人/物/景换成用户上传的。"""
    grid_url = body.get("grid_url")
    product_image_url = body.get("product_image_url")
    model_image_url = body.get("model_image_url")
    scene_image_url = body.get("scene_image_url")  # 可选
    model_identity = body.get("model_identity") or ""
    product_category = body.get("product_category") or "其他"

    if not grid_url:
        raise HTTPException(400, "grid_url 必填")
    if not product_image_url:
        raise HTTPException(400, "product_image_url 必填(产品图)")
    if not model_image_url:
        raise HTTPException(400, "model_image_url 必填(人物图)")

    user_id = str(current_user["id"])
    # 单次九宫格替换 ~¥1.5-2,计费 3 积分(1 积分 = ¥0.5)
    cost = 3
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    from app.api.jobs import JOBS, _save_jobs, _execute_job
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "skill_replace",
        "title": "九宫格 storyboard 元素替换",
        "params": {
            "grid_url": grid_url,
            "product_image_url": product_image_url,
            "model_image_url": model_image_url,
            "scene_image_url": scene_image_url,
            "model_identity": model_identity,
            "product_category": product_category,
            "_user_id": user_id,
        },
        "module": "video/frame-extract/replace",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    asyncio.create_task(_execute_job(job_id))
    log_info(f"frame-extract/replace submitted job={job_id} user={user_id}")
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
    """异步:推 JOBS type=skill_generate,从替换后九宫格 + scenes 出 N 段视频 → concat 成完整视频。"""
    replaced_grid_url = body.get("replaced_grid_url")
    scenes = body.get("scenes") or []
    product_image_url = body.get("product_image_url")
    model_image_url = body.get("model_image_url")
    scene_image_url = body.get("scene_image_url")  # 可选
    user_prompt = (body.get("user_prompt") or "").strip()  # 用户强调产品功能
    aspect_ratio = body.get("aspect_ratio") or "9:16"

    if not replaced_grid_url:
        raise HTTPException(400, "replaced_grid_url 必填(先调 /replace)")
    if not scenes:
        raise HTTPException(400, "scenes 必填")
    if not product_image_url or not model_image_url:
        raise HTTPException(400, "product_image_url + model_image_url 都必填")

    user_id = str(current_user["id"])
    # Seedance r2v 480p ~$1.06/8s,按 scene 数 * 5 积分(N 段并发,但都要 r2v 调用)
    cost = max(10, len(scenes) * 5)
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    from app.api.jobs import JOBS, _save_jobs, _execute_job
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "skill_generate",
        "title": f"视频生成 ({len(scenes)} 段 r2v 拼合)",
        "params": {
            "replaced_grid_url": replaced_grid_url,
            "scenes": scenes,
            "product_image_url": product_image_url,
            "model_image_url": model_image_url,
            "scene_image_url": scene_image_url,
            "user_prompt": user_prompt,
            "aspect_ratio": aspect_ratio,
            "_user_id": user_id,
        },
        "module": "video/frame-extract/generate",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    asyncio.create_task(_execute_job(job_id))
    log_info(f"frame-extract/generate submitted job={job_id} user={user_id} scenes={len(scenes)} cost={cost}")
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
