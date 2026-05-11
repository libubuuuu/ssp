"""P221 视频复刻 V2 — A2 端点实装(type=single 路径)。

详见 docs/P221-API-SCHEMA.md(v4)§3。

阶段 A2 状态:
- ✅ /upload/video           真实(复用 v1 read_bounded + fal_upload_with_retry)
- ✅ /upload/image           真实(加 role 字段)
- ✅ /preview-segments       真实(single 模式简版 — 不抽缩略图,B 阶段补)
- ✅ /estimate               真实(single 模式)
- ✅ /create                 真实(single + ultimate body 校验,但 ultimate 走的是异步 worker 503)
- ✅ /jobs/{job_id}          真实
- ✅ /jobs/{job_id}/cancel   503(B 阶段)
- ✅ /jobs                   真实
- ✅ /prompt-templates       真实(A1 已实)
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import tempfile
import time
import uuid
from typing import List, Optional, Literal

import fal_client
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.services.billing import check_user_credits, deduct_credits
from app.services.content_filter import check_prompt
from app.services.fal_service import fal_upload_with_retry
from app.services.logger import log_info, log_error
from app.services.upload_guard import read_bounded
from app.services.video_clone_v2_pricing import (
    PROMPT_TEMPLATES,
    IMAGE_ROLES,
    REPLACEMENT_MODES,
    SEGMENT_CREDITS,
    SEGMENT_DISPLAY_RMB,
    SEGMENT_INPUT_SECONDS_MAX,
    MAX_ULTIMATE_SECONDS,
    MAX_ULTIMATE_SEGMENTS,
    build_prompt,
    calc_credits,
    sha256_file,
)
from app.services.video_clone_v2_split import (
    plan_segments_v2,
    check_duration as _check_duration,
    suggest_trim_candidates,
    detect_scene_count,
)
from app.services.video_clone_v2_processor import process_v2_job
from app.services.video_clone_v2_cache import (
    store as cache_store,
    try_get as cache_try_get,
    clean_old as cache_clean_old,
)


router = APIRouter()


def _guard_enabled() -> None:
    if not get_settings().ENABLE_VIDEO_CLONE_V2:
        raise HTTPException(503, "视频复刻 V2 灰度未开放")


# ── SSRF 守卫:video_url 域名白名单(商用 SaaS 必须)─────────────────────────
# fal.media 是 fal.ai 文件存储 CDN(实际生产 host 形如 v3.fal.media)
# ailixiao.com / cdn.ailixiao.com 是我们自家 CDN(后续接 OSS 用)
_ALLOWED_VIDEO_HOSTS = frozenset({
    "fal.media",
    "ailixiao.com",
    "cdn.ailixiao.com",
})
_ALLOWED_VIDEO_HOST_SUFFIXES = (
    ".fal.media",
    ".fal.ai",
    ".ailixiao.com",
)


def validate_video_url(url: str, *, field_name: str = "video_url") -> str:
    """video_url SSRF 守卫:协议必须 https,host 必须命中白名单 / 不允许 IP 直连。

    防御场景:
    - http://localhost:8001/admin           (内网探测)
    - http://169.254.169.254/               (云元数据攻击)
    - file:///etc/passwd                    (本地文件读取)
    - http://evil.com/payload               (任意外联)

    Returns:
        url 原样返回(校验通过)

    Raises:
        HTTPException(400) 协议非 https / host 缺失 / IP 直连 / host 不在白名单
    """
    from urllib.parse import urlparse
    import ipaddress

    if not isinstance(url, str) or not url:
        raise HTTPException(400, f"{field_name} 不能为空")

    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(400, f"{field_name} 解析失败:{url[:100]}")

    if parsed.scheme != "https":
        raise HTTPException(
            400, f"{field_name} 必须 https://(实际:{parsed.scheme or '<empty>'})"
        )

    host = (parsed.hostname or "").lower()
    if not host:
        raise HTTPException(400, f"{field_name} 缺少 host")

    # 拒绝 IP 直连(防 DNS 绕过 + 云元数据 169.254.169.254)
    try:
        ipaddress.ip_address(host)
        raise HTTPException(400, f"{field_name} 不允许 IP 直连:{host}")
    except ValueError:
        pass  # 不是 IP,继续 allowlist 校验

    if host in _ALLOWED_VIDEO_HOSTS:
        return url
    for suffix in _ALLOWED_VIDEO_HOST_SUFFIXES:
        if host.endswith(suffix):
            return url

    raise HTTPException(400, f"{field_name} host 不在白名单:{host}")


VIDEO_MIMES = (
    "video/mp4", "video/quicktime", "video/webm",
    "video/x-msvideo", "video/x-m4v", "image/gif",
)
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp", "image/avif")
MAX_VIDEO_SIZE = 50 * 1024 * 1024
MAX_IMAGE_SIZE = 10 * 1024 * 1024


# ---------------------------------------------------------------- Pydantic ----

# ⚠️ Request 类 BaseModel 必须加 model_config = {"extra": "forbid"}
# Response 类保持默认 extra='allow' 以保留 schema 演化兼容性
# 设计原则:对内严(Request),对外松(Response)

# ✋ Response 子模型不加 forbid(纯输出,后端自己构造,无 round-trip)
class SegmentChoice(BaseModel):
    idx: int = Field(..., ge=0)
    start: float = Field(..., ge=0)
    duration: float = Field(..., gt=0)
    thumbnail_url: Optional[str] = None


class SegmentPlanItem(BaseModel):
    model_config = {"extra": "forbid"}
    idx: int = Field(..., ge=0)
    source_type: Literal["ai", "original"]


class ImageRef(BaseModel):
    model_config = {"extra": "forbid"}
    url: str
    role: Literal["product", "person", "scene", "reference"]


class EstimateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    type: Literal["single", "ultimate"]
    replacement_mode: Literal["partial", "full"]
    segments: List[SegmentPlanItem]


# ✋ Response 类不加 forbid
class EstimateResponse(BaseModel):
    type: str
    replacement_mode: str
    ai_segments_count: int
    original_segments_count: int
    total_segments: int
    total_credits: int
    total_rmb_display: str
    estimated_minutes: int


class CreateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    type: Literal["single", "ultimate"]
    replacement_mode: Literal["partial", "full"]
    segments: List[SegmentPlanItem]
    video_url: str
    video_duration_sec: float = Field(..., gt=0)
    video_sha256: str = Field("", description="upload/video 返回的文件 SHA256(红线 3,法务举证)")
    # 2026-05-11:产品/人物/场景 各 0-3 张,总上限 9 张(对齐 fal seedance r2v image_urls 上限)
    image_urls: List[ImageRef] = Field(default_factory=list, max_length=9)
    prompt: str = Field(..., min_length=1, max_length=2000)
    disclaimer_acknowledged: bool
    # B 阶段:check-duration 弹窗用户选完丢段位置后传回(可选,默认不裁剪整片用)
    # B+ 阶段:trim_drop_ranges 多段丢弃 [[s,e],[s,e],...],总和 = drop_seconds
    # 老字段 trim_start/end 仅作单段 drop 兼容(自动转成 1-elem ranges)
    trim_start: Optional[float] = Field(None, ge=0, description="单段丢弃起点(秒,legacy);多段请用 trim_drop_ranges")
    trim_end: Optional[float] = Field(None, gt=0, description="单段丢弃终点(秒,legacy);多段请用 trim_drop_ranges")
    trimmed_seconds: Optional[float] = Field(None, ge=0, description="丢弃总秒数(法务举证用)")
    trim_drop_ranges: Optional[List[List[float]]] = Field(
        None,
        description="多段丢弃区间 [[start,end],...],各段不重叠,总和 = check_duration 返的 drop_seconds"
    )


# ✋ Response 类不加 forbid
class CreateResponse(BaseModel):
    job_id: str
    status: str
    type: str
    replacement_mode: str
    ai_segments_count: int
    original_segments_count: int
    total_credits_charged: int
    estimated_completion_minutes: int


class PreviewSegmentsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    video_url: str
    video_duration_sec: float = Field(..., gt=0)


# ✋ Response 类不加 forbid
class PreviewSegmentsResponse(BaseModel):
    type: Literal["single", "ultimate"]
    segments: List[SegmentChoice]
    preview_token: str
    # 2026-05-11 多镜头分支:scene_count = 视频镜头数
    # ≥4 镜头 → 前端弹窗"建议剪辑成单镜头"(F 路线);<4 镜头 → 后端 brute force(H 路线)
    scene_count: int = 1


class CheckDurationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    video_duration_sec: float = Field(..., gt=0)
    # Path B(本地缓存优先):前端把 upload/video 返的 sha256 一起带回来,后端先查
    # /tmp/v2_cache/{sha256}.mp4 直读本地(<1s);miss → fallback 走 fal CDN URL(6-9s)
    video_sha256: Optional[str] = Field(None, description="upload/video 返的 SHA256;命中本地缓存可省跨境读")
    video_url: Optional[str] = Field(None, description="upload/video 返的 fal storage URL,缓存 miss 时 fallback")


# ✋ Response 子模型不加 forbid(TrimCandidate 是 CheckDurationResponse.suggestions 的元素)
class TrimCandidate(BaseModel):
    label: str
    position: Literal["head", "middle", "tail"]
    start: float
    end: float
    motion_score: float = -1.0
    recommended: bool = False


# ✋ Response 类不加 forbid
class CheckDurationResponse(BaseModel):
    needs_trim: bool
    current_duration: float
    target_duration: float
    drop_seconds: float = 0.0
    suggestions: List[TrimCandidate] = Field(default_factory=list)


# ---------------------------------------------------------------- 校验工具 ----

def _validate_segments(
    type_: str, segments: List[SegmentPlanItem], plan_back: list
) -> None:
    """create / estimate 共用校验:对照后端重算的 plan,前端 segments 必须长度匹配。

    2026-05-10 砍单档后:
    - 删 tier 校验(单档无需选)
    - 仍校验:segments 数量匹配 / single 必 1 段 / ultimate 段数 1-MAX / 至少 1 段 ai
    """
    if len(segments) != len(plan_back):
        raise HTTPException(
            400, f"segments 数量({len(segments)})跟切片不匹配({len(plan_back)})"
        )
    if type_ == "single" and len(segments) != 1:
        raise HTTPException(400, "single 模式 segments 必须 1 段")
    if type_ == "ultimate" and not (1 <= len(segments) <= MAX_ULTIMATE_SEGMENTS):
        raise HTTPException(400, f"ultimate 模式段数必须 1-{MAX_ULTIMATE_SEGMENTS}")

    if not any(seg.source_type == "ai" for seg in segments):
        raise HTTPException(400, "至少要有 1 段 source_type=ai(全 original 没工作可做)")


# ---------------------------------------------------------------- Endpoints ----

@router.post("/upload/video")
async def upload_video(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传参考视频(≤50MB)。返 fal storage URL + ffprobe 时长 + ⭐ 文件 SHA256(红线 3)。

    红线 3:每次上传立即算文件 SHA256,前端拿到后:
    - 显示前 8 位给用户("当前视频 hash: abc12345")
    - /create 时把完整 hash 传回来 → 入库 input_video_sha256
    便于事后审计"是不是上传了同一视频" + 法务 §4.4.4 举证。
    """
    _guard_enabled()
    contents = await read_bounded(file, MAX_VIDEO_SIZE, VIDEO_MIMES, "参考视频")
    suffix = ".mp4"
    if file.filename and "." in file.filename:
        suffix = "." + file.filename.rsplit(".", 1)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        video_path = tmp.name
    try:
        import subprocess as _sp, json as _j
        try:
            rr = _sp.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", video_path],
                capture_output=True, text=True, timeout=30,
            )
            dur = float(_j.loads(rr.stdout).get("format", {}).get("duration", 0))
        except Exception:
            dur = 0
        # ⭐ 红线 3:文件本体 SHA256
        file_sha256 = sha256_file(video_path)
        url = await fal_upload_with_retry(video_path)
        # 防御性校验:fal storage 应该返 *.fal.media,但万一返了别的 host
        # 也不能直接信任 — 同一个 allowlist 把关,失败就是 fal 自己 schema 变了
        validate_video_url(url, field_name="fal_storage_url")
        # Path B 缓存:把临时文件搬到 /tmp/v2_cache/{sha256}.mp4
        # 让 check-duration 直读本地(省去跨境读 fal CDN 的 6-9s)
        # store 内部成功会把 video_path 移走;失败/dedupe 时也会清掉源文件
        cached = cache_store(file_sha256, video_path)
        # opportunistic 清过期文件(防 cron 没装也不堆积)
        try: cache_clean_old()
        except Exception: pass
        log_info(
            f"[V2-UPLOAD-VIDEO] user={current_user.get('id')} "
            f"sha256={file_sha256[:8]} duration={dur:.1f}s "
            f"cache={'hit' if cached else 'miss'} url={url[:80]}"
        )
    finally:
        # store 已搬走;若失败 video_path 还在,unlink 兜底防泄漏
        if os.path.exists(video_path):
            try: os.unlink(video_path)
            except Exception: pass
    return {
        "video_url": url,
        "duration_sec": dur,
        "sha256": file_sha256,
        "sha256_short": file_sha256[:8],
    }


