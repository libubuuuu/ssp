"""P221 视频复刻 V2 — 双版本归档

每个 V2 任务完成后,把 fal.media URL 下载到本地 + 加水印生成双版本:
- {job_id}_raw.mp4         无标识版(直接归档,保留隐式 metadata)
- {job_id}_watermarked.mp4 合规版(加 simple 风格水印)

生成后上传 COS(key: video_clone_v2/{job_id}/{filename}),删除本地文件。
COS 上传失败时 fallback 到本地 /uploads/ URL(不影响任务完成)。

详见 docs/P221-API-SCHEMA.md(v4)§6.3。
"""
from __future__ import annotations
import asyncio
import os
import shutil
from pathlib import Path
from typing import Dict, Optional

import httpx

from .logger import log_info, log_error
from .video_clone_v2_watermark import emit_dual_versions, DEFAULT_STYLE


V2_UPLOADS_ROOT = Path("/opt/ssp/uploads/video_clone_v2")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://ailixiao.com").rstrip("/")
# fal.media 大视频:分类超时,read 放宽到 180s(CDN 抖动时单次读会很慢)
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=30.0, read=180.0, write=60.0, pool=30.0)
DOWNLOAD_MAX_RETRIES = 3  # fal CDN 瞬时抖动靠重试自愈,不让搬运失败整单退款空跑


async def _download_to_local(url: str, dest_path: Path) -> int:
    """流式下载 url 到 dest_path,返字节数。

    fal CDN 大视频下载常瞬时抖动(ReadTimeout/连接重置),此前零重试 → 上游
    生成成功+已扣费却因搬运失败整单退款空跑(2026-06-14 job 9473d90b)。
    网络类错误指数退避重试 DOWNLOAD_MAX_RETRIES 次;非 200/非网络错误直接抛。
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    last_exc: Exception | None = None
    for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
        total = 0
        try:
            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        # 非 200(403/404 等)重试无用,直接抛
                        raise RuntimeError(
                            f"download_to_local 失败:status={resp.status_code} url={url[:120]}"
                        )
                    with dest_path.open("wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                            total += len(chunk)
                            f.write(chunk)
            os.chmod(dest_path, 0o644)
            return total
        except httpx.HTTPError as e:
            # 仅网络/超时类错误重试,清掉半截文件避免污染
            last_exc = e
            try:
                dest_path.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt < DOWNLOAD_MAX_RETRIES:
                backoff = 2 ** attempt  # 2s, 4s
                log_error(
                    f"video_clone_v2 download attempt {attempt}/{DOWNLOAD_MAX_RETRIES} "
                    f"失败({type(e).__name__}: {e}),{backoff}s 后重试 url={url[:80]}"
                )
                await asyncio.sleep(backoff)
    raise RuntimeError(
        f"download_to_local 重试 {DOWNLOAD_MAX_RETRIES} 次仍失败: {last_exc}"
    ) from last_exc


async def upload_v2_file_to_cos(local_path: str, job_id: str) -> Optional[str]:
    """上传单个本地 mp4 到 COS，删除本地文件，返回永久 COS 地址（不含签名）。失败返回 None。

    返回格式: https://{bucket}.cos.{region}.myqcloud.com/video_clone_v2/{job_id}/{filename}
    查询时由 regenerate_cos_url() 实时签名，DB 里存的地址永不过期。
    """
    filename = os.path.basename(local_path)
    cos_key = f"video_clone_v2/{job_id}/{filename}"

    def _sync_upload() -> str:
        from .cos_upload import _make_client
        client, bucket = _make_client()
        region = os.environ.get("STORAGE_REGION", "ap-guangzhou")
        with open(local_path, "rb") as f:
            client.put_object(Bucket=bucket, Body=f, Key=cos_key)
        return f"https://{bucket}.cos.{region}.myqcloud.com/{cos_key}"

    try:
        url = await asyncio.get_event_loop().run_in_executor(None, _sync_upload)
        try:
            os.unlink(local_path)
        except OSError:
            pass
        log_info(f"video_clone_v2 COS 上传成功: {filename}")
        return url
    except Exception as e:
        log_error(f"video_clone_v2 COS 上传失败 {filename}: {e}(fallback 本地)")
        return None


async def archive_dual_versions(
    fal_video_url: str,
    job_id: str,
    style: str = DEFAULT_STYLE,
) -> Dict[str, str]:
    """把 fal 输出视频归档成双版本，上传 COS，删除本地文件。

    流程:
        1. 下载 fal.media 到本地临时目录
        2. emit_dual_versions 生成 raw + watermarked
        3. 并发上传两个文件到 COS，删本地
        4. COS 失败时 fallback 本地 /uploads/ URL

    Returns:
        {
            "raw_local_path":         None(已删)/本地路径(fallback),
            "watermarked_local_path": None(已删)/本地路径(fallback),
            "raw_url":                COS 预签名 URL 或本地 URL,
            "watermarked_url":        COS 预签名 URL 或本地 URL,
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

    # 并发上传两个文件到 COS
    raw_cos_url, wm_cos_url = await asyncio.gather(
        upload_v2_file_to_cos(raw_local, job_id),
        upload_v2_file_to_cos(wm_local, job_id),
    )

    # fallback:COS 失败时用本地 URL(文件未被删除)
    raw_url = raw_cos_url or f"{PUBLIC_BASE_URL}/uploads/video_clone_v2/{job_id}/{os.path.basename(raw_local)}"
    wm_url  = wm_cos_url  or f"{PUBLIC_BASE_URL}/uploads/video_clone_v2/{job_id}/{os.path.basename(wm_local)}"

    # COS 成功时本地文件已删，尝试清理空目录
    if raw_cos_url and wm_cos_url:
        try:
            job_dir.rmdir()
        except OSError:
            pass

    log_info(f"video_clone_v2 archive ok:job_id={job_id} wm={wm_url[:80]}")
    return {
        "raw_local_path":         None if raw_cos_url else raw_local,
        "watermarked_local_path": None if wm_cos_url else wm_local,
        "raw_url":                raw_url,
        "watermarked_url":        wm_url,
    }
