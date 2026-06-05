"""
视频生成 API
- 链接改造
- 文生视频
- 图生视频工作流
- 额度扣费：使用 @require_credits 装饰器自动处理
"""
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from enum import Enum
import asyncio
from app.services.fal_service import get_video_service
from app.services.cos_upload import upload_to_cos
from app.services.decorators import require_credits
from app.services.content_filter import assert_safe_prompt
from app.services.media_archiver import archive_url
from app.api.auth import get_current_user

router = APIRouter()


class ReplaceMode(str, Enum):
    BOTH = "both"
    CHARACTER = "character"
    BACKGROUND = "background"
    NONE = "none"


class VideoLinkRequest(BaseModel):
    """视频链接改造 - 初始化"""
    video_url: str


class VideoLinkReplaceRequest(BaseModel):
    """视频链接改造 - 替换设置"""
    task_id: str
    replace_mode: ReplaceMode
    product_same: bool
    # 图片 URL 由上传接口返回后传入


class ShotPromptUpdate(BaseModel):
    """镜头提示词更新 (会同步飞书)"""
    task_id: str
    shot_index: int
    user_modified_prompt: str


class TextToVideoRequest(BaseModel):
    """文生视频"""
    prompt: str
    duration_sec: Optional[int] = 5
    width: Optional[int] = 1280
    height: Optional[int] = 720


class ImageToVideoShot(BaseModel):
    """图生视频 - 单个镜头"""
    first_frame_url: str
    last_frame_url: Optional[str] = None
    script: str
    storyboard: str
    duration_sec: float


class ImageToVideoWorkflowRequest(BaseModel):
    """图生视频 - 工作流"""
    shots: List[ImageToVideoShot]
    maintain_character: bool = True


class ImageToVideoRequest(BaseModel):
    """图生视频 (单镜头)"""
    image_url: str
    prompt: Optional[str] = ""
    # 2026-05-13:kling o3 standard 支持 3/5/10/15s。前端用户点的秒数透传到 fal
    duration_sec: Optional[int] = 5


class VideoElementReplaceRequest(BaseModel):
    """视频元素替换请求"""
    video_url: str  # 原视频 URL
    element_image_url: str  # 新元素图片 URL
    instruction: str  # 自然语言指令，如"把视频里的水杯替换成我的产品"
    model: Optional[str] = "fal-ai/kling-video/o3/standard/edit"


class VideoCloneRequest(BaseModel):
    """视频翻拍复刻请求"""
    # model_image_url 字段跟 pydantic v2 的 model_ 受保护命名空间冲突,关保护
    model_config = ConfigDict(protected_namespaces=())
    reference_video_url: str  # 爆款视频链接
    model_image_url: str  # 我的模特图
    product_image_url: Optional[str] = None  # 我的产品图（可选）
    model: Optional[str] = "fal-ai/kling-video/o3/standard/edit"


# ============== 视频剪辑台 API ==============

class VideoParseRequest(BaseModel):
    """视频解析请求"""
    video_url: str


class ShotCard(BaseModel):
    """分镜卡片"""
    index: int
    start_time: float  # 开始时间（秒）
    end_time: float  # 结束时间（秒）
    description: str  # 画面描述
    camera_movement: str  # 运镜方式
    prompt: str  # 生成提示词
    thumbnail_url: Optional[str] = None  # 缩略图


class VideoParseResponse(BaseModel):
    """视频解析结果"""
    task_id: str
    shots: List[ShotCard]
    audio_transcript: Optional[str] = None  # 音频转写文本
    duration: float  # 视频总时长


class ShotUpdateRequest(BaseModel):
    """分镜更新请求"""
    shot_index: int
    description: Optional[str] = None
    prompt: Optional[str] = None


class VideoRegenerateRequest(BaseModel):
    """分镜重新生成请求"""
    shot_index: int
    new_prompt: str
    first_frame_url: Optional[str] = None


class VideoComposeRequest(BaseModel):
    """视频合成请求"""
    shots: List[dict]  # 分镜列表
    audio_url: Optional[str] = None  # 配音 URL


@router.post("/link/init")
async def init_video_link(req: VideoLinkRequest):
    """解析视频链接,提取分镜与提示词"""
    raise HTTPException(
        status_code=503,
        detail="视频链接改造功能正在开发中,预计 4-8 周内上线。本接口不扣积分。",
    )


@router.post("/link/replace")
async def set_replace_config(req: VideoLinkReplaceRequest):
    """设置人物/背景替换与产品信息"""
    raise HTTPException(
        status_code=503,
        detail="视频链接改造功能正在开发中,预计 4-8 周内上线。本接口不扣积分。",
    )