@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    role: str = Form(..., description="product | person | scene | reference"),
    current_user: dict = Depends(get_current_user),
):
    """上传参考图(≤10MB,role 必传)。"""
    _guard_enabled()
    if role not in IMAGE_ROLES:
        raise HTTPException(400, f"role 必须 ∈ {IMAGE_ROLES}")
    contents = await read_bounded(file, MAX_IMAGE_SIZE, IMAGE_MIMES, "参考图")
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
    return {"image_url": url, "role": role}


@router.post("/check-duration", response_model=CheckDurationResponse)
async def check_duration_endpoint(
    req: CheckDurationRequest,
    current_user: dict = Depends(get_current_user),
):
    """⭐ 智能切片提示:视频时长非 8 倍数 → 弹窗让用户选丢哪段。

    前端流程:
        upload/video 拿到 duration → 立即 POST check-duration:
        - needs_trim=False → 直接进 preview-segments → 选档生成
        - needs_trim=True → 弹"丢哪段"弹窗 → 用户选完 → /create body 带 trim_start/trim_end

    motion_score(0-100):用 ffmpeg signalstats YDIF 帧间亮度差 算运动量
        - 0 ≈ 静止帧;~5-15 中等运动;>20 大量运动
        - 推荐用户丢"motion_score 最低"的段(影响最小)
    """
    _guard_enabled()
    # SSRF 守卫:check-duration 也会去 GET video_url(motion_score 算运动量)
    if req.video_url:
        validate_video_url(req.video_url)
    try:
        result = _check_duration(req.video_duration_sec)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if not result["needs_trim"]:
        return CheckDurationResponse(**result)

    # 需要 trim → 算候选段 + motion_score
    # Path B:优先查本地缓存(/tmp/v2_cache/{sha256}.mp4),miss 才走 fal CDN URL
    cache_local = cache_try_get(req.video_sha256) if req.video_sha256 else None
    ffmpeg_source = cache_local or req.video_url
    cache_status = "hit" if cache_local else ("miss-fallback-url" if req.video_url else "miss-no-url")
    suggestions: List[TrimCandidate] = []
    if ffmpeg_source:
        try:
            t0 = time.time()
            cands = await suggest_trim_candidates(
                ffmpeg_source,
                req.video_duration_sec,
                result["target_duration"],
            )
            suggestions = [TrimCandidate(**c) for c in cands]
            log_info(
                f"[V2-CHECK-DURATION] cache={cache_status} "
                f"sha={(req.video_sha256 or '')[:8]} elapsed={time.time()-t0:.2f}s"
            )
        except Exception as e:
            log_error(
                f"[V2-CHECK-DURATION] motion_score 失败 cache={cache_status} "
                f"sha={(req.video_sha256 or '')[:8]}:{e}"
            )
            # 走静态 fallback
    if not suggestions:
        # video_url 没传 / motion 计算失败 → 给静态候选(运动量字段留 -1)
        drop = result["drop_seconds"]
        target = result["target_duration"]
        dur = req.video_duration_sec
        suggestions = [
            TrimCandidate(label=f"丢末尾 {drop:.1f} 秒", position="tail",
                          start=target, end=dur, recommended=True),
            TrimCandidate(label=f"丢开头 {drop:.1f} 秒", position="head",
                          start=0.0, end=drop),
        ]
        mid_start = dur / 2 - drop / 2
        mid_end = dur / 2 + drop / 2
        if mid_start > drop and (dur - mid_end) > drop:
            suggestions.append(TrimCandidate(
                label=f"丢中间 {drop:.1f} 秒", position="middle",
                start=round(mid_start, 2), end=round(mid_end, 2),
            ))

    return CheckDurationResponse(
        needs_trim=True,
        current_duration=result["current_duration"],
        target_duration=result["target_duration"],
        drop_seconds=result["drop_seconds"],
        suggestions=suggestions,
    )


