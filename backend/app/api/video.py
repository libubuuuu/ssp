"""
视频生成 API
- 链接改造
- 文生视频
- 图生视频工作流
- 额度扣费：使用 @require_credits 装饰器自动处理
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
from app.services.fal_service import get_video_service
from app.services.decorators import require_credits
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
    tail_image_url: Optional[str] = None
    prompt: Optional[str] = ""


class VideoElementReplaceRequest(BaseModel):
    """复刻 - 视频元素替换"""
    video_url: str  # 原视频 URL
    element_image_url: str  # 产品/人物图片 URL
    instruction: str  # 替换指令
    product_image_url: Optional[str] = None  # 第二张参考图（可选）
    model: Optional[str] = "fal-ai/kling-video/o1/video-to-video/edit"


class VideoCloneRequest(BaseModel):
    """最强复刻 - 保留运镜节奏生成新视频"""
    reference_video_url: str  # 参考爆款视频
    model_image_url: str  # 我的模特图
    product_image_url: Optional[str] = None  # 我的产品图（可选）
    instruction: Optional[str] = "保持相同的运镜、节奏和动作，将人物替换为@Element1"
    model: Optional[str] = "fal-ai/kling-video/o1/video-to-video/reference"


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
    """解析视频链接，提取分镜与提示词"""
    # TODO: 视频下载和分镜提取
    return {"task_id": "placeholder", "shots": [], "message": "视频解析中"}


@router.post("/link/replace")
async def set_replace_config(req: VideoLinkReplaceRequest):
    """设置人物/背景替换与产品信息"""
    return {"message": "已保存"}


@router.post("/link/prompt")
async def update_shot_prompt(req: ShotPromptUpdate):
    """更新镜头提示词 (同步飞书)"""
    return {"message": "已更新并同步飞书"}


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
@require_credits("video/image-to-video")
async def image_to_video(req: ImageToVideoRequest, current_user: dict = Depends(get_current_user)):
    """图生视频 (Kling)"""
    service = get_video_service()

    result = await service.generate_from_image(req.image_url, req.prompt, req.tail_image_url)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return {
        "success": True,
        **result,
        "description": f"图生视频：{req.prompt[:50] if req.prompt else '无提示词'}...",
    }


@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """查询视频生成任务状态"""
    service = get_video_service()

    result = await service.get_task_status(task_id)
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
    # 调用 FAL AI 视频编辑服务
    video_service = get_video_service()
    result = await video_service.replace_element(
        video_url=req.video_url,
        element_image_url=req.element_image_url,
        instruction=req.instruction,
        product_image_url=getattr(req, 'product_image_url', None)
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    task_id = result.get("task_id", "unknown")

    return {
        "success": True,
        "task_id": task_id,
        "status": result.get("status", "pending"),
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
    video_service = get_video_service()
    result = await video_service.clone_video(
        reference_video_url=req.reference_video_url,
        model_image_url=req.model_image_url,
        product_image_url=req.product_image_url
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    task_id = result.get("task_id", "unknown")

    return {
        "success": True,
        "task_id": task_id,
        "status": result.get("status", "pending"),
        "message": "视频翻拍任务已提交，预计需要 3-5 分钟",
        "reference_video": req.reference_video_url,
        "model_image": req.model_image_url,
        "model": result.get("model"),
        "description": f"视频翻拍：{req.reference_video_url[:50]}...",
    }


# ============== 视频剪辑台 API ==============

@router.post("/editor/parse")
async def parse_video(req: VideoParseRequest):
    """
    视频语义解析
    使用多模态大模型对视频抽帧 + 音频转文字，逆向推导出分镜时间轴
    """
    # TODO: 实现视频解析
    # 1. 使用 LLaVA 抽帧分析 -> 分镜描述
    # 2. 使用 Whisper -> 音频转文字
    # 3. 输出分镜时间轴 JSON
    return {
        "task_id": "parse_" + str(hash(req.video_url)),
        "shots": [
            {
                "index": 0,
                "start_time": 0.0,
                "end_time": 3.5,
                "description": "一位年轻女性站在咖啡店柜台前，微笑着看向镜头",
                "camera_movement": "固定镜头，轻微推近",
                "prompt": "young woman standing in front of coffee shop counter, smiling at camera",
                "thumbnail_url": None,
            },
            {
                "index": 1,
                "start_time": 3.5,
                "end_time": 7.0,
                "description": "女性拿起咖啡杯，轻轻闻了一下香气",
                "camera_movement": "特写镜头，聚焦手部动作",
                "prompt": "woman picks up coffee cup, smells the aroma",
                "thumbnail_url": None,
            },
        ],
        "audio_transcript": "欢迎来到咖啡店，今天我们要品尝一杯特别的咖啡...",
        "duration": 7.0,
    }


@router.post("/editor/shot/{shot_index}/update")
async def update_shot(req: ShotUpdateRequest, shot_index: int):
    """更新分镜卡片"""
    # TODO: 实现分镜更新
    return {"message": "分镜已更新", "shot_index": shot_index}


@router.post("/editor/shot/{shot_index}/regenerate")
async def regenerate_shot(req: VideoRegenerateRequest, shot_index: int):
    """按新文本重新生成该段视频"""
    # TODO: 实现分镜重新生成
    return {
        "task_id": f"regen_shot_{shot_index}",
        "status": "pending",
        "message": "分镜重新生成任务已提交",
    }


@router.post("/editor/compose")
async def compose_video(req: VideoComposeRequest):
    """视频合成 - 将分镜片段拼接成完整视频"""
    # TODO: 实现视频合成
    return {
        "task_id": "compose_" + str(hash(str(req.shots))),
        "status": "pending",
        "message": "视频合成任务已提交",
    }


@router.post("/editor/translate")
async def translate_script(req: dict):
    """
    脚本翻译
    支持 75 种语言，中英必选
    """
    text = req.get("text", "")
    target_lang = req.get("target_lang", "en")

    # TODO: 调用翻译 API
    translations = {
        "en": "Welcome to our coffee shop, today we're going to taste a special coffee...",
        "zh": "欢迎来到咖啡店，今天我们要品尝一杯特别的咖啡...",
        "ja": "コーヒーショップへようこそ。今日は特別なコーヒーを味わいます...",
        "ko": "커피 샵에 오신 것을 환영합니다. 오늘 우리는 특별한 커피를 맛볼 것입니다...",
    }

    return {
        "original": text,
        "translated": translations.get(target_lang, text),
        "target_lang": target_lang,
    }
