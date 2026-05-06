"""
视频复刻工作台 API
================

流程:
  1. /upload/video      上传参考视频 → fal storage URL
  2. /upload/image      上传产品图 / 模特图 → fal storage URL
  3. /analyze           qwen-vl 看视频 → 出 N 段分镜(time/shot/action/visual_prompt)
  4. /generate          产品图 + 模特图 + 分镜 + aspect_ratio → 推 JOBS 队列(type=replicate)
  5. /api/jobs/list     轮询状态(用现成 JobPanel)

设计:
  - 复用 ad_video_models.compose_first_frame_for_scene(已支持 aspect_ratio)出每段首帧
  - 视频段 aliyun-wan2.7-r2v(免费)出 5/10s 视频
  - ffmpeg concat 拼最终
  - history 写 module=video/replicate
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.services.billing import get_task_cost, deduct_credits
from app.services.fal_service import fal_upload_with_retry, get_aliyun_qwenvl_service
from app.services.upload_guard import read_bounded, IMAGE_MIMES
from app.services.logger import log_info, log_error
from app.services.content_filter import assert_safe_prompt

router = APIRouter()

VIDEO_MIMES = ("video/mp4", "video/quicktime", "video/webm", "video/x-msvideo")
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100 MB


# ================= 数据模型 =================

class Scene(BaseModel):
    id: int
    time_range: str          # "0-5s"
    duration_sec: float
    shot: str                # 景别 close-up / medium / wide
    action: str              # 动作描述
    framing: str             # 构图
    visual_prompt: str       # 给 GPT-Image 2 的英文 prompt


class AnalyzeResponse(BaseModel):
    scenes: List[Scene]
    total_duration: float
    detected_aspect_ratio: str  # auto-detect 参考视频比例


class ReplicateScript(BaseModel):
    scenes: List[Scene]
    overall_setting: str = ""
    model_description: str = ""


class GenerateRequest(BaseModel):
    product_image_url: str
    model_image_url: Optional[str] = None
    reference_video_url: str       # 复刻参考(传给 wan2.7 当 reference_video)
    script: ReplicateScript
    aspect_ratio: str = "9:16"     # 9:16 / 16:9 / 1:1
    engine: str = "aliyun-wan2.7-r2v"  # 目前先支持这一个


# ================= 端点 1:上传视频 =================

@router.post("/upload/video")
async def upload_video(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    contents = await read_bounded(file, MAX_VIDEO_SIZE, VIDEO_MIMES, "参考视频")
    import tempfile, os
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


# ================= 端点 2:上传图片(产品 / 模特) =================

@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    contents = await read_bounded(file, 10 * 1024 * 1024, IMAGE_MIMES, "图片")
    import tempfile, os
    from PIL import Image
    import io
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
            os.unlink(tmp_path)
    except Exception as e:
        raise HTTPException(500, f"图片处理失败: {str(e)[:200]}")
    return {"image_url": url}


# ================= 端点 3:分析参考视频 → N 段分镜 =================

_ANALYZE_INSTRUCTION = """你是视频复刻专家。看完这段视频,按时序拆成 N 个分镜(每段 5 秒为基准,余数并入末段)。
对每段输出 JSON,严格按下面格式,不加任何 markdown:

{
  "total_duration_seconds": 总秒数,
  "scenes": [
    {
      "id": 1,
      "time_range": "0-5s",
      "duration_sec": 5.0,
      "shot": "close-up | medium-shot | wide-shot | medium close-up",
      "action": "本段主要动作的中文描述(15 字内)",
      "framing": "构图描述(中心 / 左 / 右 / 仰拍 / 俯拍 等,10 字内)",
      "visual_prompt": "完整英文 prompt,描述本段画面 — 镜头语言 + 动作 + 灯光 + 构图,不写人物身体特征/年龄/外貌。会传给 GPT-Image 2 出首帧。"
    }
  ]
}