@router.post("/preview-segments", response_model=PreviewSegmentsResponse)
async def preview_segments(
    req: PreviewSegmentsRequest,
    current_user: dict = Depends(get_current_user),
):
    """切片预览(不扣费)。

    A2 简版:返 plan_segments_v2 输出,thumbnail_url=None(B 阶段补 ffmpeg 抽帧)。
    前端 single 模式只 1 段,可以直接用 video_url 显示首帧;ultimate 模式 B 阶段做缩略图。
    """
    _guard_enabled()
    validate_video_url(req.video_url)
    try:
        plan = plan_segments_v2(req.video_duration_sec)
    except ValueError as e:
        raise HTTPException(400, str(e))
    type_ = "single" if req.video_duration_sec <= 8 else "ultimate"
    segments = [
        SegmentChoice(
            idx=p["idx"], start=p["start"], duration=p["duration"],
            thumbnail_url=None,  # A2 简版,B 阶段补
            # 2026-05-10 砍单档:allowed_tiers 跟随 SegmentChoice 模型一起删
        ) for p in plan
    ]
    # 2026-05-11 多镜头检测(F 弹窗触发用)
    # 检测失败 fallback 1(单镜头视为安全分支),不阻塞 preview 流程
    try:
        scene_count = await detect_scene_count(req.video_url)
    except Exception:
        scene_count = 1
    preview_token = uuid.uuid4().hex
    return PreviewSegmentsResponse(
        type=type_, segments=segments, preview_token=preview_token,
        scene_count=scene_count,
    )


