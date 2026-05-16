"""
通用产品视频生成工作台(P215 2026-05-08)
================================================

跟视频复刻 / AI 带货视频的区别:
- **不限品类**(食品/日用品/化妆品/3C/服装),qwen-vl 自动识别
- **多张产品图**(主图 + 详情页 + 外包装,2-5 张)— 严守用户上传
- **模特参考**(可选):模特图 OR 模特视频(取中间帧)→ 真用户提供的人作模特
- **真 cat-vton 链路**(catvton + pixverse-swap)— 产品 100% 严守

端点:
  POST /upload/image      — 上传产品图(支持多张)
  POST /upload/video      — 上传模特视频(可选)
  POST /upload/model-image — 上传模特图(可选)
  POST /analyze           — qwen-vl 看多图 → 品类 + 卖点 + 推荐脚本
  POST /generate          — 推 JOBS 队列(type=video_general)
  GET  /analyze/status/{job_id} — 异步轮询 analyze 结果

成本:1 积分 /analyze + N 段动作复刻费(按 duration 估)
"""
import asyncio
import os
import time
import uuid
import tempfile
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.services.billing import deduct_credits
from app.services.fal_service import fal_upload_with_retry
from app.services.logger import log_info, log_error
from app.services.upload_guard import read_bounded, IMAGE_MIMES

router = APIRouter()

VIDEO_MIMES = ("video/mp4", "video/quicktime", "video/webm", "video/x-msvideo")
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_IMAGE_SIZE = 10 * 1024 * 1024   # 10 MB


@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传单张产品图(主图 / 详情页 / 包装,前端多次调用)"""
    contents = await read_bounded(file, MAX_IMAGE_SIZE, IMAGE_MIMES, "产品图")
    suffix = ".jpg"
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
    return {"image_url": url}


@router.post("/upload/video")
async def upload_model_video(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传模特参考视频(可选,后端会抽中间帧作模特图)"""
    contents = await read_bounded(file, MAX_VIDEO_SIZE, VIDEO_MIMES, "模特视频")
    suffix = ".mp4"
    if file.filename and "." in file.filename:
        suffix = "." + file.filename.rsplit(".", 1)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        video_path = tmp.name
    try:
        # ffprobe 拿时长
        import subprocess as _sp
        import json as _j
        rr = _sp.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", video_path],
            capture_output=True, text=True, timeout=30,
        )
        duration = 5.0
        try:
            duration = float(_j.loads(rr.stdout).get("format", {}).get("duration", 5.0))
        except Exception:
            pass
        # 抽中间帧
        with tempfile.TemporaryDirectory() as tmpdir:
            mid_path = os.path.join(tmpdir, "model_mid.jpg")
            cp = _sp.run(
                ["ffmpeg", "-y", "-ss", f"{duration*0.5:.2f}", "-i", video_path, "-vframes", "1", "-q:v", "3", mid_path],
                capture_output=True, timeout=30,
            )
            if cp.returncode != 0 or not os.path.exists(mid_path):
                raise HTTPException(500, "中间帧抽取失败")
            video_url = await fal_upload_with_retry(video_path)
            model_image_url = await fal_upload_with_retry(mid_path)
    finally:
        try: os.unlink(video_path)
        except Exception: pass
    return {
        "video_url": video_url,
        "model_image_url": model_image_url,
        "duration_sec": duration,
    }


@router.post("/upload/scene-image")
async def upload_scene_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """2026-05-11 P226:上传场景图(可选,用作 GPT-Image 2 出图的背景锚)"""
    contents = await read_bounded(file, MAX_IMAGE_SIZE, IMAGE_MIMES, "场景图")
    suffix = ".jpg"
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
    return {"scene_image_url": url}


@router.post("/upload/model-image")
async def upload_model_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传模特图(可选,跟 /upload/video 二选一)"""
    contents = await read_bounded(file, MAX_IMAGE_SIZE, IMAGE_MIMES, "模特图")
    suffix = ".jpg"
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
    return {"model_image_url": url}


_ANALYZE_INSTRUCTION = """你是顶级短视频广告脚本专家。看完用户提供的所有产品图,完成 3 件事。

