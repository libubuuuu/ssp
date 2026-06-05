"""P221 视频复刻 V2 — 任务调度 + aiview 调用 + 重试 + 保险 + 多段拼接

A2 范围:type=single 单段路径(已上线)
B 阶段:type=ultimate 多段并发 + 失败段跳过+按段退款 + ffmpeg 拼接 + 音轨方案 B

详见 docs/P221-API-SCHEMA.md(v4)§5。
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import get_settings
from ..database import get_db
from .billing import add_credits
from .cos_upload import upload_to_cos
from .logger import log_info, log_error
from .video_clone_v2_archive import archive_dual_versions
from .video_clone_v2_pricing import (
    CREDITS_PER_SEC,
    SEGMENT_INPUT_SECONDS_MAX,
    ROLE_TO_AT_LABEL,
    build_prompt,
    calc_segment_credits,
    rate_for_model,
    sha256_file,
    sha256_url_first8,
)


# aiview Seedance 成本估算(fal billing CSV 实证,aiview 计费结构相近):
# 暂用 worst-case 估算:$0.962/段(8s × $0.0925 × 1.3),偏高但安全。
_COST_ESTIMATE_PER_SEC = 0.0925
_COST_ESTIMATE_OVERRATE = 1.3


def stable_seed(job_id: str) -> int:
    """同 job 所有段共用 seed,保持视觉一致性(用户最终决议)。"""
    return int(hashlib.sha256(job_id.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF


def _compute_keep_ranges(
    drops: List[tuple], full_duration: float
) -> List[tuple]:
    """drop 区间数组 → 保留区间补集(去重叠 + 排序)。

    例:
      drops=[(5,7),(12,13)],full=18 → keeps=[(0,5),(7,12),(13,18)]
      drops=[(0,2)],full=18         → keeps=[(2,18)]
      drops=[],full=18              → keeps=[(0,18)]
    """
    if not drops:
        return [(0.0, full_duration)]
    sorted_drops = sorted([
        (max(0.0, float(s)), min(full_duration, float(e)))
        for s, e in drops
        if float(e) > float(s)
    ])
    # 合并重叠的 drop(防止重复扣除)
    merged: List[tuple] = []
    for s, e in sorted_drops:
        if merged and s <= merged[-1][1] + 0.01:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    # 求补集:[0, full] - merged drops
    keeps: List[tuple] = []
    cur = 0.0
    for s, e in merged:
        if s - cur > 0.05:
            keeps.append((cur, s))
        cur = e
    if full_duration - cur > 0.05:
        keeps.append((cur, full_duration))
    return keeps


# ─── ffmpeg 切片 ────────────────────────────────────────────────────────

async def _ffprobe_duration(path: str) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return float(stdout.decode().strip())


async def _ffmpeg(args: List[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败:{stderr.decode(errors='replace')[-1500:]}")


async def split_input_video(
    input_local_path: str,
    plan: List[Dict[str, Any]],
    work_dir: str,
) -> List[str]:
    """按 plan 切分视频(每段先尝试无损 -c copy,失败 / 段太短回退重编码)。

    ⭐ 红线 1:每段切完算 SHA256,有 collision 立即 raise ValueError 防止"切出 N 段相同视频"。
       触发场景:plan 里两段 start+duration 完全重合 / ffmpeg 异常输出空文件 / 视频流问题等。

    Returns:
        [seg_file_path_0, seg_file_path_1, ...]  按 idx 顺序
    Raises:
        ValueError: 段间出现 hash collision(理论上不该发生)
    """
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    seg_files: List[str] = []
    for seg in plan:
        idx = seg["idx"]
        out = os.path.join(work_dir, f"seg_{idx}.mp4")
        # 2026-05-13 修:不再用 -c copy 第一遍试。`-c copy` 不能从非关键帧切,
        # ffmpeg 会对齐到关键帧,但 PTS 保留原值,fal 收到 video 从 3.7s 开始的怪
        # 文件(audio 在 0s)。memory feedback_ssp_ffmpeg_no_copy_first_segment.md
        # 同类雷已踩。直接重编码,精确到帧。
        # `-ss` 放在 `-i` 后 = 输出端 seek,decoder 解码到位再裁,逐帧精确。
        # 加 `-avoid_negative_ts make_zero` + `-reset_timestamps` 强制 PTS 从 0 开始。
        await _ffmpeg([
            "-i", input_local_path,
            "-ss", str(seg["start"]),
            "-t", str(seg["duration"]),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-avoid_negative_ts", "make_zero",
            "-reset_timestamps", "1",
            "-movflags", "+faststart",
            out,
        ])
        seg_files.append(out)

    # ⭐ 红线 1:hash 校验(单段也跑,记日志便于审计)
    if len(seg_files) > 1:
        hashes = [sha256_file(f) for f in seg_files]
        log_info(
            f"[V2-SPLIT] segments={len(seg_files)} "
            f"hashes={[h[:8] for h in hashes]}"
        )
        if len(set(hashes)) != len(hashes):
            log_error(
                f"[V2-SPLIT-COLLISION] 切片产生重复段!file_paths={seg_files} "
                f"hashes={hashes}"
            )
            raise ValueError(
                f"segments hash collision detected: "
                f"{[(f, h[:8]) for f, h in zip(seg_files, hashes)]}"
            )

    return seg_files


async def prepare_segment_input(seg_file: str, plan_item: Dict[str, Any]) -> str:
    """ai 段输入直接用 split 出的整段。

    ⚠ 仅 source_type='ai' 段调用本函数(original 段在 processor 直接 short-circuit)。

    2026-05-10 改:不再做截短 / padding。
    - fal seedance fast/r2v 端点接受 duration ∈ {'auto', '4'-'15'},input < output 时
      模型 hallucinate 后段 → reference 漂移
    - input = output = 段实际秒数,全程都有原视频指导,reference 不漂
    - plan_segments_v2 已保证所有段 ≥ 4s(末段 < 4s 并到前段),不存在 padding 必要

    Returns:
        本地视频路径(等于 seg_file)
    """
    assert plan_item.get("source_type") == "ai", "original 段不应进 prepare_segment_input"
    return seg_file



_AIVIEW_RATIO_OK = {"16:9", "9:16", "1:1", "4:3", "3:4"}
# 角色图固定排序(产品→人物→场景→其它),决定 @imageN 的编号
_AIVIEW_ROLE_ORDER = ("product", "person", "scene", "reference")


def _aiview_order_and_refs(image_urls: List[Dict[str, Any]]):
    """把角色图按固定顺序排好,返回:
       (ordered_urls, ref_map)
    ordered_urls: 传给 aiview image_urls 的有序 URL 列表
    ref_map:      {"@产品1": "@image1", "@人物1": "@image2", ...} 用于改写用户提示词
    Seedance 的 @imageN 靠"顺序"对应,所以编号 = 在 ordered_urls 里的位置。
    """
    grouped: Dict[str, list] = {}
    for img in image_urls:
        role = img.get("role") or "reference"
        grouped.setdefault(role, []).append(img)
    ordered_urls: List[str] = []
    ref_map: Dict[str, str] = {}
    roles_seen = list(_AIVIEW_ROLE_ORDER) + [r for r in grouped if r not in _AIVIEW_ROLE_ORDER]
    for role in roles_seen:
        label = ROLE_TO_AT_LABEL.get(role, "图")
        for i, img in enumerate(grouped.get(role, []), start=1):
            ordered_urls.append(img["url"])
            ref_map[f"@{label}{i}"] = f"@image{len(ordered_urls)}"
    return ordered_urls, ref_map


def _rewrite_prompt_for_aiview(prompt: str, ref_map: Dict[str, str]) -> str:
    """把用户提示词里的中文 @ 引用(@产品1 / @视频1 等)改写成 Seedance 认的英文 @imageN / @video1。
    按 key 长度降序替换,避免 @产品10 被 @产品1 提前截断。
    """
    out = prompt or ""
    # 原视频 = @video1(用户可能写 @视频1 / 视频1)
    out = out.replace("@视频1", "@video1").replace("@视频 1", "@video1")
    for old in sorted(ref_map, key=len, reverse=True):
        out = out.replace(old, ref_map[old])
    return out


async def call_aiview_seedance(
    video_url: str,
    image_urls: List[str],
    prompt_compiled: str,
    seed: int,
    aspect_ratio: str = "auto",
    *,
    job_id: str = "",
    seg_idx: int = -1,
    input_duration_sec: float = 8.0,
    model: str = "",
) -> Dict[str, Any]:
    """走第三方 aiview.club 的 Seedance 参考视频复刻(异步 提交→轮询)。
    返回结构:{"video_url", "actual_cost_usd", "raw_response"}。
    """
    from app.services.aiview_service import get_aiview_image_service
    service = get_aiview_image_service()
    if not service.is_available():
        raise RuntimeError("aiview 未配置(缺 AppKey/AppSecret)")

    input_hash = sha256_url_first8(video_url)
    out_dur = max(4, min(15, math.ceil(input_duration_sec)))
    ratio = aspect_ratio if aspect_ratio in _AIVIEW_RATIO_OK else "adaptive"
    # 视频复刻只用 2.0 / 2.0-fast(支持参考视频+多图);其它(如 1.5 Pro 不支持参考视频)强制回退
    # 优先用本单【用户所选模型】:① 显式传入 → ② 按 job_id 查库 → ③ 回落 .env 默认
    if not model and job_id:
        try:
            from app.database import get_db
            with get_db() as _c:
                _row = _c.execute(
                    "SELECT video_model FROM video_clone_v2_jobs WHERE id = ?", (job_id,)
                ).fetchone()
            if _row and _row[0]:
                model = _row[0]
        except Exception:
            pass
    model = (model or get_settings().AIVIEW_VIDEO_MODEL or "seedance-2-0-fast").lower()
    if model not in ("seedance-2-0", "seedance-2-0-fast"):
        model = "seedance-2-0-fast"
    # 口播模式:提示词含「【口播】」= 用户要换台词 → 用完整版 2.0(口型/配音最好)+ 生成新配音。
    # 普通复刻 generate_audio=False(保留原声);口播 generate_audio=True,
    # _build_segment_clip 检测到模型自带音轨会自动保留(即新台词的声音,不会被原声覆盖)。
    is_speech = "【口播】" in (prompt_compiled or "")
    if is_speech:
        model = "seedance-2-0"
    # 2026-06-01:普通复刻也开模型配音(用户要"模型生成的声音",不要贴原声)。
    # 2026-06-01:普通复刻开模型配音;aiview 不做音频内容审核,不触发整段失败。
    # 兜底:万一模型某次没出音轨,_build_segment_clip 仍会 mux 原声,保证成片不哑。
    gen_audio = True
    log_info(
        f"[V2-AIVIEW] job={job_id} seg={seg_idx} input_hash={input_hash} "
        f"prompt={prompt_compiled[:50]!r} seed={seed} model={model} "
        f"input_dur={input_duration_sec:.1f}s out_dur={out_dur} ratio={ratio}"
    )
    sub = await service.submit_video(
        reference_video_url=video_url,
        image_urls=image_urls,
        dynamic_prompt=prompt_compiled,
        input_video_duration=round(input_duration_sec),
        duration=out_dur,
        model=model,
        resolution="480p",
        ratio=ratio,
        seed=seed,
        generate_audio=gen_audio,  # 口播=True(模型出新配音) / 普通复刻=False(保留原声)
    )
    if sub.get("error"):
        raise RuntimeError(f"aiview 视频提交失败:{sub['error']}")
    rid = sub["request_id"]
    # 轮询最多 120 次 × 5s = 10 分钟(与 FAL 单段 300s 同量级,留足余量)
    for _ in range(120):
        await asyncio.sleep(5)
        st = await service.query_video(rid)
        if st.get("status") == "completed" and st.get("video_url"):
            out_url = st["video_url"]
            log_info(
                f"[V2-AIVIEW-OK] job={job_id} seg={seg_idx} "
                f"input_hash={input_hash} → output={out_url[:80]}"
            )
            return {"video_url": out_url, "actual_cost_usd": None, "raw_response": st.get("raw") or {}}
        if st.get("status") == "failed":
            raise RuntimeError(f"aiview 视频失败:{st.get('error', '未知')}")
    raise RuntimeError("aiview 视频超时(>10min)")


def _estimate_cost_usd(duration_sec: float = SEGMENT_INPUT_SECONDS_MAX) -> float:
    """aiview 不返 cost 时按实际段长估算。校准基线:8s@480p ≈ $0.962(2026-05-13 billing CSV 实证)。"""
    return duration_sec * _COST_ESTIMATE_PER_SEC * _COST_ESTIMATE_OVERRATE


# ─── 单段调度(A2 范围)─────────────────────────────────────────────────

async def _run_one_ai_segment(
    seg_file: str,
    plan_item: Dict[str, Any],
    image_urls: List[Dict[str, Any]],
    prompt_compiled: str,
    seed: int,
    aspect_ratio: str,
    job_id: str = "",
    retry: int = 0,
) -> Dict[str, Any]:
    """跑一段 ai:截输入 → 上传 fal → 调 fal → 返结果。

    Returns:
        {"idx", "source_type":"ai", "status":"completed"/"failed",
         "fal_request_id"(可能 None), "output_url"(成功才有),
         "retry_count", "actual_cost_usd", "error"}
    """
    idx = plan_item["idx"]
    try:
        async with _seg_lock(job_id):
            _db_update_segment_stage(job_id, idx, "uploading")
        prepared = await prepare_segment_input(seg_file, plan_item)
        # aiview 是国内服务，段文件必须传 COS（国内）
        input_url = await asyncio.to_thread(upload_to_cos, prepared)

        async with _seg_lock(job_id):
            _db_update_segment_stage(job_id, idx, "ai_processing")
        # 段实际秒数 → 四舍五入整秒（≥0.5进位），与 aiview out_dur 和扣费口径统一
        prepared_dur = await _ffprobe_duration(prepared)
        billed_dur = max(4, min(15, math.ceil(prepared_dur)))
        fal_called_at = time.strftime("%Y-%m-%d %H:%M:%S")
        # Seedance @imageN 靠顺序对应:按角色固定排序图,并把提示词里的
        # @产品1/@人物1/@视频1 改写成 @image1/@image2/@video1
        ordered_urls, _ref_map = _aiview_order_and_refs(image_urls)
        aiview_prompt = _rewrite_prompt_for_aiview(prompt_compiled, _ref_map)
        result = await call_aiview_seedance(
            input_url, ordered_urls, aiview_prompt, seed, aspect_ratio,
            job_id=job_id, seg_idx=idx,
            input_duration_sec=billed_dur,
        )
        actual_cost = result["actual_cost_usd"]
        if actual_cost is None:
            actual_cost = _estimate_cost_usd(billed_dur)
        return {
            "idx": idx,
            "source_type": "ai",
            "status": "completed",
            "stage": "completed",
            "fal_called_at": fal_called_at,
            "fal_request_id": (result["raw_response"] or {}).get("request_id"),
            "output_url": result["video_url"],
            "retry_count": retry,
            "actual_cost_usd": actual_cost,
            "error": None,
        }
    except Exception as e:
        return {
            "idx": idx,
            "source_type": "ai",
            "status": "failed",
            "stage": "failed",
            "fal_request_id": None,
            "output_url": None,
            "retry_count": retry,
            "actual_cost_usd": 0.0,
            "error": str(e)[:500],
        }


async def run_single_ai_segment_with_retry(
    seg_file: str,
    plan_item: Dict[str, Any],
    image_urls: List[Dict[str, Any]],
    prompt_compiled: str,
    seed: int,
    aspect_ratio: str,
    job_id: str = "",
) -> Dict[str, Any]:
    """跑一段 ai + 失败最多 2 次重试(共 3 次机会),每次换 seed。
    2026-05-13:1 次重试 → 2 次重试。
      fal 内容策略 stochastic + 服务偶发抖动,3 次机会把单段失败率从 ~5-10%
      压到 ~0.1-1%。整片(8 段)成功率 ~99.2-99.9%。
      重试只发生在失败时,正常成功的段无额外成本。"""
    MAX_ATTEMPTS = 3
    last_result: Dict[str, Any] = {}
    for attempt in range(MAX_ATTEMPTS):
        attempt_seed = seed + attempt  # seed, seed+1, seed+2
        last_result = await _run_one_ai_segment(
            seg_file, plan_item, image_urls, prompt_compiled, attempt_seed, aspect_ratio,
            job_id=job_id, retry=attempt,
        )
        if last_result["status"] != "failed":
            if attempt > 0:
                log_info(
                    f"video_clone_v2 segment idx={plan_item['idx']} 第 {attempt + 1} 次尝试成功"
                    f"(seed={attempt_seed})"
                )
            return last_result
        if attempt < MAX_ATTEMPTS - 1:
            next_seed = seed + attempt + 1
            log_info(
                f"video_clone_v2 segment idx={plan_item['idx']} attempt {attempt + 1}/{MAX_ATTEMPTS} "
                f"失败,换 seed={next_seed} 继续重试:{(last_result.get('error') or '')[:200]}"
            )
    log_info(
        f"video_clone_v2 segment idx={plan_item['idx']} {MAX_ATTEMPTS} 次都失败,放弃"
    )
    return last_result


# ─── DB 操作(单段路径)────────────────────────────────────────────────

def _db_load_job(job_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM video_clone_v2_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


# asyncio lock per job — ultimate 路径多段并发写 segments_results 时保证原子性
_seg_locks: Dict[str, asyncio.Lock] = {}


def _seg_lock(job_id: str) -> asyncio.Lock:
    if job_id not in _seg_locks:
        _seg_locks[job_id] = asyncio.Lock()
    return _seg_locks[job_id]


def _db_update_segment_stage(job_id: str, idx: int, stage: str) -> None:
    """Read-modify-write: 更新单段 stage 字段到 segments_results。调用方持锁。"""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with get_db() as conn:
        row = conn.execute(
            "SELECT segments_results FROM video_clone_v2_jobs WHERE id = ?",
            (job_id,)
        ).fetchone()
        if not row:
            return
        results = json.loads((row["segments_results"] or "[]"))
        found = False
        for r in results:
            if r.get("idx") == idx:
                r["stage"] = stage
                r["stage_updated_at"] = now
                found = True
                break
        if not found:
            results.append({
                "idx": idx, "source_type": "ai", "status": "processing",
                "stage": stage, "started_at": now, "stage_updated_at": now,
                "fal_request_id": None, "output_url": None,
                "retry_count": 0, "actual_cost_usd": 0.0, "error": None,
            })
        conn.execute(
            "UPDATE video_clone_v2_jobs SET segments_results = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(results, ensure_ascii=False), job_id)
        )
        conn.commit()


def _db_update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    set_clause += ", updated_at = CURRENT_TIMESTAMP"
    values = list(fields.values()) + [job_id]
    with get_db() as conn:
        conn.execute(
            f"UPDATE video_clone_v2_jobs SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()


# ─── 主入口:process_v2_job(单段优先)──────────────────────────────────

async def process_v2_job(job_id: str) -> None:
    """异步 worker:跑一个 V2 任务从 split → fal → archive 全链路。

    支持 type=single(单段)+ type=ultimate(多段并发拼接)。
    """
    try:
        await _process_v2_job_inner(job_id)
    except BaseException as e:
        # BaseException 捕获 Exception + CancelledError 等,防 job 永远卡在 processing
        from app.services.logger import log_error as _le
        _le(f"process_v2_job 未捕获异常 job_id={job_id}: {type(e).__name__}: {e}")
        try:
            job = _db_load_job(job_id)
            if job and job.get("status") == "processing":
                _db_update_job(job_id, status="failed", error_step="unexpected",
                               error_message=f"任务中断:{type(e).__name__}:{str(e)[:200]}")
                await _refund_full(job)
        except Exception:
            pass
        # CancelledError 必须 re-raise，否则 asyncio 无法正常取消任务
        if isinstance(e, asyncio.CancelledError):
            raise
    finally:
        _seg_locks.pop(job_id, None)  # 释放 lock,防内存泄漏
        work_dir = f"/tmp/video_clone_v2_work/{job_id}"
        shutil.rmtree(work_dir, ignore_errors=True)


async def _process_v2_job_inner(job_id: str) -> None:
    job = _db_load_job(job_id)
    if not job:
        log_error(f"process_v2_job 找不到 job_id={job_id}")
        return

    log_info(f"video_clone_v2 process 开始:job_id={job_id} type={job['type']}")

    plan = json.loads(job["segments_plan"])

    if job["type"] == "single":
        if len(plan) != 1:
            log_error(f"single 模式 plan 长度异常:{len(plan)}")
            _db_update_job(job_id, status="failed", error_step="bad_plan",
                           error_message=f"single plan len={len(plan)}")
            return
    elif job["type"] == "ultimate":
        if not (1 <= len(plan) <= 8):
            log_error(f"ultimate plan 长度异常:{len(plan)}")
            _db_update_job(job_id, status="failed", error_step="bad_plan",
                           error_message=f"ultimate plan len={len(plan)}")
            return
    else:
        log_error(f"process_v2_job 不支持 type={job['type']}")
        _db_update_job(job_id, status="failed", error_step="type_not_supported",
                       error_message=f"未知 type={job['type']}")
        return

    seed = stable_seed(job_id)

    # 1. 下载用户输入视频到本地(fal storage URL 已在 create 入口校验)
    settings = get_settings()
    work_dir = f"/tmp/video_clone_v2_work/{job_id}"
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    input_video_url = job["input_video_url"]
    input_full = os.path.join(work_dir, "input_full.mp4")

    import httpx
    _DOWNLOAD_DELAYS = [5, 15, 40]  # 3 次重试间隔(秒)
    last_download_err: str = ""
    for _attempt, _delay in enumerate([0] + _DOWNLOAD_DELAYS):
        if _delay:
            log_info(f"download input retry attempt={_attempt} delay={_delay}s job_id={job_id}")
            await asyncio.sleep(_delay)
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                async with client.stream("GET", input_video_url) as resp:
                    if resp.status_code != 200:
                        raise RuntimeError(f"下载用户输入失败:status={resp.status_code}")
                    with open(input_full, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=64*1024):
                            f.write(chunk)
            last_download_err = ""
            break  # 成功
        except Exception as e:
            err_str = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            last_download_err = err_str
            log_error(f"download input failed attempt={_attempt} job_id={job_id}: {err_str}")
            if isinstance(e, RuntimeError) and "status=" in str(e):
                break  # 4xx/5xx 不重试(重试也不会变)
    else:
        # 重试耗尽仍失败 — 最后一次的错误已在循环里记录
        pass

    if last_download_err:
        _db_update_job(job_id, status="failed", error_step="download_input",
                       error_message=last_download_err[:500])
        await _refund_full(job)
        return

    # 1.5 应用 trim 丢弃区间(B+ 阶段:支持任意多段非连续 drop)
    #     plan.start 是相对裁剪后 effective 时长 0-based,所以这里先把视频处理好
    full_duration = float(job["input_video_duration_sec"])
    drops: List[tuple] = []

    # 优先从 JSON 读多段
    ranges_json = job.get("trim_drop_ranges_json")
    if ranges_json:
        try:
            raw = json.loads(ranges_json)
            drops = [(float(x[0]), float(x[1])) for x in raw if len(x) == 2 and float(x[1]) > float(x[0])]
        except Exception as e:
            log_error(f"trim_drop_ranges_json 解析失败 job_id={job_id}: {e}")

    # legacy fallback:trim_start/end 当单段 drop
    if not drops:
        ts = float(job.get("trim_start") or 0.0)
        te_raw = job.get("trim_end")
        te = float(te_raw) if te_raw is not None else 0.0
        if te > ts + 0.05:
            drops = [(ts, te)]

    if drops:
        keep_ranges = _compute_keep_ranges(drops, full_duration)
        if not keep_ranges:
            log_error(f"trim drop 覆盖整片 job_id={job_id}")
            _db_update_job(job_id, status="failed", error_step="trim",
                           error_message="trim 区间覆盖整片")
            await _refund_full(job)
            return

        input_local = os.path.join(work_dir, "input.mp4")
        try:
            if len(keep_ranges) == 1:
                # 单段 keep — 直接 slice
                ks, ke = keep_ranges[0]
                await _ffmpeg([
                    "-i", input_full,
                    "-ss", str(ks),
                    "-t", str(ke - ks),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac",
                    input_local,
                ])
            else:
                # 多段 keep — 每段 cut + concat
                clips: List[str] = []
                for i, (ks, ke) in enumerate(keep_ranges):
                    clip = os.path.join(work_dir, f"input_keep_{i}.mp4")
                    await _ffmpeg([
                        "-i", input_full,
                        "-ss", str(ks),
                        "-t", str(ke - ks),
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                        "-c:a", "aac",
                        clip,
                    ])
                    clips.append(clip)
                await _concat_with_demuxer(clips, input_local, work_dir)
            log_info(
                f"[V2-TRIM] job={job_id} drops={drops} keep_ranges={keep_ranges} "
                f"effective={sum(ke-ks for ks,ke in keep_ranges):.2f}s (full={full_duration:.2f}s)"
            )
        except Exception as e:
            log_error(f"trim input failed job_id={job_id}: {e}")
            _db_update_job(job_id, status="failed", error_step="trim",
                           error_message=str(e)[:500])
            await _refund_full(job)
            return
    else:
        input_local = input_full

    # 2. 切片(单段也走,统一接口)
    try:
        seg_files = await split_input_video(input_local, plan, work_dir)
    except Exception as e:
        log_error(f"split failed job_id={job_id}: {e}")
        _db_update_job(job_id, status="failed", error_step="split",
                       error_message=str(e)[:500])
        await _refund_full(job)
        return

    # 3. 跑段
    image_urls_obj = json.loads(job["image_urls"])
    prompt_compiled = job["prompt_compiled"] or job["prompt"]

    # ─── ultimate 路径 ─────────────────────────────
    if job["type"] == "ultimate":
        await _process_ultimate(
            job, plan, seg_files, image_urls_obj, prompt_compiled,
            seed, work_dir, input_local, settings,
        )
        return

    # ─── single 路径 ─────────────────────────────
    seg = plan[0]
    if seg["source_type"] == "original":
        # original 段 — 不调 fal,直接用切出来的本地段
        result = {
            "idx": seg["idx"],
            "source_type": "original",
            "status": "ready",
            "fal_request_id": None,
            "output_url": None,  # original 段在归档时直接读 local_path
            "local_path": seg_files[0],
            "retry_count": 0,
            "actual_cost_usd": 0.0,
            "error": None,
        }
        # original 段 single 模式:整段就是用户原视频,直接用 input_local 归档
        # 但 source 段已经是 input 的子集(如果 plan duration < total),用 seg_files[0] 更准
        archive_source = seg_files[0]
    else:
        # ai 段 — 跑 fal
        aspect_ratio = "auto"  # 2026-05-11 对齐老板 fal playground 成功配方(playground 用 auto;fal 自动检测原视频比例)
        try:
            result = await run_single_ai_segment_with_retry(
                seg_files[0], seg, image_urls_obj, prompt_compiled, seed, aspect_ratio,
                job_id=job_id,
            )
        except Exception as e:
            log_error(f"run segment with retry failed job_id={job_id}: {e}")
            _db_update_job(job_id, status="failed", error_step="aiview",
                           error_message=str(e)[:500])
            await _refund_full(job)
            return

        if result["status"] != "completed":
            # 重试也失败 → 全失败(单段路径就是全失败)
            _db_update_job(
                job_id, status="refunded", error_step="aiview",
                error_message=f"ai 段失败:{result['error'][:300]}",
                segments_results=json.dumps([result], ensure_ascii=False),
            )
            await _refund_full(job)
            return

        # 保险 1 — 单段成本超限 → 全额退 + 报警
        if result["actual_cost_usd"] > settings.VC2_MAX_SEGMENT_COST_USD:
            log_error(
                f"video_clone_v2 保险 1 触发:job_id={job_id} idx={seg['idx']} "
                f"actual_cost_usd={result['actual_cost_usd']} > {settings.VC2_MAX_SEGMENT_COST_USD}"
            )
            _db_update_job(
                job_id, status="failed", error_step="cost_overflow",
                error_message=f"单段成本超限 ${result['actual_cost_usd']:.2f}",
                segments_results=json.dumps([result], ensure_ascii=False),
            )
            await _refund_full(job)
            return

        # ai 段:用 fal 输出 URL 归档
        # archive_dual_versions 接受 URL 不接受 local;但 ai 段 fal 输出要先下回来归档
        archive_source = None  # 用 result.output_url 走 archive_dual_versions

    # 4. 归档(双版本)
    try:
        if seg["source_type"] == "ai":
            # 单段 AI:直接用 aiview 输出归档。generate_audio=True 时模型自带声音,
            # emit_dual_versions 的 -c:a copy 会保留模型声音。声音完全由模型+prompt 决定,
            # 后端不贴原视频原声(用户决议 2026-06-01)。模型没出声 = 成片无声,由 prompt 调。
            urls = await archive_dual_versions(result["output_url"], job_id)
        else:
            # original 段 — 没 fal URL,直接把 seg_files[0] 拷到归档目录 + 加水印
            urls = await _archive_local_dual(archive_source, job_id)
    except Exception as e:
        log_error(f"archive failed job_id={job_id}: {e}")
        _db_update_job(
            job_id, status="failed", error_step="archive",
            error_message=str(e)[:500],
            segments_results=json.dumps([result], ensure_ascii=False),
        )
        # 已 fal 已扣钱,不退(用户已享受 fal 服务,只是归档失败)
        # 实际生产中可以考虑退,但这是边角 case,A2 不做
        return

    # 5. 写日志:fal 实扣
    fal_cost = result.get("actual_cost_usd", 0.0) or 0.0

    # 6. 更新 DB:status=completed
    result["output_url"] = urls["watermarked_url"]   # segments_results 里存合规版 URL
    _db_update_job(
        job_id,
        status="completed",
        final_video_url=urls["watermarked_url"],   # 旧字段:合规版(默认推荐)
        final_video_url_watermarked=urls["watermarked_url"],
        final_video_url_raw=urls["raw_url"],
        final_video_local_path=urls["watermarked_local_path"],
        fal_cost_total_usd=fal_cost,
        completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        segments_results=json.dumps([result], ensure_ascii=False),
    )

    # 7. 保险 3 — 写每日预算
    try:
        await _record_daily_spend(fal_cost)
    except Exception as e:
        log_error(f"record_daily_spend failed:{e}(不影响 job 完成)")

    log_info(
        f"video_clone_v2 process ok:job_id={job_id} fal_cost_usd={fal_cost} "
        f"wm={urls['watermarked_url']} raw={urls['raw_url']}"
    )


async def _archive_local_dual(local_video_path: str, job_id: str) -> Dict[str, str]:
    """source_type=original 走这条 — 不下载 fal,直接归档本地段 + 加水印。"""
    from .video_clone_v2_watermark import emit_dual_versions, DEFAULT_STYLE
    import shutil as _sh

    job_dir = Path("/opt/ssp/uploads/video_clone_v2") / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    wm_local, raw_local = await emit_dual_versions(
        local_video_path, str(job_dir), job_id, style=DEFAULT_STYLE
    )

    for p in (wm_local, raw_local):
        try:
            _sh.chown(p, user="ssp-app", group="ssp-app")
        except (LookupError, PermissionError):
            pass

    base_url = os.environ.get("PUBLIC_BASE_URL", "https://ailixiao.com").rstrip("/")
    return {
        "raw_local_path":         raw_local,
        "watermarked_local_path": wm_local,
        "raw_url":                f"{base_url}/uploads/video_clone_v2/{job_id}/{os.path.basename(raw_local)}",
        "watermarked_url":        f"{base_url}/uploads/video_clone_v2/{job_id}/{os.path.basename(wm_local)}",
    }


# ─── 退款 + 预算 ─────────────────────────────────────────────────────────

async def _refund_full(job: Dict[str, Any]) -> None:
    """全额退款(写 pending_refunds 幂等 + 调 add_credits)。"""
    job_id = job["id"]
    user_id = job["user_id"]
    credits = int(job["total_credits_charged"])
    if credits <= 0:
        return
    with get_db() as conn:
        # pending_refunds 幂等 INSERT(同表已有 PK task_id,重复 INSERT 会报错被吞)
        try:
            conn.execute(
                "INSERT INTO pending_refunds (task_id, user_id, cost, registered_at, refunded) "
                "VALUES (?, ?, ?, strftime('%s','now'), 0)",
                (job_id, user_id, credits),
            )
            conn.commit()
        except Exception:
            pass  # 已存在,继续
        # 检查是否已退过
        row = conn.execute(
            "SELECT refunded FROM pending_refunds WHERE task_id = ?", (job_id,)
        ).fetchone()
        if not row or row[0] == 1:
            log_info(f"refund 已退过或不存在:job_id={job_id}")
            return
    add_credits(user_id, credits, reason="task_refund", ref_id=job_id, module="aiview/seedance-v2")
    with get_db() as conn:
        conn.execute(
            "UPDATE pending_refunds SET refunded = 1 WHERE task_id = ? AND refunded = 0",
            (job_id,),
        )
        conn.execute(
            "UPDATE video_clone_v2_jobs SET total_credits_refunded = ? WHERE id = ?",
            (credits, job_id),
        )
        conn.commit()
    log_info(f"video_clone_v2 refund_full ok:job_id={job_id} credits={credits}")


async def _record_daily_spend(usd: float) -> None:
    """累加当日 fal 花销日志。原保险 3(超日预算自动关 V2)已废,用户要求 V2 永久启用。
    超阈值仍写 ERROR 日志报警,但不再 mutate ENABLE_VIDEO_CLONE_V2。
    保险 1(单段超限)+ 保险 2(单 job 总额)仍在,防止单次跑飞。"""
    if usd <= 0:
        return
    today = time.strftime("%Y-%m-%d")
    settings = get_settings()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO video_clone_v2_daily_budget (date, spent_usd, locked) "
            "VALUES (?, ?, 0) ON CONFLICT(date) DO UPDATE SET "
            "spent_usd = spent_usd + ?, updated_at = CURRENT_TIMESTAMP",
            (today, usd, usd),
        )
        conn.commit()
        row = conn.execute(
            "SELECT spent_usd FROM video_clone_v2_daily_budget WHERE date = ?",
            (today,),
        ).fetchone()
    if row and row[0] >= settings.VC2_DAILY_FAL_BUDGET_USD:
        log_error(
            f"video_clone_v2 日预算超限(仅报警,不再自动关):date={today} "
            f"spent_usd={row[0]} >= {settings.VC2_DAILY_FAL_BUDGET_USD}"
        )


# ════════════════════════════════════════════════════════════════════════
# B 阶段:ultimate 多段路径
# ════════════════════════════════════════════════════════════════════════

async def _refund_partial(job: Dict[str, Any], seg_idx: int, credits: int) -> None:
    """单段失败按段退款(写 pending_refunds 用 sub-key 防重复).

    pending_refunds.task_id = "{job_id}:seg_{idx}" 区分单段 vs 整单退款。
    """
    job_id = job["id"]
    user_id = job["user_id"]
    if credits <= 0:
        return
    sub_key = f"{job_id}:seg_{seg_idx}"
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO pending_refunds (task_id, user_id, cost, registered_at, refunded) "
                "VALUES (?, ?, ?, strftime('%s','now'), 0)",
                (sub_key, user_id, credits),
            )
            conn.commit()
        except Exception:
            pass  # 已写过
        row = conn.execute(
            "SELECT refunded FROM pending_refunds WHERE task_id = ?", (sub_key,)
        ).fetchone()
        if not row or row[0] == 1:
            log_info(f"refund_partial 已退过或不存在:{sub_key}")
            return
    add_credits(user_id, credits, reason="task_partial_refund",
                ref_id=sub_key, module="video/clone-v2")
    with get_db() as conn:
        conn.execute(
            "UPDATE pending_refunds SET refunded = 1 WHERE task_id = ? AND refunded = 0",
            (sub_key,),
        )
        # 累加 jobs 行的 total_credits_refunded
        conn.execute(
            "UPDATE video_clone_v2_jobs "
            "SET total_credits_refunded = total_credits_refunded + ? WHERE id = ?",
            (credits, job_id),
        )
        conn.commit()
    log_info(f"refund_partial ok:{sub_key} credits={credits}")


async def _has_audio_stream(video_path: str) -> bool:
    """ffprobe 看视频是否含音频流(无音频拼接时要补静音)。"""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        video_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return b"audio" in stdout


async def _extract_audio_clip(input_local: str, start: float, duration: float, out_path: str) -> bool:
    """从原视频抽 [start, start+duration] 的音频段。无音频流返 False(由 caller 补静音)。"""
    if not await _has_audio_stream(input_local):
        return False
    await _ffmpeg([
        "-i", input_local,
        "-ss", str(start),
        "-t", str(duration),
        "-vn",
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ])
    return True


async def _gen_silence(duration: float, out_path: str) -> None:
    """生成 duration 秒静音 AAC,采样率 44100 stereo。"""
    await _ffmpeg([
        "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(duration),
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ])


async def _mux_video_audio(video_path: str, audio_path: str, out_path: str, video_duration: float) -> None:
    """把 video + audio 合成一个 mp4。-shortest 确保按视频长度截断音频。"""
    await _ffmpeg([
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac",
        "-shortest",
        "-t", str(video_duration),
        out_path,
    ])


async def _ffprobe_video_duration(video_path: str) -> float:
    """复用 _ffprobe_duration 的别名(语义清晰用)。"""
    return await _ffprobe_duration(video_path)


async def _download_to_local(url: str, out_path: str) -> None:
    """下载 fal 输出 URL 到本地文件。"""
    import httpx
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"download fal output failed:status={resp.status_code}")
            with open(out_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64*1024):
                    f.write(chunk)


async def _build_segment_clip(
    seg_result: Dict[str, Any],
    plan_item: Dict[str, Any],
    seg_local_path: str,
    input_local: str,
    work_dir: str,
) -> str:
    """把一个成功段(ai 或 original)处理成"视频+音频"齐全的本地 mp4。

    AI 段:fal 输出无音频 → 抽原视频对应区间音轨 → mux
    Original 段:切出来的本地段已带音轨 → 直接用

    返回:final clip 本地路径(可直接进 concat demuxer)
    """
    idx = seg_result["idx"]
    if seg_result["source_type"] == "original":
        # 原视频段,seg_local_path 已含音轨,直接用
        # 若原视频无音轨,补静音(防 concat 时音轨不一致)
        if await _has_audio_stream(seg_local_path):
            return seg_local_path
        # 补静音 audio
        clip_dur = await _ffprobe_video_duration(seg_local_path)
        silence = os.path.join(work_dir, f"seg_{idx}_silence.aac")
        await _gen_silence(clip_dur, silence)
        out = os.path.join(work_dir, f"seg_{idx}_final.mp4")
        await _mux_video_audio(seg_local_path, silence, out, clip_dur)
        return out

    # AI 段:下载 aiview output → 拉伸到精确 plan duration(B'' setpts 方案)
    # → 模型自带音轨则保留,否则 mux 原视频音轨
    # 2026-05-13 加 setpts 拉伸:seedance r2v fast 输出比 input 短约 0.08-0.1s
    # (8s 请求出 7.917s)。用 setpts 拉伸到精确 plan duration,确保用户拿到 16s 就是 16s。
    # 副作用:视频整体播慢 1%,人耳/眼分辨阈值 ~3%,无人察觉。
    # 详见 memory project_ssp_v2_setpts_tradeoff。
    fal_local = os.path.join(work_dir, f"seg_{idx}_ai.mp4")
    await _download_to_local(seg_result["output_url"], fal_local)
    clip_dur = await _ffprobe_video_duration(fal_local)

    target_dur = float(plan_item["duration"])
    if abs(clip_dur - target_dur) > 0.02 and clip_dur > 0:
        speed_ratio = target_dur / clip_dur  # > 1 = 慢放;< 1 = 快放
        stretched = os.path.join(work_dir, f"seg_{idx}_ai_stretched.mp4")
        has_fal_audio = await _has_audio_stream(fal_local)
        if has_fal_audio:
            # video setpts + audio atempo,保证音视频同步
            filter_complex = (
                f"[0:v]setpts={speed_ratio:.6f}*PTS[v];"
                f"[0:a]atempo={1.0 / speed_ratio:.6f}[a]"
            )
            args = [
                "ffmpeg", "-y", "-i", fal_local,
                "-filter_complex", filter_complex,
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                stretched,
            ]
        else:
            # 只拉伸视频(fal 没音轨,原视频音轨后面再 mux,长度自然对齐 plan duration)
            args = [
                "ffmpeg", "-y", "-i", fal_local,
                "-vf", f"setpts={speed_ratio:.6f}*PTS",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-an",
                stretched,
            ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"seg {idx} setpts 拉伸失败 (ratio={speed_ratio:.4f}): "
                f"{stderr.decode(errors='replace')[-500:]}"
            )
        log_info(
            f"seg {idx} setpts 拉伸: {clip_dur:.3f}s → {target_dur:.3f}s "
            f"(ratio={speed_ratio:.4f}, audio={'fal' if has_fal_audio else 'orig'})"
        )
        fal_local = stretched
        clip_dur = target_dur

    if await _has_audio_stream(fal_local):
        # fal 音轨存在(拉伸时已 atempo 同步)— 直接用 fal 输出
        out = os.path.join(work_dir, f"seg_{idx}_final.mp4")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-i", fal_local,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            out,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"seg {idx} re-encode 失败: {stderr.decode(errors='replace')[-500:]}")
        return out

    # fal 没出音轨 — fallback 用原视频对应段音轨 mux(音频长度 = plan duration,拉伸后视频已对齐)
    seg_start = float(plan_item["start"])
    seg_dur = float(plan_item["duration"])
    audio_dur = min(clip_dur, seg_dur)
    audio_path = os.path.join(work_dir, f"seg_{idx}_audio.aac")
    has_real_audio = await _extract_audio_clip(input_local, seg_start, audio_dur, audio_path)
    if not has_real_audio:
        await _gen_silence(clip_dur, audio_path)

    out = os.path.join(work_dir, f"seg_{idx}_final.mp4")
    await _mux_video_audio(fal_local, audio_path, out, clip_dur)
    return out


async def _concat_with_demuxer(clip_paths: List[str], out_path: str, work_dir: str) -> None:
    """ffmpeg concat demuxer 拼接 N 段(段间编码必须一致,我们前面已统一 h264+aac)。

    若 demuxer copy 失败 → fallback 到 -filter_complex concat 重编码兜底。
    """
    list_file = os.path.join(work_dir, "concat_list.txt")
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")
    try:
        await _ffmpeg([
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            "-movflags", "+faststart",
            out_path,
        ])
    except RuntimeError as e:
        log_info(f"concat demuxer copy 失败,fallback 重编码:{e}")
        # filter_complex concat 兜底(重编码 = 慢但可靠)
        inputs = []
        for p in clip_paths:
            inputs.extend(["-i", p])
        n = len(clip_paths)
        filter_str = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n)) + f"concat=n={n}:v=1:a=1[outv][outa]"
        await _ffmpeg([
            *inputs,
            "-filter_complex", filter_str,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            out_path,
        ])


async def _process_ultimate(
    job: Dict[str, Any],
    plan: List[Dict[str, Any]],
    seg_files: List[str],
    image_urls_obj: List[Dict[str, Any]],
    prompt_compiled: str,
    seed: int,
    work_dir: str,
    input_local: str,
    settings,
) -> None:
    """ultimate 多段路径:并发 fal → 失败段跳过+按段退款 → 拼接 → 双版本归档。

    并发策略:
    - asyncio.Semaphore(5) 限制同时跑 fal 的段数(防 rate limit / 内存爆)
    - original 段不占 semaphore(本地操作,瞬时完成)

    失败段策略:
    - ai 段失败 → _refund_partial 退该段 credits,从拼接中跳过
    - 全部 ai 失败 → 整单 _refund_full + abort
    - 拼接幸存段(至少 1 段)
    """
    job_id = job["id"]
    aspect_ratio = "auto"  # 2026-05-11 对齐老板 fal playground 成功配方(playground 用 auto;fal 自动检测原视频比例)

    # 1. 并发跑所有段
    sem = asyncio.Semaphore(5)

    async def _run_seg_idx(i: int) -> Dict[str, Any]:
        plan_item = plan[i]
        seg_file = seg_files[i]
        if plan_item.get("source_type") == "original":
            return {
                "idx": plan_item["idx"],
                "source_type": "original",
                "status": "ready",
                "fal_request_id": None,
                "output_url": None,
                "local_path": seg_file,
                "retry_count": 0,
                "actual_cost_usd": 0.0,
                "error": None,
            }
        async with sem:
            r = await run_single_ai_segment_with_retry(
                seg_file, plan_item, image_urls_obj, prompt_compiled,
                seed, aspect_ratio, job_id=job_id,
            )
            r["local_path"] = None  # ai 段:走下载 output_url
            return r

    results = await asyncio.gather(*[_run_seg_idx(i) for i in range(len(plan))])
    log_info(
        f"video_clone_v2 ultimate gather done: job_id={job_id} "
        f"{[r['status'] for r in results]}"
    )

    # 2. 单段成本超限保险 1(标 cost_overflow,从拼接里去掉,但已扣 fal 不退)
    for r in results:
        if (r["source_type"] == "ai" and r["status"] == "completed"
                and r["actual_cost_usd"] > settings.VC2_MAX_SEGMENT_COST_USD):
            log_error(
                f"video_clone_v2 保险 1 触发(ultimate):job={job_id} idx={r['idx']} "
                f"cost=${r['actual_cost_usd']} > ${settings.VC2_MAX_SEGMENT_COST_USD}"
            )
            r["status"] = "cost_overflow"
            r["error"] = f"单段成本超限 ${r['actual_cost_usd']:.2f}"

    # 3. 2026-05-13 v3 设计 — 部分失败时:
    #    - 失败段:per-seg 退款,不拼进成片
    #    - 成功段:每段单独归档双版本(wm + raw),用户分别下载
    #    - 整单不拼接(避免给用户残片误以为是完整)
    #    - status="partial_completed" + error_message 说明哪段失败
    #
    # 历史:
    # - v1 (旧):partial salvage 拼幸存段成 1 个短视频,用户拿到 16s 期望但实际 8s,以为是完整
    # - v2 (2026-05-13 早):任意 ai 段失败 → 全额退 + 不拼。但 2 段成功 1 段失败时用户损失了
    #   2 段已经生成的内容
    # - v3 (本):2 段成功 1 段失败 → 给用户 2 段独立可下载 + 退 1 段。用户损失最小。
    # 保险 1 cost_overflow 也算 AI 失败(已超额扣过 fal,只退积分不退 fal)。
    ai_failed = [r for r in results
                 if r["source_type"] == "ai" and r["status"] != "completed"]
    ai_completed = [r for r in results
                    if r["source_type"] == "ai" and r["status"] == "completed"]

    # 全失败:status=failed + per-seg 退款(等于全额退,但不归档,行为同 v2 旧契约)
    if ai_failed and not ai_completed:
        # 2026-05-13 改按段实际 duration × CREDITS_PER_SEC 积分退,不再固定 SEGMENT_CREDITS
        plan_by_idx_for_refund = {p["idx"]: p for p in plan}
        _rate_rf = rate_for_model(job.get("video_model"))  # 按本单所选模型费率退,跟扣费一致
        for r in ai_failed:
            dur = float(plan_by_idx_for_refund.get(r["idx"], {}).get("duration") or 0)
            await _refund_partial(job, r["idx"], calc_segment_credits(dur, _rate_rf))
        fail_summary = "; ".join(
            f"seg {r['idx']}: {(r.get('error') or '未知')[:80]}" for r in ai_failed
        )
        log_error(f"video_clone_v2 ultimate 全 AI 段失败 job={job_id} fails=[{fail_summary[:300]}]")
        _db_update_job(
            job_id, status="failed", error_step="ai_seg_failed",
            error_message=f"全部 {len(ai_failed)} 段失败:{fail_summary}"[:500],
            segments_results=json.dumps(results, ensure_ascii=False),
        )
        return

    # 部分失败:per-seg 归档成功段 + per-seg 退款失败段(本次 user 老板新设计)
    if ai_failed:
        fail_summary = "; ".join(
            f"seg {r['idx']}: {(r.get('error') or '未知')[:80]}"
            for r in ai_failed
        )
        log_error(
            f"video_clone_v2 ultimate 部分失败 {len(ai_failed)}/{sum(1 for r in results if r['source_type']=='ai')} AI 段,"
            f"per-seg 归档 + 失败段退款:job_id={job_id} fails=[{fail_summary[:300]}]"
        )

        # 3a. 失败段 per-seg 退款(2026-05-13 按段 duration × CREDITS_PER_SEC)
        plan_by_idx_for_refund = {p["idx"]: p for p in plan}
        _rate_rf = rate_for_model(job.get("video_model"))  # 按本单所选模型费率退,跟扣费一致
        for r in ai_failed:
            dur = float(plan_by_idx_for_refund.get(r["idx"], {}).get("duration") or 0)
            await _refund_partial(job, r["idx"], calc_segment_credits(dur, _rate_rf))

        # 3b. 成功段(AI + original)每段单独归档双版本
        plan_by_idx = {p["idx"]: p for p in plan}
        per_seg_archives: List[Dict[str, Any]] = []
        for r in sorted(results, key=lambda x: x["idx"]):
            if r["source_type"] == "ai" and r["status"] != "completed":
                per_seg_archives.append({
                    "idx": r["idx"],
                    "source_type": "ai",
                    "status": "failed",
                    "error": (r.get("error") or "未知")[:200],
                    "watermarked_url": None,
                    "raw_url": None,
                })
                continue
            try:
                plan_item = plan_by_idx[r["idx"]]
                seg_local = seg_files[ next(i for i, p in enumerate(plan) if p["idx"] == r["idx"]) ]
                clip = await _build_segment_clip(r, plan_item, seg_local, input_local, work_dir)
                # archive 单段双版本,filename prefix = "{job_id}_seg_{idx}"
                seg_archive_id = f"{job_id}_seg_{r['idx']}"
                from .video_clone_v2_watermark import emit_dual_versions, DEFAULT_STYLE
                import shutil as _sh
                job_dir = Path("/opt/ssp/uploads/video_clone_v2") / job_id
                job_dir.mkdir(parents=True, exist_ok=True)
                wm_local, raw_local = await emit_dual_versions(
                    clip, str(job_dir), seg_archive_id, style=DEFAULT_STYLE
                )
                for p in (wm_local, raw_local):
                    try:
                        _sh.chown(p, user="ssp-app", group="ssp-app")
                    except (LookupError, PermissionError):
                        pass
                base_url = os.environ.get("PUBLIC_BASE_URL", "https://ailixiao.com").rstrip("/")
                per_seg_archives.append({
                    "idx": r["idx"],
                    "source_type": r["source_type"],
                    "status": "completed",
                    "error": None,
                    "watermarked_url": f"{base_url}/uploads/video_clone_v2/{job_id}/{os.path.basename(wm_local)}",
                    "raw_url":         f"{base_url}/uploads/video_clone_v2/{job_id}/{os.path.basename(raw_local)}",
                })
            except Exception as e:
                log_error(f"video_clone_v2 per-seg archive 失败 idx={r['idx']} job={job_id}: {e}")
                per_seg_archives.append({
                    "idx": r["idx"], "source_type": r["source_type"],
                    "status": "archive_failed", "error": str(e)[:200],
                    "watermarked_url": None, "raw_url": None,
                })

        # 3c. 写入 results 含 per-seg URLs,标 partial_completed 状态
        for r in results:
            arc = next((a for a in per_seg_archives if a["idx"] == r["idx"]), None)
            if arc:
                r["watermarked_url"] = arc["watermarked_url"]
                r["raw_url"] = arc["raw_url"]
        # fal cost 累加(只算成功段)
        fal_cost = sum((r.get("actual_cost_usd") or 0.0) for r in ai_completed)
        _db_update_job(
            job_id, status="partial_completed",
            error_step="ai_seg_failed",
            error_message=f"{len(ai_failed)} 段失败 / {len(ai_completed)} 段成功:{fail_summary}"[:500],
            segments_results=json.dumps(results, ensure_ascii=False),
            fal_cost_total_usd=fal_cost,
            completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        if fal_cost > 0:
            await _record_daily_spend(fal_cost)
        return

    # 5. 拼接所有段(到这里所有 AI 段都 completed,original 段全部保留)
    surviving = sorted(results, key=lambda r: r["idx"])
    plan_by_idx = {p["idx"]: p for p in plan}

    try:
        clip_paths: List[str] = []
        for r in surviving:
            plan_item = plan_by_idx[r["idx"]]
            seg_local = seg_files[ next(i for i, p in enumerate(plan) if p["idx"] == r["idx"]) ]
            clip = await _build_segment_clip(r, plan_item, seg_local, input_local, work_dir)
            clip_paths.append(clip)

        final_raw = os.path.join(work_dir, "final_concat.mp4")
        await _concat_with_demuxer(clip_paths, final_raw, work_dir)
    except Exception as e:
        log_error(f"video_clone_v2 ultimate 拼接失败 job_id={job_id}: {e}")
        _db_update_job(
            job_id, status="failed", error_step="concat",
            error_message=str(e)[:500],
            segments_results=json.dumps(results, ensure_ascii=False),
        )
        # 拼接失败属归档侧 — fal 已扣,仅退失败段已退;主路径不退
        return

    # 6. 双版本归档(用 _archive_local_dual,不需要再下 fal)
    try:
        urls = await _archive_local_dual(final_raw, job_id)
    except Exception as e:
        log_error(f"video_clone_v2 ultimate 归档失败 job_id={job_id}: {e}")
        _db_update_job(
            job_id, status="failed", error_step="archive",
            error_message=str(e)[:500],
            segments_results=json.dumps(results, ensure_ascii=False),
        )
        return

    # 7. 写入 segments_results 含 wm output URL(单段路径用了同样字段命名)
    for r in surviving:
        if r["source_type"] == "ai":
            r["output_url_archived"] = urls["watermarked_url"]

    # 8. 更新 DB
    fal_cost = sum(
        (r["actual_cost_usd"] or 0.0)
        for r in results
        if r["source_type"] == "ai" and r["status"] == "completed"
    )
    _db_update_job(
        job_id,
        status="completed",
        final_video_url=urls["watermarked_url"],
        final_video_url_watermarked=urls["watermarked_url"],
        final_video_url_raw=urls["raw_url"],
        final_video_local_path=urls["watermarked_local_path"],
        fal_cost_total_usd=fal_cost,
        completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        segments_results=json.dumps(results, ensure_ascii=False),
    )

    # 9. 保险 3 写日报
    try:
        await _record_daily_spend(fal_cost)
    except Exception as e:
        log_error(f"record_daily_spend failed:{e}(不影响 job 完成)")

    log_info(
        f"video_clone_v2 ultimate ok:job_id={job_id} "
        f"survived={len(surviving)}/{len(plan)} fal_cost_usd={fal_cost} "
        f"wm={urls['watermarked_url']}"
    )