@router.post("/estimate", response_model=EstimateResponse)
async def estimate(
    req: EstimateRequest,
    current_user: dict = Depends(get_current_user),
):
    """价格预估(不扣费)。

    A2 实现:校验 segments + 算总积分。video_duration_sec 这里没传(estimate 假定前端
    已经从 preview-segments 拿到 plan,只是问"我这套 tier 选择多少积分")。
    """
    _guard_enabled()

    # 单档:tier 校验已删,只需要校验"至少 1 段 ai"
    if not any(seg.source_type == "ai" for seg in req.segments):
        raise HTTPException(400, "至少要有 1 段 source_type=ai")

    # 算总积分(calc_credits 单档版:ai 段数 × SEGMENT_CREDITS)
    seg_dicts = [s.model_dump() for s in req.segments]
    total_credits = calc_credits(seg_dicts)

    # 估算 fal 成本(check 保险 2)— worst-case 8s × $0.0925 × 1.3 / ai 段
    settings = get_settings()
    ai_count = sum(1 for s in req.segments if s.source_type == "ai")
    estimated_usd = ai_count * SEGMENT_INPUT_SECONDS_MAX * 0.0925 * 1.3
    if estimated_usd > settings.VC2_MAX_ORDER_COST_USD:
        raise HTTPException(
            400,
            f"订单估算成本 ${estimated_usd:.2f} 超上限 ${settings.VC2_MAX_ORDER_COST_USD}"
        )

    original_count = len(req.segments) - ai_count
    # rmb_display 直接按"ai 段数 × 单价"算,跟积分系统解耦(memory 教训:不强绑定汇率)
    rmb_display = f"{ai_count * float(SEGMENT_DISPLAY_RMB):.1f}"
    estimated_minutes = max(2, ai_count * 2)  # 粗略每段 2 分钟

    return EstimateResponse(
        type=req.type,
        replacement_mode=req.replacement_mode,
        ai_segments_count=ai_count,
        original_segments_count=original_count,
        total_segments=len(req.segments),
        total_credits=total_credits,
        total_rmb_display=rmb_display,
        estimated_minutes=estimated_minutes,
    )


