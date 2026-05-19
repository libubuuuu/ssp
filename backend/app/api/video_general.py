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


class GenerateSceneRequest(BaseModel):
    description: str = Field(..., description="场景描述")
    orientation: str = Field("portrait", description="portrait=9:16 / landscape=16:9")


@router.post("/generate-scene")
async def generate_scene(
    body: GenerateSceneRequest,
    current_user: dict = Depends(get_current_user),
):
    """AI 生成场景图（调 fal-ai/gpt-image-2，免费不扣积分）"""
    import fal_client as _fal

    image_size = {"width": 1024, "height": 1792} if body.orientation == "portrait" else {"width": 1792, "height": 1024}
    prompt = (
        f"Professional photography of {body.description.strip()}. "
        "Empty scene without any people. Wide angle, natural lighting, "
        "high quality, realistic. No text, no watermarks."
    )
    try:
        result = await _fal.run_async(
            "fal-ai/gpt-image-2",
            arguments={
                "prompt": prompt,
                "image_size": image_size,
                "num_images": 1,
                "quality": "auto",
            },
        )
        img_url = (result.get("images") or [{}])[0].get("url", "")
        if not img_url:
            raise RuntimeError("gpt-image-2 未返回图片 URL")
    except Exception as e:
        log_error(f"generate_scene 失败: {e}")
        raise HTTPException(500, f"场景图生成失败: {str(e)[:200]}")

    log_info(f"generate_scene OK user={current_user['id']} desc={body.description[:50]}")
    return {"scene_image_url": img_url}


class GenerateCopyRequest(BaseModel):
    script: str = Field(..., description="脚本文字")
    platform: str = Field("TikTok", description="TikTok或抖音")
    target_lang: str = Field("en", description="目标语言代码")


@router.post("/generate-copy")
async def generate_copy(
    body: GenerateCopyRequest,
    current_user: dict = Depends(get_current_user),
):
    """用户确认脚本后生成文案（标题/描述/标签/发布时间），免费不扣积分。"""
    from app.config import get_settings
    from openai import AsyncOpenAI
    s = get_settings()
    client = AsyncOpenAI(base_url=s.LINGMENG_BASE_URL, api_key=s.LINGMENG_API_KEY)
    copy_data = await _call_copywriter(client, body.script, body.platform, body.target_lang)
    log_info(f"generate_copy OK user={current_user['id']} lang={body.target_lang}")
    return copy_data or {"title": "", "description": "", "hashtags": [], "best_time": ""}


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
    resolution: str = Field("1080p", description="输出分辨率：'1080p' / '2k' / '4k'")
    contrast_image_url: Optional[str] = Field(None, description="对比产品图URL（起/承阶段用，可选）")
    enable_voice: bool = Field(True, description="是否开启TTS+Lipsync")
    target_duration: int = Field(10, description="视频时长，只能是5/10/15/30/60")
    target_lang: str = Field("en", description="目标语言代码 en/zh/ja/ko/es/pt/ar")
    is_replicate: bool = Field(False, description="视频拆解路径，跳过时长白名单校验")


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

    _ALLOWED_DURATIONS = {5, 10, 15, 30, 60}
    if not body.is_replicate and body.target_duration not in _ALLOWED_DURATIONS:
        raise HTTPException(400, f"时长只能是 {sorted(_ALLOWED_DURATIONS)}，收到 {body.target_duration}")

    # 解析脚本
    scenes = parse_script(body.script)
    if not scenes:
        log_error(f"脚本解析失败 user={current_user['id']} script_head={body.script[:200]!r}")
        raise HTTPException(400, "脚本解析失败，未找到有效分镜。请确认脚本格式包含 [镜头X]：时间范围 |...")

    cost = max(65, body.target_duration * 65)  # 视频
    cost += 13  # 模特头像（原18改13）
    cost += 13  # 场景图
    cost += 26  # 趋势搜索
    cost += 26  # 审稿员
    cost += 13  # 文案师

    if not body.is_replicate:
        _RESOLUTION_PER_SEC = {"1080p": 3, "2k": 6, "4k": 11}
        cost += _RESOLUTION_PER_SEC.get(body.resolution, 3) * body.target_duration

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
            "target_lang": body.target_lang,
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
    cost = 25
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