【任务一:产品分析】
- 自动判断品类(食品/日用品/化妆品/3C 数码/服装/配饰/家居/其他)
- ⭐ **product_specifics(关键!锁产品不变形)**:
  - subcategory:具体子类英文 + 中文,eg "wide-leg pants (阔腿裤)" / "T-shirt (短袖T恤)" / "dress (连衣裙)" / "lipstick (口红)" / "smartphone case (手机壳)" / "moisturizer cream (面霜)" — 必须精确到子类,不能只写"服装/化妆品"
  - form_constraint:产品形态硬约束英文,eg "pants with two separate leg openings, NOT a single-piece skirt/dress" / "tube with twist-up applicator, NOT a stick foundation" — 说明跟什么类似品形态不同
  - key_visual_features:从产品图能看见的关键视觉特征 3-5 条英文,eg ["pastel pink color", "high-waisted design", "wide-leg silhouette", "soft drape fabric texture", "subtle white logo on left thigh"]
- 锁定目标用户画像:1 句话,eg "20-30 岁都市上班族女性/30+ 精致妈妈/学生党/数码玩家"
- 提取产品核心卖点 3 条(品牌/规格/功能/材质/颜色/差异化)

【任务二:广告创意脑图(creative_brief 7 元素)】
基于品类 + 目标用户,生成完整广告创意结构。短视频"可复刻 5 大核心" + 3 个补充元素:
1. 🎣 hook(钩子):前 0-3 秒抓眼球的视觉冲突/反差/悬念,1 句话
2. 💔 pain_point(痛点):目标用户的真实问题(显瘦?省钱?省时?品质焦虑?),1 句话
3. 🎢 emotional_arc(情绪主线):X 情绪 → Y 情绪 的转化轨迹,eg "焦虑→放心"、"自卑→自信"
4. 🪞 scene_setting(场景代入):目标用户真实生活场景,eg "下班通勤地铁"、"周末厨房"
5. ✨ resonance_signal(共鸣信号):让目标用户秒懂"这就是说我"的具体视觉符号或动作
6. 💎 memorable_moment(记忆点):反转/金句/反差/视觉强符号,让用户截图保存的瞬间
7. 📢 cta(结尾召唤):"立即下单"/"点击购买同款"/"关注获取链接" 之类

【任务三:N 段脚本 — 严格 10 秒切片】
总时长 user_total_duration 秒。规则:**每段 ≤ 10 秒,最少 4 秒(fal 端点限制)**。
**所有段 duration_sec 之和必须 = total_duration**(严格)。
档位:

档位 A — total = 5 秒 → 1 段(5s),role cta
档位 B — total = 10 秒 → 1 段(10s),role cta(综合钩子+展示+召唤)
档位 C — total = 15 秒 → 2 段:[hook+showcase 10s, cta 5s]
档位 D — total = 20 秒 → 2 段:[hook 10s, showcase+cta 10s]
档位 E — total = 30 秒 → 3 段:[hook 10s, showcase 10s, cta 10s]
档位 F — total = 60 秒 → 6 段:[hook 10, setup_pain 10, showcase 10, solve 10, memorable 10, cta 10]

通用规则:段数 N = ceil(total / 10);前 N-1 段一律 10s,末段 = total - 10*(N-1)(≥5)。

⚠️ 每段 duration_sec ∈ [4, 10] 整数。每段都是独立的 fal seedance r2v 生成,
   段越长 fal 越慢(8s ~ 3 分钟,10s ~ 4 分钟)。
⚠️ 后端会强制改写 duration_sec 对齐用户秒数,不要试图扩展或缩短。

每段字段:
- id, time_range(如 "0-3s"), duration_sec(整数秒)
- narrative_role: hook / setup_pain / showcase / solve / memorable / cta
- shot: close-up / medium / wide
- action: 模特动作(按品类合理 — 食品拿/吃/展示包装,化妆品涂/抹/对比,服装试穿/转身/搭配,3C 操作/演示,日用品使用/对比)
- visual_prompt: 英文 GPT-Image 2 prompt(必含 产品 + 动作 + 背景 + 镜头 + 光线 + 风格)
- speech: 带货话术,**语言由 region 决定**(CN → 中文,Global → 英文),**配合 narrative_role**(hook 段冲击感,pain 段痛戳,cta 段召唤)