@router.post("/create", response_model=CreateResponse)
async def create(
    req: CreateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """创建任务(扣费 + 异步推 worker)。

    A2:type=single 单段路径上线
    B 阶段:type=ultimate 多段并发拼接 + 可选 trim 范围(check-duration 弹窗用户选完才传)
    """
    _guard_enabled()

    # 0. SSRF 守卫:video_url 必须 https + host 在白名单
    #    后续 processor 会 GET 这个 URL 下载用户视频,严防内网/元数据探测
    validate_video_url(req.video_url)

    # 1. disclaimer 检查
    if not req.disclaimer_acknowledged:
        raise HTTPException(400, "必须勾选《视频复刻 V2 上传声明书》才能提交")

    # 2. prompt 内容审核
    safe, reason = check_prompt(req.prompt)
    if not safe:
        raise HTTPException(400, f"提示词触发内容安全过滤:{reason}")

    # 3. 计算 effective_duration(含 trim 后实际用于切片的时长)
    #    B+ 语义:trim_drop_ranges 多段丢弃数组(优先);trim_start/end 兼容单段
    full_duration = req.video_duration_sec
    drop_ranges_input = req.trim_drop_ranges
    if drop_ranges_input is None and (req.trim_start is not None or req.trim_end is not None):
        # legacy 单段 drop 总是转成 1-elem 数组,后续主校验来判合法性
        s = req.trim_start or 0.0
        e = req.trim_end if req.trim_end is not None else 0.0
        drop_ranges_input = [[s, e]]

    import math
    if not math.isfinite(full_duration) or full_duration <= 0:
        raise HTTPException(400, f"video_duration_sec 非法:{full_duration}")

    if drop_ranges_input:
        if len(drop_ranges_input) > 8:
            # 上限保险:防止恶意提交几千段拖死 ffmpeg
            raise HTTPException(400, f"trim 丢弃区间最多 8 段(收到 {len(drop_ranges_input)})")
        # 校验每段
        norm_ranges: List[tuple] = []
        for r in drop_ranges_input:
            if len(r) != 2:
                raise HTTPException(400, f"trim_drop_ranges 元素必须是 [start, end]:{r}")
            s, e = float(r[0]), float(r[1])
            if not (math.isfinite(s) and math.isfinite(e)):
                raise HTTPException(400, f"trim 丢弃区间含 NaN/Infinity:[{s}, {e}]")
            if s < 0 or e > full_duration + 0.05 or e <= s:
                raise HTTPException(
                    400,
                    f"trim 丢弃区间非法:[{s}, {e}] 不在 [0, {full_duration}]"
                )
            if e - s < 0.05:
                raise HTTPException(400, f"trim 单段太短(< 0.05s):[{s}, {e}]")
            norm_ranges.append((s, e))
        # 重叠允许,按并集算总丢弃秒数(用户拖动可穿过 → 重叠部分只算一次)
        # 排序后线性合并,效果同 processor._compute_keep_ranges 第一步
        norm_ranges.sort(key=lambda x: x[0])
        merged: List[tuple] = []
        for s, e in norm_ranges:
            if merged and s <= merged[-1][1] + 0.05:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        total_drop = sum(e - s for s, e in merged)
        effective_duration = full_duration - total_drop
        if effective_duration < 4:
            raise HTTPException(400, f"trim 后剩余时长 {effective_duration:.2f}s 不足 4 秒最低门槛")
        # 入库存 merged 后干净非重叠数组(processor 直接用,法务审计也清晰)
        trim_drop_ranges_json = json.dumps([[s, e] for s, e in merged])
        # legacy 列存第一段端点(给老查询用,新代码不依赖)
        trim_start = merged[0][0]
        trim_end = merged[0][1]
    else:
        trim_drop_ranges_json = None
        trim_start = trim_end = 0.0
        effective_duration = full_duration

    # 4. 后端重算 plan,跟前端 segments 对齐(用 effective_duration 而非 full)
    try:
        plan_back = plan_segments_v2(effective_duration)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _validate_segments(req.type, req.segments, plan_back)

    # 4. 算总积分 + 保险 2
    settings = get_settings()
    seg_dicts = [s.model_dump() for s in req.segments]
    total_credits = calc_credits(seg_dicts)
    ai_count = sum(1 for s in req.segments if s.source_type == "ai")
    estimated_usd = ai_count * SEGMENT_INPUT_SECONDS_MAX * 0.0925 * 1.3
    if estimated_usd > settings.VC2_MAX_ORDER_COST_USD:
        raise HTTPException(400, f"订单估算成本超上限 ${settings.VC2_MAX_ORDER_COST_USD}")

    # 5. 检查 + 扣费
    user_id = str(current_user["id"])
    if not check_user_credits(user_id, total_credits):
        raise HTTPException(402, f"积分不足,需 {total_credits} 积分")
    job_id = str(uuid.uuid4())
    if not deduct_credits(user_id, total_credits, ref_id=job_id, module="video/clone-v2"):
        raise HTTPException(402, "扣费失败(并发竞争)")

    # 6. build_prompt(⭐ 功能 3)
    image_urls_obj = [img.model_dump() for img in req.image_urls]
    prompt_compiled = build_prompt(req.prompt, image_urls_obj)

    # 7. segments_plan 合并前端 segments + 后端 plan_back(单档:不再有 tier 字段)
    # input_seconds 字段保留作 fallback,跟段实际秒数对齐(memory: feedback_ssp_verify_before_delete)
    segments_plan = []
    for seg, back in zip(req.segments, plan_back):
        segments_plan.append({
            "idx": back["idx"],
            "start": back["start"],
            "duration": back["duration"],
            "source_type": seg.source_type,
            "input_seconds": back["duration"],  # 段实际秒数
            "thumbnail_url": None,
        })

    # 8. video_sha256(红线 3:优先用前端传回的文件本体 SHA256;前端没传 → fallback URL hash)
    if req.video_sha256:
        video_sha256 = req.video_sha256
    else:
        video_sha256 = hashlib.sha256(req.video_url.encode()).hexdigest()
        log_info(f"[V2-CREATE] 警告:前端未传 video_sha256,fallback 用 url hash;job_id={job_id}")

    # 9. INSERT video_clone_v2_jobs + disclaimer_log
    ai_count = sum(1 for s in req.segments if s.source_type == "ai")
    original_count = len(req.segments) - ai_count

    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")[:500]

    # 9. 写 trim 字段(B+ 阶段:trim_drop_ranges_json 多段;legacy 列做单段兼容)
    trimmed_seconds = req.trimmed_seconds
    if trimmed_seconds is None:
        trimmed_seconds = max(0.0, full_duration - effective_duration)

    with get_db() as conn:
        conn.execute("""
            INSERT INTO video_clone_v2_jobs (
                id, user_id, type, replacement_mode, tier, segment_tiers,
                input_video_url, input_video_duration_sec, input_video_sha256,
                image_urls, prompt, prompt_compiled,
                segments_plan, segments_count, segments_results,
                total_credits_charged, status,
                trim_start, trim_end, trimmed_seconds, trim_drop_ranges_json
            ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, 'processing', ?, ?, ?, ?)
        """, (
            job_id, user_id, req.type, req.replacement_mode,
            req.video_url, req.video_duration_sec, video_sha256,
            json.dumps(image_urls_obj, ensure_ascii=False),
            req.prompt, prompt_compiled,
            json.dumps(segments_plan, ensure_ascii=False),
            len(segments_plan),
            total_credits,
            trim_start, trim_end, trimmed_seconds, trim_drop_ranges_json,
        ))
        conn.execute("""
            INSERT INTO video_clone_v2_disclaimer_log (
                user_id, job_id, ip, user_agent, video_sha256, disclaimer_version
            ) VALUES (?, ?, ?, ?, ?, 'v1')
        """, (user_id, job_id, client_ip, user_agent, video_sha256))
        conn.commit()

    # 10. 异步推 worker
    asyncio.create_task(process_v2_job(job_id))

    log_info(
        f"video_clone_v2 create:job_id={job_id} user={user_id} type={req.type} "
        f"replacement_mode={req.replacement_mode} ai={ai_count} original={original_count} "
        f"credits={total_credits}"
    )

    return CreateResponse(
        job_id=job_id,
        status="processing",
        type=req.type,
        replacement_mode=req.replacement_mode,
        ai_segments_count=ai_count,
        original_segments_count=original_count,
        total_credits_charged=total_credits,
        estimated_completion_minutes=max(2, ai_count * 2),
    )


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """查询任务。鉴权:仅本人 + admin。"""
    _guard_enabled()
    user_id = str(current_user["id"])
    is_admin = current_user.get("role") == "admin"
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM video_clone_v2_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "任务不存在")
    if not is_admin and row["user_id"] != user_id:
        raise HTTPException(403, "无权查看")

    plan = json.loads(row["segments_plan"])
    results_raw = json.loads(row["segments_results"] or "[]")
    results_by_idx = {r["idx"]: r for r in results_raw}

    seg_view = []
    for p in plan:
        r = results_by_idx.get(p["idx"], {})
        seg_view.append({
            "idx": p["idx"],
            "source_type": p["source_type"],
            # 2026-05-10 砍单档:tier 字段不再返(前端 JobView.segments 也已删)
            "status": r.get("status", "pending"),
            "output_url": r.get("output_url"),
        })

    completed = sum(1 for s in seg_view if s["status"] in ("completed", "ready"))
    total_ai = sum(1 for s in seg_view if s["source_type"] == "ai")
    total_original = sum(1 for s in seg_view if s["source_type"] == "original")

    return {
        "job_id": row["id"],
        "type": row["type"],
        "replacement_mode": row["replacement_mode"],
        "status": row["status"],
        "progress": {
            "completed": completed,
            "total_ai": total_ai,
            "total_original": total_original,
        },
        "segments": seg_view,
        "final_video_url": row["final_video_url"],
        "final_video_url_watermarked": row["final_video_url_watermarked"],
        "final_video_url_raw": row["final_video_url_raw"],
        "total_credits_charged": row["total_credits_charged"],
        "total_credits_refunded": row["total_credits_refunded"],
        "error": row["error_message"],
        # ⭐ 红线 3:返 hash 给前端审计(用户能眼睛验证"同一视频还是不同视频")
        "input_video_sha256": row["input_video_sha256"],
        "input_video_sha256_short": (row["input_video_sha256"] or "")[:8],
    }


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """取消任务(B 阶段实装)。"""
    _guard_enabled()
    raise HTTPException(503, "video_clone_v2 cancel 端点 B 阶段实装")