_CHAT_SYSTEM_PROMPT = """你是林久·创意导师，TikTok/抖音带货视频脚本专家。
你的任务：通过对话了解用户的产品，然后生成高转化的带货视频脚本。

核心知识（内化运用，不要复述给用户）：
- 黄金结构：钩子(0-3s) → 痛点(3-7s) → 产品+证据(7-12s) → CTA(最后2-3s)
- 钩子类型：反差冲突/痛点提问/视觉冲击/数据震惊/悬念/场景带入/反常识/挑战质疑
- 信任证据：实时实验 > 前后对比 > 微观细节（❌已失效："销量10万+""明星同款"）
- CTA原则：优惠可见 + 路径唯一 + 操作无脑（❌失败指令："点击左下角"）
- TikTok风格：手持感+自然光+粗糙真实，1秒定胜负
- 抖音风格：稍精致但有真实感，3秒内必须给钩子
- 用"你"不用"我们"，台词像朋友说话不像广告

⛔⛔⛔ 产品图拆解铁律（写脚本前必须做）：
看正面图：分析外观设计（杯型/面料/颜色/肩带）
看反面图：分析背面结构（搭扣/背带/排扣数量）
理解怎么穿：从哪里扣、穿上后从外面看是什么效果
内衣类产品穿在衣服里面！穿着效果镜头必须描述外衣，产品在里面看不到！

对话策略：
- 第一轮（必问）：用户已选好平台（{platform}）、语言（{target_lang}）、时长（{duration}秒），⛔不要再问这三个！直接问产品：目标客户/最大卖点/可演示功能。用结构化问题卡片问3个问题。
- 第二轮：深入了解产品细节，问使用场景/竞品差异/用户痛点
- 第三轮起：信息够了就直接生成脚本

提问格式（必须严格遵守JSON）：
===QUESTIONS_START===
[{{"question":"问题","description":"为什么问","options":["选项1","选项2","选项3"],"allow_custom":true}}]
===QUESTIONS_END===

⛔⛔⛔ 场景数量铁律（写脚本前必须检查！违反=废稿！）：
- 5秒或10秒：只能1个场景！禁止切换场景！
- 15秒：最多2个场景
- 30秒：最多3个场景
- 60秒：最多4个场景

脚本格式（必须严格遵守）：
===SCRIPT_START===
[目标语言]：根据target_lang参数填写（English/日本語/한국어/Español/Português/العربية/中文）
[产品名称]：xxx
[视频时长]：{duration}秒
[视频类型]：xxx
[环境]：具体场景、光线、氛围
[音乐]：匹配氛围的音乐风格
[分镜]：
[镜头一]：0-3s | 场景：xxx | 功能，景别，详细画面描述，模特说：台词
[镜头二]：3-7s | 场景：xxx | 功能，景别，详细画面描述，模特说：台词
[镜头三]：7-10s | 场景：xxx | 功能，景别，详细画面描述，模特说：台词
===SCRIPT_END===

⛔ 时长铁律：所有镜头时长加起来必须精确等于{duration}秒！场景数量不得超过上限！验算两项都合格再输出！

⛔⛔⛔ 画面描述铁律（缺一废稿）：
每个镜头必须包含：
1. 身体姿势：具体到手脚位置（"右手伸到背后拉扯肩带"不是"调整肩带"）
2. 表情：具体到五官（"眉头紧皱嘴角下垂"不是"困扰表情"）
3. 穿着：从外到内每一层（"外穿白色衬衫，衬衫下是深棕色钢圈文胸，肩带勒进皮肤"）
4. 镜头运动：起始→结束（"中景正面推近到右肩特写"）
5. 视觉焦点：观众最应该看到什么

❌ 废稿："模特穿着普通内衣，背后勒痕明显，整理衬衫露出困扰表情"
✅ 合格："模特穿着白色修身衬衫（纽扣全扣），衬衫下是深棕色传统钢圈文胸。模特站在卧室全身镜前，右手伸到背后拉扯文胸后背带，左手撑着腰。她皱眉低头看镜子里自己的右肩——衬衫领口处可以看到文胸肩带勒进皮肤形成的红色凹痕。镜头从中景正面缓慢推近到右肩特写，焦点对准肩带勒痕。"

⛔⛔⛔ 修改脚本铁律：
用户要求修改 → 1-2句说明改了什么 → 立刻输出完整===SCRIPT_START===格式 → 禁止只说"我会修改"！

如果脚本需要对比旧产品，在脚本前加：
===NEED_CONTRAST_IMAGE===

回复格式：
- 绝对禁止###标题/**加粗**/-列表等markdown！像微信聊天一样说话！
- 台词必须用{target_lang}指定的语言
- TikTok台词禁止出现中文（除非target_lang是zh）

用户参数：
- 发布平台：{platform}（已选定，不要问）
- 目标市场：{market}（已选定，不要问）
- 视频时长：{duration}秒（已选定，不要问）
- 目标语言：{target_lang}（已选定，台词必须用此语言）
- 已上传产品图：{n_images}张"""