@router.post("/link/prompt")
async def update_shot_prompt(req: ShotPromptUpdate):
    """更新镜头提示词 (同步飞书)"""
    raise HTTPException(
        status_code=503,
        detail="视频链接改造功能正在开发中,预计 4-8 周内上线。本接口不扣积分。",
    )


@router.post("/text-to-video")
async def text_to_video(req: TextToVideoRequest):
    """文生视频"""
    # Kling 需要首帧图片，纯文生视频需要先调用图片生成
    # 当前返回错误，引导用户使用图生视频
    raise HTTPException(
        status_code=501,
        detail="Kling 模型需要首帧图片，请使用 /api/video/image-to-video 接口"
    )


@router.post("/image-to-video")
async def image_to_video(req: ImageToVideoRequest, current_user: dict = Depends(get_current_user)):
    """图生视频 (Kling) — 2026-05-13:按 duration × 50 积分动态扣费(不再 flat 50)。"""
    assert_safe_prompt(req.prompt)
    from app.services import task_ownership
    from app.services.billing import check_user_credits, deduct_credits, add_credits, create_consumption_record
    from app.services.rate_limiter import check_task_quota
    import uuid as _uuid

    user_id = current_user["id"]
    role = current_user.get("role", "user")

    # 2026-05-13 动态定价:50 积分 / 秒(clamp 到 kling 支持的 3/5/10/15s)
    safe_dur = max(3, min(15, int(req.duration_sec or 5)))
    cost = safe_dur * 50

    # 任务级限流
    allowed, reason = check_task_quota(user_id, cost, role=role)
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)

    # 检查 + 扣费
    if not check_user_credits(user_id, cost):
        from app.services.billing import get_user_credits
        raise HTTPException(
            status_code=402,
            detail=f"额度不足，需要 {cost} 积分，当前剩余 {get_user_credits(user_id)} 积分",
        )
    task_id_internal = str(_uuid.uuid4())
    if not deduct_credits(user_id, cost, ref_id=task_id_internal, module="video/image-to-video"):
        raise HTTPException(status_code=500, detail="扣费失败，请重试")

    try:
        service = get_video_service()
        result = await service.generate_from_image(
            req.image_url, req.prompt, duration_sec=safe_dur,
        )

        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])

        task_ownership.register(result.get("task_id", ""), current_user["id"])

        # BUG-2: 归档(若同步返回了 video_url;异步任务在 jobs.py / WS 完成时归档)
        if result.get("video_url"):
            result["video_url"] = await archive_url(result["video_url"], current_user["id"], "video")

        # 消费记录 + 退款挂钩 + 余额回填
        record_id = result.get("task_id") or task_id_internal
        create_consumption_record(
            user_id=user_id, task_id=record_id, module="video/image-to-video",
            cost=cost,
            description=f"图生视频 {safe_dur}s：{(req.prompt or '')[:50]}",
            videos=[result["video_url"]] if result.get("video_url") else None,
        )
        if result.get("task_id"):
            from app.services.refund_tracker import register as register_refund
            register_refund(result["task_id"], user_id, cost)

        from app.services.billing import get_user_credits
        return {
            "success": True,
            **result,
            "cost": cost,
            "duration_sec": safe_dur,
            "new_credits": get_user_credits(user_id),
            "description": f"图生视频 {safe_dur}s：{(req.prompt or '无提示词')[:50]}...",
        }
    except HTTPException:
        add_credits(user_id, cost, reason="task_refund",
                    ref_id=task_id_internal, module="video/image-to-video")
        raise
    except Exception as e:
        add_credits(user_id, cost, reason="task_refund",
                    ref_id=task_id_internal, module="video/image-to-video")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{task_id}")
async def get_task_status(task_id: str, current_user: dict = Depends(get_current_user)):
    """查询视频生成任务状态(前端 video/replace、video/clone 在用)。

    五十四续加鉴权:之前匿名可调,任意人猜 task_id 可拿归档视频 URL(隐私泄漏)。
    现在 require login + task_ownership.verify 校归属:
      - 401:未登录 / token 失效 / 吊销
      - 403:登录了但不是这个 task 的 owner

    失败时同 /api/tasks/status:走 refund_tracker.try_refund 原子退款。
    """
    from app.services import task_ownership
    if not task_ownership.verify(task_id, current_user["id"]):
        raise HTTPException(status_code=403, detail="not your task")

    service = get_video_service()
    result = await service.get_task_status(task_id)

    if result.get("status") == "failed":
        from app.services.refund_tracker import try_refund
        refunded = try_refund(task_id)
        if refunded > 0:
            result["refunded"] = refunded
        task_ownership.unregister(task_id)

    return result


@router.post("/image-to-video-workflow")
async def image_to_video_workflow(req: ImageToVideoWorkflowRequest):
    """图生视频工作流 (人物一致 + 连贯)"""
    # TODO: 多镜头工作流，需要按顺序生成并保持人物一致性
    raise HTTPException(
        status_code=501,
        detail="多镜头工作流尚未实现，当前仅支持单镜头生成"
    )