@router.get("/jobs")
async def list_jobs(
    limit: int = 20,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
):
    """历史列表(本人最近 N 条)。"""
    _guard_enabled()
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    user_id = str(current_user["id"])
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, type, replacement_mode, status, segments_count,
                   total_credits_charged, total_credits_refunded,
                   final_video_url_watermarked, final_video_url_raw,
                   created_at, completed_at, error_message
            FROM video_clone_v2_jobs
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (user_id, limit, offset)).fetchall()
    return {
        "items": [dict(r) for r in rows],
        "limit": limit,
        "offset": offset,
    }


@router.get("/prompt-templates")
async def get_prompt_templates(
    current_user: dict = Depends(get_current_user),
):
    """5 个 prompt 模板(不依赖灰度开关)。"""
    return {"templates": list(PROMPT_TEMPLATES)}


# ─── ⭐ AI 优化 prompt(2026-05-11)─────────────────────────────────────
# 用户大白话 → fal seedance r2v 风格简短中文 prompt。
# 出处:playground 实证 019e0951 完美换裤的 prompt = "视频中的裤子换成上传的图片"
# (15-50 字简短中文,无 @ 占位符,直白陈述句)。
# V1 generate-prompt 输出英文 + @Image1 引用,跟 V2 不对齐,故 V2 自己实现。