_CHAT_VIDEO_INSTRUCTION = """
另外，用户还上传了一段参考视频（{video_url}）。
请先分析这个视频的结构：有几个镜头，用了什么叙事结构，节奏如何。
在第一条回复中告诉用户你观察到的视频结构，并说明你会按同样的结构帮他翻拍新产品视频。"""

# ── 多角色 AI 系统辅助 ─────────────────────────────────────────────
import re as _re_chat
import json as _json_chat

_XIAOLI_DONE_MARKER = "===XIAOLI_DONE==="

_REVIEWER_SYSTEM = """你是一个TikTok/抖音带货视频脚本审稿专家（数据驱动版）。审查以下脚本并给出评分和改进建议。

评分标准（总分60分，每项0-10分）：
1. 开头钩子力（10分）：3秒内是否制造冲突/好奇？是否用了验证过的钩子类型（反差/痛点提问/视觉冲击/数据/悬念/场景带入/反常识/挑战）？
2. 痛点共鸣度（10分）：是否用第二人称"你"？是否有具体场景还原+情绪闭环（三连问结构）？
3. 产品植入自然度（10分）：产品是"解决方案"还是"硬广"？是否有可即时验证的证据（实时实验>前后对比>微观细节）？
4. 促单力度（10分）：CTA是否无脑操作？是否有紧迫感？路径是否唯一？有没有用失败指令（"点击左下角"）？
5. 台词口语化（10分）：像朋友说话还是广告文案？有没有用"你"而非"我们的产品"？
6. 视觉可执行性（10分）：场景是否具体？镜头切换是否合理？Seedance能否生成这些画面？

扣分项（明确标注）：
- 开头超3秒才进主题：-3分
- 台词像广告文案（"尊享优质体验"等）：-5分
- 没有任何可验证证据：-5分
- CTA说"点击左下角"：-3分
- 使用"我们的产品"而非"你"的视角：-3分

加分项（明确标注）：
- 使用"无限循环"脚本（结尾接开头）：+3分
- 台词有可传播金句：+2分
- 包含用户会截图分享的画面：+2分

输出格式：
总分：XX/60
各项评分：1.X/10  2.X/10  3.X/10  4.X/10  5.X/10  6.X/10
扣分/加分：（列出触发的项目）
改进建议：（具体说哪里要改、怎么改，结合知识库公式给出替代台词）"""


def _extract_all_text(messages: list) -> str:
    parts = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
    return " ".join(parts)


def _detect_platform_and_category(messages: list):
    text = _extract_all_text(messages)
    text_lower = text.lower()
    platform = None
    if "tiktok" in text_lower:
        platform = "TikTok"
    elif "抖音" in text or "douyin" in text_lower:
        platform = "抖音"

    category = None
    cats = ["内衣", "文胸", "护肤", "美妆", "化妆品", "口红", "眼影", "粉底", "食品", "零食",
            "小吃", "饮料", "3C", "数码", "手机", "耳机", "平板", "穿搭", "连衣裙", "T恤",
            "外套", "裤子", "鞋子", "包包", "家居", "厨房", "运动", "健身", "宠物", "母婴"]
    for cat in cats:
        if cat in text:
            category = cat
            break
    return platform, category


