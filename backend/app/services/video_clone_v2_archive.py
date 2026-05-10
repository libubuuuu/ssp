"""P221 视频复刻 V2 — 双版本归档

每个 V2 任务完成后,把 fal.media URL 下载到本地 + 加水印生成双版本:
- {job_id}_raw.mp4         无标识版(直接归档,保留隐式 metadata)
- {job_id}_watermarked.mp4 合规版(加 simple 风格水印)

详见 docs/P221-API-SCHEMA.md(v4)§6.3。
"""
from __future__ import annotations
import asyncio
import os
import shutil
from pathlib import Path
from typing import Dict

import httpx

from .logger import log_info, log_error
from .video_clone_v2_watermark import emit_dual_versions, DEFAULT_STYLE


V2_UPLOADS_ROOT = Path("/opt/ssp/uploads/video_clone_v2")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://ailixiao.com").rstrip("/")
DOWNLOAD_TIMEOUT = 120  # fal.media 大视频留足下载窗口


async def _download_to_local(url: str, dest_path: Path) -> int:
    """流式下载 url 到 dest_path,返字节数。失败抛 RuntimeError。"""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise RuntimeError(
                    f"download_to_local 失败:status={resp.status_code} url={url[:120]}"
                )
            with dest_path.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    total += len(chunk)
                    f.write(chunk)
    os.chmod(dest_path, 0o644)
    return total


async def archive_dual_versions(
    fal_video_url: str,
    job_id: str,
    style: str = DEFAULT_STYLE,
) -> Dict[str, str]:
    """把 fal 输出视频归档成双版本。

    流程:
        1. 下载 fal.media 到本地 /opt/ssp/uploads/video_clone_v2/{job_id}/{job_id}_source.mp4
        2. 调 emit_dual_versions(...) 生成 raw + watermarked
        3. raw 是 source 的拷贝(保留 fal 原始画质);watermarked 加水印
        4. 删 source 临时文件(已被 raw 拷贝)

    Args:
        fal_video_url: fal.media 上的视频 URL
        job_id:        任务 ID(uuid)
        style:         水印风格,默认 simple(六审决议)

    Returns:
        {
            "raw_local_path":         "/opt/ssp/uploads/.../{job_id}_raw.mp4",
            "watermarked_local_path": "/opt/ssp/uploads/.../{job_id}_watermarked.mp4",
            "raw_url":                "https://ailixiao.com/uploads/video_clone_v2/.../{job_id}_raw.mp4",
            "watermarked_url":        "https://ailixiao.com/uploads/video_clone_v2/.../{job_id}_watermarked.mp4",
        }
    Raises:
        RuntimeError: 下载失败 / 水印加盖失败
    """
    job_dir = V2_UPLOADS_ROOT / job_id
    source_path = job_dir / f"{job_id}_source.mp4"

    log_info(f"video_clone_v2 archive 开始:job_id={job_id} fal_url={fal_video_url[:80]}")
    size = await _download_to_local(fal_video_url, source_path)
    log_info(f"video_clone_v2 download ok:job_id={job_id} size={size}")

    try:
        wm_local, raw_local = await emit_dual_versions(
            str(source_path), str(job_dir), job_id, style=style
        )
    except Exception as e:
        log_error(f"video_clone_v2 emit_dual_versions 失败 job_id={job_id}: {e}")
        raise

    # source 已被 raw 拷贝,删临时
    try:
        source_path.unlink(missing_ok=True)
    except OSError:
        pass

    # owner 设为 ssp-app(防 root 跑出来 nginx 读取异常)
    for p in (raw_local, wm_local):
        try:
            shutil.chown(p, user="ssp-app", group="ssp-app")
        except (LookupError, PermissionError):
            pass  # 测试环境无 ssp-app 用户/无权限,跳过

    raw_url = f"{PUBLIC_BASE_URL}/uploads/video_clone_v2/{job_id}/{os.path.basename(raw_local)}"
    wm_url  = f"{PUBLIC_BASE_URL}/uploads/video_clone_v2/{job_id}/{os.path.basename(wm_local)}"

    log_info(f"video_clone_v2 archive ok:job_id={job_id} wm={wm_url} raw={raw_url}")
    return {
        "raw_local_path":         raw_local,
        "watermarked_local_path": wm_local,
        "raw_url":                raw_url,
        "watermarked_url":        wm_url,
    }