_V2_PROMPT_LLM_ENDPOINT = "openrouter/router"
_V2_PROMPT_LLM_MODEL = "qwen/qwen3-vl-235b-a22b-instruct"
_V2_PROMPT_LLM_FALLBACK = "google/gemini-2.5-flash"

_V2_PROMPT_LLM_SYSTEM_CN = """你是 fal seedance 2.0 视频替换模型的 prompt 工程师。用户上传一段视频和参考图(产品/人物/场景),用大白话说想换什么。你的任务是输出一段简洁但内容丰富的中文 prompt,涵盖视频内容制作的核心元素。

【输出必含元素】(按优先级排序):
1. ⭐ 主体替换语义:明确"视频里的 X 换成上传的图片/产品/人物/场景"
2. ⭐ 替换对象细节:颜色/款式/材质/廓形/品牌特征(从用户描述提取)
3. 主体动作:主角在做什么(走路/试穿/展示/坐姿等,用户描述或合理推断)
4. 场景调性:都市街拍/工作室/复古咖啡馆/极简白底 等(用户描述或推断)
5. 镜头风格:特写/全景/跟拍/中景(可选,有助于模型理解)
6. 光线氛围:柔光/黄金时段/明亮自然光/电影感冷调(可选)
7. 节奏感:稳定/慢镜/灵动(可选)
8. 整体风格:写实/电影感/复古/极简(可选)

【⚠️ 关键技术约束】(fal seedance r2v 端点物理特性):
- 端点本质 = "替换对象,保留原视频其他",原视频的镜头/动作/光线已固定
- prompt 描述这些元素时是"告诉模型上下文",**不要写指挥性语句**("镜头要变快"❌)
- prompt 末尾必须加一句"保持原视频的镜头、动作、光线、节奏不变"(防模型自作主张)

【输出风格规则】:
1. 纯中文,80-150 字,**绝不超过 200 字**(过长干扰模型)
2. 自然流畅的描述句,不用 @Image1 / @Video1 等占位符
3. 不要 markdown,不要前缀"Prompt:",不要引号,不要编号
4. 一段流畅中文,可有 1-2 个逗号分隔的子句

【输出示例】:
- "视频中的裤子换成上传的浅蓝色宽松牛仔裤款式,保持腿部线条修长的廓形特征,街拍写实风格,自然光线下的都市街道,保持原视频的镜头、动作、光线、节奏不变。"
- "把视频里的人换成上传的人物图,亚洲女性,白皙肤色,文艺气质,在咖啡馆温暖柔光的中景镜头中保持端坐姿态,保持原视频的镜头、动作、光线、节奏不变。"
- "视频中的椅子换成上传的北欧实木风格扶手椅,深木色调,极简工作室白底背景,中景特写,保持原视频的镜头、动作、光线、节奏不变。"

输出:一段流畅中文 prompt(80-150 字),不要解释。"""

_V2_PROMPT_LLM_SYSTEM_EN = """You are a prompt engineer for the fal seedance 2.0 video object replacement model. The user uploads a video and reference images (product / person / scene) and describes in any language what they want. Your task is to output a concise but rich English prompt covering the core elements of video content creation.

[Required Elements] (by priority):
1. ⭐ Replacement semantics: state clearly "Replace the X in the video with the uploaded image/product/person/scene"
2. ⭐ Object details: color / style / material / silhouette / brand features (from user description)
3. Subject action: what the subject is doing (walking / trying on / displaying / sitting, etc.)
4. Setting: urban street / studio / vintage cafe / minimal white backdrop, etc.
5. Camera: close-up / wide shot / tracking / medium shot (optional)
6. Lighting: soft light / golden hour / natural daylight / cinematic cool tone (optional)
7. Pacing: stable / slow-motion / dynamic (optional)
8. Overall style: photorealistic / cinematic / vintage / minimal (optional)

[⚠️ Critical Technical Constraint] (fal seedance r2v endpoint physics):
- Endpoint replaces objects while preserving the rest of the original video (camera/action/lighting are fixed)
- Describe these elements as CONTEXT, not as commands ("make camera faster" ❌)
- ALWAYS end with: "Preserve the original video's camera, motion, lighting and pacing."

[Style Rules]:
1. Pure English, 30-60 words, **never exceed 80 words**
2. Natural flowing description, NO @ tag references
3. No markdown, no "Prompt:" prefix, no quotes, no numbering
4. One flowing paragraph with 1-2 comma-separated clauses

[Examples]:
- "Replace the pants in the video with the uploaded loose-fit light-blue denim style, preserving the slim leg silhouette, urban street photorealistic look with natural daylight. Preserve the original video's camera, motion, lighting and pacing."
- "Replace the person in the video with the uploaded reference, an Asian woman with fair skin and artistic vibe, seated in a warm-lit cafe medium shot. Preserve the original video's camera, motion, lighting and pacing."
- "Replace the chair in the video with the uploaded Nordic solid-wood armchair, deep wood tone, minimal studio white backdrop, medium close-up. Preserve the original video's camera, motion, lighting and pacing."

Output: ONE flowing English paragraph (30-60 words), no explanation."""