def _xiaoli_already_searched(messages: list) -> bool:
    for m in messages:
        if m.get("role") == "assistant":
            content = m.get("content", "")
            if isinstance(content, str) and _XIAOLI_DONE_MARKER in content:
                return True
    return False


def _script_in_history(messages: list):
    for m in reversed(messages):
        if m.get("role") == "assistant":
            content = m.get("content", "")
            if isinstance(content, str):
                match = _re_chat.search(r"===SCRIPT_START===\s*([\s\S]+?)\s*===SCRIPT_END===", content)
                if match:
                    return match.group(1).strip()
    return None


def _should_trigger_copywriter(messages: list) -> bool:
    if not _script_in_history(messages):
        return False
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not last_user:
        return False
    content = last_user.get("content", "")
    if not isinstance(content, str):
        return False
    triggers = ["生成视频", "开始生成", "确认脚本", "可以生成", "开始制作", "生成吧",
                "制作视频", "用这个脚本", "就这个脚本", "可以了", "好的生成"]
    return any(t in content for t in triggers)


async def _call_xiaoli_search(client, platform: str, category: str, lang_name: str = "English") -> str:
    from app.config import get_settings
    from openai import AsyncOpenAI as _OAI
    s = get_settings()
    # 用联网搜索专用 key；如未配置则降级使用 client（会 503）
    search_client = _OAI(base_url=s.SEARCH_BASE_URL, api_key=s.SEARCH_API_KEY) if s.SEARCH_API_KEY else client
    search_prompt = (
        f"搜索{platform}平台上{lang_name}市场关于{category}的最新带货爆款视频趋势，包括："
        "1.当前最火的视频格式和结构 2.热门的开头钩子手法 3.成功案例的特点 "
        "4.当前流行的BGM风格。只返回最新2025-2026年的信息。"
    )
    try:
        resp = await search_client.chat.completions.create(
            model="gpt-4o-search-preview",
            messages=[{"role": "user", "content": search_prompt}],
            max_tokens=1500,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        log_error(f"小李搜索失败: {e}")
        return ""


async def _call_reviewer(client, script: str) -> dict:
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _REVIEWER_SYSTEM},
                {"role": "user", "content": f"请审查以下脚本：\n\n{script}"},
            ],
            max_tokens=1000,
        )
        review_text = resp.choices[0].message.content or ""
        score_match = _re_chat.search(r"总分[：:]\s*(\d+)\s*/\s*60", review_text)
        score = int(score_match.group(1)) if score_match else 0
        sugg_idx = review_text.find("改进建议")
        suggestions = review_text[sugg_idx:].strip() if sugg_idx != -1 else review_text
        details = review_text[:sugg_idx].strip() if sugg_idx != -1 else review_text
        return {"score": score, "details": details, "suggestions": suggestions}
    except Exception as e:
        log_error(f"审稿员调用失败: {e}")
        return {"score": 0, "details": "", "suggestions": ""}


_LANG_NAMES = {
    "en": "English", "zh": "中文", "ja": "日本語",
    "ko": "한국어", "es": "Español", "pt": "Português", "ar": "العربية",
    "fr": "Français", "de": "Deutsch", "it": "Italiano",
    "th": "ไทย", "vi": "Tiếng Việt", "id": "Bahasa Indonesia",
    "ms": "Bahasa Melayu", "tr": "Türkçe", "ru": "Русский",
    "pl": "Polski", "nl": "Nederlands", "hi": "हिन्दी",
}