⚠️【关于 user_brief 用户输入】
如果用户在 user_brief 里写了想法(目标人群/卖点重点/风格偏好/CTA 方向),**必须**纳入 creative_brief 和 scenes 的具体设计中。
如果 user_brief 为空,则完全由你基于产品图自动生成。

【输出 JSON】严格按以下格式,不要任何 markdown 标记,不要 ```json 包裹:
{
  "category": "服装",
  "product_specifics": {
    "subcategory": "wide-leg pants (阔腿裤)",
    "form_constraint": "pants with two separate leg openings, NOT a single-piece skirt or dress",
    "key_visual_features": ["pastel pink color", "high-waisted design", "wide-leg drape silhouette", "soft fabric with natural fold lines"]
  },
  "target_user": "20-30 岁都市上班族女性",
  "selling_points": ["卖点1", "卖点2", "卖点3"],
  "creative_brief": {
    "hook": "...",
    "pain_point": "...",
    "emotional_arc": "X 情绪 → Y 情绪",
    "scene_setting": "...",
    "resonance_signal": "...",
    "memorable_moment": "...",
    "cta": "..."
  },
  "scenes": [
    {"id":1,"time_range":"0-3s","duration_sec":3,"narrative_role":"hook","shot":"close-up","action":"...","visual_prompt":"...","speech":"..."},
    {"id":2,"time_range":"3-8s","duration_sec":5,"narrative_role":"setup_pain","shot":"medium","action":"...","visual_prompt":"...","speech":"..."},
    ...
  ]
}
"""


@router.post("/analyze")
async def analyze_submit(
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    """异步分析多张产品图 → 品类 + 卖点 + 广告创意脑图 + N 段叙事脚本(推 JOBS 队列)"""
    product_image_urls = body.get("product_image_urls") or []
    if not product_image_urls or not isinstance(product_image_urls, list):
        raise HTTPException(400, "product_image_urls 必填(list)")
    total_duration = int(body.get("total_duration") or 15)
    total_duration = max(5, min(60, total_duration))
    region = body.get("region") or "CN"
    safe_region = "Global" if str(region).lower() in ("global", "en", "international", "海外") else "CN"
    # 2026-05-11:user_brief 可选,用户可写大概想法(目标人群/卖点重点/风格/CTA 方向),AI 纳入脚本
    user_brief = (body.get("user_brief") or "").strip()[:500]

    user_id = str(current_user["id"])
    cost = 5  # 2026-05-13:生成文案 5 积分
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    from app.services.fal_service import get_aliyun_qwenvl_service
    svc = get_aliyun_qwenvl_service()
    if not svc or not svc.is_available():
        from app.services.billing import add_credits
        add_credits(user_id, cost, reason="task_refund")
        raise HTTPException(503, "qwen-vl 视频理解服务不可用")

    # 推到 JOBS 队列 type=video_general_analyze
    from app.api.jobs import JOBS, _save_jobs, _execute_job, create_tracked_task
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "video_general_analyze",
        "title": "通用产品视频 · AI 分析",
        "params": {
            "product_image_urls": product_image_urls,
            "total_duration": total_duration,
            "region": safe_region,
            "user_brief": user_brief,
            "instruction": (
                _ANALYZE_INSTRUCTION.replace("user_total_duration", str(total_duration))
                + (f"\n\n【用户输入 user_brief】\n{user_brief}\n" if user_brief else "\n\n【用户输入 user_brief】\n(空,由你自动生成)\n")
                + f"\n【region】{safe_region} — 所有 scenes[*].speech 字段语言:{'中文' if safe_region == 'CN' else '英文'}(强制)\n"
            ),
            "_user_id": user_id,
        },
        "module": "video/general/analyze",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    create_tracked_task(_execute_job(job_id))
    log_info(f"video_general/analyze submitted job={job_id} user={user_id} n_images={len(product_image_urls)}")
    return {"analyze_job_id": job_id, "status": "pending"}


@router.get("/analyze/status/{job_id}")
async def analyze_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """前端轮询 analyze 状态"""
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


class GeneralGenerateRequest(BaseModel):
    product_image_urls: List[str] = Field(..., description="产品图列表(正面/反面/背面 多角度,主图必填)")
    scene_image_url: Optional[str] = Field(None, description="场景图(可选,用作 GPT-Image 2 背景锚)")
    model_image_url: Optional[str] = Field(None, description="模特图(可选,跟 model_video_url 二选一)")
    model_video_url: Optional[str] = Field(None, description="模特视频(可选,会抽中间帧作模特图)")
    category: str = Field(..., description="产品品类(qwen-vl 判出来的)")
    # 2026-05-11:从 analyze 阶段透传,worker 用来构建 unified lookbook 锁住人物/场景/风格一致性
    target_user: Optional[str] = Field(None, description="目标用户画像(analyze 输出)")
    creative_brief: Optional[dict] = Field(None, description="创意脑图 7 元素(analyze 输出)")
    # 2026-05-11 P226:产品具体子类 + 形态硬约束 + 关键视觉特征(qwen-vl 看图判,锁产品形态不变形)
    product_specifics: Optional[dict] = Field(None, description="{subcategory, form_constraint, key_visual_features}")
    scenes: List[dict] = Field(..., description="N 段脚本")
    total_duration: int = Field(15, ge=5, le=60)
    region: str = Field("CN")
    aspect_ratio: str = Field("9:16")
    # 2026-05-12:用户指定的搭配/场景描述,优先级压过 AI 自动生成
    user_outfit: Optional[str] = Field(None, description="人物搭配(除产品外,如'白色T恤+牛仔裤+小白鞋')")
    user_scene: Optional[str] = Field(None, description="场景描述(如'明亮的客厅,落地窗,午后阳光')")
    # 2026-05-12:批量生成 — 1 次提交跑 N 个独立版本(同 prompt 不同种子,挑最佳)
    batch_count: int = Field(1, ge=1, le=5, description="批量生成数量 1-5")
    # 2026-05-12:storyboard 输出的 N 宫格图(整图)+ 宫格数,worker 裁成 N 子图直接作 i2v 首帧
    # 跳过 compose_first_frame_for_scene(GPT 重画场景帧),省 N×2-3 分钟
    storyboard_image_url: Optional[str] = Field(None, description="storyboard /storyboard 返回的 grid url")
    storyboard_n_panels: int = Field(0, ge=0, le=9, description="storyboard 宫格数 0/2/3/4/6/9")
    # 2026-05-12:character_sheet 整图 url,worker 裁 4 panels(脸/正/反/侧)作 Seedance r2v 多图 reference
    character_sheet_image_url: Optional[str] = Field(None, description="character_sheet 2x2 整图 url")


class StoryboardRequest(BaseModel):
    """2026-05-11 P226:分镜板预览 — GPT-Image 2 出 character sheet + N 宫格 storyboard,用户看着满意再触发完整视频。"""
    product_image_urls: List[str] = Field(..., min_length=1)
    scene_image_url: Optional[str] = None  # 用户提供的场景图(可选,用作 GPT 背景锚)
    model_image_url: Optional[str] = None
    model_video_url: Optional[str] = None
    category: str = ""
    target_user: Optional[str] = None
    creative_brief: Optional[dict] = None
    product_specifics: Optional[dict] = None
    scenes: List[dict] = Field(..., min_length=1)
    region: str = "CN"
    aspect_ratio: str = "9:16"
    # 2026-05-12:用户指定的搭配/场景描述,优先级压过 AI 自动生成
    user_outfit: Optional[str] = None
    user_scene: Optional[str] = None


@router.post("/storyboard")
async def storyboard_submit(
    req: StoryboardRequest,
    current_user: dict = Depends(get_current_user),
):
    """生成分镜板预览(1 张 N 宫格图,N=min(4, len(scenes)))"""
    user_id = str(current_user["id"])
    cost = 20  # 2026-05-13:分镜图 1 张 20 积分
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    from app.api.jobs import JOBS, _save_jobs, _execute_job, create_tracked_task
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "video_general_storyboard",
        "title": f"通用产品视频 · 分镜板预览",
        "params": {
            "product_image_urls": req.product_image_urls,
            "scene_image_url": req.scene_image_url,
            "model_image_url": req.model_image_url,
            "model_video_url": req.model_video_url,
            "category": req.category or "其他",
            "target_user": req.target_user or "",
            "creative_brief": req.creative_brief or {},
            "product_specifics": req.product_specifics or {},
            "scenes": [s if isinstance(s, dict) else dict(s) for s in req.scenes],
            "region": req.region,
            "aspect_ratio": req.aspect_ratio,
            "user_outfit": (req.user_outfit or "").strip(),
            "user_scene": (req.user_scene or "").strip(),
            "_user_id": user_id,
        },
        "module": "video/general/storyboard",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    create_tracked_task(_execute_job(job_id))
    log_info(f"video_general/storyboard submitted job={job_id} user={user_id} n_scenes={len(req.scenes)}")
    return {"job_id": job_id, "status": "pending", "cost": cost}


@router.get("/storyboard/status/{job_id}")
async def storyboard_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """前端轮询 storyboard 状态"""
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


@router.post("/generate")
async def generate(
    req: GeneralGenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """生成通用产品视频(推 JOBS 队列异步跑)"""
    if not req.product_image_urls:
        raise HTTPException(400, "至少 1 张产品图")
    if not req.scenes:
        raise HTTPException(400, "至少 1 段脚本")

    user_id = str(current_user["id"])
    # 2026-05-13:50 积分/秒,batch_count 倍(批量生成 N 个独立版本)
    total_duration_sec = sum(int(s.duration_sec or 0) for s in req.scenes)
    cost = max(55, total_duration_sec * 55) * max(1, min(5, req.batch_count or 1))
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    from app.api.jobs import JOBS, _save_jobs, _execute_job, create_tracked_task
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "video_general",
        "title": f"通用产品视频 · {req.category}",
        "params": {
            "product_image_urls": req.product_image_urls,
            "scene_image_url": req.scene_image_url,
            "model_image_url": req.model_image_url,
            "model_video_url": req.model_video_url,
            "category": req.category,
            "target_user": req.target_user or "",
            "creative_brief": req.creative_brief or {},
            "product_specifics": req.product_specifics or {},
            "scenes": [s if isinstance(s, dict) else dict(s) for s in req.scenes],
            "total_duration": req.total_duration,
            "region": req.region,
            "aspect_ratio": req.aspect_ratio,
            "user_outfit": (req.user_outfit or "").strip(),
            "user_scene": (req.user_scene or "").strip(),
            "batch_count": max(1, min(5, req.batch_count or 1)),
            "storyboard_image_url": req.storyboard_image_url or "",
            "storyboard_n_panels": int(req.storyboard_n_panels or 0),
            "character_sheet_image_url": req.character_sheet_image_url or "",
            "_user_id": user_id,
        },
        "module": "video/general",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    create_tracked_task(_execute_job(job_id))
    log_info(f"video_general/generate submitted job={job_id} user={user_id} n_scenes={len(req.scenes)} category={req.category}")
    return {"job_id": job_id, "status": "pending", "cost": cost}


# ──────────────────────────────────────────────────────────────────────────────
# 入口B：爆款脚本生成（独立于 analyze/storyboard/generate，不影响现有流程）
# POST /api/video/general/script
# ──────────────────────────────────────────────────────────────────────────────

class ScriptRequest(BaseModel):
    """脚本生成请求体"""
    # 产品图：二选一传入
    image_base64: Optional[str] = Field(None, description="产品图 base64（支持带/不带 data URI 前缀）")
    image_url: Optional[str] = Field(None, description="产品图 fal storage URL（与 image_base64 二选一）")
    image_mime: str = Field("image/jpeg", description="图片 MIME 类型")
    # 脚本参数
    market: str = Field("海外", description="目标市场，如 '海外' / '国内' / '欧美' / '东南亚'")
    duration: int = Field(15, ge=5, le=120, description="视频总时长（秒）")
    model_info: str = Field("AI 自动生成模特", description="模特信息描述")
    user_idea: str = Field("", description="用户额外想法（可为空）", max_length=500)
    mode: str = Field("story", description="脚本模式：'story'（剧情）或 'direct'（直接带货）")


@router.post("/script")
async def script_generate(
    body: ScriptRequest,
    current_user: dict = Depends(get_current_user),
):
    """入口B第一步：产品图 → Gemini 生成爆款短视频脚本。

    - 同步接口（Gemini 通常 10–30s 内返回）
    - 扣 35 积分，失败全退
    - 不影响 analyze / storyboard / generate 现有流程
    """
    from app.services.billing import add_credits
    from app.services.video_general_script import generate_script
    import httpx as _httpx
    import base64 as _b64

    if not body.image_base64 and not body.image_url:
        raise HTTPException(400, "image_base64 和 image_url 至少传一个")

    user_id = str(current_user["id"])
    cost = 35
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足，需 {cost} 积分")

    try:
        # 如果传的是 URL，先下载转成 base64
        image_base64 = body.image_base64
        image_mime = body.image_mime or "image/jpeg"

        if not image_base64 and body.image_url:
            try:
                async with _httpx.AsyncClient(timeout=30, follow_redirects=True) as cli:
                    r = await cli.get(body.image_url)
                    r.raise_for_status()
                    image_base64 = _b64.b64encode(r.content).decode("utf-8")
                    ct = r.headers.get("content-type", "")
                    if ct and ct.startswith("image/"):
                        image_mime = ct.split(";")[0].strip()
            except Exception as e:
                add_credits(user_id, cost, reason="task_refund",
                            ref_id="script_img_download_fail", module="video/general/script")
                raise HTTPException(400, f"产品图下载失败: {str(e)[:200]}")

        script = await generate_script(
            image_base64=image_base64,
            image_mime=image_mime,
            market=body.market,
            duration=body.duration,
            model_info=body.model_info,
            user_idea=body.user_idea,
            mode=body.mode if body.mode in ("story", "direct") else "story",
        )

    except HTTPException:
        raise
    except Exception as e:
        add_credits(user_id, cost, reason="task_refund",
                    ref_id="script_gen_fail", module="video/general/script")
        log_error(f"video_general/script 生成失败 user={user_id}: {e}")
        raise HTTPException(500, f"脚本生成失败，积分已退还: {str(e)[:300]}")

    log_info(f"video_general/script OK user={user_id} market={body.market} duration={body.duration}s script_len={len(script)}")
    return {
        "script": script,
        "cost": cost,
        "market": body.market,
        "duration": body.duration,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 入口B第二步：脚本 → 视频生成
# POST /api/video/general/script-to-video
# ──────────────────────────────────────────────────────────────────────────────

class ScriptToVideoRequest(BaseModel):
    script: str = Field(..., description="Gemini 生成的脚本文字")
    product_image_urls: List[str] = Field(..., description="产品图 URL 列表（正面必传）")
    scene_image_url: Optional[str] = Field(None)
    model_image_url: Optional[str] = Field(None)
    model_video_url: Optional[str] = Field(None)
    model_source: str = Field("auto", description="'auto' / 'image' / 'video'")
    aspect_ratio: str = Field("9:16")
    ref_video_url: Optional[str] = Field(None, description="入口A参考视频URL（可选，Seedance 60% 折扣）")
    resolution: str = Field("480p", description="输出分辨率：'480p' / '1080p' / '4k'")
    contrast_image_url: Optional[str] = Field(None, description="对比产品图URL（起/承阶段用，可选）")
    enable_voice: bool = Field(True, description="是否开启TTS+Lipsync")
    target_duration: int = Field(15, ge=5, le=120, description="目标视频总时长（秒），用于校准分镜时长")


@router.post("/script-to-video")
async def script_to_video_submit(
    body: ScriptToVideoRequest,
    current_user: dict = Depends(get_current_user),
):
    """脚本确认后生成视频：解析分镜 → Seedance 2.0 批处理 → ffmpeg 拼接"""
    import math as _math
    from app.services.billing import add_credits
    from app.services.video_general_script import parse_script

    if not body.script.strip():
        raise HTTPException(400, "script 必填")
    if not body.product_image_urls:
        raise HTTPException(400, "product_image_urls 必填")

    # 解析脚本
    scenes = parse_script(body.script)
    if not scenes:
        raise HTTPException(400, "脚本解析失败，未找到有效分镜。请确认脚本格式包含 [镜头X]：时间范围 |...")

    # 按批处理分组估算积分（同 skill_generate 逻辑）
    try:
        from app.database import get_app_config
        _batch_max = float(get_app_config("batch_max_duration", "8"))
    except Exception:
        _batch_max = 8.0

    def _preview_cost(scs: list, max_dur: float = 8.0, min_dur: int = 4) -> int:
        tasks, cur = [], 0.0
        for s in scs:
            d = float(s.get("duration_sec") or 4)
            if cur + d <= max_dur:
                cur += d
            else:
                tasks.append(cur)
                cur = d
        if cur > 0:
            tasks.append(cur)
        return sum(max(min_dur, _math.ceil(d)) for d in tasks)

    billing_sec = _preview_cost(scenes, _batch_max)
    cost = max(65, billing_sec * 65)

    # 分辨率附加费
    _RESOLUTION_SURCHARGE = {"480p": 0, "1080p": 10, "4k": 50}
    cost += _RESOLUTION_SURCHARGE.get(body.resolution, 0)

    user_id = str(current_user["id"])
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足，需 {cost} 积分（约 {len(scenes)} 个分镜）")

    from app.api.jobs import JOBS, _save_jobs, _execute_job, create_tracked_task
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "user_numeric_id": user_id,
        "type": "script_to_video",
        "title": f"脚本生成视频（{len(scenes)} 段）",
        "params": {
            "script": body.script,
            "scenes": scenes,
            "product_image_urls": body.product_image_urls,
            "scene_image_url": body.scene_image_url or "",
            "model_image_url": body.model_image_url or "",
            "model_video_url": body.model_video_url or "",
            "aspect_ratio": body.aspect_ratio,
            "ref_video_url": body.ref_video_url or "",
            "resolution": body.resolution,
            "contrast_image_url": body.contrast_image_url or "",
            "enable_voice": body.enable_voice,
            "target_duration": body.target_duration,
            "_user_id": user_id,
        },
        "module": "video/general/script-to-video",
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    create_tracked_task(_execute_job(job_id))
    log_info(f"script-to-video submitted job={job_id} user={user_id} scenes={len(scenes)} cost={cost}")
    return {"job_id": job_id, "status": "pending", "cost": cost, "n_scenes": len(scenes)}


# ──────────────────────────────────────────────────────────────────────────────
# 入口A：视频复刻分析
# POST /api/video/general/video-analyze
# ──────────────────────────────────────────────────────────────────────────────

class VideoAnalyzeRequest(BaseModel):
    video_url: str = Field(..., description="参考视频 URL（通过 /upload/video 上传得到）")
    product_image_urls: List[str] = Field(..., description="产品图 URL 列表（正面必传）")
    model_info: str = Field("AI 自动生成模特")
    market: str = Field("海外")
    duration: int = Field(15, ge=5, le=120)
    user_idea: str = Field("", max_length=500)


@router.post("/video-analyze")
async def video_analyze_submit(
    body: VideoAnalyzeRequest,
    current_user: dict = Depends(get_current_user),
):
    """入口A第一步：上传参考视频 → Gemini 分析拆镜 → 替换产品 → 返回脚本。"""
    from app.services.billing import add_credits
    from app.services.video_general_script import analyze_video

    if not body.video_url.strip():
        raise HTTPException(400, "video_url 必填")
    if not body.product_image_urls:
        raise HTTPException(400, "product_image_urls 必填")

    user_id = str(current_user["id"])
    cost = 35
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足，需 {cost} 积分")

    try:
        script = await analyze_video(
            video_url=body.video_url,
            product_image_urls=body.product_image_urls,
            market=body.market,
            duration=body.duration,
            model_info=body.model_info,
            user_idea=body.user_idea,
        )
    except Exception as e:
        add_credits(user_id, cost, reason="task_refund",
                    ref_id="video_analyze_fail", module="video/general/video-analyze")
        log_error(f"video-analyze 失败 user={user_id}: {e}")
        raise HTTPException(500, f"视频分析失败，积分已退还: {str(e)[:300]}")

    log_info(f"video-analyze OK user={user_id} market={body.market} script_len={len(script)}")
    return {"script": script, "cost": cost}