@router.post("/replace/element")
@require_credits("video/replace/element")
async def replace_video_element(req: VideoElementReplaceRequest, current_user: dict = Depends(get_current_user)):
    """
    视频元素替换
    使用 Kling O1 Edit 模型，根据自然语言指令替换视频中的元素
    """
    assert_safe_prompt(req.instruction)
    # 调用 FAL AI 视频编辑服务
    from app.services import task_ownership

    video_service = get_video_service()
    result = await video_service.replace_element(
        video_url=req.video_url,
        element_image_url=req.element_image_url,
        instruction=req.instruction
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    task_id = result.get("task_id", "unknown")
    task_ownership.register(task_id, current_user["id"])

    if result.get("video_url"):
        result["video_url"] = await archive_url(result["video_url"], current_user["id"], "video")

    return {
        "success": True,
        "task_id": task_id,
        "status": result.get("status", "pending"),
        "video_url": result.get("video_url"),
        "message": "视频元素替换任务已提交，预计需要 2-5 分钟",
        "instruction": req.instruction,
        "model": result.get("model"),
        "description": f"视频元素替换：{req.instruction[:50]}...",
    }


@router.post("/clone")
@require_credits("video/clone")
async def clone_video(req: VideoCloneRequest, current_user: dict = Depends(get_current_user)):
    """
    视频翻拍复刻
    提取参考视频的运镜、节奏、动作，将主体替换为用户的模特和产品
    使用 Kling O1 Edit 模型
    """
    from app.services import task_ownership

    video_service = get_video_service()
    result = await video_service.clone_video(
        reference_video_url=req.reference_video_url,
        model_image_url=req.model_image_url,
        product_image_url=req.product_image_url
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    task_id = result.get("task_id", "unknown")
    task_ownership.register(task_id, current_user["id"])

    if result.get("video_url"):
        result["video_url"] = await archive_url(result["video_url"], current_user["id"], "video")

    return {
        "success": True,
        "task_id": task_id,
        "status": result.get("status", "pending"),
        "video_url": result.get("video_url"),
        "message": "视频翻拍任务已提交，预计需要 3-5 分钟",
        "reference_video": req.reference_video_url,
        "model_image": req.model_image_url,
        "model": result.get("model"),
        "description": f"视频翻拍：{req.reference_video_url[:50]}...",
    }


# ============== 视频剪辑台 API ==============

@router.post("/editor/parse")
async def parse_video(req: VideoParseRequest):
    """视频语义解析(多模态分镜)"""
    raise HTTPException(
        status_code=503,
        detail="视频剪辑台(分镜解析)功能正在开发中,预计 6-10 周内上线。本接口不扣积分。",
    )


@router.post("/editor/shot/{shot_index}/update")
async def update_shot(req: ShotUpdateRequest, shot_index: int):
    """更新分镜卡片"""
    raise HTTPException(
        status_code=503,
        detail="视频剪辑台(分镜更新)功能正在开发中,预计 6-10 周内上线。本接口不扣积分。",
    )


@router.post("/editor/shot/{shot_index}/regenerate")
async def regenerate_shot(req: VideoRegenerateRequest, shot_index: int):
    """按新文本重新生成该段视频"""
    raise HTTPException(
        status_code=503,
        detail="视频剪辑台(分镜重生成)功能正在开发中,预计 6-10 周内上线。本接口不扣积分。",
    )


@router.post("/editor/compose")
async def compose_video(req: VideoComposeRequest):
    """视频合成 - 将分镜片段拼接成完整视频"""
    raise HTTPException(
        status_code=503,
        detail="视频剪辑台(合成)功能正在开发中,预计 6-10 周内上线。本接口不扣积分。",
    )


@router.post("/editor/translate")
async def translate_script(req: dict):
    """脚本翻译"""
    raise HTTPException(
        status_code=503,
        detail="脚本翻译功能正在开发中,预计 4-6 周内上线。本接口不扣积分。",
    )


@router.post("/upload/video")
async def upload_video(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    import fal_client, tempfile, os
    from app.services.upload_guard import read_bounded, SHORT_VIDEO_MIMES
    # 短视频限 100MB(图生视频 / 编辑用,> 100MB 走 studio 分片)
    contents = await read_bounded(file, max_bytes=100 * 1024 * 1024, allowed_mimes=SHORT_VIDEO_MIMES, label="视频")
    suffix = os.path.splitext(file.filename)[1] or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    try:
        url = await asyncio.to_thread(upload_to_cos, tmp_path)
        return {"url": url}
    finally:
        os.unlink(tmp_path)
