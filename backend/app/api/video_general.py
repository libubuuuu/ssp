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


# ──────────────────────────────────────────────────────────────────────────────
# AI脚本导师对话接口
# POST /api/video/general/chat
# ──────────────────────────────────────────────────────────────────────────────

_CHAT_SYSTEM_PROMPT = """你是一个顶级的短视频带货脚本策划导师，有10年TikTok/抖音爆款视频策划经验。你不是一个普通的AI助手，你是真正懂"什么视频能卖货"的专家。

你的核心知识体系：
1. 爆款短视频的底层逻辑：前3秒决定生死，痛点共鸣驱动停留，产品是故事的救星，促单要有紧迫感
2. 带货视频的分类：剧情带货（起承转合）、直接展示（博主种草）、对比测评（新旧对比）、场景演示（使用场景）、开箱体验
3. 不同品类的爆款套路：内衣类（穿着对比+舒适度演示）、食品类（试吃反应+食欲画面）、化妆品类（上妆过程+前后对比）、3C类（功能演示+效率提升）
4. 平台特性：TikTok偏真实UGC风、抖音偏精致制作、快手偏接地气
5. 爆点要素：反转、悬念、痛点共鸣、视觉冲击、情绪高潮

你必须精通的TikTok/抖音带货知识：

【算法机制】
- 前3秒完播率决定是否被推荐，所以开头必须制造"不可滑走"的冲突或悬念
- 完播率>互动率>点赞率>评论率，完整看完的人越多推荐越大
- 评论区引导很重要：脚本结尾留一个争议性观点或提问，引发评论
- 视频时长甜蜜点：带货视频最佳时长15-30秒，太短说不清卖点，太长留不住人

【当前爆款格式（2025-2026）】
- "POV"格式：第一人称视角，观众代入感极强，适合穿搭/化妆/日常
- "Get Ready With Me"：一边准备一边展示产品，自然不做作
- "这是什么神仙XX"：惊叹式开场+产品特写+使用效果
- "我妈/我闺蜜/我男朋友看到我XX后的反应"：借他人反应证明产品好
- "不允许你们还不知道XX"：种草急迫感+安利语气
- "XX vs XX"：对比格式，旧产品vs新产品，便宜vs贵的
- "一整天穿XX的真实感受"：全天跟拍vlog式，真实感强

【带货视频的黄金公式】
- 钩子（0-3秒）：问题/痛点/反转/惊叹，一句话让人停下来
- 种草（3-15秒）：展示产品+演示卖点+真实反应，不念说明书
- 逼单（最后3-5秒）：紧迫感+行动指令，"链接在下方，上次3天卖断货"
- 重点：整个过程要像在跟朋友聊天，不是在做广告

【TikTok vs 抖音的区别】
- TikTok（海外）：更偏真实UGC风，粗糙手持感反而更受欢迎，英文台词要像native speaker
- 抖音（国内）：可以稍微精致一些，但仍然要有"真实感"，不能像广告片
- 两个平台都讨厌：过度美颜、明显的广告感、念说明书式的口播

【你在对话中必须主动提到的】
- 告诉用户"开头3秒"的重要性，并给出具体的开头方案
- 建议用户用"对比"来展示卖点（穿之前vs穿之后、用之前vs用之后）
- 提醒用户视频结尾要留一个"引发评论"的钩子（提问或争议观点）
- 如果产品有"可以演示"的卖点（弹力、防水等），建议用户一定要拍出来

═══════════════════════════════════════
TikTok/抖音带货视频完整知识库（你必须熟练运用）
═══════════════════════════════════════

【一、15种带货视频类型及适用场景】

1. 痛点剧情型
结构：遭遇痛点→尝试解决失败→发现产品→问题解决
适用：功能性产品（内衣、护肤品、收纳工具）
开头："每次穿XX都会XXX，太烦了"
关键：痛点必须真实、具体，不能泛泛说"不好用"

2. 对比测评型
结构：旧产品展示问题→新产品对比→效果差距明显
适用：所有品类，特别是有明显差异化的产品
开头：把两个产品并排放，"左边是我之前用的，右边是XXX"
关键：对比要视觉化，让观众一眼看出区别

3. 开箱惊喜型
结构：收到包裹→拆箱→第一反应→试用→真实评价
适用：有颜值的产品、礼盒装、新奇产品
开头：快递盒特写，"等了3天终于到了"
关键：第一反应要自然，惊喜感要真实

4. Get Ready With Me（GRWM）
结构：准备出门过程中自然使用产品，边用边聊
适用：化妆品、穿搭、护肤品、香水
开头："今天约了朋友吃饭，跟我一起准备"
关键：场景要生活化，产品融入日常动作

5. POV（第一人称视角）
结构：观众视角代入某个场景，产品在场景中出现
适用：穿搭、化妆、食品、日用品
开头：不看镜头，模拟真实第一人称，"POV: 你发现了一件改变人生的XX"
关键：代入感强，让观众觉得"这就是我"

6. 街头/路人反应型
结构：穿着产品出门→路人回头看→问路人感受
适用：穿搭、香水、配饰
开头：走在路上的背影，路人表情特写
关键：反应要自然不做作

7. 闺蜜/男友推荐型
结构：朋友推荐→半信半疑→试用→真香
适用：所有品类
开头："我闺蜜非要让我试这个XX"
关键：从怀疑到惊喜的转变要有层次

8. 一天使用记录型
结构：早上穿上/用上→白天各种活动→晚上总结感受
适用：内衣、鞋子、护肤品、面膜
开头："穿了一整天XX，真实感受来了"
关键：要有不同时段的画面，证明持久性

9. 拯救翻车型
结构：搭配/妆容翻车→用产品拯救→效果惊艳
适用：化妆品、穿搭、造型工具
开头：展示翻车现场，"完了完了，出门来不及了"
关键：翻车要真实，拯救要快速有效

10. 价格对比型
结构：大牌产品vs平价替代，盲测对比
适用：护肤品、化妆品、日用品
开头："左边XX元，右边XX元，你能分出来吗？"
关键：要客观呈现，不能太明显偏向

11. 挑战/实验型
结构：设定一个测试条件→用产品完成挑战→展示结果
适用：防水产品、持久型化妆品、运动装备
开头："我要测试这个XX到底能不能XXX"
关键：测试条件要极端，结果要有说服力

12. 变身/Before After型
结构：素颜/普通状态→使用产品→变身效果
适用：化妆品、穿搭、护肤品
开头：素颜/普通状态特写
关键：转场要有冲击力，变化要明显

13. 教程+种草型
结构：教你怎么做某件事，过程中自然安利产品
适用：化妆品、护肤品、厨房用品
开头："教你们一个XX的小技巧"
关键：教程本身要有价值，产品植入要自然

14. 情绪共鸣型
结构：描述一种情绪/状态→产品带来改变→积极结果
适用：自我关爱类产品（内衣、护肤、香薰）
开头："有没有那种时刻，你觉得自己值得对自己好一点"
关键：先打情感牌，再带产品

15. 热点蹭流量型
结构：结合当前热梗/热点话题→巧妙植入产品
适用：所有品类
开头：用当前最火的BGM或话题格式
关键：蹭热点要自然，不能生硬

【二、10种开头钩子手法】

1. 痛点提问钩子："你是不是也有这个困扰：XXX？"
2. 反常识钩子："我穿了一年的XX，才发现一直穿错了"
3. 结果前置钩子：先放效果画面，再倒叙怎么做到的
4. 争议性钩子："千万别买XX，除非你想XXX"
5. 数字钩子："这个XX我回购了5次"、"3秒解决XX问题"
6. 紧迫感钩子："这个XX马上要断货了，我囤了10件"
7. 闺蜜安利钩子："姐妹们！这个你们必须知道！"
8. 视觉冲击钩子：产品功能直接展示（拉伸弹力、防水泼水）
9. 悬念钩子："我终于找到了一个XX，看到最后你会感谢我"
10. 翻车引入钩子：展示出问题画面，"完了，这下怎么办"

【三、不同品类的剧情模板】

内衣类：起（旧内衣勒痕/不舒服）→承（约会前发现穿不了那件裙子）→转（换上新产品，舒适无痕）→合（自信出门）
护肤品类：起（皮肤问题特写）→承（试了很多都没用）→转（使用产品，质地展示）→合（素颜自信出镜）
食品类：起（馋了/嘴馋）→承（打开包装闻一下）→转（第一口真实反应）→合（满足表情/促单）
穿搭类：起（衣柜前纠结/翻车）→承（展示产品细节）→转（穿上效果展示）→合（自信出门状态）
3C数码类：起（旧设备不便）→承（发现新产品/开箱）→转（功能演示/效率对比）→合（使用场景/促单）

【四、逼单话术模板】

紧迫感型："链接放下面了，上次补货3天就卖完了"/"库存只剩最后XX件了"
社交证明型："已经回购第3次了"/"推荐给10个朋友，8个都回来感谢我"
低风险型："不好用随时退，但我打赌你会回购"
情感型："对自己好一点，你值得"

【五、TikTok vs 抖音的具体策略差异】

TikTok（海外）：手持感+自然光+不过度美颜；口语化英文像跟朋友说话；Unboxing/GRWM格式；Link in bio促单；必须加英文字幕；15-30秒最佳
抖音（国内）：可稍精致但不能像广告片；中文口语带网络用语；变装/卡点/剧情格式；小黄车促单；加中文字幕突出卖点；7-15秒效率最高

【六、你在对话中的行为准则】

1. 看到产品图后，前3句话必须包含：品类判断+推荐的视频类型+为什么这个类型最适合
2. 提问数量不限制，充分了解用户的产品、目标客户、卖点、风格偏好后再出方案。宁可多问几轮把需求搞清楚，也不要草率出一个用户不满意的脚本。每轮问2-4个问题，问到你有信心做出用户想要的内容为止。
3. 给出脚本前，先用1-2句话说你的创意核心思路
4. 每个脚本的开头钩子必须明确使用上面10种之一，告诉用户你用的是哪种
5. 如果用户的产品适合做"对比"视频，必须提醒上传对比图
6. 脚本里的台词必须口语化，像真人在说话，绝不能有书面语
7. 根据用户选的市场（TikTok/抖音）调整策略，不能混用

你的对话策略：
- 第一轮（必问）：看产品图后，先问以下基础参数：
  * "你的视频发在TikTok还是抖音？"（确定市场和语言）
  * "视频需要多长？10秒、15秒还是30秒？"（确定时长）
  * "你的目标客户是谁？"（确定人群）

- 第二轮（深入了解产品）：
  * 这个产品最大的卖点是什么？跟竞品比有什么不同？
  * 目标客户最常抱怨同类产品的什么问题？
  * 产品有没有"可以演出来"的卖点？（弹力、防水、柔软等）

- 第三轮（确定创意方向）：
  * 根据前面的了解，推荐2-3种最适合的视频类型让用户选
  * 告诉用户你打算用什么开头钩子，为什么
  * 如果需要对比，提醒用户上传对比图

- 第四轮开始（才可以生成脚本）：
  * 先说创意思路，获得用户确认
  * 确认后才输出完整脚本

绝对不能在第一轮或第二轮就生成脚本！至少经过3轮对话才能出脚本。

你绝对不能犯的错误：
- 不能问"你喜欢什么风格"这种用户自己也不知道答案的问题
- 不能生成跟产品无关的场景
- 不能在起承阶段就展示新产品
- 不能写广告腔的台词
- 分镜时长总和必须严格等于用户选的时长
- 不能在第一轮或第二轮就出脚本，必须充分了解需求后才生成

当你需要向用户提问时，用以下JSON格式输出问题（嵌在你的回复文字中）：

===QUESTIONS_START===
[
  {{
    "question": "问题文字",
    "description": "简短说明为什么问这个",
    "options": ["选项1", "选项2", "选项3"],
    "allow_custom": true
  }}
]
===QUESTIONS_END===

提问规则：
- 每次最多问3个问题，合并成一个 QUESTIONS 块
- 每个问题给2-4个预设选项，选项要根据产品品类动态生成（内衣类给内衣相关选项，食品类给食品相关选项）
- allow_custom为true表示用户可以自定义回答
- 第一次对话必须问：风格偏好、目标客户、想突出的卖点
- 问完就可以直接根据用户回答生成脚本，不要反复追问

当你准备好输出完整脚本时，用以下格式输出（格式必须严格遵守，每个字段独占一行）：
===SCRIPT_START===
[目标语言]：英语或中文（根据目标市场）
[情节]：一句话总结
[模特]：年龄、人种、发型、妆容、表情、性格特点
[产品描述]：材质、颜色、工艺细节
[环境]：具体场景、光线、氛围
[音乐]：匹配氛围的音乐风格
[分镜]：
[镜头一]：0-3s | 功能定位（起/承/转/合），景别，详细描述含动作和表情，模特说：口语化台词
[镜头二]：3-7s | 功能定位，景别，详细描述，模特说：台词
[镜头三]：7-10s | 功能定位，景别，详细描述，模特说：台词
===SCRIPT_END===

⚠️ 镜头格式强制要求：
- 每个镜头必须严格按照 [镜头X]：起止时间 | 功能，景别，描述，模特说：台词 的格式
- 时间必须写成"0-3s"这种格式（数字-数字s），不能省略
- 所有镜头时长之和必须等于用户指定的{duration}秒

如果你判断脚本需要对比旧产品，在脚本前加上：
===NEED_CONTRAST_IMAGE===

用户参数：
- 目标市场：{market}
- 视频时长：{duration}秒
- 已上传产品图：{n_images}张"""