要求:
1. 每段 visual_prompt 必须英文,150 字内,描述本段独特的镜头/动作/构图。
2. 严禁敏感词:不写身体部位(腰/胸/腿/臀)、年龄、性别外貌。只写场景/动作/构图/光线。
3. 时序连贯:第 1 段要"开场"语感,中间段"展示",最后段"结束/CTA"。
"""


def _detect_video_aspect(video_url: str) -> str:
    """ffprobe 探视频比例 — 失败默认 9:16"""
    try:
        import subprocess, json as _j
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", video_url],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return "9:16"
        d = _j.loads(r.stdout)
        s = d.get("streams", [{}])[0]
        w, h = s.get("width"), s.get("height")
        if not w or not h:
            return "9:16"
        ratio = w / h
        if abs(ratio - 9/16) < 0.1: return "9:16"
        if abs(ratio - 16/9) < 0.1: return "16:9"
        if abs(ratio - 1.0) < 0.1: return "1:1"
        if abs(ratio - 4/3) < 0.1: return "4:3"
        if abs(ratio - 3/4) < 0.1: return "3:4"
        return "9:16"
    except Exception:
        return "9:16"


@router.post("/analyze")
async def analyze(
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    """qwen-vl 看视频出 N 段分镜。1 积分。"""
    video_url = body.get("video_url")
    if not video_url:
        raise HTTPException(400, "video_url 必填")
    user_id = str(current_user["id"])
    cost = 1
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")
    try:
        svc = get_aliyun_qwenvl_service()
        if not svc or not svc.is_available():
            raise HTTPException(503, "qwen-vl 视频理解服务不可用(DASHSCOPE_API_KEY 未配置)")
        res = await svc.analyze_video(video_url, _ANALYZE_INSTRUCTION)
        if "error" in res:
            raise HTTPException(502, f"qwen-vl 失败: {res.get('error','?')[:200]}")
        text = (res.get("text") or "").strip()
        # 清掉可能的 markdown
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except Exception as e:
            log_error(f"replicate/analyze JSON parse fail: {e} text[:200]={text[:200]}")
            raise HTTPException(502, "qwen-vl 输出解析失败,请重试")
        scenes_raw = data.get("scenes") or []
        if not scenes_raw:
            raise HTTPException(502, "qwen-vl 未返回分镜")
        # 过滤每段 visual_prompt 防敏感
        clean_scenes = []
        for sc in scenes_raw:
            try:
                assert_safe_prompt(sc.get("visual_prompt", ""))
            except HTTPException:
                # 替换为通用安全 prompt,不阻塞整体
                sc["visual_prompt"] = "Cinematic product showcase, soft natural lighting, professional commercial style"
            clean_scenes.append(sc)
        aspect = _detect_video_aspect(video_url)
        log_info(f"replicate/analyze ok user={user_id} scenes={len(clean_scenes)} ratio={aspect}")
        return {
            "scenes": clean_scenes,
            "total_duration": data.get("total_duration_seconds", sum(s.get("duration_sec", 5) for s in clean_scenes)),
            "detected_aspect_ratio": aspect,
        }
    except HTTPException:
        # 退款
        from app.services.billing import add_credits
        add_credits(user_id, cost, reason="task_refund")
        raise
    except Exception as e:
        from app.services.billing import add_credits
        add_credits(user_id, cost, reason="task_refund")
        log_error(f"replicate/analyze 异常: {e}")
        raise HTTPException(500, str(e)[:200])


# ================= 端点 4:生成视频 → 推 JOBS =================

@router.post("/generate")
async def generate(
    req: GenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """生成视频:推 JOBS 队列异步跑。按总时长积分定价。"""
    # 内容过滤
    for sc in req.script.scenes:
        assert_safe_prompt(sc.visual_prompt)

    if not req.script.scenes:
        raise HTTPException(400, "至少 1 段")
    if req.engine != "aliyun-wan2.7-r2v":
        raise HTTPException(400, f"engine 暂只支持 aliyun-wan2.7-r2v(收到 {req.engine})")

    user_id = current_user.get("id") or current_user.get("email", "unknown")
    user_id_str = str(user_id)

    total_duration = sum(s.duration_sec for s in req.script.scenes)
    # 定价:¥0.5/秒 ≈ 1 积分/秒,留毛利 → 1.5 积分/秒
    cost = max(5, int(round(total_duration * 1.5)))
    module = "video/replicate"
    if not deduct_credits(user_id, cost):
        raise HTTPException(402, f"积分不足,需 {cost}")

    job_id = str(uuid.uuid4())[:8]
    from app.api.jobs import JOBS, _save_jobs, _execute_job

    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id_str,
        "user_numeric_id": user_id,
        "type": "replicate",
        "title": f"视频复刻 ({total_duration:.0f}s,{len(req.script.scenes)} 段)",
        "params": {
            "product_image_url": req.product_image_url,
            "model_image_url": req.model_image_url,
            "reference_video_url": req.reference_video_url,
            "scenes": [s.model_dump() for s in req.script.scenes],
            "overall_setting": req.script.overall_setting,
            "model_description": req.script.model_description,
            "aspect_ratio": req.aspect_ratio,
            "engine": req.engine,
        },
        "module": module,
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    asyncio.create_task(_execute_job(job_id))

    return {"job_id": job_id, "status": "pending", "cost": cost}