def _pick_system_prompt(region: str) -> str:
    """region=CN → 中文 prompt(playground 实证完美);region=Global → 英文 prompt"""
    return _V2_PROMPT_LLM_SYSTEM_EN if region == "Global" else _V2_PROMPT_LLM_SYSTEM_CN


class GenerateV2PromptRequest(BaseModel):
    model_config = {"extra": "forbid"}
    user_description: str = Field(..., min_length=1, max_length=500,
                                  description="用户大白话需求,会被 LLM 优化成简短 prompt")
    # 2026-05-11:目标市场 toggle,影响输出语言(CN 中文 / Global 英文)
    region: Literal["CN", "Global"] = Field("CN", description="CN 国内中文 / Global 海外英文")


class GenerateV2PromptResponse(BaseModel):
    generated_prompt: str
    model: str
    region: str


async def _call_deepseek_optimize(user_description: str, region: str = "CN") -> Optional[str]:
    """DeepSeek API 优化 prompt(中文母语,$0.27/M in + $1.10/M out,直连不过 fal)。
    region=CN 输出中文,region=Global 输出英文。返 None = 失败,调用方走 fal fallback。"""
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return None
    import httpx
    is_en = region == "Global"
    user_content = (
        f"User description: {user_description}\n\nOutput ONE short English sentence prompt (8-25 words)."
        if is_en
        else f"用户描述:{user_description}\n\n请输出一句简短的中文 prompt(对齐 playground 实测成功配方风格)。"
    )
    body = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": _pick_system_prompt(region)},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.7,
        "max_tokens": 500,  # 2026-05-11:8 元素丰富 prompt 需要更多 token(中文 80-150 字 / 英文 30-60 词)
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as cli:
            r = await cli.post("https://api.deepseek.com/chat/completions", headers=headers, json=body)
        if r.status_code != 200:
            log_error(f"deepseek {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        return text if text else None
    except Exception as e:
        log_error(f"deepseek 异常: {str(e)[:200]}")
        return None


@router.post("/generate-prompt", response_model=GenerateV2PromptResponse)
async def generate_v2_prompt(
    req: GenerateV2PromptRequest,
    current_user: dict = Depends(get_current_user),
):
    """大白话 → fal seedance 风格简短 prompt(中文 / 英文双语,跟前端 region 选择)。
    2026-05-11:首选 DeepSeek(便宜直连)+ region toggle,fal qwen/gemini 作 2 层 fallback。"""
    import re as _re

    region = req.region
    is_en = region == "Global"
    text = ""
    used_model = ""

    # ⭐ Primary:DeepSeek(直连不过 fal,$0.0003/次,跟 region 切语言)
    ds_text = await _call_deepseek_optimize(req.user_description, region=region)
    if ds_text:
        text = ds_text
        used_model = "deepseek-chat"

    # Fallback 1+2:fal openrouter qwen/gemini(DeepSeek 失败时,跟 region 切语言)
    if not text:
        user_msg = (
            f"User description: {req.user_description}\n\nOutput ONE short English sentence prompt (8-25 words)."
            if is_en
            else f"用户描述:{req.user_description}\n\n请输出一句简短的中文 prompt(对齐 playground 实测成功配方风格)。"
        )
        for model in (_V2_PROMPT_LLM_MODEL, _V2_PROMPT_LLM_FALLBACK):
            try:
                result = await asyncio.wait_for(
                    fal_client.run_async(
                        _V2_PROMPT_LLM_ENDPOINT,
                        arguments={
                            "prompt": user_msg,
                            "system_prompt": _pick_system_prompt(region),
                            "model": model,
                            "temperature": 0.7,
                        },
                    ),
                    timeout=40,
                )
                text = (result.get("output") or "").strip()
                if text:
                    used_model = f"fal-{model.split('/')[-1]}"
                    break
            except Exception as e:
                log_error(f"v2 generate-prompt fal LLM={model} 失败: {str(e)[:200]}")
                continue

    if not text:
        # 最终 fallback:返用户原话(不优化总比报错强)
        text = req.user_description.strip()
        used_model = "fallback-passthrough"

    # 清理 markdown / 引号(LLM 偶尔不听话)
    text = _re.sub(r"^```(?:[a-z]*)?\s*|\s*```$", "", text, flags=_re.MULTILINE).strip()
    text = text.strip('"').strip("'").strip()
    # 2026-05-11:8 元素丰富 prompt 可能跨多行,合并空白(不再 split[0] 截首行)
    text = _re.sub(r"\s+", " ", text).strip()

    log_info(
        f"v2 generate-prompt user={current_user.get('id')} region={region} "
        f"model={used_model} desc_len={len(req.user_description)} out_len={len(text)}"
    )
    return GenerateV2PromptResponse(generated_prompt=text, model=used_model, region=region)
