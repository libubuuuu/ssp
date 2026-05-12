"""P221 视频复刻 V2 — ffmpeg 水印加盖(双版本下载用)

每个生成任务输出两个文件:
- {job_id}_watermarked.mp4 — 合规版(右下角 "xiaoLi ai · AI 生成")
- {job_id}_raw.mp4         — 无标识版(原始 fal 输出 + 隐式 metadata)

水印风格历史(用户最终决议:六审回到纯文字 simple,删掉 logo_with_text):
- simple:     Bold 白字 + 黑色描边(六审最终决议,默认)
- futuristic: 青蓝色 + 蓝边 + 青光 Bold(四审备选)
- gold:       金色 + 黑边 + 黑影 Bold(四审备选)
- logo:       白字 + 黑底 box Regular(四审备选)
- (删除)logo_with_text:五审 logo 图 + 小字方案,8% 短边下视觉糊,六审撤回

详见:
- docs/P221-API-SCHEMA.md(v4)
- docs/legal/terms-of-service.md(§6.3 双版本规格)

运行依赖:
- 系统 ffmpeg
- Noto Sans CJK SC Bold(已 apt install fonts-noto-cjk)
"""
from __future__ import annotations
import asyncio
import math
import os
import shutil
import time
from pathlib import Path
from typing import Literal

from .video_clone_v2_pricing import (
    WATERMARK_TEXT,
    WATERMARK_FONT_SIZE_PCT,
    WATERMARK_OPACITY,
    WATERMARK_BORDER_OPACITY,
    WATERMARK_BORDER_WIDTH,
    WATERMARK_EDGE_PAD,
)


WatermarkStyle = Literal["simple", "futuristic", "gold", "logo"]
DEFAULT_STYLE: WatermarkStyle = "simple"   # 六审最终决议:纯文字 Bold 白字 + 黑边


# Regular 字体(simple / logo 风格)
_FONT_REGULAR_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",   # 黑体类 fallback
    "/System/Library/Fonts/PingFang.ttc",
)

# Bold 字体(futuristic / gold 风格)
_FONT_BOLD_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",   # wqy 没单独 Bold,降级用 Regular
    "/System/Library/Fonts/PingFang.ttc",
)


def _resolve_font_file(weight: Literal["regular", "bold"] = "regular") -> str:
    """找第一个存在的 CJK 字体文件,找不到就抛错。"""
    candidates = _FONT_BOLD_CANDIDATES if weight == "bold" else _FONT_REGULAR_CANDIDATES
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(
        f"未找到 CJK 字体文件({weight}),尝试过:{candidates}。"
        "Ubuntu 装:apt install fonts-noto-cjk"
    )


async def _ffprobe_short_side(video_path: str) -> int:
    """ffprobe 拿视频的短边像素数(width / height 较小者),用于算水印字号。"""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=,:p=0",
        video_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败({video_path}):{stderr.decode(errors='replace')}")
    parts = stdout.decode().strip().split(",")
    if len(parts) != 2:
        raise RuntimeError(f"ffprobe 输出格式异常:{stdout!r}")
    w, h = int(parts[0]), int(parts[1])
    return min(w, h)


# ─── 滤镜构造(每个 style 一份) ─────────────────────────────────────────

def _vf_simple(font_size: int, pad: int, font_file_bold: str) -> str:
    """六审最终决议:Bold 白字 + 黑色描边(纯文字方案,默认)。

    fontcolor=white@0.6 + bordercolor=black@0.6 + borderw=1 + 边距 10px
    pad 参数已传入 WATERMARK_EDGE_PAD(10),确保跟用户决议一致。
    """
    return (
        f"drawtext="
        f"text='{WATERMARK_TEXT}':"
        f"fontfile='{font_file_bold}':"
        f"fontsize={font_size}:"
        f"fontcolor=white@{WATERMARK_OPACITY}:"
        f"bordercolor=black@{WATERMARK_BORDER_OPACITY}:"
        f"borderw={WATERMARK_BORDER_WIDTH}:"
        f"x=w-tw-{pad}:y=h-th-{pad}"
    )


def _vf_futuristic(font_size: int, pad: int, font_file_bold: str) -> str:
    """⭐ 用户偏好:青蓝色 + 蓝色描边 + 青色发光(科技感)。

    ffmpeg drawtext 没原生渐变,用单一主色 + 描边 + 阴影模拟科技感:
      - fontcolor: 青色 #00D4FF @85%
      - bordercolor: 深蓝 #0066FF @60%(描边粗 2 像素,产生"立体光晕"感)
      - shadowcolor: 青色 #00FFFF @40%(右下偏移 2/2,产生"发光残影"感)
    """
    return (
        f"drawtext="
        f"text='{WATERMARK_TEXT}':"
        f"fontfile='{font_file_bold}':"
        f"fontsize={font_size}:"
        f"fontcolor=0x00D4FF@0.85:"
        f"bordercolor=0x0066FF@0.6:"
        f"borderw=2:"
        f"shadowcolor=0x00FFFF@0.4:"
        f"shadowx=2:shadowy=2:"
        f"x=w-tw-{pad}:y=h-th-{pad}"
    )