async def _call_copywriter(client, script: str, platform: str, target_lang: str = "en") -> dict:
    lang_name = _LANG_NAMES.get(target_lang, "English")
    if target_lang == "zh":
        system_prompt = (
            f"你是抖音的文案专家。根据以下视频脚本，生成发布时需要的："
            "1. 视频标题（吸引点击，15字以内）"
            "2. 视频描述（包含关键词，50字以内）"
            "3. 话题标签（3-5个，策略：1个热门+2个品类+1个长尾，不要加#fyp）"
            "4. 推荐发布时间（抖音：工作日12:00-13:00午休/18:00-19:00下班/21:00-22:00睡前；周末10:00-11:00或15:00-16:00）\n\n"
            f"平台：{platform}\n"
            "所有内容必须用中文输出。\n\n"
            '请用以下JSON格式输出（只输出JSON，不要其他文字）：\n'
            '{"title": "...", "description": "...", "hashtags": ["#tag1", "#tag2"], "best_time": "..."}'
        )
        user_msg = f"脚本内容：\n\n{script}"
    else:
        system_prompt = (
            f"You are a {platform} copywriting expert. Based on the video script below, generate:\n"
            "1. Video title (compelling, under 60 characters)\n"
            "2. Video description (keyword-rich, under 150 characters)\n"
            "3. Hashtags (3-5 tags ONLY: 1 trending + 2 niche category + 1 long-tail. ALWAYS include #TikTokMadeMeBuyIt. Do NOT use #fyp — it does not guarantee reach)\n"
            "4. Best posting time (TikTok: Sunday 20:00 / Tuesday 16:00 / Wednesday 17:00, post 3-5x/week)\n\n"
            f"Platform: {platform}\n"
            f"IMPORTANT: ALL content (title, description, hashtags) MUST be written in {lang_name}. "
            f"This targets the {lang_name}-speaking market.\n\n"
            'Output ONLY valid JSON, no other text:\n'
            '{"title": "...", "description": "...", "hashtags": ["#tag1", "#tag2"], "best_time": "..."}'
        )
        user_msg = f"Script:\n\n{script}"
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=500,
        )
        copy_text = resp.choices[0].message.content or ""
        json_match = _re_chat.search(r"\{[\s\S]+\}", copy_text)
        if json_match:
            data = _json_chat.loads(json_match.group())
            return {
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "hashtags": data.get("hashtags", []),
                "best_time": data.get("best_time", ""),
            }
    except Exception as e:
        log_error(f"文案师调用失败: {e}")
    return {"title": "", "description": "", "hashtags": [], "best_time": ""}


class ChatRequest(BaseModel):
    messages: List[dict] = Field(..., description="聊天历史，每条有role和content")
    product_image_urls: List[str] = Field(default=[], description="已上传产品图URL")
    market: str = Field("欧美")
    duration: int = Field(10)
    target_lang: str = Field("en", description="目标语言代码 en/zh/ja/ko/es/pt/ar")
    platform: str = Field("tiktok", description="tiktok或douyin")
    video_url: Optional[str] = Field(None, description="入口A的参考视频URL")