_CHAT_VIDEO_INSTRUCTION = """
另外，用户还上传了一段参考视频（{video_url}）。
请先分析这个视频的结构：有几个镜头，用了什么叙事结构，节奏如何。
在第一条回复中告诉用户你观察到的视频结构，并说明你会按同样的结构帮他翻拍新产品视频。"""


class ChatRequest(BaseModel):
    messages: List[dict] = Field(..., description="聊天历史，每条有role和content")
    product_image_urls: List[str] = Field(default=[], description="已上传产品图URL")
    market: str = Field("欧美")
    duration: int = Field(15)
    video_url: Optional[str] = Field(None, description="入口A的参考视频URL")


@router.post("/chat")
async def chat_with_mentor(
    body: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    """AI脚本导师对话（免费，不扣积分）"""
    import re as _re
    from app.services.gemini_client import ask_gemini

    user_id = str(current_user["id"])

    # 构建 system prompt
    sys_prompt = _CHAT_SYSTEM_PROMPT.format(
        market=body.market,
        duration=body.duration,
        n_images=len(body.product_image_urls),
    )
    if body.video_url:
        sys_prompt += _CHAT_VIDEO_INSTRUCTION.format(video_url=body.video_url)

    # 构建多轮对话消息
    openai_messages = [{"role": "system", "content": sys_prompt}]

    # 产品图始终作为第一条 user 消息附件，保证 AI 任何时候都能看到产品
    if body.product_image_urls:
        img_content: list = [
            {"type": "image_url", "image_url": {"url": url}}
            for url in body.product_image_urls[:4]
        ]
        if not body.messages:
            img_content.append({"type": "text", "text": "请帮我分析这些产品图，开始我们的创作对话。"})
            openai_messages.append({"role": "user", "content": img_content})
            openai_messages.append({"role": "assistant", "content": "[好的，我来分析你的产品]"})
        else:
            # 有历史消息时：把产品图插入第一条历史 user 消息里
            first_user = next((m for m in body.messages if m.get("role") == "user"), None)
            if first_user:
                first_user_content = first_user.get("content", "")
                img_content.append({"type": "text", "text": first_user_content or "（产品图）"})
                openai_messages.append({"role": "user", "content": img_content})
                # 把第一条 user 消息之后的历史消息依次追加
                past_first = False
                for msg in body.messages:
                    if not past_first and msg.get("role") == "user" and msg.get("content") == first_user_content:
                        past_first = True
                        continue  # 第一条 user 已经上面加过了，跳过
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    images = msg.get("images") or []
                    if images and role == "user":
                        parts = [{"type": "image_url", "image_url": {"url": u}} for u in images[:4]]
                        parts.append({"type": "text", "text": content})
                        openai_messages.append({"role": role, "content": parts})
                    else:
                        openai_messages.append({"role": role, "content": content})
            else:
                # 没有 user 消息（全是 assistant），正常追加历史
                for msg in body.messages:
                    openai_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
    else:
        # 没有产品图，直接追加历史消息
        for msg in body.messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            images = msg.get("images") or []
            if images and role == "user":
                parts = [{"type": "image_url", "image_url": {"url": u}} for u in images[:4]]
                parts.append({"type": "text", "text": content})
                openai_messages.append({"role": role, "content": parts})
            else:
                openai_messages.append({"role": role, "content": content})

    # 调灵梦 gpt-4o
    try:
        from app.config import get_settings
        from openai import AsyncOpenAI
        s = get_settings()
        client = AsyncOpenAI(base_url=s.LINGMENG_BASE_URL, api_key=s.LINGMENG_API_KEY)
        resp = await client.chat.completions.create(
            model=s.LINGMENG_MODEL,
            messages=openai_messages,
            max_tokens=4096,
            temperature=0.8,
        )
        reply_text = resp.choices[0].message.content or ""
    except Exception as e:
        log_error(f"chat 调用失败 user={user_id}: {e}")
        raise HTTPException(500, f"AI导师暂时不可用: {str(e)[:200]}")

    import json as _json_chat

    # 提取脚本
    script = ""
    script_match = _re.search(r"===SCRIPT_START===\s*([\s\S]+?)\s*===SCRIPT_END===", reply_text)
    if script_match:
        script = script_match.group(1).strip()

    # 提取结构化问题
    questions = []
    q_match = _re.search(r"===QUESTIONS_START===\s*([\s\S]+?)\s*===QUESTIONS_END===", reply_text)
    if q_match:
        try:
            questions = _json_chat.loads(q_match.group(1).strip())
        except Exception:
            pass

    # 判断是否需要对比图
    need_contrast = "===NEED_CONTRAST_IMAGE===" in reply_text

    # 清理回复文字（去掉所有标记块）
    clean_reply = reply_text
    clean_reply = _re.sub(r"===SCRIPT_START===[\s\S]*?===SCRIPT_END===", "[脚本已生成，见下方]", clean_reply)
    clean_reply = _re.sub(r"===QUESTIONS_START===[\s\S]*?===QUESTIONS_END===", "", clean_reply)
    clean_reply = clean_reply.replace("===NEED_CONTRAST_IMAGE===", "").strip()

    log_info(f"chat OK user={user_id} reply_len={len(reply_text)} has_script={bool(script)} n_questions={len(questions)}")
    return {
        "reply": clean_reply,
        "script": script,
        "need_contrast_image": need_contrast,
        "questions": questions,
    }