def _vf_gold(font_size: int, pad: int, font_file_bold: str) -> str:
    """金色 + 黑色阴影(高级感)。

      - fontcolor: 金色 #FFD700 @90%
      - bordercolor: 黑 @60%(描边 2 像素)
      - shadowcolor: 黑 @50%(右下偏移 1/1,微阴影)
    """
    return (
        f"drawtext="
        f"text='{WATERMARK_TEXT}':"
        f"fontfile='{font_file_bold}':"
        f"fontsize={font_size}:"
        f"fontcolor=0xFFD700@0.9:"
        f"bordercolor=0x000000@0.6:"
        f"borderw=2:"
        f"shadowcolor=0x000000@0.5:"
        f"shadowx=1:shadowy=1:"
        f"x=w-tw-{pad}:y=h-th-{pad}"
    )


def _vf_logo(font_size: int, pad: int, font_file: str) -> str:
    """黑底白字 logo 风(Apple 风)。

    drawtext 自带 box=1 参数 → 白字 + 黑色半透明背景,自动包裹文字。
    boxborderw 让黑底比文字外扩(每边),实现"圆角矩形"视觉(无圆角但够紧凑)。
      - fontcolor: 白 @90%
      - box: 1(启用)
      - boxcolor: 黑 @70%
      - boxborderw: max(6, font_size//3)(背景内边距,跟随字号)
    """
    box_pad = max(6, font_size // 3)
    return (
        f"drawtext="
        f"text='{WATERMARK_TEXT}':"
        f"fontfile='{font_file}':"
        f"fontsize={font_size}:"
        f"fontcolor=white@0.9:"
        f"box=1:"
        f"boxcolor=0x000000@0.7:"
        f"boxborderw={box_pad}:"
        f"x=w-tw-{pad + box_pad}:y=h-th-{pad + box_pad}"
    )


def _build_filter(style: WatermarkStyle, font_size: int, pad: int) -> tuple[str, str]:
    """根据 style 返 (filter_string, font_file_used)。"""
    if style == "simple":
        ff = _resolve_font_file("bold")    # 六审升级 Regular → Bold
        return _vf_simple(font_size, pad, ff), ff
    if style == "futuristic":
        ff = _resolve_font_file("bold")
        return _vf_futuristic(font_size, pad, ff), ff
    if style == "gold":
        ff = _resolve_font_file("bold")
        return _vf_gold(font_size, pad, ff), ff
    if style == "logo":
        ff = _resolve_font_file("regular")
        return _vf_logo(font_size, pad, ff), ff
    raise ValueError(f"未知 style:{style!r}(允许 simple|futuristic|gold|logo)")


# ─── 入口函数 ────────────────────────────────────────────────────────────

async def add_watermark(
    input_path: str,
    output_path: str,
    style: WatermarkStyle = DEFAULT_STYLE,
) -> dict:
    """给视频右下角加水印。

    Args:
        input_path:  原始视频本地路径(无水印)
        output_path: 加完水印的输出路径(覆写已存在)
        style:       水印风格,默认 futuristic(用户偏好)

    Returns:
        {"output_path", "style", "font_size", "font_file", "vf", "elapsed_sec"}
    """
    short_side = await _ffprobe_short_side(input_path)
    # 法规要求"≥3%",必须向上取整(只增不减)
    font_size = max(8, math.ceil(short_side * WATERMARK_FONT_SIZE_PCT))
    # 边距固定 10px(六审最终决议),不再随字号 scale
    pad = WATERMARK_EDGE_PAD

    vf, font_file = _build_filter(style, font_size, pad)

    # 2026-05-13 修:原 `-an` 会把音频砍掉。但此函数运行时输入已是 concat 完的成片
    # (含音轨,_build_segment_clip 提前 mux 过),用 `-c:a copy` 透传音频。
    # 注:`-c:a copy` 在无音频流的输入上不会失败(ffmpeg 没找到 a 流就跳过)。
    t0 = time.time()
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    elapsed = time.time() - t0
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 加水印失败({style}):{stderr.decode(errors='replace')[-2000:]}")
    return {
        "output_path": output_path,
        "style": style,
        "font_size": font_size,
        "font_file": font_file,
        "vf": vf,
        "elapsed_sec": round(elapsed, 2),
    }


async def emit_dual_versions(
    raw_input_path: str,
    archive_dir: str,
    job_id: str,
    style: WatermarkStyle = DEFAULT_STYLE,
) -> tuple[str, str]:
    """给一段视频同时输出双版本(合规版 + 无标识版)。

    Args:
        raw_input_path: 拼接完成的成片本地路径(无水印)
        archive_dir:    归档目录
        job_id:         任务 ID
        style:          水印风格,默认 futuristic

    Returns:
        (watermarked_local_path, raw_local_path)
    """
    Path(archive_dir).mkdir(parents=True, exist_ok=True)

    raw_out = os.path.join(archive_dir, f"{job_id}_raw.mp4")
    wm_out  = os.path.join(archive_dir, f"{job_id}_watermarked.mp4")

    # raw 版:直接拷贝(不重编码,保留 fal 原始画质)
    shutil.copy2(raw_input_path, raw_out)

    # watermarked 版:按 style 加滤镜
    await add_watermark(raw_input_path, wm_out, style=style)

    return wm_out, raw_out