@router.post("/chat")
async def chat_with_mentor(
    body: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    """AI脚本导师对话（每次扣 18 积分）"""
    from app.config import get_settings
    from openai import AsyncOpenAI

    user_id = str(current_user["id"])

    CHAT_COST = 39
    if not deduct_credits(user_id, CHAT_COST):
        raise HTTPException(402, f"积分不足，每次对话消耗 {CHAT_COST} 积分")

    s = get_settings()
    client = AsyncOpenAI(base_url=s.LINGMENG_BASE_URL, api_key=s.LINGMENG_API_KEY)

    # 构建 system prompt
    _tl = body.target_lang or "en"
    sys_prompt = _CHAT_SYSTEM_PROMPT.format(
        market=body.market,
        duration=body.duration,
        n_images=len(body.product_image_urls),
        target_lang=_LANG_NAMES.get(_tl, "English"),
        platform="TikTok" if body.platform == "tiktok" else "抖音",
    )
    if body.video_url:
        sys_prompt += _CHAT_VIDEO_INSTRUCTION.format(video_url=body.video_url)

    # ── 注入每日趋势数据（trend_cache，2天内） ────────────────────
    try:
        from app.database import get_db
        _plat_filter = body.platform  # "tiktok" 或 "douyin"
        with get_db() as _tc_conn:
            _trends = _tc_conn.execute(
                "SELECT platform, category, content FROM trend_cache "
                "WHERE platform=? AND created_at > datetime('now', '-2 days') "
                "ORDER BY created_at DESC LIMIT 4",
                (_plat_filter,),
            ).fetchall()
        if _trends:
            _trend_lines = "\n".join(
                f"[{t[0]}/{t[1]}趋势] {t[2][:200]}" for t in _trends
            )
            sys_prompt += f"\n\n[最新平台趋势数据（每日自动更新）]\n{_trend_lines}"
            log_info(f"trend_cache 注入 {len(_trends)} 条 platform={_plat_filter}")
    except Exception as _te:
        log_error(f"trend_cache 注入失败（跳过）: {_te}")

    # ── 小李·趋势研究员 ──────────────────────────────────────────
    search_result = None
    _, category = _detect_platform_and_category(body.messages)
    _mkt_map = {"欧美": "TikTok", "日韩": "TikTok", "东南亚": "TikTok", "中国": "抖音", "中国大陆": "抖音"}
    detected_platform = _mkt_map.get(body.market, "TikTok")
    _xiaoli_category = category or "fashion product"
    # 第2轮对话起触发小李搜索（不依赖关键词检测）
    _lang_name = _LANG_NAMES.get(_tl, "English")
    if len(body.messages) >= 2 and not _xiaoli_already_searched(body.messages):
        raw = await _call_xiaoli_search(client, detected_platform, _xiaoli_category, _lang_name)
        if raw:
            search_result = raw
            sys_prompt += (
                f"\n\n[小李趋势研究员实时搜索 — 平台:{detected_platform} 品类:{_xiaoli_category} 语言:{_lang_name}]\n"
                f"{raw}\n"
                "[请将以上最新趋势融入你的建议和脚本中]"
            )
            log_info(f"小李搜索完成 user={user_id} platform={detected_platform} category={_xiaoli_category} lang={_lang_name}")

    # ── 构建多轮对话消息 ──────────────────────────────────────────
    openai_messages = [{"role": "system", "content": sys_prompt}]

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
            first_user = next((m for m in body.messages if m.get("role") == "user"), None)
            if first_user:
                first_user_content = first_user.get("content", "")
                img_content.append({"type": "text", "text": first_user_content or "（产品图）"})
                openai_messages.append({"role": "user", "content": img_content})
                past_first = False
                for msg in body.messages:
                    if not past_first and msg.get("role") == "user" and msg.get("content") == first_user_content:
                        past_first = True
                        continue
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
                for msg in body.messages:
                    openai_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
    else:
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

    # ── 林久主调用 ────────────────────────────────────────────────
    try:
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

    # 提取脚本
    def _extract_script(text: str) -> str:
        m = _re_chat.search(r"===SCRIPT_START===\s*([\s\S]+?)\s*===SCRIPT_END===", text)
        return m.group(1).strip() if m else ""

    script = _extract_script(reply_text)

    # ── 脚本语言验证：台词含中文但 target_lang 非 zh → 强制重写 ──────────
    if script and _tl != "zh":
        import re as _re_lang
        _dialogue_lines = _re_lang.findall(r'模特说[：:]\s*(.+)', script)
        _has_chinese = any(_re_lang.search(r'[一-鿿]', ln) for ln in _dialogue_lines)
        if _has_chinese:
            log_info(f"脚本语言检查：target_lang={_tl} 但台词含中文，触发重写")
            _retry_messages = openai_messages + [
                {"role": "assistant", "content": reply_text},
                {"role": "user", "content": (
                    f"错误！台词语言不对！用户选择的目标语言是{_LANG_NAMES.get(_tl, 'English')}，"
                    f"但你写的台词里有中文。请立刻用{_LANG_NAMES.get(_tl, 'English')}重写整个脚本，"
                    f"所有台词必须是{_LANG_NAMES.get(_tl, 'English')}，禁止出现任何中文！"
                    "输出完整的===SCRIPT_START===格式。"
                )},
            ]
            try:
                _retry_resp = await client.chat.completions.create(
                    model=s.LINGMENG_MODEL,
                    messages=_retry_messages,
                    max_tokens=2000,
                )
                _retry_text = _retry_resp.choices[0].message.content or ""
                _retry_script = _extract_script(_retry_text)
                if _retry_script:
                    script = _retry_script
                    reply_text = _retry_text
                    log_info(f"脚本语言重写成功 user={user_id}")
                else:
                    log_error(f"脚本语言重写未返回合法脚本 user={user_id}")
            except Exception as _re:
                log_error(f"脚本语言重写调用失败 user={user_id}: {_re}")

    # ── 场景数量强制检查：超限就让林久重写 ──────────────────────────
    if script:
        import re as _re_scene
        _scene_labels = list(set(_re_scene.findall(r'场景[：:]\s*([^|，,\n]+)', script)))
        _max_scenes = {5: 1, 10: 1, 15: 2, 30: 3, 60: 4}.get(body.duration, 2)
        if len(_scene_labels) > _max_scenes:
            log_info(
                f"脚本场景超限 user={user_id}: {len(_scene_labels)}个场景 > 上限{_max_scenes}个, 触发重写"
            )
            _fix_msgs = openai_messages + [
                {"role": "assistant", "content": reply_text},
                {"role": "user", "content": (
                    f"错误！你写了{len(_scene_labels)}个场景（{', '.join(_scene_labels)}），"
                    f"但{body.duration}秒视频只能有{_max_scenes}个场景！"
                    f"立刻重写，所有镜头都在同一个场景里，输出完整===SCRIPT_START===格式。"
                )},
            ]
            try:
                _fix_resp = await client.chat.completions.create(
                    model="gpt-4o", messages=_fix_msgs, max_tokens=2000
                )
                _fix_text = _fix_resp.choices[0].message.content or ""
                _fix_script = _extract_script(_fix_text)
                if _fix_script:
                    script = _fix_script
                    reply_text = _fix_text
                    log_info(f"脚本场景数量重写成功 user={user_id}")
                else:
                    log_error(f"脚本场景数量重写未返回合法脚本 user={user_id}")
            except Exception as _fe:
                log_error(f"脚本场景数量重写调用失败 user={user_id}: {_fe}")

    # ── 审稿员：每次有新脚本就审查，不自动修改，由用户决定 ──────────
    review_data = None
    if script:
        review_data = await _call_reviewer(client, script)
        log_info(f"审稿员评分 user={user_id} score={review_data.get('score', 0)}")

    # 文案师在用户确认脚本后才调用（由前端单独请求 /generate-copy 端点）
    copy_data = None

    # 提取结构化问题
    questions = []
    q_match = _re_chat.search(r"===QUESTIONS_START===\s*([\s\S]+?)\s*===QUESTIONS_END===", reply_text)
    if q_match:
        try:
            questions = _json_chat.loads(q_match.group(1).strip())
        except Exception:
            pass

    need_contrast = "===NEED_CONTRAST_IMAGE===" in reply_text

    # 清理回复文字
    clean_reply = reply_text
    clean_reply = _re_chat.sub(r"===SCRIPT_START===[\s\S]*?===SCRIPT_END===", "[脚本已生成，见下方]", clean_reply)
    clean_reply = _re_chat.sub(r"===QUESTIONS_START===[\s\S]*?===QUESTIONS_END===", "", clean_reply)
    clean_reply = clean_reply.replace("===NEED_CONTRAST_IMAGE===", "").replace(_XIAOLI_DONE_MARKER, "").strip()

    if script:
        log_info(f"chat script preview user={user_id}: {script[:300].replace(chr(10), '|')}")
    log_info(
        f"chat OK user={user_id} reply_len={len(reply_text)} has_script={bool(script)} "
        f"n_questions={len(questions)} has_review={bool(review_data)} "
        f"has_copy={bool(copy_data)} has_search={bool(search_result)}"
    )

    result: dict = {
        "reply": clean_reply,
        "script": script,
        "need_contrast_image": need_contrast,
        "questions": questions,
    }
    if search_result:
        # 只返回摘要（前600字），完整版已注入 sys_prompt
        result["search_result"] = search_result[:600]
    if review_data and review_data.get("score", 0) > 0:
        result["review"] = {
            "score": review_data["score"],
            "details": review_data["details"],
            "suggestions": review_data["suggestions"],
        }
    if copy_data and copy_data.get("title"):
        result["copy"] = copy_data
    return result
