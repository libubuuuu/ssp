"""
视频复刻 Vidu Q2 Pro Reference-to-Video — P219 骨架(2026-05-09)
================================================================

跟 video_clone.py(Seedance 2.0)的关键差异:
  - 模型:fal-ai/vidu/q2/reference-to-video/pro
  - 定价:720p 定额 $0.30/段(不按秒,跑满 8s 最划算)
  - 单段最长 8 秒(强制 duration=8,不让用户改)
  - 输入支持:任意时长原视频 → 后端 ffmpeg 切片 → 每段独立生成 → ffmpeg concat 拼接
  - 失败重试:支持只重生成失败片段(不重做整个视频)
  - Prompt:后端要把 @Video1 / @Image1 占位符去掉再传给模型(Vidu 不解析 @ 语法)
  - 测试期:test_vidu_clone.py 通过后才真接入,先放骨架等测试
"""
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import get_current_user

router = APIRouter()

# 720p 定额价(段单价,不按秒)
PRICE_USD_PER_SEGMENT_720P = 0.30
PRICE_USD_PER_SEGMENT_540P = 0.20
PRICE_USD_PER_SEGMENT_360P = 0.10
SEGMENT_LENGTH_SEC = 8         # 强制 8 秒/段(720p 定额制,不跑满就亏)
USD_TO_RMB = 7.2
GROSS_MARGIN = 1.5
CREDITS_PER_RMB = 1


def _calc_segments(video_duration_sec: float) -> int:
    """任意时长 → ⌈duration / 8⌉ 段"""
    import math
    return max(1, math.ceil(video_duration_sec / SEGMENT_LENGTH_SEC))


def _calc_credits(segments: int, count: int = 1, resolution: str = "720p") -> int:
    """N 段 × $0.30 (720p) × count × 7.2 × 1.5"""
    if resolution == "1080p":
        usd_per_seg = 0.20 + 0.10 * SEGMENT_LENGTH_SEC  # 1080p $0.20 + $0.10/s,跑满 8s
    elif resolution == "540p" or resolution == "520p":
        usd_per_seg = PRICE_USD_PER_SEGMENT_540P
    elif resolution == "360p":
        usd_per_seg = PRICE_USD_PER_SEGMENT_360P
    else:
        usd_per_seg = PRICE_USD_PER_SEGMENT_720P
    usd = segments * usd_per_seg * count
    rmb = usd * USD_TO_RMB * GROSS_MARGIN
    return max(1, int(round(rmb * CREDITS_PER_RMB)))


@router.get("/price")
async def price(duration_sec: int = 8, resolution: str = "720p", count: int = 1):
    """实时价格预估 — 切片数量 + 单段定额"""
    duration_sec = max(1, min(600, int(duration_sec)))
    count = max(1, min(4, int(count)))
    segments = _calc_segments(duration_sec)
    credits = _calc_credits(segments, count, resolution)
    if resolution == "1080p":
        per_seg_usd = 0.20 + 0.10 * SEGMENT_LENGTH_SEC
    elif resolution == "540p" or resolution == "520p":
        per_seg_usd = PRICE_USD_PER_SEGMENT_540P
    elif resolution == "360p":
        per_seg_usd = PRICE_USD_PER_SEGMENT_360P
    else:
        per_seg_usd = PRICE_USD_PER_SEGMENT_720P
    total_usd = round(segments * per_seg_usd * count, 4)
    return {
        "duration_sec": duration_sec,
        "segments": segments,
        "segment_length_sec": SEGMENT_LENGTH_SEC,
        "resolution": resolution,
        "count": count,
        "price_usd": total_usd,
        "price_rmb": round(total_usd * USD_TO_RMB * GROSS_MARGIN, 2),
        "credits": credits,
    }


@router.post("/upload/video")
async def upload_video(current_user: dict = Depends(get_current_user)):
    """[骨架] 等 test_vidu_clone.py 验证通过后实现"""
    raise HTTPException(503, "Vidu Q2 Pro 链路开发中,正在等模型测试结果(预计 1-2 天)")


@router.post("/upload/image")
async def upload_image(current_user: dict = Depends(get_current_user)):
    """[骨架] 等 test 通过后实现"""
    raise HTTPException(503, "Vidu Q2 Pro 链路开发中")


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    reference_video_url: str
    reference_image_urls: List[str] = Field(default_factory=list, max_length=4)
    resolution: str = Field("720p")
    aspect_ratio: str = Field("9:16")
    count: int = Field(1, ge=1, le=4)


@router.post("/generate")
async def generate(
    req: GenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """[骨架] 等 test 通过后实现:切片 → 并发 fal submit → polling → ffmpeg concat → 归档"""
    raise HTTPException(503, "Vidu Q2 Pro 链路开发中,正在等模型测试结果")
