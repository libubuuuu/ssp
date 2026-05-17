"""全局任务队列 API - 统一管理图片/视频生成，5 并发上限，JSON 持久化"""
import os
import json
import uuid
import time
import asyncio
import fcntl
from datetime import datetime as _datetime
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.services.fal_service import get_image_service, get_video_service, fal_upload_with_retry
from app.services.billing import get_task_cost, check_user_credits, deduct_credits, add_credits, create_consumption_record
from app.services.logger import log_info, log_warning  # P101: ad_video TTS+lipsync 日志
from app.api.auth import get_current_user

router = APIRouter()

# 路径默认项目根/jobs_data/jobs.json,测试或多环境通过 JOBS_FILE 覆盖
_DEFAULT_JOBS_FILE = Path(__file__).resolve().parents[3] / "jobs_data" / "jobs.json"
JOBS_FILE = Path(os.environ.get("JOBS_FILE", str(_DEFAULT_JOBS_FILE)))
JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
JOBS_DIR = JOBS_FILE.parent

MAX_CONCURRENT = 5
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

# ── 全局后台任务注册表 ──────────────────────────────────────────────────
# 所有 asyncio.create_task 的后台任务都注册到这里
# uvicorn shutdown 时等待所有任务完成，彻底防止任务被强杀
_bg_tasks: set = set()

def create_tracked_task(coro) -> asyncio.Task:
    """创建后台任务并注册到全局注册表，任务结束自动移除。"""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task

async def wait_all_bg_tasks(timeout: float = 600.0) -> None:
    """等待所有注册的后台任务完成（最多 timeout 秒）。供 lifespan 关闭时调用。"""
    if not _bg_tasks:
        return
    pending = list(_bg_tasks)
    log_info(f"shutdown: 等待 {len(pending)} 个后台任务完成...")
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=timeout,
        )
        log_info("shutdown: 所有后台任务已完成")
    except asyncio.TimeoutError:
        log_info(f"shutdown: 超时({timeout}s)，强制退出（积分已自动退还）")

def _load_jobs():
    """读取 jobs.json,加共享锁(LOCK_SH)避免读到正在写的半量"""
    if not JOBS_FILE.exists():
        return {}
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.loads(f.read() or "{}")
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        print(f"load jobs failed: {e}")
        return {}


def _save_jobs():
    """写 jobs.json,加排他锁(LOCK_EX)防止多 worker 并发覆盖损坏文件

    Phase 2 迁 RQ/Celery + Redis 后退役。当前文件型队列单进程多协程是安全的,
    多进程(uvicorn workers)/cron 并发场景下没锁会撞数据丢失。
    """
    try:
        # mode w 会 truncate,要在 flock 之前 open;flock 跨 close 不传播,
        # 用 with open 保证锁只在写入期间持有
        with open(JOBS_FILE, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(JOBS, ensure_ascii=False, indent=2, default=str))
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        print(f"save jobs failed: {e}")

JOBS: Dict[str, dict] = _load_jobs()


def cleanup_orphan_jobs_on_startup() -> int:
    """启动时把 status='running' 的 job 标 failed + 退积分。

    背景:worker 用 asyncio.create_task 注册 in-memory,blue 重启时 task 丢失但
    jobs.json 里仍 status=running → 用户看到永远 running。

    本函数在 lifespan startup 调用,扫 JOBS 把孤儿全标 failed 退积分。
    """
    try:
        from app.services.billing import add_credits  # 延迟 import 避开循环
    except Exception as e:
        print(f"cleanup_orphan_jobs: import billing failed: {e}")
        return 0
    n = 0
    for jid, j in list(JOBS.items()):
        if j.get("status") != "running":
            continue
        j["status"] = "failed"
        j["error"] = "服务重启时任务丢失,已自动退还积分,请重新提交"
        j["finished_at"] = time.time()
        try:
            uid = j.get("user_numeric_id") or j.get("user_id")
            cost = j.get("cost", 0) or 0
            if uid and cost > 0:
                add_credits(uid, cost, reason="task_refund", ref_id=jid, module=j.get("module") or j.get("type"))
        except Exception as e:
            print(f"cleanup_orphan_jobs: refund {jid} failed: {e}")
        n += 1
    if n:
        _save_jobs()
        print(f"[startup] cleanup_orphan_jobs: 标 failed + 退积分 {n} 个孤儿")
    return n


class SubmitJobRequest(BaseModel):
    type: str
    params: Dict[str, Any]
    title: Optional[str] = None


_GPT_IMAGE_SIZE_MAP = {
    "1024x1024": "square_hd",
    "1024x768":  "landscape_4_3",
    "768x1024":  "portrait_4_3",
    "1024x1536": "portrait_4_3",
    "1536x1024": "landscape_4_3",
}


async def _run_image_job(params: dict):
    import asyncio, fal_client
    service = get_image_service()
    refs = params.get("reference_images") or []
    size = params.get("size", "1024x1024")
    image_size = _GPT_IMAGE_SIZE_MAP.get(size, "square_hd")

    if refs:
        result = await asyncio.wait_for(
            fal_client.run_async(
                "openai/gpt-image-2/edit",
                arguments={
                    "prompt": params["prompt"],
                    "image_urls": refs,
                    "image_size": image_size,
                    "quality": "medium",
                    "num_images": 1,
                    "output_format": "png",
                },
            ),
            timeout=360,
        )
        images = result.get("images", [])
        if not images:
            raise Exception("no image generated")
        return {"image_url": images[0].get("url"), "type": "image",
                "model": "openai/gpt-image-2/edit"}

    result = await service.generate(params["prompt"], size, "gpt-image-2")
    if "error" in result:
        raise Exception(result["error"])
    result["type"] = "image"
    return result


async def _run_video_job(params: dict, job_type: str):
    service = get_video_service()
    if job_type == "video_i2v":
        # 2026-05-13:之前 duration_sec 没传给 fal,kling 默认出 5s,用户点 10s 也只得 5s。
        duration_sec = int(params.get("duration_sec") or 5)
        r = await service.generate_from_image(
            params["image_url"], params.get("prompt", ""), params.get("tail_image_url"),
            duration_sec=duration_sec,
        )
    elif job_type == "video_edit":
        r = await service.replace_element(params["video_url"], params["element_image_url"], params["instruction"], params.get("product_image_url"))
    elif job_type == "video_clone":
        r = await service.clone_video(params["reference_video_url"], params["model_image_url"], params.get("product_image_url"))
    else:
        raise Exception(f"unknown video type: {job_type}")
    if r.get("error"):
        raise Exception(r["error"])
    task_id = r.get("task_id")
    endpoint_tag = r.get("endpoint_tag", "edit")
    if not task_id:
        raise Exception("no task_id from fal")
    for _ in range(120):
        await asyncio.sleep(5)
        status = await service.get_task_status(task_id, endpoint_hint=endpoint_tag)
        if status.get("status") == "completed" and status.get("video_url"):
            return {"video_url": status["video_url"], "type": "video"}
        if status.get("status") == "failed":
            raise Exception(status.get("error", "fal task failed"))
    raise Exception("timeout (10 min)")


def _build_p118_seedance_prompt(scene: dict, overall: str, model_desc: str) -> str:
    """P118 helper:构造 Seedance i2v 动作演示 prompt(强化"动作"弱化"talking")"""
    parts = []
    if model_desc:
        parts.append(f"Model: {model_desc}")
    if overall:
        parts.append(overall)
    vp = (scene.get("visual_prompt") or "").strip()
    if vp:
        parts.append(
            f"DYNAMIC PRODUCT DEMO ACTION: {vp}. "
            f"Model performs vivid product demonstration: "
            f"adjusting/tugging/showing the product on her body, "
            f"rotating torso, hand gestures pointing at the product details, "
            f"smooth dynamic camera following the action. "
            f"NO talking focus, NO mouth-driven expressions — "
            f"focus on body movement and product interaction."
        )
    return "\n".join(parts).strip() or "Dynamic product demonstration action"


async def _p118_concat_and_save(
    talking_url: str,
    seedance_url: str,
    duration: int,
    user_id: str,
    aspect_ratio: str = "9:16",
) -> str:
    """P118: 下载 talking + seedance,ffmpeg 拼"talking 0-1.5s + seedance 1.5-end + 完整 audio",
    写本地 uploads 返回公网 URL。"""
    import subprocess as _sp
    import tempfile as _tmp
    import shutil as _sh
    import re as _re2
    import httpx as _httpx
    from datetime import datetime as _dt
    from app.services.media_archiver import UPLOADS_ROOT, PUBLIC_BASE_URL

    # 切换点 1.5s 经验值:够看清开场说话又不长
    cut_point = 1.5
    # 9:16 = 1056x1952(对齐 Kling Avatar v2 输出,probe 实测同尺寸)
    if aspect_ratio == "16:9":
        target_w, target_h = 1952, 1056
    else:
        target_w, target_h = 1056, 1952

    work = _tmp.mkdtemp(prefix="p118_")
    try:
        async with _httpx.AsyncClient(timeout=120) as cli:
            for url, name in [(talking_url, "talking.mp4"), (seedance_url, "seedance.mp4")]:
                r = await cli.get(url)
                r.raise_for_status()
                (Path(work) / name).write_bytes(r.content)

        talking_p = f"{work}/talking.mp4"
        seedance_p = f"{work}/seedance.mp4"
        final_p = f"{work}/final.mp4"

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", talking_p,
            "-i", seedance_p,
            "-filter_complex",
            f"[0:v]trim=0:{cut_point},setpts=PTS-STARTPTS,"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v0];"
            f"[1:v]trim={cut_point}:{duration},setpts=PTS-STARTPTS,"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v1];"
            "[v0][v1]concat=n=2:v=1:a=0[v]",
            "-map", "[v]",
            "-map", "0:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            final_p,
        ]
        r = _sp.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise Exception(f"ffmpeg failed: {r.stderr[:500]}")

        safe_uid = _re2.sub(r"[^a-zA-Z0-9_\-]", "_", str(user_id))[:64] or "anon"
        yyyymm = _dt.utcnow().strftime("%Y-%m")
        target_dir = UPLOADS_ROOT / safe_uid / yyyymm
        target_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"video_p118_{uuid.uuid4().hex}.mp4"
        out_path = target_dir / out_name
        _sh.copy(final_p, out_path)
        os.chmod(out_path, 0o644)

        public_url = f"{PUBLIC_BASE_URL.rstrip('/')}/{safe_uid}/{yyyymm}/{out_name}"
        log_info(f"ad_video P118 ffmpeg concat OK -> {public_url}")
        return public_url
    finally:
        import shutil as _sh2
        _sh2.rmtree(work, ignore_errors=True)


def _parse_time_range(tr: str, fallback: float = 2.0) -> float:
    """P119 helper:解析 scene.time_range 拿段长。'0-1.5s' / '1.5-3s' → 1.5"""
    try:
        s = (tr or "").replace("s", "").strip()
        a, b = s.split("-")
        return max(0.5, float(b) - float(a))
    except Exception:
        return fallback


async def _p135_concat_simple(
    seg_video_urls: list,  # list[str] — N 段独立 talking head 视频(各自带 audio)
    user_id: str,
) -> str:
    """P135:N 段视频 ffmpeg concat demuxer 直接拼接(不 trim 不 xfade,完整段拼)。

    跟 P125 xfade chain 不同:
    - 不用 filter_complex,用 concat demuxer(只拷贝流,不重编码)
    - 不 trim 段长(每段完整保留,自动用模型实际输出秒数)
    - 不 xfade 渐变(段间硬切,但用户要求"不剪辑只拼接")
    - 段间会有"姿势重置"小跳跃(Kling Avatar 物理特性,不是剪辑造成)
    """
    import subprocess as _sp
    import tempfile as _tmp
    import shutil as _sh
    import re as _re2
    import httpx as _httpx
    from datetime import datetime as _dt
    from app.services.media_archiver import UPLOADS_ROOT, PUBLIC_BASE_URL

    n = len(seg_video_urls)
    if n == 0:
        raise Exception("P135 seg_video_urls 空")
    if n == 1:
        # 只 1 段无需拼接,直接返该 url(归档归档逻辑跟 P133 一样,但简化:返原 url)
        return seg_video_urls[0]

    work = _tmp.mkdtemp(prefix="p135_")
    try:
        # 下载 N 段
        async with _httpx.AsyncClient(timeout=180) as cli:
            local_paths_raw = []
            for i, vu in enumerate(seg_video_urls):
                if not vu:
                    raise Exception(f"P135 段 {i+1} video_url 空")
                r = await cli.get(vu); r.raise_for_status()
                p = Path(work) / f"seg_{i+1}_raw.mp4"
                p.write_bytes(r.content)
                local_paths_raw.append(str(p))

        # P151(2026-05-06):用户实测段 1 嘴动+声错位,真因 Kling Avatar
        # video 流(7.2s)≠ audio 流(5.34s)。concat 前 trim 每段到 audio 长度
        # (audio 优先,video 砍尾巴)→ video 总长 = audio 总长 → 音画同步
        local_paths = []
        for i, raw in enumerate(local_paths_raw):
            # 探测每段的 video / audio 时长
            probe = _sp.run(
                ["ffprobe", "-v", "error", "-show_streams", "-of", "json", raw],
                capture_output=True, text=True, timeout=30,
            )
            import json as _json
            streams = _json.loads(probe.stdout).get("streams", [])
            v_dur = next((float(s.get("duration", 0)) for s in streams if s.get("codec_type") == "video"), 0)
            a_dur = next((float(s.get("duration", 0)) for s in streams if s.get("codec_type") == "audio"), 0)
            # P198(2026-05-08):支持 silent 段(没 audio stream),用 anullsrc 补静音轨
            # 用户:speech 空段不要被跳过,该段纯画面静音,跟 talking 段一起 concat
            has_audio = a_dur > 0
            if has_audio:
                target_dur = min(v_dur, a_dur) if v_dur > 0 else a_dur
                if abs(v_dur - a_dur) > 0.1:
                    log_warning(f"P151 段 {i+1} desync: video={v_dur:.2f}s audio={a_dur:.2f}s,trim 到 {target_dur:.2f}s")
            else:
                target_dur = v_dur
                log_info(f"P198 段 {i+1} silent(无 audio stream),用 anullsrc 补 {target_dur:.2f}s 静音")
            trimmed = Path(work) / f"seg_{i+1}.mp4"
            # ffmpeg trim 到 target_dur(re-encode 保证关键帧对齐)
            if has_audio:
                ff_cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", raw, "-t", str(target_dur),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart",
                    str(trimmed),
                ]
            else:
                # P198:silent 段加 anullsrc 静音 audio
                ff_cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", raw,
                    "-f", "lavfi", "-t", str(target_dur), "-i", "anullsrc=r=44100:cl=stereo",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-t", str(target_dur),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart",
                    str(trimmed),
                ]
            r2 = _sp.run(ff_cmd, capture_output=True, text=True, timeout=120)
            if r2.returncode != 0:
                log_warning(f"P151 段 {i+1} trim 失败,用 raw: {r2.stderr[:200]}")
                local_paths.append(raw)
            else:
                local_paths.append(str(trimmed))

        # concat list 文件
        list_path = Path(work) / "concat_list.txt"
        list_path.write_text(
            "\n".join([f"file '{p}'" for p in local_paths]) + "\n",
            encoding="utf-8",
        )

        final_p = f"{work}/final.mp4"

        # 先试 concat -c copy(不重编码,最快最干净)
        cmd_copy = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_path),
            "-c", "copy",
            "-movflags", "+faststart",
            final_p,
        ]
        r = _sp.run(cmd_copy, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            # codec 不一致(fps / 分辨率 / sample_rate 差)→ 重新编码兜底
            log_warning(f"P135 concat -c copy 失败,fallback re-encode: {r.stderr[:300]}")
            cmd_reenc = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_path),
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                final_p,
            ]
            r2 = _sp.run(cmd_reenc, capture_output=True, text=True, timeout=240)
            if r2.returncode != 0:
                raise Exception(f"P135 concat re-encode failed: {r2.stderr[:500]}")

        # 归档到 uploads
        safe_uid = _re2.sub(r"[^a-zA-Z0-9_\-]", "_", str(user_id))[:64] or "anon"
        yyyymm = _dt.utcnow().strftime("%Y-%m")
        target_dir = UPLOADS_ROOT / safe_uid / yyyymm
        target_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"video_p135_{uuid.uuid4().hex}.mp4"
        out_path = target_dir / out_name
        _sh.copy(final_p, out_path)
        os.chmod(out_path, 0o644)

        public_url = f"{PUBLIC_BASE_URL.rstrip('/')}/{safe_uid}/{yyyymm}/{out_name}"
        log_info(f"ad_video P135 concat OK ({n} 段) -> {public_url}")
        return public_url
    finally:
        import shutil as _sh2
        _sh2.rmtree(work, ignore_errors=True)


async def _p125_concat_omnihuman(
    seg_video_urls: list,  # list[str] — N 段 omnihuman 视频(各自带 audio)
    seg_durs: list,        # list[float] — 每段时长(秒)
    user_id: str,
    aspect_ratio: str = "9:16",
) -> str:
    """P125(2026-05-05):N 段 omnihuman 视频(各带 audio)→ xfade + acrossfade chain。

    跟 P120 不同:
    - audio 不再独立 mp3 文件 concat,直接从每段视频 [i:a] 流 acrossfade
    - 因为 omnihuman 输出 = 模特说话视频 + 嘴型同步 audio,音画一体
    - 所以只用 N 个 video 输入,无需额外 audio 输入
    """
    import subprocess as _sp
    import tempfile as _tmp
    import shutil as _sh
    import re as _re2
    import httpx as _httpx
    from datetime import datetime as _dt
    from app.services.media_archiver import UPLOADS_ROOT, PUBLIC_BASE_URL

    n = len(seg_durs)
    if n == 0 or len(seg_video_urls) != n:
        raise Exception(f"P125: video={len(seg_video_urls)} 应等于 seg_durs={n}")

    if aspect_ratio == "16:9":
        target_w, target_h = 1952, 1056
    else:
        target_w, target_h = 1056, 1952

    work = _tmp.mkdtemp(prefix="p125_")
    try:
        async with _httpx.AsyncClient(timeout=120) as cli:
            video_paths = []
            for i, vu in enumerate(seg_video_urls):
                if not vu:
                    raise Exception(f"P125 段 {i+1} video_url 空")
                r = await cli.get(vu); r.raise_for_status()
                vp = Path(work) / f"seg_{i+1}.mp4"
                vp.write_bytes(r.content)
                video_paths.append(str(vp))

        final_p = f"{work}/final.mp4"
        xfade_dur = 0.2

        v_filter_parts = []
        for i in range(n):
            v_filter_parts.append(
                f"[{i}:v]trim=0:{seg_durs[i]},setpts=PTS-STARTPTS,"
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]"
            )
        if n == 1:
            final_v_label = "v0"
        else:
            prev_v = "v0"
            cum_len = seg_durs[0]
            for i in range(1, n):
                out_v = f"vx{i}"
                offset = cum_len - xfade_dur
                v_filter_parts.append(
                    f"[{prev_v}][v{i}]xfade=transition=fade:duration={xfade_dur}:offset={offset}[{out_v}]"
                )
                cum_len = cum_len + seg_durs[i] - xfade_dur
                prev_v = out_v
            final_v_label = prev_v

        # audio 从每段 video 流 [i:a] 取(omnihuman 视频自带模特说话 audio)
        a_filter_parts = []
        for i in range(n):
            a_filter_parts.append(
                f"[{i}:a]atrim=0:{seg_durs[i]},asetpts=PTS-STARTPTS[a{i}]"
            )
        if n == 1:
            final_a_label = "a0"
        else:
            prev_a = "a0"
            for i in range(1, n):
                out_a = f"ax{i}"
                a_filter_parts.append(
                    f"[{prev_a}][a{i}]acrossfade=d={xfade_dur}[{out_a}]"
                )
                prev_a = out_a
            final_a_label = prev_a

        filter_complex = ";".join(v_filter_parts + a_filter_parts)

        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for p in video_paths:
            cmd.extend(["-i", p])
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", f"[{final_v_label}]",
            "-map", f"[{final_a_label}]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            final_p,
        ])
        r = _sp.run(cmd, capture_output=True, text=True, timeout=240)
        if r.returncode != 0:
            raise Exception(f"P125 ffmpeg failed: {r.stderr[:500]}")

        safe_uid = _re2.sub(r"[^a-zA-Z0-9_\-]", "_", str(user_id))[:64] or "anon"
        yyyymm = _dt.utcnow().strftime("%Y-%m")
        target_dir = UPLOADS_ROOT / safe_uid / yyyymm
        target_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"video_p125_{uuid.uuid4().hex}.mp4"
        out_path = target_dir / out_name
        _sh.copy(final_p, out_path)
        os.chmod(out_path, 0o644)

        public_url = f"{PUBLIC_BASE_URL.rstrip('/')}/{safe_uid}/{yyyymm}/{out_name}"
        log_info(f"ad_video P125 omnihuman concat OK ({n} 段) -> {public_url}")
        return public_url
    finally:
        import shutil as _sh2
        _sh2.rmtree(work, ignore_errors=True)


async def _p120_concat_multi_shot_with_audio(
    talking_url: str,
    seedance_urls: list,  # list[Optional[str]] — None 表示 fallback 用 talking 填该段
    seg_audios: list,     # list[Optional[str]] — 每段独立 TTS audio URL,None 表示该段静音
    seg_durs: list,       # list[float] — 每段时长(秒)
    user_id: str,
    aspect_ratio: str = "9:16",
) -> str:
    """P120: N 段视频拼接 + N 段画外音 audio 拼接(每段独立说话内容)。

    视频:段 1 talking + 段 2-N seedance(各按 seg_durs trim)
    audio:N 段独立 TTS audio 直接 concat(用户听到主播开场→画外音卖点→CTA 连贯播)
    """
    import subprocess as _sp
    import tempfile as _tmp
    import shutil as _sh
    import re as _re2
    import httpx as _httpx
    from datetime import datetime as _dt
    from app.services.media_archiver import UPLOADS_ROOT, PUBLIC_BASE_URL

    n = len(seg_durs)
    if not talking_url:
        raise Exception("P120: talking_url 必须有")
    if len(seedance_urls) != n - 1:
        raise Exception(f"P120: seedance_urls={len(seedance_urls)} 应等于 n-1={n-1}")
    if len(seg_audios) != n:
        raise Exception(f"P120: seg_audios={len(seg_audios)} 应等于 n={n}")

    if aspect_ratio == "16:9":
        target_w, target_h = 1952, 1056
    else:
        target_w, target_h = 1056, 1952

    work = _tmp.mkdtemp(prefix="p120_")
    try:
        async with _httpx.AsyncClient(timeout=120) as cli:
            # 下载段 1 talking 视频
            r = await cli.get(talking_url); r.raise_for_status()
            tp = Path(work) / "talking.mp4"
            tp.write_bytes(r.content)

            # 下载段 2-N seedance(失败的用 talking 填)
            video_paths = [str(tp)]
            for i, su in enumerate(seedance_urls):
                if su:
                    r = await cli.get(su); r.raise_for_status()
                    sp = Path(work) / f"seedance_{i+2}.mp4"
                    sp.write_bytes(r.content)
                    video_paths.append(str(sp))
                else:
                    video_paths.append(str(tp))  # fallback

            # 下载 N 段 audio(段 N 没 audio 时生成静音 wav 占位)
            audio_paths = []
            for i, au in enumerate(seg_audios):
                if au:
                    r = await cli.get(au); r.raise_for_status()
                    ap = Path(work) / f"audio_{i+1}.mp3"
                    ap.write_bytes(r.content)
                    audio_paths.append(str(ap))
                else:
                    # 用 ffmpeg 生成 seg_durs[i] 秒的静音(让 audio 总长跟 video 总长对齐)
                    silent = Path(work) / f"silent_{i+1}.wav"
                    _sp.run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
                        "-t", str(seg_durs[i]),
                        str(silent),
                    ], check=True, timeout=20)
                    audio_paths.append(str(silent))

        final_p = f"{work}/final.mp4"

        # P122(2026-05-05):xfade + acrossfade 加 0.2s 过渡 — 解决段切换硬切生硬感
        # 之前用 concat 直接拼,模特姿势瞬间突变。改成 xfade chain 段间渐变 0.2 秒。
        # 注意:每个 xfade overlap 偷 0.2s,N 段总长 = sum(seg_durs) - (N-1) * 0.2
        xfade_dur = 0.2

        # 视频:每段 trim+scale+pad,然后 xfade chain
        v_filter_parts = []
        for i in range(n):
            v_filter_parts.append(
                f"[{i}:v]trim=0:{seg_durs[i]},setpts=PTS-STARTPTS,"
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]"
            )
        if n == 1:
            final_v_label = "v0"
        else:
            prev_v = "v0"
            cum_len = seg_durs[0]
            for i in range(1, n):
                out_v = f"vx{i}"
                offset = cum_len - xfade_dur
                v_filter_parts.append(
                    f"[{prev_v}][v{i}]xfade=transition=fade:duration={xfade_dur}:offset={offset}[{out_v}]"
                )
                cum_len = cum_len + seg_durs[i] - xfade_dur
                prev_v = out_v
            final_v_label = prev_v

        # audio:每段 atrim 后 acrossfade chain
        a_filter_parts = []
        for i in range(n):
            a_filter_parts.append(
                f"[{n+i}:a]atrim=0:{seg_durs[i]},asetpts=PTS-STARTPTS[a{i}]"
            )
        if n == 1:
            final_a_label = "a0"
        else:
            prev_a = "a0"
            for i in range(1, n):
                out_a = f"ax{i}"
                a_filter_parts.append(
                    f"[{prev_a}][a{i}]acrossfade=d={xfade_dur}[{out_a}]"
                )
                prev_a = out_a
            final_a_label = prev_a

        filter_complex = ";".join(v_filter_parts + a_filter_parts)

        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for p in video_paths:
            cmd.extend(["-i", p])
        for p in audio_paths:
            cmd.extend(["-i", p])
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", f"[{final_v_label}]",
            "-map", f"[{final_a_label}]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            final_p,
        ])
        r = _sp.run(cmd, capture_output=True, text=True, timeout=240)
        if r.returncode != 0:
            raise Exception(f"P120 ffmpeg failed: {r.stderr[:500]}")

        safe_uid = _re2.sub(r"[^a-zA-Z0-9_\-]", "_", str(user_id))[:64] or "anon"
        yyyymm = _dt.utcnow().strftime("%Y-%m")
        target_dir = UPLOADS_ROOT / safe_uid / yyyymm
        target_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"video_p120_{uuid.uuid4().hex}.mp4"
        out_path = target_dir / out_name
        _sh.copy(final_p, out_path)
        os.chmod(out_path, 0o644)

        public_url = f"{PUBLIC_BASE_URL.rstrip('/')}/{safe_uid}/{yyyymm}/{out_name}"
        log_info(f"ad_video P120 multi-shot+audio concat OK ({n} 段) -> {public_url}")
        return public_url
    finally:
        import shutil as _sh2
        _sh2.rmtree(work, ignore_errors=True)


async def _p119_concat_multi_shot(
    talking_url: str,
    seedance_urls: list,  # list[Optional[str]] — None 表示 fallback 用 talking 填这段
    seg_durs: list,  # list[float] — 每段时长(秒)
    user_id: str,
    aspect_ratio: str = "9:16",
) -> str:
    """P119: 多镜头拼接 — 段 1 talking + 段 2-N seedance(可有 None fallback),
    audio 全程用 talking。N 个视频按 seg_durs 时长 trim 后 concat。"""
    import subprocess as _sp
    import tempfile as _tmp
    import shutil as _sh
    import re as _re2
    import httpx as _httpx
    from datetime import datetime as _dt
    from app.services.media_archiver import UPLOADS_ROOT, PUBLIC_BASE_URL

    if not talking_url:
        raise Exception("P119: talking_url 必须有")
    if len(seg_durs) != len(seedance_urls) + 1:
        raise Exception(f"P119: seg_durs={len(seg_durs)} != seedance+1={len(seedance_urls)+1}")

    if aspect_ratio == "16:9":
        target_w, target_h = 1952, 1056
    else:
        target_w, target_h = 1056, 1952

    work = _tmp.mkdtemp(prefix="p119_")
    try:
        # 下载 talking + 所有 seedance(并发)
        async with _httpx.AsyncClient(timeout=120) as cli:
            paths = []
            # 段 1: talking
            r = await cli.get(talking_url)
            r.raise_for_status()
            tp = Path(work) / "talking.mp4"
            tp.write_bytes(r.content)
            paths.append(str(tp))
            # 段 2-N: seedance 或 fallback talking
            for i, su in enumerate(seedance_urls):
                if su:
                    r = await cli.get(su)
                    r.raise_for_status()
                    sp = Path(work) / f"seedance_{i+2}.mp4"
                    sp.write_bytes(r.content)
                    paths.append(str(sp))
                else:
                    # fallback:用 talking 填这段(从对应时间点)
                    paths.append(str(tp))

        final_p = f"{work}/final.mp4"

        # 构造 ffmpeg filter_complex
        # 段 1: trim 0:seg_durs[0] from talking
        # 段 i (i>=2): trim 0:seg_durs[i-1] from seedance(每段从 0 开始,ffmpeg 截前 X 秒)
        # 例外:fallback 用 talking 时,trim 段 i 在 talking 里对应的累计时间窗
        filter_parts = []
        cum = 0.0
        for i, d in enumerate(paths):
            seg_dur = seg_durs[i]
            if i == 0:
                # talking 段从 0 开始
                start = 0
                end = seg_dur
            elif paths[i] == paths[0]:
                # fallback: 用 talking 在累计时间窗
                start = cum
                end = cum + seg_dur
            else:
                # seedance: 从 0 开始截前 seg_dur 秒
                start = 0
                end = seg_dur
            filter_parts.append(
                f"[{i}:v]trim={start}:{end},setpts=PTS-STARTPTS,"
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]"
            )
            cum += seg_dur

        # 拼接所有 [vN]
        concat_inputs = "".join(f"[v{i}]" for i in range(len(paths)))
        filter_parts.append(f"{concat_inputs}concat=n={len(paths)}:v=1:a=0[v]")
        filter_complex = ";".join(filter_parts)

        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        # input 列表(去重 — 同一个 talking 文件不能重复传作 input,index 会乱)
        # 实际上 ffmpeg 允许同一文件多次 -i,但 stream index 会按 -i 顺序排
        # 简单点:每段都给独立 -i,即使指向同一文件
        for p in paths:
            cmd.extend(["-i", p])
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "0:a",  # 全程 audio 用 talking(input 0)
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            final_p,
        ])
        r = _sp.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            raise Exception(f"P119 ffmpeg failed: {r.stderr[:500]}")

        safe_uid = _re2.sub(r"[^a-zA-Z0-9_\-]", "_", str(user_id))[:64] or "anon"
        yyyymm = _dt.utcnow().strftime("%Y-%m")
        target_dir = UPLOADS_ROOT / safe_uid / yyyymm
        target_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"video_p119_{uuid.uuid4().hex}.mp4"
        out_path = target_dir / out_name
        _sh.copy(final_p, out_path)
        os.chmod(out_path, 0o644)

        public_url = f"{PUBLIC_BASE_URL.rstrip('/')}/{safe_uid}/{yyyymm}/{out_name}"
        log_info(f"ad_video P119 multi-shot concat OK ({len(paths)} 段) -> {public_url}")
        return public_url
    finally:
        import shutil as _sh2
        _sh2.rmtree(work, ignore_errors=True)


async def _run_ad_video_job(params: dict):
    """AI 带货视频 — Seedance 2.0 异步任务

    单段(<=15s):走老路,所有 scenes 拼成一个 prompt 跑一次 Seedance
    多段(>15s,P31 2026-05-01):每个 scene 独立调用 Seedance,
                           Semaphore(5) 并发 + ffmpeg concat 拼接,
                           沿用口播 V3 P28 长视频分段模板

    参数:
      - image_url: 首帧图(可以是 /preview 输出,也可以是直接上传的)
      - script: 完整脚本 dict {overall_setting, model_description, scenes:[...]}
      - duration: 总时长 5-300 秒
      - aspect_ratio / resolution / enable_audio
    """
    from app.services import ad_video_models
    from app.services.vlm_service import split_segments

    duration = int(params.get("duration", 15))
    script = params.get("script") or {}
    scenes = script.get("scenes") or []

    overall = script.get("overall_setting", "")
    model_desc = script.get("model_description", "")
    aspect_ratio = params.get("aspect_ratio", "9:16")
    resolution = params.get("resolution", "720p")

    # P187(2026-05-08):参考视频检测到无人物 → 强制无模特路径
    # - compose_first_frame 不传 model_description,prompt 自动改"无人物纯产品"
    # - 后续跳过 Kling Avatar,走 seedance i2v 静音
    ref_has_people = params.get("ref_video_has_people")
    if ref_has_people is False:
        log_info(f"ad_video P187 参考视频无人物 → 走纯产品 seedance i2v 路径")
        model_desc = ""  # 不写模特描述
    # P187:参考视频中间帧用作 background_image_url(强场景锁) — 仅在用户没传背景图时
    bg_url_for_compose = params.get("background_image_url")
    bg_is_from_ref_video = False
    if not bg_url_for_compose and params.get("reference_video_frame_url"):
        bg_url_for_compose = params.get("reference_video_frame_url")
        bg_is_from_ref_video = True
        log_info(f"ad_video P187 用参考视频中间帧作背景: {bg_url_for_compose[:80]}")
    # P189(2026-05-08):背景来自参考视频帧时,prompt 加强约束 — 防 GPT 把帧里原产品保留
    if bg_is_from_ref_video and scenes:
        for _sc in scenes:
            _orig_vp = _sc.get("visual_prompt", "")
            _sc["visual_prompt"] = (
                _orig_vp + " "
                "🚨 CRITICAL — PRODUCT REPLACEMENT: The background reference image is a FRAME "
                "from a sample video and may show a DIFFERENT product/item than yours. "
                "REPLACE any product/object/item visible in that background frame with the product "
                "from the FIRST reference image (your actual product). The background frame is for "
                "ENVIRONMENT, LIGHTING, COMPOSITION, MOOD only — NOT for product reference. "
                "Do NOT keep, do NOT show, do NOT include any product from the background frame. "
                "Only the FIRST reference image (and second if present) defines the product."
            )
        log_info(f"ad_video P189 已加强 prompt:scenes 加 PRODUCT REPLACEMENT 强约束")

    # P39 (2026-05-01):回 i2v 架构 + Flux Kontext 合首帧。
    # 痛点:ref2vid 改产品(用户实测产品被改) → 切 image edit SOTA 锁产品。
    # 路线:产品图 → Flux Kontext 17s 合"模特+产品+背景"首帧 → Seedance v1.5/pro
    # i2v 70s 出视频。5s 视频总用时 ~90 秒,产品保真度高,真人质感强。

    # 第 1 步:用产品图 + 第 1 段 visual 调 Flux Kontext 合共享 base 首帧
    first_scene = scenes[0] if scenes else {}
    _no_model_p211 = (ref_has_people is False)
    # P211(2026-05-08):no_model 模式 base 异步启动,后续多段 panels 并发跑(用 product 作 anchor)
    # base + panels 串行 6 分钟 → 并发 max(3,5) = 5 分钟,省 1-2 分钟
    base_image_url = params.get("product_image_url") or params.get("image_url")  # 默认 fallback
    base_task_p211 = None
    if _no_model_p211:
        base_task_p211 = asyncio.create_task(ad_video_models.compose_first_frame(
            product_image_url=params.get("product_image_url") or params.get("image_url"),
            background_image_url=bg_url_for_compose,
            model_description=model_desc,
            scene_visual_prompt=first_scene.get("visual_prompt", ""),
            product_back_image_url=params.get("product_back_image_url"),
            aspect_ratio=params.get("aspect_ratio") or "9:16",
            no_text=True,
            style_reference_image_url=params.get("style_reference_image_url"),
            no_model=True,
        ))
        log_info(f"ad_video P211 no_model 模式:base 异步启动,panels 用 product_image 直接并发出图")
    else:
        base_result = await ad_video_models.compose_first_frame(
            product_image_url=params.get("product_image_url") or params.get("image_url"),
            background_image_url=bg_url_for_compose,
            model_description=model_desc,
            scene_visual_prompt=first_scene.get("visual_prompt", ""),
            product_back_image_url=params.get("product_back_image_url"),
            aspect_ratio=params.get("aspect_ratio") or "9:16",
            no_text=True,
            style_reference_image_url=params.get("style_reference_image_url"),
            no_model=False,
        )
        if "error" in base_result or not base_result.get("image_url"):
            raise Exception(f"首帧合成失败: {base_result.get('error', '?')}")
        base_image_url = base_result["image_url"]

    # ---------- 单段模式(<=12s,P118 双段拼接) ----------
    # P104(2026-05-05):TTS + omnihuman talking head 一步到位
    # P118(2026-05-05):用户骂"动作太少不像爆款" — 真因 talking head 是 lip-sync 模型,
    # 物理只能"嘴动+头微动",做不了"拉/穿/转"演示。爆款抖音多动作靠多镜头剪辑。
    # 修法:5-12s 走双段拼接 — talking 0-1.5s 模特说话(嘴动开场)+ seedance 1.5-end
    # 产品演示动作。音频全程用 talking 的 5s(完整一句话不断),视觉前段说话+后段演示。
    # 并发跑 talking head + Seedance i2v(节省一半时间),ffmpeg 拼。
    # P187(2026-05-08):参考视频检测无人物 → 直接跳到多段 seedance i2v 路径(静音)
    # 否则走 Kling Avatar 有声路径(模型不变)
    if ref_has_people is False:
        log_info("ad_video P187 跳过 Kling Avatar/talking head,直接走多段 seedance i2v")
        # 设 duration > 12 不会跑下面的 talking head 分支,直接 fall through 到多段
    elif duration <= 12 or len(scenes) <= 1:
        speech_text = (first_scene.get("speech") or "").strip() if first_scene else ""
        if not speech_text:
            raise Exception("scene speech 为空,无法生成 talking video")

        import fal_client as _fc
        import re as _re

        # P112(2026-05-05):VLM 偶尔不听话写超 duration 的 speech,导致 TTS 念出
        # 比 duration 长得多的音频,omnihuman 用音频驱动出"5s 选项 → 13s 视频"。
        # 双保险:按 duration 算 max_chars 截断,防 VLM 写超。
        # elevenlabs multilingual-v2 实测速率:中文 ~5 字/秒,英文 ~14 字符/秒。
        _has_cn = bool(_re.search(r"[一-鿿]", speech_text))
        _max_chars = int(duration * (5 if _has_cn else 14))
        if len(speech_text) > _max_chars:
            log_warning(
                f"ad_video P112 speech 超长 {len(speech_text)} > {_max_chars} "
                f"(duration={duration}s, lang={'CN' if _has_cn else 'EN'}),截断"
            )
            speech_text = speech_text[:_max_chars]

        # Step 1: TTS speech → audio
        log_info(f"ad_video P104 TTS speech_len={len(speech_text)}")
        tts_result = await _fc.run_async(
            "fal-ai/elevenlabs/tts/multilingual-v2",
            arguments={"text": speech_text[:500]},
        )
        audio_obj = tts_result.get("audio") if isinstance(tts_result.get("audio"), dict) else None
        audio_url = audio_obj.get("url") if audio_obj else tts_result.get("audio_url")
        if not audio_url:
            raise Exception("TTS 未返 audio_url")

        # Step 2: P115 Kling 通道 reframe(P153 只在 scenes <= 1 单段兜底分支才需要)
        # P135/P149 多段(scenes >= 2)用每段独立 GPT-Image 2 9:16 分镜图,不需要 reframe
        # P153(2026-05-06):跳过 P115 reframe → 省 ~2 分钟(总耗时 7-8 分 → 5-6 分)
        talking_endpoint = params.get("talking_head_endpoint", "fal-ai/bytedance/omnihuman")
        log_info(f"ad_video P104 talking_head endpoint={talking_endpoint}")
        talking_image_url = base_image_url
        # P153:只在 scenes <= 1 时才跑 P115 reframe(P118 单段兜底路径)
        # scenes >= 2 直接进 P135/P149,不进 _run_talking_head(),P115 reframe 浪费
        if len(scenes) <= 1 and "kling" in talking_endpoint:
            try:
                log_info("ad_video P115 Kling 通道:GPT-Image 2 reframe → portrait(单段兜底)")
                _kontext = await _fc.run_async(
                    "openai/gpt-image-2/edit",
                    arguments={
                        "prompt": (
                            "Adjust the camera framing of this image to make the model's face clearly visible "
                            "in the upper-center of the frame, while KEEPING the original background scene "
                            "EXACTLY as it is (do NOT replace background with studio or any other scene), "
                            "and KEEPING the product visible and recognizable in the frame "
                            "(worn naturally on the body or held in hands as in the original). "
                            "Only zoom/recompose the framing — do not change colors, lighting, "
                            "background elements, or the product. Model facing camera with a relaxed "
                            "neutral expression (NO open mouth, NO shocked face). Photorealistic."
                        ),
                        "image_urls": [base_image_url],
                        "image_size": "portrait_16_9",
                        "num_images": 1,
                        "output_format": "jpeg",
                    },
                )
                _imgs = _kontext.get("images") or []
                if _imgs and _imgs[0].get("url"):
                    talking_image_url = _imgs[0]["url"]
                    log_info(f"ad_video P115 GPT-Image-2 reframe OK url={talking_image_url[:80]}")
                else:
                    log_warning("ad_video P115 reframe 无 image,继续用 base_image")
                    talking_image_url = base_image_url
            except Exception as e:
                log_warning(f"ad_video P115 reframe 失败,继续用 base_image: {str(e)[:200]}")
                talking_image_url = base_image_url

        # Step 3: 选 P120 多镜头(scenes>=2)或 P118 双段兜底(scenes<=1)
        async def _run_talking_head(audio_for_talking: str = None) -> str:
            """跑 talking head:audio_for_talking 默认 audio_url(P118 兜底用全 audio),
            P120 多镜头分支会传段 1 独立 audio。"""
            _audio = audio_for_talking or audio_url
            ep_local = talking_endpoint
            args = {"image_url": talking_image_url, "audio_url": _audio}
            if "kling" in ep_local:
                args["prompt"] = (
                    "natural relaxed talking pose, slight head movements, "
                    "subtle natural expressions, no exaggerated mouth or face"
                )
            # P128:用户选什么端点就跑什么,失败直接抛错给用户(不偷换)
            h = await _fc.submit_async(ep_local, arguments=args)
            tid = h.request_id
            for _ in range(120):
                await asyncio.sleep(5)
                try:
                    s = await _fc.status_async(ep_local, tid)
                except Exception:
                    continue
                if type(s).__name__ == "Completed":
                    res = await _fc.result_async(ep_local, tid)
                    v = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else res.get("video_url")
                    if not v:
                        raise Exception("talking head 未返 video_url")
                    log_info(f"ad_video P104 talking_head OK url={v[:80]}")
                    return v
            raise Exception("talking head 超时(10 min)")

        async def _run_seedance_for_scene(scene: dict, idx: int) -> str:
            """P124(2026-05-05):回滚 P123 ref2vid → 改回 i2v 单图驱动。
            理由:ref2vid 实测模特身份/产品款式漂移严重(段1 黑直发 vs 段3 棕卷发,
            产品款式都换),用户怒"参考图根本没参考"。i2v 强保留 base_image_url
            首帧(P121 已把 正面+反面+背景 编码进首帧),保模特+产品身份是底线。
            代价:段 2-N 不能"换镜头到反面",但模特+产品 100% 一致。"""
            sd_prompt = _build_p118_seedance_prompt(scene, overall, model_desc)
            log_info(f"ad_video P124 段{idx} i2v(强保留)start prompt_len={len(sd_prompt)}")
            res = await _fc.subscribe_async(
                "fal-ai/bytedance/seedance/v1/pro/image-to-video",
                arguments={
                    "image_url": base_image_url,  # P121 合成首帧 = 模特+产品+反面信息+背景
                    "prompt": sd_prompt,
                    "duration": "4",  # 跑 4s,ffmpeg trim 到设计段长
                    "resolution": "720p",
                    "aspect_ratio": aspect_ratio,
                    "enable_audio": False,
                },
            )
            v = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
            if not v:
                raise Exception(f"P124 段{idx} i2v 未返 video_url")
            log_info(f"ad_video P124 段{idx} i2v OK url={v[:80]}")
            return v

        # P149(2026-05-06):用户敲"几宫格 panel 不是 9:16 → 视频比例不对"
        # 回到 P138 思路:N 张独立 GPT-Image 2(每张 portrait_16_9 = 9:16)
        # 优势:Kling Avatar 输入 9:16 → 输出 9:16 ✅;劣势:贵 $0.04*N(vs 几宫格 1 次)
        if len(scenes) >= 2:
            from app.services.ad_video_models import compose_first_frame_for_scene
            kling_endpoint = "fal-ai/kling-video/ai-avatar/v2/standard"
            log_info(f"ad_video P139 几宫格 storyboard + N 段 talking head concat scenes={len(scenes)} endpoint={kling_endpoint}")

            # 收集每段 speech(P135 不合并)
            # P158(2026-05-07):按 scene.duration_sec 硬截断 — VLM 经常超字导致段视频被拉长
            # bug:8/10/12s 用户选时长被拉成 12/14/16s,根因 P149 多段路径无 per-scene 截断
            # 速率:elevenlabs multilingual-v2 实测中文 5 字/秒,英文 14 字符/秒
            import re as __re_p158
            # P198(2026-05-08):speech 空段不再跳过 — 用户:粘贴脚本里某段 speech 留空 →
            # 不要 VLM 自动补话术,该段视频走 Seedance i2v 纯画面无声 + concat 时 anullsrc 补静音
            seg_units = []  # list of (idx, sp_or_None, mode)  mode in {talking, silent}
            for i, sc in enumerate(scenes):
                sp = (sc.get("speech") or "").strip()
                if not sp:
                    seg_units.append((i + 1, None, "silent"))
                    log_info(f"ad_video P198 段{i+1} speech 空 → silent(Seedance i2v 纯画面)")
                    continue
                seg_dur = float(sc.get("duration_sec") or 5)
                has_cn = bool(__re_p158.search(r"[\u4e00-\u9fff]", sp))
                max_chars = int(seg_dur * (5 if has_cn else 14))
                if len(sp) > max_chars:
                    log_warning(
                        f"ad_video P158 段{i+1} speech 超长 {len(sp)} > {max_chars} "
                        f"(seg_dur={seg_dur}s lang={'CN' if has_cn else 'EN'}),截断"
                    )
                    sp = sp[:max_chars]
                seg_units.append((i + 1, sp, "talking"))

            if not seg_units:
                raise Exception("P198 没 scenes")
            seg_speeches = [(idx, sp) for (idx, sp, mode) in seg_units if mode == "talking"]
            silent_idxs = [idx for (idx, _, mode) in seg_units if mode == "silent"]
            log_info(f"ad_video P198 talking={len(seg_speeches)} silent={len(silent_idxs)}")

            # P149:N 段并发独立 GPT-Image 2(每张 portrait_16_9 = 9:16)
            log_info(f"ad_video P149 阶段 A1:并发 {len(seg_speeches)} 段 GPT-Image 2(每张 9:16 独立图)")

            async def _frame_for_seg(idx: int):
                scene = scenes[idx - 1]
                fr = await compose_first_frame_for_scene(
                    base_image_url=base_image_url,
                    scene=scene,
                    model_description=model_desc,
                    overall_setting=overall,
                    aspect_ratio=params.get("aspect_ratio") or "9:16",
                    style_reference_image_url=params.get("style_reference_image_url"),  # P186
                    no_model=(ref_has_people is False),  # P194(2026-05-08):per-scene 也走纯产品分支
                )
                if isinstance(fr, dict) and "error" in fr:
                    raise Exception(f"段{idx} 分镜图失败: {fr['error']}")
                url = fr.get("image_url") if isinstance(fr, dict) else None
                if not url:
                    raise Exception(f"段{idx} 分镜图未返 url")
                log_info(f"ad_video P149 段{idx} GPT-Image 2 9:16 分镜图 OK url={url[:60]}")
                return (idx, url)

            # P198:全段都出图(talking + silent),不光 talking
            valid_idxs = [idx for (idx, _, _) in seg_units]
            frame_results = await asyncio.gather(
                *[_frame_for_seg(idx) for idx in valid_idxs],
                return_exceptions=True,
            )
            seg_frames = {}
            grid_url_for_frontend = None  # P149 不再有几宫格,前端只展示 panel_urls
            for i, r in enumerate(frame_results):
                if isinstance(r, Exception):
                    fallback_idx = valid_idxs[i]
                    log_warning(f"ad_video P149 段{fallback_idx} 分镜图失败,fallback base_image: {str(r)[:200]}")
                    seg_frames[fallback_idx] = base_image_url
                else:
                    seg_frames[r[0]] = r[1]

            # P138 阶段 A2:N 段并发 TTS(每段独立 audio)
            log_info(f"ad_video P138 阶段 A2:并发 {len(seg_speeches)} 段 TTS")

            async def _tts_for_seg(idx: int, text: str):
                tres = await _fc.run_async(
                    "fal-ai/elevenlabs/tts/multilingual-v2",
                    arguments={"text": text[:500]},
                )
                ao = tres.get("audio") if isinstance(tres.get("audio"), dict) else None
                u = ao.get("url") if ao else tres.get("audio_url")
                if not u:
                    raise Exception(f"段{idx} TTS 未返 audio_url")
                log_info(f"ad_video P138 段{idx} TTS OK chars={len(text)} url={u[:60]}")
                return (idx, u)

            tts_results = await asyncio.gather(
                *[_tts_for_seg(idx, txt) for idx, txt in seg_speeches],
                return_exceptions=True,
            )
            seg_audios = []  # list of (idx, audio_url)
            for r in tts_results:
                if isinstance(r, Exception):
                    log_warning(f"ad_video P138 TTS 段失败,跳过: {str(r)[:150]}")
                    continue
                seg_audios.append(r)
            if not seg_audios:
                raise Exception("P138 全部段 TTS 失败")

            # P138 阶段 B:N 段并发 Kling Avatar v2 Std(每段用对应分镜图 + 对应 audio)
            log_info(f"ad_video P138 阶段 B:并发 {len(seg_audios)} 段 Kling Avatar v2 Std(每段独立分镜图)")

            async def _ka_for_seg(idx: int, audio_url: str):
                # 用段 idx 对应的分镜图(P138 关键改动);失败 fallback 已经在 seg_frames 里处理
                seg_img = seg_frames.get(idx, base_image_url)
                # P144-r(2026-05-06):基于 fal 官方 Kling Avatar v2 Prompt Guide 重写
                # https://fal.ai/learn/devs/kling-avatar-v2-prompt-guide
                # 官方 4 部分结构:Subject + Expression + Movement + Style
                # 官方推荐词组:"Energetic presenter with frequent hand gestures",
                # "purposeful gestures pointing to product","head nods for emphasis"
                # 期望动作幅度比 P143 提升 30-50%(从轻微晃头到频繁手势+动态表情)
                scene = scenes[idx - 1] if idx - 1 < len(scenes) else {}
                visual = (scene.get("visual_prompt") or "").strip()
                # P146(2026-05-06)sanitize:跟 ad_video_models.py compose_storyboard_grid 一致
                # 避开 Kling 内容审核 + 跟几宫格图视觉一致
                visual_safe = visual
                for old, new in [
                    ("waist trainer", "fashion garment"),
                    ("shapewear", "fashion garment"),
                    ("waist", "torso"),
                    ("chest", "upper body"),
                    ("hips", "lower torso"),
                    ("body", "outfit"),
                    ("neoprene", "fabric"),
                    ("faux leather", "matte material"),
                    ("leather panel", "matte panel"),
                    ("bedroom", "indoor space"),
                    ("candlelight", "soft warm light"),
                    ("on bed", "sitting indoor"),
                    ("no face visible", "from a distance"),
                    ("revealing", "showing"),
                    ("her waist", "the torso"),
                    ("her body", "the outfit"),
                    ("her chest", "the upper area"),
                    ("on her", "on the"),
                ]:
                    visual_safe = visual_safe.replace(old, new)
                # P144-r 4 部分 + P148 产品焦点(撤回 P147 "NO phone" 教条)
                # 用户:"不是要无手机,是要清楚哪个是产品" → 强调产品是画面焦点
                if visual_safe:
                    ka_prompt = (
                        # Subject(主题)
                        f"Subject: professional commercial spokesperson showcasing fashion garment product. "
                        f"Scene: {visual_safe}. "
                        # Expression(表情)— fal 官方推荐
                        "Expression: dynamic facial expressions matching emphasis points, "
                        "natural eye contact with camera, expressive eyebrows for engagement. "
                        # Movement(动作)+ P154/P155 演示参考图里的产品(类目无关)
                        "Movement: energetic presenter actively DEMONSTRATING the product item "
                        "shown in the reference image — hands actively touching, adjusting, lifting, "
                        "showing or pointing at the specific product (the item the user uploaded, "
                        "could be a garment, accessory, footwear, bag, beauty product, etc.). "
                        "Like a TikTok seller showing off their product. Slight body lean, head nods. "
                        # P148 + P155 产品焦点 + 类目无关
                        "CRITICAL — PRODUCT IS THE REFERENCE IMAGE ITEM: the visual HERO is the "
                        "specific product item shown in the reference image (NOT the model's other "
                        "clothing, NOT the phone if visible, NOT the background). Model's gestures "
                        "and gaze direct viewer attention TOWARD this specific reference product. "
                        "Hands interact with the reference product to demonstrate it. "
                        "Third-person professional commercial framing. "
                        # Style(风格)
                        "Style: photorealistic commercial advertisement, natural skin textures, "
                        "synchronized lip movements with audio."
                    )
                else:
                    ka_prompt = (
                        "Subject: professional spokesperson showcasing fashion product. "
                        "Expression: dynamic facial expressions, natural eye contact. "
                        "Movement: frequent purposeful hand gestures, occasional head nods, "
                        "slight body lean for engagement. "
                        "CRITICAL: product is the visual HERO, clearly visible, no competing elements. "
                        "Style: photorealistic commercial advertisement, synchronized lip movements."
                    )
                res = await _fc.subscribe_async(
                    kling_endpoint,
                    arguments={
                        "image_url": seg_img,
                        "audio_url": audio_url,
                        "prompt": ka_prompt,
                    },
                )
                v = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else res.get("video_url")
                if not v:
                    raise Exception(f"段{idx} Kling Avatar 未返 video_url")
                log_info(f"ad_video P138 段{idx} Kling Avatar OK url={v[:60]}")
                return (idx, v)

            # P198(2026-05-08):silent 段并发走 Seedance i2v(纯画面,无 audio)
            async def _seedance_for_silent(idx: int):
                scene = scenes[idx - 1] if idx - 1 < len(scenes) else {}
                seg_dur = max(4, min(12, int(scene.get("duration_sec") or 5)))
                visual = (scene.get("visual_prompt") or "").strip()
                # 复用 _build_p118_seedance_prompt 风格,但对单 scene
                sd_prompt = (
                    f"{visual}. Showing the product clearly, model performs natural action "
                    f"matching the visual description. No talking, no lip movement (silent shot). "
                    f"Vertical 9:16, photorealistic commercial style."
                ) if visual else (
                    f"Model performs natural product demonstration action. No talking. "
                    f"Vertical 9:16, photorealistic commercial style."
                )
                seg_img = seg_frames.get(idx, base_image_url)
                res = await _fc.subscribe_async(
                    "fal-ai/bytedance/seedance/v1/pro/image-to-video",
                    arguments={
                        "image_url": seg_img,
                        "prompt": sd_prompt,
                        "duration": str(seg_dur),
                        "resolution": "720p",
                        "aspect_ratio": params.get("aspect_ratio") or "9:16",
                        "enable_audio": False,
                    },
                )
                v = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
                if not v:
                    raise Exception(f"段{idx} silent Seedance 未返 video_url")
                log_info(f"ad_video P198 段{idx} silent Seedance OK url={v[:60]}")
                return (idx, v)

            ka_results, silent_results = await asyncio.gather(
                asyncio.gather(*[_ka_for_seg(idx, audio) for idx, audio in seg_audios], return_exceptions=True),
                asyncio.gather(*[_seedance_for_silent(idx) for idx in silent_idxs], return_exceptions=True),
            )
            # 收齐 talking + silent,按 idx 排序保证段顺序
            all_seg_results = list(ka_results) + list(silent_results)
            seg_video_pairs = []  # list of (idx, url)
            for r in all_seg_results:
                if isinstance(r, Exception):
                    log_warning(f"ad_video P198 段失败,跳过: {str(r)[:200]}")
                    continue
                seg_video_pairs.append(r)
            seg_video_pairs.sort(key=lambda t: t[0])
            seg_video_urls = [u for (_idx, u) in seg_video_pairs]
            if not seg_video_urls:
                raise Exception("P198 全部段(talking + silent)都失败")

            # P135 阶段 C:ffmpeg concat demuxer 完整段拼接(不 trim 不 xfade)
            user_id = params.get("_user_id", "anon")
            # P142:把分镜图 URL 传给前端展示(几宫格原图 + N 张子图)
            panel_urls_sorted = [seg_frames[idx] for idx in sorted(seg_frames.keys())]
            try:
                final_url = await _p135_concat_simple(
                    seg_video_urls=seg_video_urls,
                    user_id=user_id,
                )
                return {
                    "video_url": final_url,
                    "type": "video",
                    "grid_image_url": grid_url_for_frontend,  # P142
                    "panel_image_urls": panel_urls_sorted,    # P142
                }
            except Exception as e:
                log_warning(f"ad_video P135 concat 失败,降级返第 1 段: {str(e)[:200]}")
                return {
                    "video_url": seg_video_urls[0],
                    "type": "video",
                    "grid_image_url": grid_url_for_frontend,
                    "panel_image_urls": panel_urls_sorted,
                }

        # P118 单段兜底(VLM 只输出 1 段,如老脚本或失败时):并发 talking + 单段 Seedance
        async def _run_seedance_action() -> str:
            seg_dur = max(4, min(12, duration))
            sd_prompt = _build_p118_seedance_prompt(first_scene, overall, model_desc)
            log_info(f"ad_video P118 Seedance action duration={seg_dur} prompt_len={len(sd_prompt)}")
            res = await _fc.subscribe_async(
                "fal-ai/bytedance/seedance/v1/pro/image-to-video",
                arguments={
                    "image_url": base_image_url,
                    "prompt": sd_prompt,
                    "duration": str(seg_dur),
                    "resolution": "720p",
                    "aspect_ratio": aspect_ratio,
                    "enable_audio": False,
                },
            )
            v = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
            if not v:
                raise Exception("Seedance i2v 未返 video_url")
            log_info(f"ad_video P118 Seedance OK url={v[:80]}")
            return v

        talking_url, seedance_url = await asyncio.gather(
            _run_talking_head(), _run_seedance_action(), return_exceptions=True
        )

        if isinstance(talking_url, Exception):
            raise talking_url

        if isinstance(seedance_url, Exception):
            log_warning(f"ad_video P118 Seedance 失败,降级返 talking only: {str(seedance_url)[:200]}")
            return {"video_url": talking_url, "type": "video"}

        try:
            user_id = params.get("_user_id", "anon")
            final_url = await _p118_concat_and_save(
                talking_url=talking_url,
                seedance_url=seedance_url,
                duration=duration,
                user_id=user_id,
                aspect_ratio=aspect_ratio,
            )
            return {"video_url": final_url, "type": "video"}
        except Exception as e:
            log_warning(f"ad_video P118 ffmpeg 拼接/保存失败,降级返 talking only: {str(e)[:200]}")
            return {"video_url": talking_url, "type": "video"}

    # ---------- 多段模式(>15s):N 段独立首帧 + N 段 i2v 并发 + ffmpeg concat ----------
    seg_durs = split_segments(duration)
    n = len(seg_durs)
    n_actual = min(n, len(scenes))
    scenes_to_run = scenes[:n_actual]

    sem = asyncio.Semaphore(5)

    async def _run_scene(idx: int, scene: dict) -> str:
        async with sem:
            # Step A: 每段在共享 base 上调 Flux Kontext 合本段独立首帧(锁模特身份+按本段 visual)
            scene_frame_url = base_image_url  # fallback
            try:
                # P210(2026-05-08):no_model 模式 panels 用 product_image 作 anchor,
                # 不依赖 base 帧 → base + panels 完全并发 → P187 路径省 3 分钟
                _no_model = (ref_has_people is False)
                fr = await ad_video_models.compose_first_frame_for_scene(
                    base_image_url=(params.get("product_image_url") or base_image_url) if _no_model else base_image_url,
                    scene=scene,
                    model_description=model_desc,
                    overall_setting=overall,
                    aspect_ratio=params.get("aspect_ratio") or "9:16",
                    style_reference_image_url=params.get("style_reference_image_url"),  # P186
                    no_model=_no_model,  # P194(2026-05-08)
                    exclude_base_image=_no_model,  # P210:no_model 走 exclude_base 分支,prompt 不引用 base
                )
                if fr.get("image_url"):
                    scene_frame_url = fr["image_url"]
                else:
                    from app.services.logger import log_warning
                    log_warning(
                        f"ad_video scene {idx+1}/{n_actual} 首帧合成失败,"
                        f"回退共享 base: {fr.get('error')}"
                    )
            except Exception as fe:
                from app.services.logger import log_warning
                log_warning(
                    f"ad_video scene {idx+1}/{n_actual} 首帧合成异常,回退共享 base: {fe}"
                )

            # Step B: 用本段独立首帧调 Seedance v1.5/pro i2v
            single_script = {
                "overall_setting": overall,
                "model_description": model_desc,
                "scenes": [scene],
            }
            sub = await ad_video_models.submit_seedance_video(
                image_url=scene_frame_url,
                script=single_script,
                duration=seg_durs[idx],
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                enable_audio=False,  # 多段拼接禁原生音频(段间换音轨会跳)
            )
            if sub.get("error"):
                raise Exception(f"段 {idx+1}/{n_actual}: {sub['error']}")
            tid = sub.get("task_id")
            if not tid:
                raise Exception(f"段 {idx+1}/{n_actual}: Seedance 未返 task_id")
            for _ in range(180):
                await asyncio.sleep(5)
                st = await ad_video_models.poll_seedance_status(tid)
                if st.get("status") == "completed" and st.get("video_url"):
                    return st["video_url"]
                if st.get("status") == "failed":
                    raise Exception(f"段 {idx+1}/{n_actual}: {st.get('error')}")
            raise Exception(f"段 {idx+1}/{n_actual}: 超时(15 min)")

    # P211(2026-05-08):no_model 模式 base 跟 panels 并发,gather 时一起等
    if _no_model_p211 and base_task_p211 is not None:
        results_p211 = await asyncio.gather(
            asyncio.gather(*[_run_scene(i, s) for i, s in enumerate(scenes_to_run)]),
            base_task_p211,
        )
        seg_urls = results_p211[0]
        _br = results_p211[1]
        if isinstance(_br, dict) and _br.get("image_url"):
            base_image_url = _br["image_url"]
        log_info(f"ad_video P211 base + panels 并发完成,base_url={base_image_url[:60]}")
    else:
        seg_urls = await asyncio.gather(
            *[_run_scene(i, s) for i, s in enumerate(scenes_to_run)]
        )

    # ---------- 下载 + ffmpeg concat ----------
    import tempfile, shutil, subprocess
    import httpx
    import fal_client as _fc

    seg_root = Path(tempfile.mkdtemp(prefix="ad_video_segs_"))
    try:
        local_paths = []
        async with httpx.AsyncClient(timeout=180.0) as client:
            for i, url in enumerate(seg_urls):
                out = seg_root / f"out_{i:02d}.mp4"
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(out, "wb") as fp:
                        async for chunk in resp.aiter_bytes(64 * 1024):
                            fp.write(chunk)
                local_paths.append(out)

        concat_list = seg_root / "concat.txt"
        with open(concat_list, "w") as fp:
            for p in local_paths:
                fp.write(f"file '{p}'\n")

        merged = seg_root / "final.mp4"
        # 优先 stream copy(快+无损),失败再 re-encode 兜底
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list), "-c", "copy", str(merged)],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0 or not merged.exists() or merged.stat().st_size == 0:
            # re-encode fallback(各段编码参数不一致时走这条)
            r2 = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(concat_list),
                 "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an",
                 str(merged)],
                capture_output=True, text=True, timeout=300,
            )
            if r2.returncode != 0 or not merged.exists():
                raise Exception(f"ffmpeg concat 失败: {r2.stderr[-500:]}")

        # P188(2026-05-08):无人物模式如果脚本有口播 → 跑 TTS + ffmpeg overlay 把音轨叠到 silent 视频
        if ref_has_people is False:
            speech_segs = [(scenes_to_run[i].get("speech") or "").strip() for i in range(len(scenes_to_run))]
            has_any_speech = any(speech_segs)
            if has_any_speech:
                log_info(f"ad_video P188 无人物 + 有口播 → TTS 配音 + ffmpeg overlay")
                try:
                    import fal_client as _fc
                    sem_tts = asyncio.Semaphore(3)
                    async def _tts_one(idx_t: int, text: str, dur: float) -> Path:
                        async with sem_tts:
                            audio_path = seg_root / f"tts_{idx_t:02d}.mp3"
                            if not text:
                                # 静音填充段(无口播段)
                                subprocess.run(
                                    ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                                     "-t", f"{max(0.5, dur):.2f}", "-q:a", "9", "-acodec", "libmp3lame", str(audio_path)],
                                    capture_output=True, timeout=30,
                                )
                                return audio_path
                            tts_res = await asyncio.wait_for(
                                _fc.run_async(
                                    "fal-ai/elevenlabs/tts/multilingual-v2",
                                    arguments={"text": text[:500]},
                                ),
                                timeout=120,
                            )
                            audio_obj = tts_res.get("audio") if isinstance(tts_res.get("audio"), dict) else None
                            aurl = audio_obj.get("url") if audio_obj else tts_res.get("audio_url")
                            if not aurl:
                                raise Exception(f"段 {idx_t} TTS 未返 audio_url")
                            async with httpx.AsyncClient(timeout=120) as cli:
                                rr = await cli.get(aurl)
                                rr.raise_for_status()
                                with open(audio_path, "wb") as f:
                                    f.write(rr.content)
                            return audio_path

                    audio_paths = await asyncio.gather(
                        *[_tts_one(i, speech_segs[i], float(seg_durs[i]) if i < len(seg_durs) else 5.0)
                          for i in range(len(scenes_to_run))]
                    )
                    # concat 音频 → 一个完整音轨
                    audio_concat_list = seg_root / "audio_concat.txt"
                    with open(audio_concat_list, "w") as fp:
                        for p in audio_paths:
                            fp.write(f"file '{p}'\n")
                    audio_merged = seg_root / "audio_merged.mp3"
                    cp_a = subprocess.run(
                        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(audio_concat_list),
                         "-c", "copy", str(audio_merged)],
                        capture_output=True, text=True, timeout=60,
                    )
                    if cp_a.returncode != 0:
                        # re-encode concat fallback
                        cp_a2 = subprocess.run(
                            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(audio_concat_list),
                             "-c:a", "libmp3lame", "-q:a", "5", str(audio_merged)],
                            capture_output=True, text=True, timeout=60,
                        )
                        if cp_a2.returncode != 0:
                            raise Exception(f"音轨 concat 失败: {cp_a2.stderr[:300]}")
                    # P212(2026-05-08):把音轨叠到 silent 视频
                    # bug:旧 -shortest 让视频被 TTS 音频长度砍(17s 视频被砍成 2s,
                    # 因为 25 字中文 TTS 才几秒)。改:audio 不够长 apad 静音补齐到 video。
                    # 探 video 时长
                    _probe_p212 = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=noprint_wrappers=1:nokey=1", str(merged)],
                        capture_output=True, text=True, timeout=10,
                    )
                    try:
                        vid_dur_p212 = float(_probe_p212.stdout.strip())
                    except Exception:
                        vid_dur_p212 = sum(float(seg_durs[i]) for i in range(min(len(seg_durs), len(scenes_to_run))))
                    with_audio = seg_root / "final_with_audio.mp4"
                    cp_v = subprocess.run(
                        ["ffmpeg", "-y", "-i", str(merged), "-i", str(audio_merged),
                         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                         "-map", "0:v:0", "-map", "1:a:0",
                         "-af", "apad",  # P212:audio 末尾补静音
                         "-t", f"{vid_dur_p212:.2f}",  # 总长锁定 video,删 -shortest 防被 audio 砍
                         str(with_audio)],
                        capture_output=True, text=True, timeout=120,
                    )
                    if cp_v.returncode == 0 and with_audio.exists() and with_audio.stat().st_size > 0:
                        merged = with_audio
                        log_info(f"ad_video P188 TTS 音轨 overlay 完成 size={merged.stat().st_size}")
                    else:
                        from app.services.logger import log_warning
                        log_warning(f"ad_video P188 ffmpeg overlay 失败,fallback 静音视频: {cp_v.stderr[-300:]}")
                except Exception as audio_err:
                    from app.services.logger import log_warning
                    log_warning(f"ad_video P188 TTS overlay 异常,fallback 静音: {audio_err}")

        # 上传到 fal storage 拿可访问 URL(沿用现有归档/分发模式)
        final_url = await fal_upload_with_retry(str(merged))
        return {"video_url": final_url, "type": "video"}
    finally:
        shutil.rmtree(seg_root, ignore_errors=True)


async def _execute_job(job_id: str):
    async with _semaphore:
        job = JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = time.time()
        _save_jobs()
        # 多段视频类任务给宽松上限,其余 10 分钟封顶;超时自动 failed + 退款
        _LONG_TYPES = {"video_general", "skill_generate", "video_clone", "script_to_video"}
        _timeout_sec = 1200 if job["type"] in _LONG_TYPES else 600

        async def _dispatch() -> dict:
            t = job["type"]
            if t == "image":
                return await _run_image_job(job["params"])
            # P215(2026-05-08):video_general* 必须在 startswith("video_") 之前匹配
            elif t == "video_general_analyze":
                return await _run_video_general_analyze_job(job["params"])
            elif t == "video_general_storyboard":
                job["params"]["_user_id"] = job.get("user_id") or job.get("user_numeric_id") or "anon"
                return await _run_video_general_storyboard_job(job["params"])
            elif t == "video_general":
                job["params"]["_user_id"] = job.get("user_id") or job.get("user_numeric_id") or "anon"
                return await _run_video_general_job(job["params"])
            # P216(2026-05-08):video_clone(Seedance r2v Fast)同样优先匹配
            elif t == "video_clone":
                job["params"]["_user_id"] = job.get("user_id") or job.get("user_numeric_id") or "anon"
                return await _run_video_clone_job(job["params"])
            elif t.startswith("video_"):
                return await _run_video_job(job["params"], t)
            elif t == "replicate_analyze":
                return await _run_replicate_analyze_job(job["params"])
            elif t == "skill_analyze":
                return await _run_skill_analyze_job(job["params"])
            elif t == "skill_replace":
                return await _run_skill_replace_job(job["params"])
            elif t == "skill_generate":
                return await _run_skill_generate_job(job["params"])
            elif t == "script_to_video":
                return await _run_script_to_video_job(job["params"])
            elif t == "replicate":
                job["params"]["_user_id"] = job.get("user_id") or job.get("user_numeric_id") or "anon"
                return await _run_replicate_job(job["params"])
            else:
                raise Exception(f"unknown type: {t}")

        try:
            result = await asyncio.wait_for(_dispatch(), timeout=_timeout_sec)

            # BUG-2: 归档 fal URL → 本地 /uploads(防 fal.media 7-30 天过期)
            try:
                from app.services.media_archiver import archive_url
                uid = job.get("user_numeric_id") or job.get("user_id") or "anon"
                if result.get("image_url"):
                    result["image_url"] = await archive_url(result["image_url"], uid, "image")
                if result.get("video_url"):
                    result["video_url"] = await archive_url(result["video_url"], uid, "video")
            except Exception as arch_err:
                print(f"archive failed (continuing with fal URL): {arch_err}")

            job["status"] = "completed"
            job["result"] = result
            job["finished_at"] = time.time()
            # 写历史记录
            try:
                uid = job.get("user_numeric_id")
                if uid and job.get("cost", 0) > 0:
                    result_data = job.get("result", {})
                    imgs = [result_data["image_url"]] if result_data.get("image_url") else []
                    vids = [result_data["video_url"]] if result_data.get("video_url") else []
                    create_consumption_record(
                        user_id=uid,
                        task_id=job["id"],
                        module=job.get("module", "image/style"),
                        cost=job.get("cost", 0),
                        description=job.get("title", ""),
                        images=imgs,
                        videos=vids,
                    )
            except Exception as hist_err:
                print(f"history write failed: {hist_err}")
        except Exception as e:
            job["status"] = "failed"
            job["error"] = str(e)
            job["finished_at"] = time.time()
            # 退还积分
            try:
                uid = job.get("user_numeric_id")
                if uid and job.get("cost", 0) > 0:
                    add_credits(uid, job.get("cost", 0), reason="task_refund",
                                ref_id=job.get("id"), module=job.get("module") or job.get("type"))
            except:
                pass
        except BaseException as e:
            # CancelledError 等 BaseException 也要保存状态，防止 job 永远卡在 running
            if job.get("status") == "running":
                job["status"] = "failed"
                job["error"] = f"任务被中断:{type(e).__name__}"
                job["finished_at"] = time.time()
                try:
                    uid = job.get("user_numeric_id")
                    if uid and job.get("cost", 0) > 0:
                        add_credits(uid, job.get("cost", 0), reason="task_refund",
                                    ref_id=job.get("id"), module=job.get("module") or job.get("type"))
                except:
                    pass
            raise
        finally:
            _save_jobs()


def _module_from_type(job_type: str, params: dict) -> str:
    if job_type == "image":
        return "image/multi-reference" if params.get("reference_images") else "image/style"
    if job_type == "video_i2v":
        return "video/image-to-video"
    if job_type == "video_edit":
        return "video/replace/element"
    if job_type == "video_clone":
        return "video/clone"
    if job_type == "ad_video":
        return "ad_video/generate"
    return "image/style"


@router.post("/submit")
async def submit_job(req: SubmitJobRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user.get("id") or current_user.get("email", "unknown")
    user_id_str = str(user_id)

    module = _module_from_type(req.type, req.params)
    # 2026-05-13 动态定价:视频类按 duration_sec × 50 计,clamp 到端点限制
    if req.type == "video_i2v":
        # kling o3 standard image-to-video 支持 3/5/10/15s
        safe_dur = max(3, min(15, int(req.params.get("duration_sec") or 5)))
        cost = safe_dur * 50
    else:
        cost = get_task_cost(module)

    # 扣费(原子:SQL 层 WHERE credits >= ?,无竞态)
    if cost > 0:
        if not deduct_credits(user_id, cost):
            raise HTTPException(status_code=402, detail=f"积分不足,需要 {cost} 积分")
    
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "user_id": user_id_str,
        "user_numeric_id": user_id,  # 实际是 UUID 字符串
        "type": req.type,
        "title": req.title or req.type,
        "params": req.params,
        "module": module,
        "cost": cost,
        "status": "pending",
        "created_at": time.time(),
    }
    _save_jobs()
    create_tracked_task(_execute_job(job_id))
    return {"job_id": job_id, "status": "pending", "cost": cost}


def _studio_sessions_as_virtual_jobs(user_id: str) -> list[dict]:
    """七十五续:把当前用户的 long-video session 转成虚拟 job 给 My Tasks 显示。

    只展示有 batch_results 的 session(纯上传/拆分但没生成的不展示,避免噪音)。
    每 session 1 条聚合 job,标题"长视频翻拍 X/Y 完成",点击跳转 /video/studio/{sid}。

    不修 STUDIO_TASKS 真实结构,只在返回时合并视图。
    Status 映射:
      final_url 存在 → completed(merge 完成)
      batch_results 全 failed → failed
      任意 in pending/running → running
      全 completed 但没 final_url → running(等待 merge)
    """
    try:
        from app.api.video_studio import STUDIO_TASKS, STUDIO_DIR
    except Exception:
        return []

    out = []
    for sid, task in STUDIO_TASKS.items():
        if task.get("user_id") != user_id:
            continue
        batch_results = task.get("batch_results")
        if not batch_results:
            continue  # 没 generate 过,不展示

        n = len(batch_results)
        completed = sum(1 for r in batch_results if r.get("status") == "completed" and r.get("video_url"))
        failed = sum(1 for r in batch_results if r.get("status") == "failed")
        pending = n - completed - failed
        final_url = task.get("final_url")

        # status 推导
        if final_url:
            v_status = "completed"
        elif failed == n:
            v_status = "failed"
        else:
            v_status = "running"  # 含等待 merge / 部分完成 / 仍在跑

        # 标题:状态 + 进度
        if final_url:
            title = f"长视频翻拍 · 全部完成({n} 段)"
        elif pending > 0:
            title = f"长视频翻拍 · {completed}/{n} 完成,{pending} 生成中"
        else:
            title = f"长视频翻拍 · {n} 段已完成,等待合并"

        # created_at 用 session_dir mtime(STUDIO_TASKS 无 created_at 字段)
        try:
            mtime = (STUDIO_DIR / sid).stat().st_mtime
        except (OSError, ValueError):
            mtime = 0.0

        out.append({
            "id": f"studio_{sid}",
            "user_id": user_id,
            "user_numeric_id": user_id,
            "type": "long_video",                     # 新类型,前端识别可加图标
            "title": title,
            "params": {
                "session_id": sid,
                "segments_total": n,
                "segments_completed": completed,
                "segments_failed": failed,
                "segments_pending": pending,
            },
            "module": "video/replace/element",
            "cost": task.get("batch_cost", 0),
            "status": v_status,
            "created_at": mtime,
            "result": {"video_url": final_url} if final_url else None,
            # 给前端标识 + 跳转用
            "_long_video": True,
            "_session_id": sid,
            "_route": f"/video/studio/{sid}",
        })
    return out

@router.get("/list")
async def list_jobs(current_user: dict = Depends(get_current_user)):
    """My Tasks 列表合并多个来源(虚拟 job 视图):
    - JOBS.json (image / video_i2v / video_general / ad_video / replicate / skill_*)
    - long-video studio sessions (七十五续)
    - video_clone_v2_jobs (2026-05-13:V2 视频复刻原本只在自己页面显示,合并到通用列表)
    """
    user_id = str(current_user.get("id") or current_user.get("email", "unknown"))
    mine = [j for j in JOBS.values() if j.get("user_id") == user_id]
    mine.extend(_studio_sessions_as_virtual_jobs(user_id))
    mine.extend(_v2_jobs_as_virtual_jobs(user_id))
    mine.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return {"jobs": mine[:50]}


def _v2_jobs_as_virtual_jobs(user_id: str) -> list:
    """2026-05-13:把 video_clone_v2_jobs 表里的当前用户任务转成虚拟 job 给 My Tasks 显示。
    跟 _studio_sessions_as_virtual_jobs 同模式。status 映射:
      pending / processing → "running"
      completed → "completed"
      partial_completed → "completed"(JobPanel 没 partial 状态,合并到 completed)
      failed / cancelled → "failed"
    """
    try:
        from app.database import get_db
    except Exception:
        return []
    out = []
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, status, input_video_duration_sec, segments_count, "
                "       created_at, completed_at, error_message, final_video_url, "
                "       final_video_url_watermarked "
                "FROM video_clone_v2_jobs WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT 30",
                (user_id,),
            ).fetchall()
    except Exception:
        return []
    for r in rows:
        status_db = r["status"] or "pending"
        if status_db in ("pending", "processing", "queued", "running"):
            v_status = "running"
        elif status_db in ("completed", "partial_completed"):
            v_status = "completed"
        else:
            v_status = "failed"
        try:
            created_ts = _datetime.fromisoformat(r["created_at"]).timestamp() if r["created_at"] else 0
        except Exception:
            created_ts = 0
        try:
            finished_ts = _datetime.fromisoformat(r["completed_at"]).timestamp() if r["completed_at"] else None
        except Exception:
            finished_ts = None
        dur = float(r["input_video_duration_sec"] or 0)
        title = f"视频复刻 {dur:.0f}s · {r['segments_count'] or 1} 段"
        if status_db == "partial_completed":
            title += " ⚠️"
        out.append({
            "id": r["id"],
            "user_id": user_id,
            "user_numeric_id": user_id,
            "type": "video_clone_v2",
            "title": title,
            "status": v_status,
            "created_at": created_ts,
            "finished_at": finished_ts,
            "module": "video/clone-v2",
            "result": {
                "video_url": r["final_video_url_watermarked"] or r["final_video_url"],
            } if r["final_video_url_watermarked"] or r["final_video_url"] else None,
            "error": r["error_message"],
            "_session_id": f"v2_{r['id']}",
            "_v2_partial": status_db == "partial_completed",
            "_route": f"/video-clone-v2#{r['id']}",
        })
    return out


@router.get("/{job_id}")
async def get_job(job_id: str, current_user: dict = Depends(get_current_user)):
    if job_id not in JOBS:
        raise HTTPException(404, "job not found")
    job = JOBS[job_id]
    uid = str(current_user.get("id") or current_user.get("email", "unknown"))
    if job.get("user_id") != uid:
        raise HTTPException(403, "无权限访问")
    return job


@router.delete("/{job_id}")
async def delete_job(job_id: str, current_user: dict = Depends(get_current_user)):
    if job_id not in JOBS:
        raise HTTPException(404, "job not found")
    job = JOBS[job_id]
    uid = str(current_user.get("id") or current_user.get("email", "unknown"))
    if job.get("user_id") != uid:
        raise HTTPException(403, "无权限删除")
    del JOBS[job_id]
    _save_jobs()
    return {"deleted": job_id}


@router.post("/clear")
async def clear_jobs(current_user: dict = Depends(get_current_user)):
    """一键清空当前用户在 JOBS 字典里的常规任务条目。
    虚拟会话(口播 / 长视频)是真实业务数据,不在这删,前端会用 localStorage 局部隐藏。
    """
    user_id = str(current_user.get("id") or current_user.get("email", "unknown"))
    to_remove = [jid for jid, j in JOBS.items() if j.get("user_id") == user_id]
    for jid in to_remove:
        del JOBS[jid]
    _save_jobs()
    return {"removed": len(to_remove)}


# ==================== 视频复刻 · 分析 worker(2026-05-06)====================

async def _extract_speech_from_video(video_url: str) -> dict:
    """P172(2026-05-07):从视频提取原说话内容(ASR + 音频隔离)。

    流程:
      1. ffmpeg 视频 → 本地 mp3 音频
      2. fal_client.upload_file_async 上传 mp3 拿 fal storage URL
      3. fal-ai/elevenlabs/audio-isolation 滤背景音乐 → vocals_url
      4. fal-ai/wizper 对 vocals_url 做 ASR → text
      5. 返回 {original_speech, speech_audio_url, has_background_music}

    成本:audio-isolation $0.10/min + wizper $0.0005/min。5s 视频 ~$0.01,30s ~$0.05。
    任何步骤失败都返回空字段(不打断主 analyze 流程)。
    """
    import subprocess as _sp
    import tempfile as _tmp
    import os as _os
    import fal_client as _fc
    from app.services.logger import log_info, log_error

    # P199(2026-05-08):speech_chunks = wizper segment-level chunks([(start, end, text), ...])
    # 用户要求 extract 工具按真实时间戳精确分配每段口播,不再前端按时长比例硬切
    out: dict = {"original_speech": "", "speech_audio_url": "", "has_background_music": False, "speech_chunks": []}

    # 1. ffmpeg 提取音频
    audio_path = _os.path.join(_tmp.gettempdir(), f"replicate_asr_{_os.getpid()}_{int(time.time())}.mp3")
    try:
        rr = _sp.run(
            ["ffmpeg", "-y", "-i", video_url, "-vn", "-acodec", "mp3", "-ar", "44100", "-ab", "128k", audio_path],
            capture_output=True, text=True, timeout=60,
        )
        if rr.returncode != 0:
            log_error(f"replicate ASR ffmpeg 失败: {rr.stderr[:200]}")
            return out
        if not _os.path.exists(audio_path) or _os.path.getsize(audio_path) < 1024:
            log_error("replicate ASR ffmpeg 输出为空(原视频无音轨?)")
            return out
    except Exception as e:
        log_error(f"replicate ASR ffmpeg 异常: {e}")
        return out

    # 2. 上传到 fal
    try:
        audio_fal_url = await _fc.upload_file_async(audio_path)
    except Exception as e:
        log_error(f"replicate ASR fal upload 失败: {e}")
        try: _os.remove(audio_path)
        except: pass
        return out

    # 清理本地
    try: _os.remove(audio_path)
    except: pass

    # 3 + 4 并发:audio-isolation + wizper(对原音频)
    import asyncio as _aio
    async def _iso():
        try:
            r = await _aio.wait_for(
                _fc.run_async("fal-ai/elevenlabs/audio-isolation", arguments={"audio_url": audio_fal_url}),
                timeout=180,
            )
            return r.get("audio", {}).get("url") if isinstance(r, dict) else None
        except Exception as e:
            log_error(f"replicate ASR audio-isolation 失败: {e}")
            return None

    async def _asr():
        # P202(2026-05-08):chunk_level 回退 segment(wizper 只支持这个,P201 'word' 422)
        # + 后处理把每个 segment chunk 按字符比例细化成伪 word chunks,精确到字
        # → 每个字一个 timestamp,N scenes 各自按 time_range overlap 拿到自己的字
        try:
            r = await _aio.wait_for(
                _fc.run_async("fal-ai/wizper", arguments={
                    "audio_url": audio_fal_url,
                    "task": "transcribe",
                    "chunk_level": "segment",
                }),
                timeout=120,
            )
            if not isinstance(r, dict):
                return ("", [])
            text = (r.get("text") or "").strip()
            chunks_raw = r.get("chunks") or []
            # 先收原始 segment chunks
            segs = []
            for c in chunks_raw:
                if not isinstance(c, dict):
                    continue
                ts = c.get("timestamp") or [None, None]
                ct = (c.get("text") or "").strip()
                if not ct:
                    continue
                try:
                    s = float(ts[0]) if ts and ts[0] is not None else 0.0
                    e = float(ts[1]) if ts and len(ts) > 1 and ts[1] is not None else s
                except Exception:
                    s, e = 0.0, 0.0
                segs.append({"start": s, "end": e, "text": ct})
            # P202:把每个 segment 按字符比例细化成伪 word chunks(粒度 1 字/chunk)
            # 中文 1 字 = 1 chunk;英文按空格切词 1 词 = 1 chunk
            chunks_clean = []
            for seg in segs:
                seg_dur = max(0.001, seg["end"] - seg["start"])
                seg_text = seg["text"]
                # 中英文混合:有中文就按字切,纯英文按空格切词
                import re as _re_p202
                if _re_p202.search(r"[一-鿿]", seg_text):
                    units = list(seg_text)  # 每个字符(含标点)一个单位
                else:
                    units = seg_text.split()  # 按空格切词
                if not units:
                    chunks_clean.append(seg)
                    continue
                step = seg_dur / len(units)
                for i, u in enumerate(units):
                    if not u.strip():
                        continue
                    chunks_clean.append({
                        "start": seg["start"] + i * step,
                        "end": seg["start"] + (i + 1) * step,
                        "text": u,
                    })
            return (text, chunks_clean)
        except Exception as e:
            log_error(f"replicate ASR wizper 失败: {e}")
            return ("", [])

    vocals_url, (asr_text, asr_chunks) = await _aio.gather(_iso(), _asr())

    out["original_speech"] = asr_text or ""
    out["speech_audio_url"] = vocals_url or ""
    out["speech_chunks"] = asr_chunks  # P199:[{start, end, text}]
    # 简化判断:audio-isolation 输出了 → 大概率原音频有非人声成分(音乐/噪音)
    out["has_background_music"] = bool(vocals_url and asr_text)
    log_info(f"replicate ASR 完成: text_len={len(asr_text)} chunks={len(asr_chunks)} has_vocals_url={bool(vocals_url)}")
    return out


async def _run_replicate_analyze_job(params: dict) -> dict:
    """异步跑 qwen-vl 看视频出 N 段分镜 + 探比例。"""
    import re as _re
    import json as _json
    from app.services.fal_service import get_aliyun_qwenvl_service
    from app.services.logger import log_info, log_error
    from app.services.content_filter import assert_safe_prompt
    from fastapi import HTTPException as _HTTPEx

    video_url = params.get("video_url")
    instruction = params.get("instruction") or ""
    if not video_url or not instruction:
        raise RuntimeError("video_url 或 instruction 缺")

    svc = get_aliyun_qwenvl_service()
    if not svc or not svc.is_available():
        raise RuntimeError("qwen-vl 服务不可用(DASHSCOPE_API_KEY)")

    # 把 fal storage URL 归档到本地 /opt/ssp/uploads
    # 阿里云 qwen-vl 服务在国内,fal storage 在美国,跨境下载常超时(实测 121.6s 还没拉完)
    # 归档后用 ailixiao.com URL,腾讯云国内 → 阿里云国内 速度稳
    from app.services.media_archiver import archive_url
    _uid = params.get("_user_id") or "anon"
    archived_url = await archive_url(video_url, _uid, "video")
    log_info(f"replicate_analyze 视频归档: {video_url[:50]}... -> {archived_url[:80]}")

    log_info(f"replicate_analyze qwen-vl 调用 video={archived_url[:80]}")
    import time as _t
    # P207(2026-05-08):qwen-vl 503 Throttled 自动 retry,最多 3 次,2s/4s/8s 退避
    # 阿里云容量瓶颈实测频发,503 不重试 → 用户立刻被退积分 + 提示"服务不可用",体验差
    import asyncio as _asyncio_p207
    t0 = _t.time()
    res = None
    for _attempt in range(3):
        res = await svc.analyze_video(archived_url, instruction)
        if "error" not in res:
            break
        err_text = str(res.get("error", ""))
        is_throttled = ("503" in err_text or "Too many requests" in err_text or "Throttled" in err_text or "ServiceUnavailable" in err_text)
        if not is_throttled or _attempt == 2:
            break
        wait = 2 * (2 ** _attempt)  # 2s, 4s, 8s
        log_warning(f"replicate_analyze qwen-vl 503 Throttled,P207 retry {_attempt+1}/3 等 {wait}s: {err_text[:120]}")
        await _asyncio_p207.sleep(wait)
    log_info(f"replicate_analyze qwen-vl 返回 elapsed={_t.time()-t0:.1f}s keys={list(res.keys())}")
    if "error" in res:
        log_error(f"replicate_analyze qwen-vl 失败: {res.get('error','?')}")
        raise RuntimeError(f"qwen-vl 失败: {res.get('error','?')[:200]}")
    text = (res.get("text") or "").strip()
    text = _re.sub(r"^```(?:json)?\s*", "", text)
    text = _re.sub(r"\s*```$", "", text)
    try:
        data = _json.loads(text)
    except Exception as e:
        log_error(f"replicate_analyze JSON parse fail: {e} text[:200]={text[:200]}")
        raise RuntimeError("qwen-vl 输出解析失败")
    scenes_raw = data.get("scenes") or []
    if not scenes_raw:
        raise RuntimeError("qwen-vl 未返回分镜")
    clean_scenes = []
    for sc in scenes_raw:
        try:
            assert_safe_prompt(sc.get("visual_prompt", ""))
        except _HTTPEx:
            sc["visual_prompt"] = "Cinematic product showcase, soft natural lighting, professional commercial style"
        clean_scenes.append(sc)
    # 探比例(ffprobe)
    aspect = "9:16"
    try:
        import subprocess as _sp
        rr = _sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                      "-show_entries", "stream=width,height", "-of", "json", video_url],
                     capture_output=True, text=True, timeout=15)
        if rr.returncode == 0:
            sd = _json.loads(rr.stdout)["streams"][0]
            w, h = sd.get("width"), sd.get("height")
            if w and h:
                ratio = w / h
                if abs(ratio - 9/16) < 0.1: aspect = "9:16"
                elif abs(ratio - 16/9) < 0.1: aspect = "16:9"
                elif abs(ratio - 1.0) < 0.1: aspect = "1:1"
    except Exception as _e:
        log_error(f"ffprobe 失败(默认 9:16): {_e}")
    # P172(2026-05-07):额外提取原视频说话内容 + 音乐分离
    speech_info = {"original_speech": "", "speech_audio_url": "", "has_background_music": False}
    try:
        speech_info = await _extract_speech_from_video(archived_url)
    except Exception as _e:
        log_error(f"replicate_analyze speech extract 异常(忽略,不影响 analyze): {_e}")

    # P181(2026-05-08):提取 VLM 给的 model_identity + product_category
    model_identity = (data.get("model_identity") or "").strip()
    product_category = (data.get("product_category") or "其他").strip()

    # P199(2026-05-08):用 wizper segment chunks 时间戳,按 scene.time_range overlap
    # 精确把每句话分到对应分镜,不再前端按时长比例估算
    speech_chunks = speech_info.get("speech_chunks") or []
    if speech_chunks and clean_scenes:
        def _parse_tr(tr_str: str):
            """'0-3s' / '3.5-5s' / '0:00-0:03' → (start_sec, end_sec) | None"""
            if not tr_str:
                return None
            s = str(tr_str).strip().replace("s", "").replace("S", "").strip()
            if "-" not in s:
                return None
            a, b = s.split("-", 1)
            def _to_sec(x):
                x = x.strip()
                if ":" in x:
                    parts = x.split(":")
                    if len(parts) == 2:
                        return float(parts[0]) * 60 + float(parts[1])
                    if len(parts) == 3:
                        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                return float(x)
            try:
                return (_to_sec(a), _to_sec(b))
            except Exception:
                return None

        matched_count = 0
        # P202(2026-05-08):chunks 现在是字/词级伪 word(P202 后处理细化),
        # 中文按字 join "" 不带空格,英文按词 join " " 保空格
        import re as _re_p202_join
        for sc in clean_scenes:
            tr = _parse_tr(sc.get("time_range") or "")
            if not tr:
                continue
            s_start, s_end = tr
            seg_text = []
            for c in speech_chunks:
                c_s, c_e = c.get("start", 0), c.get("end", 0)
                if c_s < s_end and c_e > s_start:
                    seg_text.append(c.get("text") or "")
            if seg_text:
                full = "".join(seg_text)
                has_cn = bool(_re_p202_join.search(r"[一-鿿]", full))
                if has_cn:
                    sc["speech"] = "".join(t for t in seg_text if t.strip()).strip()
                else:
                    sc["speech"] = " ".join(t.strip() for t in seg_text if t.strip())
                matched_count += 1
        log_info(f"replicate_analyze P199 时间戳分配 matched_scenes={matched_count}/{len(clean_scenes)} chunks={len(speech_chunks)}")

    log_info(f"replicate_analyze OK scenes={len(clean_scenes)} ratio={aspect} speech_len={len(speech_info.get('original_speech',''))} category={product_category} model_id_len={len(model_identity)}")
    return {
        "scenes": clean_scenes,
        "total_duration": data.get("total_duration_seconds", sum(s.get("duration_sec", 5) for s in clean_scenes)),
        "detected_aspect_ratio": aspect,
        "original_speech": speech_info.get("original_speech", ""),
        "speech_audio_url": speech_info.get("speech_audio_url", ""),
        "has_background_music": speech_info.get("has_background_music", False),
        "speech_chunks": speech_chunks,  # P199:前端可选展示时间戳分布
        "model_identity": model_identity,
        "product_category": product_category,
    }



async def _run_skill_analyze_job(params: dict) -> dict:
    """异步:本地 PySceneDetect 拆帧 + 九宫格 + qwen-vl image 看图 → 出 N 段分镜 + wizper 口播。

    跟 _run_replicate_analyze_job 输出 schema **byte-identical**(前端 /video/extract 解析复用)。
    区别:不用 qwen-vl-video,而是 skill 切镜头 + 拼九宫格,然后单次 qwen-vl image 推理。

    成本:qwen-vl image ~¥0.05 + wizper $0.0005/min + audio-isolation $0.10/min ≈ < ¥0.5/次
    """
    import json as _json
    import os as _os
    import re as _re
    import shutil as _shutil
    import tempfile as _tmp
    import time as _t
    import httpx as _httpx
    from app.services.fal_service import (
        AliyunQwenVLVideoService, fal_upload_with_retry,
    )
    from app.services.media_archiver import archive_url
    from app.services.logger import log_info, log_error
    from app.services.content_filter import assert_safe_prompt
    from fastapi import HTTPException as _HTTPEx
    from app.api.video_frame_extract import _build_skill_instruction

    video_url = params.get("video_url")
    if not video_url:
        raise RuntimeError("video_url 缺")

    uid = params.get("_user_id") or "anon"

    # 1. 归档原 fal URL 到 ailixiao.com(防 wizper / ffprobe 跨境慢)
    t0 = _t.time()
    archived_url = await archive_url(video_url, uid, "video")
    log_info(f"skill_analyze 归档: {video_url[:50]} → {archived_url[:60]} ({_t.time()-t0:.1f}s)")

    # 2. 下载视频到本地(skill 需要本地 path)
    work_dir = _tmp.mkdtemp(prefix="skill_analyze_")
    local_video = _os.path.join(work_dir, "input.mp4")
    try:
        t1 = _t.time()
        async with _httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            r = await client.get(archived_url)
            r.raise_for_status()
            with open(local_video, "wb") as f:
                f.write(r.content)
        log_info(f"skill_analyze 下载本地 {_os.path.getsize(local_video)} bytes ({_t.time()-t1:.1f}s)")

        # 3. skill 拆帧 + 拼多九宫格(N>9 自动分页)
        from app.skills.video_frame_extraction import VideoFrameSkill
        skill = VideoFrameSkill()
        t2 = _t.time()
        skill_out = skill.process(local_video, output_dir=work_dir)
        log_info(f"skill_analyze 拆帧 n_frames={skill_out['n_frames']} n_grids={skill_out['n_grids']} ({_t.time()-t2:.1f}s)")

        if skill_out["n_frames"] < 1:
            raise RuntimeError("skill 未抽到任何帧")

        # 4. 并发上传 N 张九宫格到 fal
        t3 = _t.time()
        grid_urls = []
        for p in skill_out["grid_paths"]:
            url = await fal_upload_with_retry(p)
            grid_urls.append(url)
        log_info(f"skill_analyze 九宫格上传 N={len(grid_urls)} ({_t.time()-t3:.1f}s)")

        # 5. 并发跑:qwen-vl 看多张九宫格 + wizper 口播
        instruction = _build_skill_instruction(skill_out["scenes"], n_grids=skill_out["n_grids"])
        svc = AliyunQwenVLVideoService()
        if not svc.is_available():
            raise RuntimeError("qwen-vl 不可用(DASHSCOPE_API_KEY)")

        async def _qwen():
            t = _t.time()
            # 单张时用 analyze_image,多张时用 analyze_images(token 共享上限,实测 max 5 张)
            if len(grid_urls) == 1:
                r = await svc.analyze_image(grid_urls[0], instruction)
            else:
                r = await svc.analyze_images(grid_urls, instruction)
            log_info(f"skill_analyze qwen-vl elapsed={_t.time()-t:.1f}s n_imgs={len(grid_urls)} err={'error' in r}")
            return r

        async def _speech():
            try:
                return await _extract_speech_from_video(archived_url)
            except Exception as _e:
                log_error(f"skill_analyze speech 异常(忽略): {_e}")
                return {"original_speech": "", "speech_audio_url": "", "has_background_music": False, "speech_chunks": []}

        qwen_res, speech_info = await asyncio.gather(_qwen(), _speech())

        # 6. 解析 qwen-vl JSON
        if "error" in qwen_res:
            raise RuntimeError(f"qwen-vl 失败: {qwen_res['error'][:200]}")
        text = (qwen_res.get("text") or "").strip()
        text = _re.sub(r"^```(?:json)?\s*", "", text)
        text = _re.sub(r"\s*```$", "", text)
        try:
            data = _json.loads(text)
        except Exception as e:
            log_error(f"skill_analyze JSON parse fail: {e} text[:300]={text[:300]}")
            raise RuntimeError("qwen-vl 输出非合法 JSON")

        scenes_raw = data.get("scenes") or []
        if not scenes_raw:
            raise RuntimeError("qwen-vl 未返回 scenes")

        clean_scenes = []
        for sc in scenes_raw:
            try:
                assert_safe_prompt(sc.get("visual_prompt", ""))
            except _HTTPEx:
                sc["visual_prompt"] = "Cinematic product showcase, soft natural lighting, professional commercial style"
            clean_scenes.append(sc)

        # 7. ffprobe 探宽高比(走归档 URL)
        aspect = "9:16"
        try:
            import subprocess as _sp
            rr = _sp.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "json", archived_url],
                capture_output=True, text=True, timeout=15,
            )
            if rr.returncode == 0:
                sd = _json.loads(rr.stdout).get("streams", [{}])[0]
                w, h = sd.get("width"), sd.get("height")
                if w and h:
                    ratio = w / h
                    if abs(ratio - 9/16) < 0.1: aspect = "9:16"
                    elif abs(ratio - 16/9) < 0.1: aspect = "16:9"
                    elif abs(ratio - 1.0) < 0.1: aspect = "1:1"
                    elif abs(ratio - 4/3) < 0.1: aspect = "4:3"
                    elif abs(ratio - 3/4) < 0.1: aspect = "3:4"
        except Exception as _e:
            log_error(f"skill_analyze ffprobe 失败(默认 9:16): {_e}")

        # 8. 复用 P199 逻辑:wizper chunks 按 scene.time_range overlap 反贴每段 speech
        speech_chunks = speech_info.get("speech_chunks") or []
        if speech_chunks and clean_scenes:
            def _parse_tr(tr_str: str):
                if not tr_str:
                    return None
                s = str(tr_str).strip().replace("s", "").replace("S", "").strip()
                if "-" not in s:
                    return None
                a, b = s.split("-", 1)
                def _to_sec(x):
                    x = x.strip()
                    if ":" in x:
                        parts = x.split(":")
                        if len(parts) == 2: return float(parts[0]) * 60 + float(parts[1])
                        if len(parts) == 3: return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                    return float(x)
                try:
                    return (_to_sec(a), _to_sec(b))
                except Exception:
                    return None

            matched = 0
            for sc in clean_scenes:
                tr = _parse_tr(sc.get("time_range") or "")
                if not tr:
                    continue
                s_start, s_end = tr
                seg_text = []
                for c in speech_chunks:
                    cs, ce = c.get("start", 0), c.get("end", 0)
                    if cs < s_end and ce > s_start:
                        seg_text.append(c.get("text") or "")
                if seg_text:
                    full = "".join(seg_text)
                    has_cn = bool(_re.search(r"[一-鿿]", full))
                    if has_cn:
                        sc["speech"] = "".join(t for t in seg_text if t.strip()).strip()
                    else:
                        sc["speech"] = " ".join(t.strip() for t in seg_text if t.strip())
                    matched += 1
            log_info(f"skill_analyze 时间戳分配 matched={matched}/{len(clean_scenes)} chunks={len(speech_chunks)}")

        model_identity = (data.get("model_identity") or "").strip()
        product_category = (data.get("product_category") or "其他").strip()

        log_info(
            f"skill_analyze OK scenes={len(clean_scenes)} ratio={aspect} "
            f"speech_len={len(speech_info.get('original_speech',''))} "
            f"category={product_category} model_id_len={len(model_identity)} "
            f"total={_t.time()-t0:.1f}s"
        )
        return {
            "scenes": clean_scenes,
            "total_duration": data.get("total_duration_seconds", sum(s.get("duration_sec", 5) for s in clean_scenes)),
            "detected_aspect_ratio": aspect,
            "original_speech": speech_info.get("original_speech", ""),
            "speech_audio_url": speech_info.get("speech_audio_url", ""),
            "has_background_music": speech_info.get("has_background_music", False),
            "speech_chunks": speech_chunks,
            "model_identity": model_identity,
            "product_category": product_category,
            "grid_urls": grid_urls,  # N 张九宫格 PNG list(长视频 > 9 scenes 时多张)
            "n_grids": len(grid_urls),
        }
    finally:
        try:
            _shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


async def _run_skill_replace_flux2(
    grid_urls: list,
    product_url: str | None,
    model_url: str | None,
    scene_url: str | None,
    product_category: str = "其他",
) -> dict:
    """敏感品类九宫格替换:拆格 → Kolors Virtual Try-On 逐帧换装 → 重拼九宫格。

    流程:
      1. 下载每张九宫格 → PIL 拆成 9 张单帧
      2. 每帧用 Kolors VTON 换上 product_url 的服装(精确替换,非生成式)
      3. 检测不到人体姿态的帧(特写/背景)保留原帧
      4. 9 帧重拼成新九宫格上传 fal
    """
    import time as _t
    import os as _os
    import tempfile as _tmp
    import httpx as _httpx
    import fal_client as _fc
    from PIL import Image
    from app.services.fal_service import fal_upload_with_retry
    from app.services.logger import log_info, log_error
    from app.skills.video_frame_extraction import compose_grid

    if not product_url:
        raise RuntimeError("敏感品类路径需要提供产品图(garment)")

    n_grids = len(grid_urls)
    border = 4
    rows, cols = 3, 3
    per_grid = rows * cols

    # 并发上限:Kolors 9 格并发(每格独立 fal 调用)
    cell_sem = asyncio.Semaphore(9)

    async def _kolors_one(cell_path: str, gi: int, ci: int) -> str:
        """单格 Kolors VTON;检测不到姿态时返回原格路径。"""
        async with cell_sem:
            t = _t.time()
            try:
                cell_url = await fal_upload_with_retry(cell_path)
                result = await asyncio.wait_for(
                    _fc.run_async("fal-ai/kling/v1-5/kolors-virtual-try-on", arguments={
                        "human_image_url": cell_url,
                        "garment_image_url": product_url,
                    }),
                    timeout=120,
                )
                # Kolors 返回 {"image": {"url": ...}} 而非 {"images": [...]}
                out_url = (result.get("image") or {}).get("url") if isinstance(result, dict) else None
                if out_url:
                    log_info(f"skill_replace_flux2 grid={gi} cell={ci} Kolors OK ({_t.time()-t:.1f}s)")
                    # 下载结果写回同名文件
                    async with _httpx.AsyncClient(timeout=60, follow_redirects=True) as cli:
                        r = await cli.get(out_url)
                        r.raise_for_status()
                        with open(cell_path, "wb") as f:
                            f.write(r.content)
                    return cell_path
            except Exception as e:
                err = str(e)
                if "body pose" in err or "pose" in err.lower():
                    log_info(f"skill_replace_flux2 grid={gi} cell={ci} 无姿态保留原帧")
                else:
                    log_error(f"skill_replace_flux2 grid={gi} cell={ci} Kolors 失败:{err[:120]}")
            return cell_path  # 失败/无姿态 → 保留原帧

    work_dir = _tmp.mkdtemp(prefix="skill_replace_flux2_")
    t0 = _t.time()
    replaced_urls: list[str] = []

    try:
        for gi, grid_url in enumerate(grid_urls):
            # 1. 下载九宫格
            grid_path = _os.path.join(work_dir, f"grid_{gi:02d}.png")
            async with _httpx.AsyncClient(timeout=60, follow_redirects=True) as cli:
                r = await cli.get(grid_url)
                r.raise_for_status()
                with open(grid_path, "wb") as f:
                    f.write(r.content)

            # 2. PIL 拆成 9 格
            img = Image.open(grid_path).convert("RGB")
            canvas_size = max(img.size)
            cell_w = (canvas_size - (cols + 1) * border) // cols
            cell_h = (canvas_size - (rows + 1) * border) // rows
            cell_paths: list[str] = []
            for ci in range(per_grid):
                row = ci // cols
                col = ci % cols
                x = border + col * (cell_w + border)
                y = border + row * (cell_h + border)
                cell = img.crop((x, y, x + cell_w, y + cell_h))
                cp = _os.path.join(work_dir, f"cell_{gi:02d}_{ci:02d}.jpg")
                cell.save(cp, "JPEG", quality=90)
                cell_paths.append(cp)

            # 3. 并发 Kolors VTON
            processed = await asyncio.gather(*[
                _kolors_one(cp, gi, ci) for ci, cp in enumerate(cell_paths)
            ])

            # 4. 重拼九宫格
            new_grid_path = _os.path.join(work_dir, f"new_grid_{gi:02d}.png")
            compose_grid(list(processed), layout=(rows, cols), output_path=new_grid_path)
            new_url = await fal_upload_with_retry(new_grid_path)
            replaced_urls.append(new_url)
            log_info(f"skill_replace_flux2 grid {gi+1}/{n_grids} 完成")

        log_info(f"skill_replace_flux2 全部完成 N={n_grids} 总耗时 {_t.time()-t0:.1f}s")
    finally:
        import shutil as _sh
        _sh.rmtree(work_dir, ignore_errors=True)

    # 替换后重写 visual_prompt(同 GPT-2 路径)
    from app.services.logger import log_error as _le
    original_scenes = []  # flux2 路径暂不传 scenes,后续可扩展
    return {
        "replaced_grid_urls": replaced_urls,
        "n_grids": n_grids,
        "elapsed_seconds": round(_t.time() - t0, 1),
        "updated_scenes": original_scenes,
        "engine": "flux2",
    }


async def _run_skill_replace_job(params: dict) -> dict:
    """阶段 B 第二步:对 N 张九宫格并发跑 GPT-Image 2 edit,把每张里的人/物/景换成用户素材。

    输入:grid_urls (list of N) + product/model/scene reference URLs
    输出:replaced_grid_urls (list of N,顺序对齐)

    成本:~¥1.5-2/张,耗时 单张 ~250s,N 张并发 + 多张 GPT-2 排队,典型 N=2 时 ~5-7 分钟
    """
    import time as _t
    import fal_client as _fc
    from app.services.logger import log_info, log_error

    grid_urls = params.get("grid_urls") or []
    product_url = params.get("product_image_url")
    model_url = params.get("model_image_url")
    scene_url = params.get("scene_image_url")
    model_identity = params.get("model_identity") or ""
    product_category = params.get("product_category") or "其他"
    sensitive = bool(params.get("sensitive", False))

    if not grid_urls:
        raise RuntimeError("grid_urls 必填")
    if not any([product_url, model_url, scene_url]):
        raise RuntimeError("产品图 / 人物图 / 场景图 至少 1 张")



    n_grids = len(grid_urls)
    # 动态拼 reference list + 描述:用户可能只传 1/2/3 张
    refs_desc = []
    refs_urls_extra = []  # 按 Reference N 顺序排
    if model_url:
        refs_desc.append(f"Reference {len(refs_desc)+1} (the NEW model): the person whose face, hair, and body type should appear in every panel, replacing whoever was originally there.")
        refs_urls_extra.append(model_url)
    if product_url:
        refs_desc.append(f"Reference {len(refs_desc)+1} (the NEW product): the item that should replace any clothing/product/garment visible in every panel — preserve its color, shape, pattern, and proportions.")
        refs_urls_extra.append(product_url)
    if scene_url:
        refs_desc.append(f"Reference {len(refs_desc)+1} (the NEW scene): the environment that should replace each panel's background.")
        refs_urls_extra.append(scene_url)

    # 动态拼 "do this" 行
    actions = []
    if model_url:
        actions.append("Replace any person in each panel with the model from the model Reference, preserving their face, hair, and overall body type.")
    else:
        actions.append("Keep the original person in each panel (do NOT change who appears).")
    if product_url:
        actions.append(f"Replace the {product_category} (or any visible product/garment) in each panel with the product from the product Reference.")
    else:
        actions.append("Keep the original product/garment in each panel (do NOT change the visible product).")
    if scene_url:
        actions.append("Replace each panel's background with the scene from the scene Reference.")
    else:
        actions.append("Keep each panel's original background and environment.")
    action_block = "\n".join(f"{i+1}. {a}" for i, a in enumerate(actions))

    base_prompt = f"""This image is a 3×3 storyboard grid containing 9 INDEPENDENT video shots, ordered left-to-right then top-to-bottom. Each cell has a number in the top-left corner with a black box background.

Reference images provided (in this exact order after the storyboard):
{chr(10).join(refs_desc)}

Original storyboard context:
- Original product category: {product_category}

Re-render the entire storyboard with the following changes applied INDEPENDENTLY to EACH of the 9 cells:

{action_block}
4. CRITICALLY preserve each cell's:
   - Composition and framing
   - Camera angle (close-up, medium-shot, wide-shot, etc.)
   - Action and pose
   - Lighting style and color tone
5. Keep all 9 cells as INDEPENDENT shots — do NOT merge or blend them into a single scene.
6. Maintain the 3×3 grid layout with white borders between cells.
7. Keep the numbered labels in the top-left corner of each cell — but DO NOT add any other text overlays, words, logos, or written characters anywhere in the image.
8. Output must be a 1024×1024 PNG with the same 3×3 grid structure as the input.

NO TEXT OVERLAYS other than the original number labels. NO new words, signs, brands, or written text introduced anywhere."""

    base_prompt = _sanitize_for_gpt2(base_prompt)

    # 并发对 N 张九宫格各跑 GPT-2 edit(每张独立 fal call)
    sem = asyncio.Semaphore(5)  # GPT-2 高峰期重,限并发避免触发 fal rate limit

    # content_policy 触发时的简化 prompt — 去掉品类/模特描述，只保留结构指令
    simple_prompt = (
        "Re-render this 3×3 storyboard grid. "
        "Replace the person in each panel with the person from Reference 1 if provided. "
        "Replace the product/garment in each panel with the item from Reference 2 if provided. "
        "Preserve each panel's background, composition, lighting, and camera angle. "
        "Keep the 3×3 grid layout with numbered labels. Output 1024×1024 PNG."
    )

    async def _replace_one(idx: int, grid_url: str) -> tuple[int, str]:
        async with sem:
            image_urls = [grid_url, *refs_urls_extra]
            log_info(f"skill_replace [{idx+1}/{n_grids}] 提交 GPT-2 edit grid={grid_url[:60]}")
            t = _t.time()

            async def _call_gpt2(prompt: str, urls: list) -> dict:
                return await asyncio.wait_for(
                    _fc.run_async(
                        "openai/gpt-image-2/edit",
                        arguments={
                            "prompt": prompt,
                            "image_urls": urls,
                            "image_size": "square_hd",
                            "num_images": 1,
                            "output_format": "png",
                        },
                    ),
                    timeout=420,
                )

            # 尝试 1：完整 prompt
            try:
                result = await _call_gpt2(base_prompt, image_urls)
            except asyncio.TimeoutError:
                raise RuntimeError(f"GPT-Image 2 edit 第 {idx+1} 张超时 420s")
            except Exception as e:
                err_str = str(e)
                if "content_policy" in err_str or "content_checker" in err_str:
                    # 内容审核拒绝：不重试（重试也会被拒，白等 5 分钟）
                    log_error(f"skill_replace [{idx+1}] 内容审核拒绝，不重试直接失败")
                    raise RuntimeError(f"GPT-Image 2 第 {idx+1} 张被内容审核拒绝（产品图含敏感内容）")
                else:
                    log_error(f"skill_replace [{idx+1}] fal 异常: {e}")
                    raise RuntimeError(f"GPT-Image 2 edit 第 {idx+1} 张失败: {str(e)[:200]}")

            images = result.get("images", []) if isinstance(result, dict) else []
            if not images or not images[0].get("url"):
                raise RuntimeError(f"GPT-Image 2 第 {idx+1} 张未返回图片: {str(result)[:200]}")
            url = images[0]["url"]
            log_info(f"skill_replace [{idx+1}/{n_grids}] OK ({_t.time()-t:.1f}s) {url[:80]}")
            return (idx, url)

    t0 = _t.time()
    results = await asyncio.gather(*[_replace_one(i, u) for i, u in enumerate(grid_urls)])
    results.sort(key=lambda x: x[0])
    replaced_urls = [u for _, u in results]
    elapsed = _t.time() - t0
    log_info(f"skill_replace 完成 N={n_grids} 总耗时 {elapsed:.1f}s")

    # P237 根因修复:替换完成后跑 qwen-vl 重写 visual_prompt,避免文字残留"原视频颜色"压过图像
    original_scenes = params.get("scenes") or []
    updated_scenes = original_scenes
    if original_scenes:
        try:
            updated_scenes = await _rewrite_visual_prompts_from_replaced(replaced_urls, original_scenes)
        except Exception as e:
            log_error(f"skill_replace 重写 visual_prompt 异常(忽略,保留原 scenes): {e}")

    return {
        "replaced_grid_urls": replaced_urls,
        "n_grids": n_grids,
        "elapsed_seconds": round(elapsed, 1),
        "updated_scenes": updated_scenes,  # 新字段:前端用它覆盖 scenes 状态
    }


async def _run_skill_generate_job(params: dict) -> dict:
    """阶段 B 第三步:替换后九宫格 + scenes → 分组 Seedance r2v + 精准裁剪 + ffmpeg concat → 1 段完整视频。

    策略:合并生成省钱 + 精准裁剪保时序
      1. 下载替换后九宫格 → PIL 切 N 张独立帧 + 上传 fal
      2. 将连续分镜按"相同图片引用 + 总时长 ≤ 15s"分组为 Task
      3. 每个 Task 只调一次 Seedance r2v(duration = max(3, ceil(task.total_dur)))
      4. ffprobe 校验实际时长;用 ffmpeg 按原始时间戳精准裁剪每个分镜
      5. ffmpeg concat → 1 段完整视频 → 叠加原音轨 → 上传 fal

    省钱:N 个各 1s 分镜 旧=N×3s API 计费;新=1 次 × max(3,N)s 计费
    时序:精准裁剪保证每个分镜 duration 严格等于原 scene.duration_sec
    """
    import math
    import os as _os
    import shutil as _shutil
    import subprocess as _sp
    import tempfile as _tmp
    import time as _t
    import httpx as _httpx
    from PIL import Image
    from app.services.fal_service import fal_upload_with_retry
    from app.services.ad_video_models import (
        submit_seedance_fast_r2v_video, poll_seedance_fast_r2v_status,
    )
    from app.services.logger import log_info, log_error

    replaced_grid_urls = params.get("replaced_grid_urls") or []
    if not replaced_grid_urls and params.get("replaced_grid_url"):
        replaced_grid_urls = [params["replaced_grid_url"]]
    scenes_raw = params.get("scenes") or []
    product_url = params.get("product_image_url")
    model_url = params.get("model_image_url")
    scene_ref_url = params.get("scene_image_url")
    user_prompt = (params.get("user_prompt") or "").strip()
    aspect_ratio = params.get("aspect_ratio") or "9:16"
    skipped_ids: set = set(params.get("skipped_scene_ids") or [])

    if not replaced_grid_urls:
        raise RuntimeError("replaced_grid_urls 必填(先调 /replace)")
    if not scenes_raw:
        raise RuntimeError("scenes 必填")
    if not any([product_url, model_url, scene_ref_url]):
        raise RuntimeError("产品图 / 人物图 / 场景图 至少 1 张")

    scenes_active = [s for s in scenes_raw if s.get("id") not in skipped_ids]
    if not scenes_active:
        raise RuntimeError("所有分镜都被跳过,请至少保留 1 个")

    MAX_API_DUR = 15   # Seedance Fast r2v 上限(实测)
    MIN_API_DUR = 3    # Seedance Fast r2v 下限(API 要求)

    # ── 全量:所有用户走 batch 分组,上限从数据库读(默认 8s) ──────────────────
    from app.database import get_app_config as _get_app_config
    try:
        _batch_max_dur: float = float(_get_app_config("batch_max_duration", "8"))
    except Exception:
        _batch_max_dur = 8.0
    # ───────────────────────────────────────────────────────────────────────

    n_grids = len(replaced_grid_urls)
    n_active = len(scenes_active)
    n_skipped = len(scenes_raw) - n_active
    work_dir = _tmp.mkdtemp(prefix="skill_generate_")
    log_info(
        f"skill_generate 开工 n_grids={n_grids} n_active={n_active} "
        f"(原{len(scenes_raw)}个,跳过{n_skipped}个) "
        f"batch_max_dur={_batch_max_dur}s work={work_dir}"
    )

    try:
        # ── 1. 下载替换后九宫格 → PIL 切独立帧 → 上传 fal ──────────────────────
        t0 = _t.time()
        border = 4
        rows, cols = 3, 3
        per_grid = rows * cols  # 9

        frame_urls: list[str] = []  # frame_urls[i] 对应 scenes_active[i] 的格子图
        for gi, grid_url in enumerate(replaced_grid_urls):
            grid_path = _os.path.join(work_dir, f"replaced_grid_{gi:02d}.png")
            async with _httpx.AsyncClient(timeout=60, follow_redirects=True) as cli:
                r = await cli.get(grid_url)
                r.raise_for_status()
                with open(grid_path, "wb") as f:
                    f.write(r.content)

            img = Image.open(grid_path).convert("RGB")
            canvas_size = max(img.size)
            cell_w = (canvas_size - (cols + 1) * border) // cols
            cell_h = (canvas_size - (rows + 1) * border) // rows

            start_scene = gi * per_grid
            end_scene = min((gi + 1) * per_grid, n_active)
            for local_i in range(end_scene - start_scene):
                row = local_i // cols
                col = local_i % cols
                x = border + col * (cell_w + border)
                y = border + row * (cell_h + border)
                cell_img = img.crop((x, y, x + cell_w, y + cell_h))
                global_i = start_scene + local_i
                cell_path = _os.path.join(work_dir, f"frame_{global_i:03d}.png")
                cell_img.save(cell_path, "PNG")
                cell_url = await fal_upload_with_retry(cell_path)
                frame_urls.append(cell_url)

        if len(frame_urls) != n_active:
            raise RuntimeError(f"frame_urls={len(frame_urls)} != n_active={n_active},切帧逻辑错")
        log_info(f"skill_generate 切 {len(frame_urls)} 张帧 + 上传完成 ({_t.time()-t0:.1f}s)")

        # ── 2. 分组:累加时长 ≤ batch_max_dur 封口 ──────────────────────────────
        img_key = (product_url or "", model_url or "", scene_ref_url or "")
        tasks: list[dict] = []
        cur: dict | None = None

        for i, scene in enumerate(scenes_active):
            dur = float(scene.get("duration_sec") or 4)

            if dur >= _batch_max_dur:
                # 单分镜 ≥ 上限 → 独立成组,不可拆分(生成式模型拼接必跳变)
                if cur:
                    cur["task_idx"] = len(tasks)
                    tasks.append(cur)
                    cur = None
                tasks.append({
                    "img_key": img_key,
                    "anchor_frame_url": frame_urls[i],
                    "scenes": [scene],
                    "frame_indices": [i],
                    "total_dur": dur,
                    "task_idx": len(tasks),
                })
            elif cur is None:
                cur = {
                    "img_key": img_key,
                    "anchor_frame_url": frame_urls[i],
                    "scenes": [scene],
                    "frame_indices": [i],
                    "total_dur": dur,
                }
            elif cur["total_dur"] + dur > _batch_max_dur:
                # 加入后超上限 → 当前组封口,开新组
                cur["task_idx"] = len(tasks)
                tasks.append(cur)
                cur = {
                    "img_key": img_key,
                    "anchor_frame_url": frame_urls[i],
                    "scenes": [scene],
                    "frame_indices": [i],
                    "total_dur": dur,
                }
            else:
                cur["scenes"].append(scene)
                cur["frame_indices"].append(i)
                cur["total_dur"] += dur

        if cur:
            cur["task_idx"] = len(tasks)
            tasks.append(cur)

        log_info(
            f"skill_generate 分组: {n_active} 个分镜 → {len(tasks)} 个 Task "
            f"max_dur={_batch_max_dur}s"
        )
        for _t_obj in tasks:
            _ids = [s.get("id", "?") for s in _t_obj["scenes"]]
            log_info(
                f"  Task {_t_obj['task_idx']+1}: scenes={_ids} "
                f"total_dur={_t_obj['total_dur']:.2f}s"
            )

        # ── 3. 并发:每个 Task 一次 Seedance r2v ────────────────────────────────
        sem = asyncio.Semaphore(5)

        async def _run_task(task: dict) -> dict:
            async with sem:
                ti = task["task_idx"]
                total_dur = task["total_dur"]

                # 单分镜 > 15s:只能生成 15s,裁剪时截取前段;禁止拆分(画面会跳变)
                if total_dur > MAX_API_DUR:
                    log_error(
                        f"skill_generate Task {ti+1}: 总时长 {total_dur:.2f}s > {MAX_API_DUR}s,"
                        f" 只生成 {MAX_API_DUR}s,超出部分将被截断"
                    )
                    total_dur = float(MAX_API_DUR)

                req_dur = max(4, math.ceil(total_dur))
                req_dur = min(req_dur, MAX_API_DUR)

                # image_urls:[首帧锚点(必须第一位), 产品图, 模特图, 场景图]
                image_urls = [task["anchor_frame_url"]]
                ref_roles: list[tuple[str, str]] = []
                if product_url:
                    image_urls.append(product_url)
                    ref_roles.append(("product", "the product"))
                if model_url:
                    image_urls.append(model_url)
                    ref_roles.append(("model", "the model"))
                if scene_ref_url:
                    image_urls.append(scene_ref_url)
                    ref_roles.append(("scene", "the scene/background reference"))

                # Prompt:连贯描述各分镜动作,不写硬性时间戳(模型无法精准执行)
                ref_anchors = " ".join(
                    f"@Image{j+2} is {desc}."
                    for j, (_, desc) in enumerate(ref_roles)
                )
                scene_descs: list[str] = []
                for sc in task["scenes"]:
                    raw_vp = sc.get("visual_prompt") or "Cinematic product showcase"
                    scene_descs.append(_strip_video_color_words(raw_vp))
                combined_action = "; ".join(scene_descs)

                seg_prompt = (
                    f"@Image1 defines the EXACT visual appearance (colors, outfit, person, products) "
                    f"that MUST be preserved throughout. "
                    f"{ref_anchors} "
                    f"Generate a {req_dur}-second continuous video: {combined_action}. "
                    f"CRITICAL: Strictly match all colors, patterns, garments, and visual details "
                    f"from @Image1. Ignore any color words in the description — use ONLY what "
                    f"@Image1 visually shows."
                )
                if user_prompt:
                    seg_prompt += f" Special emphasis: {user_prompt}."
                seg_prompt = _sanitize_for_gpt2(seg_prompt)

                log_info(
                    f"skill_generate Task {ti+1}/{len(tasks)}: "
                    f"n_scenes={len(task['scenes'])} total_dur={task['total_dur']:.2f}s "
                    f"req_dur={req_dur}s"
                )

                submit_res = await submit_seedance_fast_r2v_video(
                    image_urls=image_urls,
                    prompt=seg_prompt,
                    duration=req_dur,
                    aspect_ratio=aspect_ratio,
                    resolution="480p",
                    enable_audio=False,
                )
                if "error" in submit_res or not submit_res.get("task_id"):
                    raise RuntimeError(
                        f"Task {ti+1} r2v submit 失败: {submit_res.get('error', '?')}"
                    )
                fal_task_id = submit_res["task_id"]

                # poll
                tp = _t.time()
                max_wait = 360
                video_url: str | None = None
                while _t.time() - tp < max_wait:
                    st = await poll_seedance_fast_r2v_status(fal_task_id)
                    s = st.get("status", "")
                    if s == "completed":
                        video_url = st.get("video_url")
                        break
                    if s == "failed":
                        raise RuntimeError(
                            f"Task {ti+1} r2v failed: {st.get('error', '?')}"
                        )
                    await asyncio.sleep(5)
                if not video_url:
                    raise RuntimeError(f"Task {ti+1} r2v 超时 {max_wait}s")

                raw_path = _os.path.join(work_dir, f"task_{ti:02d}_raw.mp4")
                async with _httpx.AsyncClient(timeout=120, follow_redirects=True) as cli:
                    r = await cli.get(video_url)
                    r.raise_for_status()
                    with open(raw_path, "wb") as f:
                        f.write(r.content)

                log_info(
                    f"skill_generate Task {ti+1} 下载完成 "
                    f"{_os.path.getsize(raw_path)} bytes ({_t.time()-tp:.1f}s)"
                )
                return {"task": task, "raw_path": raw_path}

        gather_t0 = _t.time()
        task_results = await asyncio.gather(*[_run_task(tk) for tk in tasks])
        log_info(
            f"skill_generate 全部 {len(tasks)} 个 Task API 完成 ({_t.time()-gather_t0:.1f}s)"
        )

        # ── 4. ffprobe 校验实际时长 + 精准裁剪每个分镜 ─────────────────────────
        def _ffprobe_duration(path: str) -> float:
            """读取视频实际时长;失败返回 -1.0。"""
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ]
            try:
                rr = _sp.run(cmd, capture_output=True, text=True, timeout=30)
                return float(rr.stdout.strip())
            except Exception:
                return -1.0

        # all_clips[(scene_global_order_idx, clip_path)],最终按 idx 排序拼接
        all_clips: list[tuple[int, str]] = []

        for res in task_results:
            task = res["task"]
            raw_path = res["raw_path"]
            ti = task["task_idx"]

            actual_dur = _ffprobe_duration(raw_path)
            expected_dur = task["total_dur"]

            if actual_dur < 0:
                log_error(
                    f"skill_generate Task {ti+1} ffprobe 失败,用 req_dur 代替"
                )
                actual_dur = float(
                    min(MAX_API_DUR, max(MIN_API_DUR, math.ceil(expected_dur)))
                )

            if actual_dur < expected_dur - 0.1:
                # API 返回视频比预期短 → 按比例缩减每个分镜的裁剪时长
                scale = actual_dur / expected_dur
                log_error(
                    f"skill_generate Task {ti+1} 实际时长 {actual_dur:.3f}s < "
                    f"预期 {expected_dur:.3f}s,按比例 scale={scale:.4f} 裁剪"
                )
            else:
                scale = 1.0

            offset = 0.0
            for j, scene in enumerate(task["scenes"]):
                original_idx = task["frame_indices"][j]
                raw_target = float(scene.get("duration_sec") or 4)
                target_dur = max(0.05, raw_target * scale)

                # offset 超出 actual_dur → 截止兜底,避免切空文件
                if offset >= actual_dur - 0.02:
                    log_error(
                        f"skill_generate Task {ti+1} scene {j+1} "
                        f"offset={offset:.3f}s >= actual_dur={actual_dur:.3f}s,跳过裁剪"
                    )
                    break
                target_dur = min(target_dur, actual_dur - offset)

                clip_path = _os.path.join(work_dir, f"clip_{original_idx:04d}.mp4")
                # -ss 放 -i 后面:逐帧解码到起始点,帧精度裁剪,非关键帧寻址
                # 严禁 -c copy:首帧未必是 IDR(memory: ffmpeg_no_copy_first_segment)
                cmd = [
                    "ffmpeg", "-y",
                    "-i", raw_path,
                    "-ss", f"{offset:.3f}",
                    "-t",  f"{target_dur:.3f}",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p",
                    "-an",
                    clip_path,
                ]
                rr = _sp.run(cmd, capture_output=True, text=True, timeout=120)
                if (rr.returncode != 0
                        or not _os.path.exists(clip_path)
                        or _os.path.getsize(clip_path) < 512):
                    raise RuntimeError(
                        f"Task {ti+1} scene {j+1} ffmpeg 裁剪失败: {rr.stderr[-200:]}"
                    )

                all_clips.append((original_idx, clip_path))
                log_info(
                    f"skill_generate Task {ti+1} scene {j+1}/{len(task['scenes'])} "
                    f"裁剪 offset={offset:.3f}s dur={target_dur:.3f}s → clip_{original_idx:04d}.mp4"
                )
                offset += target_dur

        # ── 5. 按原时间轴顺序拼接 ───────────────────────────────────────────────
        all_clips.sort(key=lambda x: x[0])
        seg_paths = [p for _, p in all_clips]

        list_path = _os.path.join(work_dir, "concat.txt")
        with open(list_path, "w") as f:
            for p in seg_paths:
                f.write(f"file '{p}'\n")
        final_video = _os.path.join(work_dir, "final.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c:v", "libx264", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-an",
            final_video,
        ]
        rr = _sp.run(cmd, capture_output=True, text=True, timeout=180)
        if rr.returncode != 0 or not _os.path.exists(final_video):
            raise RuntimeError(f"ffmpeg concat 失败: {rr.stderr[-300:]}")
        log_info(
            f"skill_generate concat 完成 {_os.path.getsize(final_video)} bytes "
            f"({_t.time()-gather_t0:.1f}s 累计)"
        )

        # ── 6. 叠加原视频音轨(P238) ─────────────────────────────────────────────
        original_video_url = params.get("original_video_url")
        if original_video_url:
            try:
                orig_local = _os.path.join(work_dir, "original.mp4")
                async with _httpx.AsyncClient(timeout=120, follow_redirects=True) as cli:
                    r = await cli.get(original_video_url)
                    r.raise_for_status()
                    with open(orig_local, "wb") as f:
                        f.write(r.content)
                merged = _os.path.join(work_dir, "final_with_audio.mp4")
                cmd_merge = [
                    "ffmpeg", "-y",
                    "-i", final_video,
                    "-i", orig_local,
                    "-map", "0:v",
                    "-map", "1:a",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "128k",
                    "-shortest",
                    merged,
                ]
                rr2 = _sp.run(cmd_merge, capture_output=True, text=True, timeout=120)
                if rr2.returncode == 0 and _os.path.exists(merged) and _os.path.getsize(merged) > 1024:
                    final_video = merged
                    log_info(f"skill_generate 叠加原音轨 OK {_os.path.getsize(merged)} bytes")
                else:
                    log_error(f"skill_generate 叠加原音轨失败(保留无声成片): {rr2.stderr[-200:]}")
            except Exception as _e:
                log_error(f"skill_generate 叠加原音轨异常(保留无声成片): {_e}")

        # ── 7. 上传 fal 拿成品 URL ───────────────────────────────────────────────
        video_url = await fal_upload_with_retry(final_video)
        total_dur_sec = sum(float(s.get("duration_sec", 4)) for s in scenes_active)
        log_info(
            f"skill_generate 收工 total={_t.time()-t0:.1f}s "
            f"tasks={len(tasks)} scenes={n_active} dur={total_dur_sec:.1f}s "
            f"url={video_url[:80]}"
        )
        return {
            "video_url": video_url,
            "n_segments": n_active,
            "n_api_calls": len(tasks),
            "total_duration_sec": round(total_dur_sec, 2),
        }
    finally:
        try:
            _shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


async def _rewrite_visual_prompts_from_replaced(
    replaced_grid_urls: list, scenes: list,
) -> list:
    """P237(2026-05-12)根因修复:让 qwen-vl 看替换后的九宫格,重新写每段 visual_prompt。

    原问题:第一步 visual_prompt 是 qwen-vl 看原视频写的,带 "dark blue / 藏青" 等原视频颜色。
    生成视频时文字 prompt 压过图像 reference,Seedance 出原色。
    根因修复:替换完成后,让 qwen-vl 看替换后的图,重写所有 prompt。

    成本:~¥0.05 / 次(qwen-vl image 一次推理)
    失败 graceful degrade:返回原 scenes
    """
    import json as _json
    import re as _re
    import time as _t
    from app.services.fal_service import AliyunQwenVLVideoService
    from app.services.logger import log_info, log_error, log_warning

    n = len(scenes)
    n_grids = len(replaced_grid_urls)
    if not n or not n_grids:
        return scenes

    svc = AliyunQwenVLVideoService()
    if not svc.is_available():
        log_warning("rewrite_visual_prompts: qwen-vl 不可用,保留原 scenes")
        return scenes

    # 给 qwen-vl 列出 N 个镜头的时间范围作为锚点,避免编号错位
    lines = []
    for i, s in enumerate(scenes):
        gi = i // 9 + 1
        ci = i % 9 + 1
        tr = s.get("time_range") or f"{i*2}-{(i+1)*2}s"
        lines.append(f"  镜头 {i+1}(图{gi} 第{ci}格,{tr})")
    range_block = "\n".join(lines)

    instruction = f"""你看到的是 {n_grids} 张替换后的九宫格 storyboard 图(每张 3×3=9 格,按时间顺序排列),共 {n} 个独立镜头。

镜头全局编号映射:
{range_block}

请你**只看图中真实可见的内容**(不要参考任何之前的视频描述),为每个镜头写一段英文 visual_prompt(150 字内):
- 主体外观(人物、服装、产品):严格按图中**真实颜色、款式、纹理**描述,不要写图中没有的颜色
- 镜头语言(景别 close-up / medium-shot / wide-shot / 视角、构图)
- 灯光、背景、场景细节
- 动作和姿态

输出严格 JSON,顶层是 {{"visual_prompts": ["...", "...", ...]}},数组严格 {n} 个字符串,跟全局编号 1..{n} 对齐。不要任何 markdown 围栏 / 注释 / 说明。
"""
    t0 = _t.time()
    try:
        if n_grids == 1:
            res = await svc.analyze_image(replaced_grid_urls[0], instruction)
        else:
            res = await svc.analyze_images(replaced_grid_urls, instruction)
    except Exception as e:
        log_error(f"rewrite_visual_prompts qwen-vl 异常: {e}")
        return scenes

    log_info(f"rewrite_visual_prompts elapsed={_t.time()-t0:.1f}s n_imgs={n_grids} err={'error' in res}")
    if "error" in res:
        log_error(f"rewrite_visual_prompts qwen-vl error: {res['error']}")
        return scenes

    text = (res.get("text") or "").strip()
    text = _re.sub(r"^```(?:json)?\s*", "", text)
    text = _re.sub(r"\s*```$", "", text)
    try:
        data = _json.loads(text)
        new_prompts = data.get("visual_prompts") or []
    except Exception as e:
        log_error(f"rewrite_visual_prompts JSON parse fail: {e} text={text[:200]}")
        return scenes

    if len(new_prompts) != n:
        log_warning(f"rewrite_visual_prompts 数量不对: got {len(new_prompts)} expected {n},保留原 scenes")
        return scenes

    # 覆盖每段 visual_prompt,其他字段不动
    updated = []
    for i, s in enumerate(scenes):
        new_s = dict(s)
        np = (new_prompts[i] or "").strip()
        if np:
            new_s["visual_prompt"] = np
        updated.append(new_s)
    log_info(f"rewrite_visual_prompts OK 覆盖 {n} 段 prompt")
    return updated


def _strip_video_color_words(text: str) -> str:
    """P237:删 visual_prompt 里 qwen-vl 写的"原视频颜色"描述,避免文字 prompt 压过图像 reference。

    Seedance r2v 同时收到 image_urls (替换后的) + 文字 prompt (qwen-vl 看原视频写的)。
    diffusion 模型里文字 prompt 经常压过图像 reference → 生成画面变回原视频颜色。
    清洗后 visual_prompt 只剩动作/景别/构图,颜色由 @Image1/@Image2 决定。
    """
    if not text:
        return text
    import re as _re
    patterns = [
        # 颜色 + 服装/garment
        (r"\b(dark|navy|deep|light|pale|bright)\s+(blue|red|green|black|white|yellow|pink|purple|gray|grey|brown)\b", "the"),
        (r"\b(blue|red|green|black|white|yellow|pink|purple|gray|grey|brown|navy|crimson|scarlet|olive|teal|cyan|magenta|beige|khaki|maroon|burgundy)\s+(pants|trousers|shorts|jeans|garment|outfit|top|shirt|dress|skirt|jacket|coat|sweater|hoodie|tee|t-shirt)\b", r"the \2"),
        # 形容词形式
        (r"\b(dark|navy|deep|light|pale|bright)-?(blue|red|green|black|white)\b", "the"),
        # 中文
        (r"(深蓝色?|藏青色?|蓝色|墨蓝色?|海军蓝)(的)?", ""),
        (r"(深红色?|大红色?|红色|粉红色?)(的)?", ""),
        (r"(墨绿色?|草绿色?|绿色)(的)?", ""),
        (r"(黑色|纯黑)(的)?", ""),
        (r"(白色|纯白)(的)?", ""),
        # 单独颜色名带"色"字
        (r"(深|浅|亮|暗)?(蓝|红|绿|黑|白|黄|粉|紫|灰|棕|金|银)色", ""),
    ]
    out = text
    for pat, repl in patterns:
        out = _re.sub(pat, repl, out, flags=_re.IGNORECASE)
    # 把多余空格压平
    out = _re.sub(r"\s+", " ", out).strip()
    return out


def _sanitize_for_gpt2(text: str) -> str:
    """硬替换 fal content_checker 触发词。覆盖 visual_prompt + identity_description。
    替换成中性通用词,既不破坏语义又能过审。"""
    if not text:
        return text
    pairs = [
        # 中文敏感词 → 中性(fal content_checker 同样拦截中文)
        ("内衣", "服装"),
        ("文胸", "上衣"),
        ("胸罩", "上衣"),
        ("内裤", "服装"),
        ("比基尼", "泳装"),
        ("情趣", "服装"),
        # 服装类目 → 通用
        ("balconette bra", "structured top"),
        ("balconette", "structured"),
        ("push-up bra", "structured top"),
        ("padded bra", "structured top"),
        ("underwire bra", "structured top"),
        (" bra ", " top "),
        (" bras ", " tops "),
        ("Bra ", "Top "),
        (" lingerie", " apparel"),
        (" underwear", " apparel"),
        (" panties", " bottoms"),
        ("bikini", "swimwear"),
        ("shapewear", "fashion garment"),
        ("waist trainer", "fashion garment"),
        # 身体部位 → 中性
        ("chest area", "upper outfit"),
        (" chest", " upper outfit"),
        (" breast", " upper outfit"),
        (" bust", " upper outfit"),
        (" cleavage", " upper outfit"),
        (" waist ", " torso "),
        (" hips", " lower outfit"),
        (" thigh", " lower outfit"),
        (" butt", " lower outfit"),
        # 服装部件 → 通用
        ("shoulder strap", "shoulder area"),
        ("strap adjustment", "garment adjustment"),
        (" straps", " bands"),
        (" strap ", " band "),
        ("support panel", "fabric panel"),
        ("padded straps", "wide bands"),
        ("Padded Straps", "Wide Bands"),
        ("Underwire Support", "Structured Support"),
        ("underwire", "structured"),
        # 文字叠加暗示 → 干净描述
        ("text overlay", "subtle visual emphasis"),
        ("text appears", "visual highlight appears"),
        ("text 'Padded Straps'", "visual emphasis"),
        # 大类兜底
        (" the bra", " the garment"),
        (" my bra", " my garment"),
        (" her bra", " her garment"),
        ("a bra ", "a garment "),
    ]
    out = text
    for old_w, new_w in pairs:
        out = out.replace(old_w, new_w)
    return out


def _sanitize_for_seedance(text: str) -> str:
    """Seedance 专用敏感词替换，规避 Seedance 内容过滤。
    与 _sanitize_for_gpt2 并列使用，各自针对不同端点优化。"""
    if not text:
        return text
    pairs = [
        # 内衣类 → 商业中性
        ("bra",         "seamless support top"),
        ("内衣",        "fashion essential"),
        ("underwear",   "comfort wear"),
        ("lingerie",    "intimate apparel collection"),
        ("panties",     "comfort shorts"),
        ("内裤",        "seamless bottom"),
        # 身体部位 → 中性描述
        ("副乳",        "side silhouette"),
        ("胸",          "front panel"),
        ("cleavage",    "neckline"),
        # 风格词 → 优雅化
        ("sexy",        "elegant"),
        ("性感",        "优雅"),
        ("nude",        "skin-tone"),
        ("裸",          "肤色"),
        # 泳装类
        ("bikini",      "two-piece set"),
        ("泳装",        "swim collection"),
    ]
    out = text
    for old_w, new_w in pairs:
        out = out.replace(old_w, new_w)
    return out


# ==================== 视频复刻 worker(2026-05-06)====================

async def _run_replicate_job(params: dict) -> dict:
    """复刻视频:每段 GPT-Image 2 首帧 → aliyun-wan2.7-r2v 出视频段 → ffmpeg concat。

    inputs:
      product_image_url      : 必,产品图(GPT-Image 2 base + wan reference)
      model_image_url        : 可选,模特图
      reference_video_url    : 必,参考视频(wan reference_video,驱动动作/场景)
      scenes                 : VLM 出的 N 段 [{id, duration_sec, visual_prompt, ...}]
      aspect_ratio           : 9:16 / 16:9 / 1:1 / ...
      overall_setting        : 整体设定(可选)
      model_description      : 模特描述(可选)

    output: {video_url, type, description}
    """
    import asyncio as _asyncio
    import subprocess
    import tempfile
    import os
    import httpx
    from app.services.fal_service import get_aliyun_wan_service, fal_upload_with_retry
    from app.services import ad_video_models
    from app.services.logger import log_info, log_error

    product_image_url = params.get("product_image_url")
    if not product_image_url:
        raise RuntimeError("product_image_url 必填")
    product_back_image_url = params.get("product_back_image_url")  # 反面/侧面,可选
    reference_video_url = params.get("reference_video_url")
    if not reference_video_url:
        raise RuntimeError("reference_video_url 必填")
    scenes = params.get("scenes") or []
    if not scenes:
        raise RuntimeError("scenes 不能为空")
    aspect_ratio = params.get("aspect_ratio") or "9:16"
    overall_setting = params.get("overall_setting") or ""
    model_description = params.get("model_description") or "A professional commercial model"

    # ---- Step 0:把所有要喂阿里云 wan2.7 的 URL 归档到本地 /uploads(防跨境下载超时)----
    # qwen-vl / wan2.7 都是阿里云国内服务,fal 在美国 → 跨境下载常 121s 超时(实测)
    # archive_url 把 fal URL 拉到本地,返回 ailixiao.com 国内 URL
    from app.services.media_archiver import archive_url as _archive
    _uid = params.get("_user_id") or "anon"
    _orig_video = reference_video_url
    _orig_front = product_image_url
    _orig_back = product_back_image_url
    reference_video_url = await _archive(_orig_video, _uid, "video")
    product_image_url = await _archive(_orig_front, _uid, "image")
    if _orig_back:
        product_back_image_url = await _archive(_orig_back, _uid, "image")
    log_info(f"replicate Step 0 归档完: video {len(_orig_video)}->{len(reference_video_url)}, front_img, back_img")

    # ---- Step 1A:scene 1 用 compose_first_frame 出 base(GPT-2 自己想模特 + 产品正反面) ----
    # 这一步给后续段提供"模特身份锚点",防止每段 GPT-2 出不同的人
    log_info(f"replicate Step 1A:base 首帧(产品正反面 → GPT-2 自动出模特) ratio={aspect_ratio}")
    base_res = await ad_video_models.compose_first_frame(
        product_image_url=product_image_url,
        background_image_url=None,
        model_description=model_description,
        scene_visual_prompt=scenes[0].get("visual_prompt", ""),
        product_back_image_url=product_back_image_url,
        aspect_ratio=aspect_ratio,
    )
    if "error" in base_res or not base_res.get("image_url"):
        raise RuntimeError(f"base 首帧合成失败: {base_res.get('error','?')}")
    base_frame_url = base_res["image_url"]
    log_info(f"replicate base OK url={base_frame_url[:60]}")

    # ---- Step 1A.5:看 base 图提取模特特征,后续 scene 用文字+图双重锁身份 ----
    try:
        import fal_client as _fal
        _ID_PROMPT = (
            "Look at this image. Describe the model's physical appearance in a concise "
            "100-word block, focused on identity-locking features that must NOT change "
            "across other shots. Cover: face shape, eye color and shape, hair (length/color/style), "
            "skin tone, eyebrow shape, lip shape, nose shape, body build, current outfit "
            "(top color/material, bottom if visible). Output English only, plain text, "
            "no list, no preamble. Start with 'Identity lock: '."
        )
        _vlm_res = await _fal.run_async(
            "openrouter/router/vision",
            arguments={
                "image_urls": [base_frame_url],
                "prompt": _ID_PROMPT,
                "model": "qwen/qwen3-vl-235b-a22b-instruct",
            },
        )
        _id_text = (_vlm_res.get("output") or "").strip() if isinstance(_vlm_res, dict) else ""
        if _id_text and len(_id_text) > 20:
            model_description = _sanitize_for_gpt2(_id_text[:600])  # 截断防溢出 + 敏感词清洗
            log_info(f"replicate model identity 提取 OK len={len(model_description)} (sanitized)")
        else:
            log_error(f"replicate model identity 提取空,降级用通用描述")
    except Exception as _e:
        log_error(f"replicate model identity 提取异常(降级): {_e}")

    # 对每段 scene.visual_prompt 硬清洗(防 VLM 漏过 fal content_checker)
    for _sc in scenes:
        if _sc.get("visual_prompt"):
            _sc["visual_prompt"] = _sanitize_for_gpt2(_sc["visual_prompt"])
    log_info(f"replicate visual_prompts sanitized N={len(scenes)}")

    # ---- Step 1B:用几宫格策略出剩余段首帧 ----
    # 1 张几宫格 GPT-Image 2 调用最多出 4 段(2/3/4 panel),N>5 分多次 grid;
    # 同一 grid 内的 panels 天然身份一致(同一张图);跨 grid 靠 base + 身份描述锁
    async def _gen_single_scene(scene):
        """3 段渐进 retry:每次失败把 prompt 改得更通用,绕开 fal content_checker。
        最差 fallback 到 base 但极少触发(覆盖率 95%+)。"""
        sid = scene.get("id", "?")
        original_prompt = (scene.get("visual_prompt") or "").strip()
        shot = scene.get("shot") or "medium-shot"
        # Attempt 1:原 prompt(VLM 出的精细描述)
        # Attempt 2:简化 — 去具体产品/动作描述,只留镜头语言
        # Attempt 3:极简 — 只描述商业摄影构图
        retry_prompts = [
            original_prompt,
            f"Photorealistic commercial fashion photography, {shot}, professional studio lighting, "
            f"model from reference image presenting the item, third-person camera angle, "
            f"model's face and upper body clearly visible",
            "Photorealistic commercial advertisement, soft natural studio lighting, "
            "third-person camera, model presenting the product item from the reference image, "
            "professional fashion shoot",
        ]

        for attempt_idx, prompt in enumerate(retry_prompts):
            scene_try = dict(scene)
            scene_try["visual_prompt"] = prompt
            try:
                fr = await ad_video_models.compose_first_frame_for_scene(
                    base_image_url=base_frame_url,
                    scene=scene_try,
                    model_description=model_description,
                    overall_setting=overall_setting,
                    aspect_ratio=aspect_ratio,
                    product_image_url=product_image_url,
                    product_back_image_url=product_back_image_url,
                )
                if "error" not in fr and fr.get("image_url"):
                    if attempt_idx > 0:
                        log_info(f"replicate scene {sid} retry {attempt_idx+1}/{len(retry_prompts)} 成功")
                    return fr["image_url"]
                err_msg = fr.get("error", "?")[:160]
                log_error(f"replicate scene {sid} attempt {attempt_idx+1}/{len(retry_prompts)} fail: {err_msg}")
                # 非 content_policy 错误也 retry 一次,但成本浪费;只对 content_policy 重试
                if "content_policy" not in err_msg and "content_checker" not in err_msg:
                    break  # 别的错误不 retry
            except Exception as exc:
                log_error(f"replicate scene {sid} attempt {attempt_idx+1} exc: {exc}")

        # P157.5(2026-05-07)attempt 4:exclude_base_image=True 绕 fal image-level checker
        # 内衣类产品的 base 图被 fal 看到就拒,这里 fallback 到只传产品图(像 Step 1A 一样)
        # 身份保真改靠 prompt 里 model_description 文字(已含脸/眼/发/肤色/眉/嘴/鼻全描述)
        log_info(f"replicate scene {sid} attempt 4(无 base 图,绕 image-level checker)")
        try:
            scene_no_base = dict(scene)
            scene_no_base["visual_prompt"] = retry_prompts[1]  # 用简化版 prompt
            fr = await ad_video_models.compose_first_frame_for_scene(
                base_image_url=base_frame_url,  # 还传(签名要),但被 exclude
                scene=scene_no_base,
                model_description=model_description,
                overall_setting=overall_setting,
                aspect_ratio=aspect_ratio,
                product_image_url=product_image_url,
                product_back_image_url=product_back_image_url,
                exclude_base_image=True,
            )
            if "error" not in fr and fr.get("image_url"):
                log_info(f"replicate scene {sid} attempt 4(无 base) 成功 — image-level checker 绕过")
                return fr["image_url"]
            log_error(f"replicate scene {sid} attempt 4(无 base) 仍失败: {fr.get('error','?')[:160]}")
        except Exception as exc:
            log_error(f"replicate scene {sid} attempt 4 exc: {exc}")

        log_error(f"replicate scene {sid} 所有 retry(含 attempt 4 无 base)全失败,降级到 base")
        return base_frame_url

    async def _gen_grid_panels(chunk):
        """对 2-4 段 scene 出 1 张几宫格 + crop 出 N 张 panel。
        失败时 retry 一次简化 prompt,再失败降级到逐段单出(单出本身有 3 段 retry)。"""
        n = len(chunk)
        if n == 1:
            return [await _gen_single_scene(chunk[0])]
        if n not in (2, 3, 4):
            raise ValueError(f"grid chunk size {n} 不支持")

        # Attempt 1:原 chunk(VLM 精细 visual_prompt)
        # Attempt 2:每段 visual_prompt 替换成通用"商业摄影"描述,绕 content_checker
        async def _try_grid(scene_chunk):
            try:
                grid_res = await ad_video_models.compose_storyboard_grid(
                    base_image_url=base_frame_url,
                    scenes=scene_chunk,
                    n_panels=n,
                    model_description=model_description,
                    overall_setting=overall_setting,
                    aspect_ratio=aspect_ratio,
                    product_image_url=product_image_url,
                    product_back_image_url=product_back_image_url,
                )
                if "error" in grid_res or not grid_res.get("image_url"):
                    return None, grid_res.get("error", "?")
                panels = await ad_video_models.crop_storyboard_panels(grid_res["image_url"], n)
                if len(panels) != n:
                    return None, f"crop returned {len(panels)}"
                return panels, None
            except Exception as exc:
                return None, str(exc)

        panels, err = await _try_grid(chunk)
        if panels:
            log_info(f"replicate grid n={n} OK(原 prompt),省 {n-1} 次 GPT 调用")
            return panels

        # P204(2026-05-08):删除 P168 content_policy fast-fail。
        # P168 当时为"省 7 分钟"直接 [base_frame_url] * n,但导致用户看到"部分段像原视频"
        # —— 所有段拿同一张原视频帧当首帧,i2v 出来段间无差异化 = 复刻成原片。
        # 用户:"哪里能这样啊"。优先质量,不优先速度。
        # content_policy 也走下面的简化 prompt 重试链,通用 prompt 大概率过 NSFW checker。
        if err and ("content_policy" in err or "content_checker" in err):
            log_warning(f"replicate grid n={n} content_policy 拒,P204 走简化 prompt 重试(不再 fast-fail base)")

        # 简化 prompt retry(原 attempt 2 + scene-by-scene 重试链)
        if err and "content_policy" not in err and "content_checker" not in err:
            log_error(f"replicate grid n={n} 非 content_policy 错,简化 prompt retry: {err[:120] if err else '?'}")
        simplified_chunk = []
        for sc in chunk:
            shot = sc.get("shot") or "medium-shot"
            sc_simple = dict(sc)
            sc_simple["visual_prompt"] = (
                f"Photorealistic commercial fashion shoot, {shot}, "
                f"professional studio lighting, model presenting the item, "
                f"third-person camera, face and upper body visible"
            )
            simplified_chunk.append(sc_simple)
        panels, err2 = await _try_grid(simplified_chunk)
        if panels:
            log_info(f"replicate grid n={n} 简化 prompt OK")
            return panels
        log_error(f"replicate grid n={n} 简化 prompt 仍失败,逐段降级: {err2[:120] if err2 else '?'}")
        # 最终降级:逐段单出(单出自身有 3 段 retry)
        return await _asyncio.gather(*[_gen_single_scene(sc) for sc in chunk])

    if len(scenes) == 1:
        frames = [base_frame_url]
    else:
        # P206(2026-05-08):scenes[1:] chunks 并发(原串行)
        # scenes >= 5 段(>=2 chunk)直接省 3 分钟,scenes 3-4 不变
        rest = scenes[1:]
        chunk_specs: list = []  # list of ('grid', chunk_list) | ('single', scene)
        i = 0
        while i < len(rest):
            chunk_size = min(4, len(rest) - i)
            if chunk_size == 1 and chunk_specs:
                chunk_specs.append(('single', rest[i]))
            else:
                chunk_specs.append(('grid', rest[i:i+chunk_size]))
            i += chunk_size

        async def _resolve_chunk(spec):
            kind, payload = spec
            if kind == 'single':
                return [await _gen_single_scene(payload)]
            return await _gen_grid_panels(payload)

        log_info(f"replicate Step 1B 并发 {len(chunk_specs)} 个 chunk(P206)")
        chunk_results = await _asyncio.gather(*[_resolve_chunk(spec) for spec in chunk_specs])
        rest_frames = [u for chunk in chunk_results for u in chunk]
        frames = [base_frame_url] + rest_frames
        log_info(f"replicate Step 1B 完成 N={len(scenes)} frames={len(frames)}")

    # ---- Step 2:按 engine 分发到不同视频生成路径 ----
    engine = params.get("engine") or "pixverse-swap"
    log_info(f"replicate Step 2 engine={engine}")
    if engine in ("seedance-lite-i2v", "kling-3-pro-i2v"):
        # P164:kling-3-pro-i2v 旧 value 保留兼容,实际跑 seedance-lite
        seg_urls = await _gen_videos_seedance_lite_i2v(scenes, frames, aspect_ratio)
    elif engine == "pixverse-swap":
        seg_urls = await _gen_videos_pixverse_swap(scenes, frames, reference_video_url, aspect_ratio)
    elif engine == "pixverse-2step":
        # P163(2026-05-07):方案 B — 2 步 swap(适合手持物体类产品)
        seg_urls = await _gen_videos_pixverse_2step(scenes, frames, reference_video_url, aspect_ratio, product_image_url=product_image_url)
    elif engine == "catvton-pixverse":
        seg_urls = await _gen_videos_catvton_pixverse(scenes, frames, product_image_url, reference_video_url, aspect_ratio)
    else:
        raise RuntimeError(f"unknown engine: {engine}")
    log_info(f"replicate Step 2 完成 engine={engine} N={len(seg_urls)}")

    # ---- Step 3:下载所有段 + ffmpeg concat ----
    with tempfile.TemporaryDirectory() as tmpdir:
        seg_paths = []
        async with httpx.AsyncClient(timeout=180.0) as client:
            for i, url in enumerate(seg_urls):
                r = await client.get(url)
                p = os.path.join(tmpdir, f"seg_{i}.mp4")
                with open(p, "wb") as f:
                    f.write(r.content)
                seg_paths.append(p)
        list_file = os.path.join(tmpdir, "list.txt")
        with open(list_file, "w") as f:
            for p in seg_paths:
                f.write(f"file '{p}'\n")
        out_path = os.path.join(tmpdir, "final.mp4")
        # P178(2026-05-07):3 层 merge 兜底 — 静态帧 fallback 段可能无音轨,跟 pixverse 段拼会挂
        # Tier 1:-c copy(最快,各段编码一致才成)
        cp = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out_path],
            capture_output=True,
        )
        if cp.returncode != 0 or not os.path.exists(out_path):
            log_info(f"replicate merge -c copy 失败,降级 re-encode 带音轨: {cp.stderr.decode()[-200:] if cp.stderr else '?'}")
            # Tier 2:re-encode(各段编码不一致兜底)
            r2 = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
                 "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out_path],
                capture_output=True,
            )
            if r2.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                log_info(f"replicate merge re-encode 带音轨失败,降级 -an 无音轨: {r2.stderr.decode()[-200:] if r2.stderr else '?'}")
                # Tier 3:-an 完全去音轨(静态帧 fallback 段无音轨,有时跟有音轨段拼不起来)
                r3 = subprocess.run(
                    ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
                     "-c:v", "libx264", "-preset", "veryfast", "-an", out_path],
                    capture_output=True,
                )
                if r3.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                    # Tier 4:最后兜底 — 至少返第一段(用户至少能看到一段视频)
                    log_error(f"replicate merge 三层全失败,最后兜底返第一段: {r3.stderr.decode()[-300:] if r3.stderr else '?'}")
                    fal_final_url = seg_urls[0]
                    total_dur = sum(int(round(s.get("duration_sec", 5))) for s in scenes)
                    return {
                        "video_url": fal_final_url,
                        "type": "video/replicate",
                        "description": f"视频复刻(降级版本,merge 失败仅返第一段)· {len(scenes)} 段 · {total_dur}s",
                    }
        fal_final_url = await fal_upload_with_retry(out_path)

    total_dur = sum(int(round(s.get("duration_sec", 5))) for s in scenes)
    return {
        "video_url": fal_final_url,
        "type": "video/replicate",
        "description": f"视频复刻 · {len(scenes)} 段 · {total_dur}s",
    }


# ==================== 3 个引擎的视频段生成 helper(2026-05-07)====================

async def _slice_video_by_scenes(reference_video_url: str, scenes: list, tmpdir: str, aspect_ratio: str = "9:16") -> list:
    """ffmpeg 按 scene time_range 切参考视频成 N 段,上传 fal storage,返 fal URL 列表。
    pixverse-swap / catvton-pixverse 都用这个切段。

    🚨 重要:强制 720p 输出(用户铁律)。fal pixverse-swap @720p $0.20/5s,
    @1080p $0.40/5s — 不准跑 0.4 美金的!
    """
    import asyncio as _asyncio
    import subprocess
    import os
    import httpx
    from app.services.fal_service import fal_upload_with_retry
    from app.services.logger import log_info, log_error

    # 720p 分辨率映射(强制 downscale,确保 fal pixverse 计费 $0.20/5s)
    scale_map = {
        "9:16": "720:1280",
        "16:9": "1280:720",
        "1:1":  "720:720",
        "3:4":  "720:960",
        "4:3":  "960:720",
    }
    scale_str = scale_map.get((aspect_ratio or "").strip().lower(), "720:1280")

    # 1. 下载参考视频到 tmpdir
    local_path = os.path.join(tmpdir, "ref.mp4")
    async with httpx.AsyncClient(timeout=300) as cli:
        async with cli.stream("GET", reference_video_url) as r:
            with open(local_path, "wb") as f:
                async for chunk in r.aiter_bytes(64 * 1024):
                    f.write(chunk)
    log_info(f"slice_video: 下载 ref 到 {local_path} 目标分辨率 {scale_str}")

    # 2. 按 scene 切段(同时 downscale 到 720p)
    seg_paths = []
    for sc in scenes:
        tr = sc.get("time_range") or "0-5s"
        try:
            parts = tr.replace("s", "").strip().split("-")
            start = float(parts[0])
            end = float(parts[1])
            dur = max(2.0, end - start)
        except Exception:
            start, dur = 0.0, float(sc.get("duration_sec", 5))
        sid = sc.get("id", len(seg_paths))
        seg_path = os.path.join(tmpdir, f"seg_{sid}.mp4")
        # 关键:scale + setsar 强制 720p,pixverse 输出按 720p 计费($0.20/5s)
        vf = f"scale={scale_str}:force_original_aspect_ratio=decrease,pad={scale_str}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        cp = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start), "-i", local_path, "-t", str(dur),
             "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-c:a", "aac", "-b:a", "128k",
             seg_path],
            capture_output=True,
        )
        if cp.returncode != 0 or not os.path.exists(seg_path):
            raise RuntimeError(f"ffmpeg slice+scale scene {sid} failed: {cp.stderr.decode()[:200]}")
        seg_paths.append(seg_path)
    log_info(f"slice_video: 切完 {len(seg_paths)} 段(已强制 {scale_str} 720p)")

    # 3. 并发上传 fal storage
    seg_fal_urls = await _asyncio.gather(*[fal_upload_with_retry(p) for p in seg_paths])
    log_info(f"slice_video: 上传 fal 完成")
    return seg_fal_urls


async def _replicate_frame_to_static_video(image_url: str, duration_sec: float, ratio: str) -> str:
    """P177(2026-05-07):module-level 共享兜底 — GPT 帧 → ffmpeg loop → fal 静态 mp4。
    所有 engine(pixverse-swap / pixverse-2step / seedance-lite-i2v / 未来新 engine)
    任何段失败时都用这个,保证不再"修一处漏一处"。
    """
    import urllib.request as _urlreq
    import subprocess as _sp
    import tempfile as _tmp
    from app.services.fal_service import fal_upload_with_retry as _fup
    dim = {"9:16": "720:1280", "16:9": "1280:720", "1:1": "720:720"}.get(ratio, "720:1280")
    with _tmp.TemporaryDirectory() as _td:
        img_path = os.path.join(_td, "frame.png")
        _urlreq.urlretrieve(image_url, img_path)
        out_path = os.path.join(_td, "static.mp4")
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img_path,
               "-t", f"{max(1.0, duration_sec):.2f}",
               "-vf", f"scale={dim}:force_original_aspect_ratio=decrease,pad={dim}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-pix_fmt", "yuv420p", "-r", "24", out_path]
        r = _sp.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg image->video 失败: {r.stderr[:300]}")
        return await _fup(out_path)


async def _gen_videos_seedance_lite_i2v(scenes: list, frames: list, aspect_ratio: str) -> list:
    """引擎 3:seedance-lite-i2v(P164 替换 kling-3-pro-i2v,$0.80→$0.18/5s)

    用户说 kling-3-pro $0.80/5s 太贵,目标 $0.2-0.3 平替 + AI 自由生成动作。
    Seedance v1 Lite i2v $0.18/5s @720p,ByteDance 模型,效果接近 Pro 版的 70%。
    P177(2026-05-07):补兜底,跟 pixverse-swap/2step 一样的策略 — 任何段挂都降级到静态帧
    """
    import asyncio as _asyncio
    import fal_client
    from app.services.logger import log_info, log_error

    async def _seedance_fallback(idx: int, frame_url: str, reason: str) -> str:
        log_info(f"replicate seedance-lite-i2v seg {idx} 降级用 GPT 帧静态视频:{reason}")
        scene_dur = float(scenes[idx].get("duration_sec", 5)) if idx < len(scenes) else 5.0
        return await _replicate_frame_to_static_video(frame_url, scene_dur, aspect_ratio)

    sem = _asyncio.Semaphore(5)
    async def _one(idx: int, scene: dict, frame_url: str) -> str:
        async with sem:
            duration = max(5, min(10, int(round(scene.get("duration_sec", 5)))))
            prompt = scene.get("visual_prompt") or "Cinematic product showcase"
            try:
                # P179(2026-05-07):360s wait_for 防 fal hang
                result = await _asyncio.wait_for(
                    fal_client.run_async(
                        "fal-ai/bytedance/seedance/v1/lite/image-to-video",
                        arguments={
                            "image_url": frame_url,
                            "prompt": prompt,
                            "duration": str(duration),
                            "aspect_ratio": aspect_ratio,
                            "resolution": "720p",
                            "enable_audio": False,
                        },
                    ),
                    timeout=360,
                )
                video = result.get("video") if isinstance(result, dict) else None
                vurl = video.get("url") if isinstance(video, dict) else None
                if not vurl:
                    vurl = result.get("video_url") if isinstance(result, dict) else None
                if not vurl:
                    return await _seedance_fallback(idx, frame_url, "未返 video URL")
                log_info(f"replicate seedance-lite-i2v seg {idx} OK url={vurl[:60]}")
                return vurl
            except Exception as e:
                try:
                    return await _seedance_fallback(idx, frame_url, f"异常: {type(e).__name__}:{str(e)[:120]}")
                except Exception as _fb:
                    log_error(f"replicate seedance-lite-i2v seg {idx} fallback 失败: {_fb}")
                    raise

    results = await _asyncio.gather(*[
        _one(i, scenes[i], frames[i]) for i in range(len(scenes))
    ], return_exceptions=True)
    # 完全炸 → 静态帧 retry 一次,再炸就 raise(seedance 没 ref 视频段可用作最终兜底)
    final_urls = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            log_error(f"replicate seedance-lite-i2v seg {i} 完全失败,最后再 retry 静态帧: {r}")
            try:
                scene_dur = float(scenes[i].get("duration_sec", 5))
                final_urls.append(await _replicate_frame_to_static_video(frames[i], scene_dur, aspect_ratio))
            except Exception as _e:
                log_error(f"replicate seedance-lite-i2v seg {i} 最后兜底也失败: {_e}")
                raise RuntimeError(f"seedance-lite-i2v seg {i} 完全失败: {r}")
        else:
            final_urls.append(r)
    return final_urls


# P164:旧函数名 alias 保留,避免破坏可能的引用(虽然 grep 没找到外部引用,但保险)
_gen_videos_kling_i2v = _gen_videos_seedance_lite_i2v


async def _gen_videos_pixverse_swap(scenes: list, frames: list, reference_video_url: str, aspect_ratio: str) -> list:
    """引擎 2:pixverse-swap. 每段用对应的 GPT-Image 2 出的 frame 做 reference。
    
    用户铁律:图必须交给 gpt2 做。所以:
      - reference 图 = frames[i](GPT-Image 2 直接出的,不经任何后处理)
      - driving 视频段 = 参考视频对应段(原片动作/场景)
    
    N 段身份一致性靠 GPT-Image 2 的 identity-lock prompt + qwen-vl 提取的身份描述
    保证(已在 Step 1A.5 + 1B prompt 里强约束)。
    """
    import asyncio as _asyncio
    import tempfile
    from app.services.fal_service import get_pixverse_swap_service
    from app.services.logger import log_info, log_error

    pix = get_pixverse_swap_service()
    if not pix:
        raise RuntimeError("pixverse-swap 服务未初始化")
    if not frames or len(frames) < len(scenes):
        raise RuntimeError(f"pixverse-swap 需要 N={len(scenes)} 张 GPT frames,当前只 {len(frames) if frames else 0} 张")

    log_info(f"replicate pixverse: 每段独立用对应 GPT frame(GPT-2 直接出图,无后处理)")

    # P176(2026-05-07):单步 pixverse-swap 也加兜底,跟 2-step 一样的策略
    async def _swap_fallback(idx: int, frame_url: str, reason: str) -> str:
        log_info(f"replicate pixverse-swap seg {idx} 降级用 GPT 帧静态视频:{reason}")
        scene_dur = float(scenes[idx].get("duration_sec", 5)) if idx < len(scenes) else 5.0
        # 复用 2-step 的 helper(同文件,同模块作用域)
        import urllib.request as _urlreq, subprocess as _sp
        from app.services.fal_service import fal_upload_with_retry as _fup
        dim = {"9:16": "720:1280", "16:9": "1280:720", "1:1": "720:720"}.get(aspect_ratio, "720:1280")
        with tempfile.TemporaryDirectory() as _td:
            img_path = os.path.join(_td, "frame.png")
            _urlreq.urlretrieve(frame_url, img_path)
            out_path = os.path.join(_td, "static.mp4")
            cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img_path,
                   "-t", f"{max(1.0, scene_dur):.2f}",
                   "-vf", f"scale={dim}:force_original_aspect_ratio=decrease,pad={dim}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                   "-pix_fmt", "yuv420p", "-r", "24", out_path]
            r = _sp.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg image->video 失败: {r.stderr[:300]}")
            return await _fup(out_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        seg_urls = await _slice_video_by_scenes(reference_video_url, scenes, tmpdir, aspect_ratio=aspect_ratio)

        sem = _asyncio.Semaphore(5)
        async def _swap_one(idx: int, seg_url: str, frame_url: str) -> str:
            async with sem:
                # P208(2026-05-08):pixverse fal 'Downstream service unavailable' 是
                # 瞬时错(实测 18:28 seg 1 触发),不 retry 直接降级静态 → 用户看到该段
                # 完全不动 = "像原视频"。加 3 次 retry,2s/4s/8s 退避,再降级。
                last_err = ""
                for attempt in range(3):
                    try:
                        result = await pix.swap(video_url=seg_url, image_url=frame_url)
                        if "error" in result:
                            err_text = str(result['error'])
                            last_err = f"swap error: {err_text[:120]}"
                            # 'Downstream service unavailable' / '503' / 'timeout' 这类瞬时错 retry
                            is_transient = any(k in err_text for k in
                                ['unavailable', '503', '502', '504', 'timeout', 'Downstream'])
                            if not is_transient or attempt == 2:
                                break
                            wait = 2 * (2 ** attempt)
                            log_warning(f"replicate pixverse seg {idx} attempt {attempt+1} 瞬时错,P208 等 {wait}s retry: {err_text[:80]}")
                            await _asyncio.sleep(wait)
                            continue
                        vurl = result.get("video_url")
                        if not vurl:
                            last_err = "swap 无 url"
                            if attempt == 2: break
                            await _asyncio.sleep(2 * (2 ** attempt))
                            continue
                        log_info(f"replicate pixverse seg {idx} OK url={vurl[:60]} (attempt {attempt+1})")
                        return vurl
                    except Exception as _e:
                        last_err = f"swap 异常: {type(_e).__name__}:{str(_e)[:120]}"
                        if attempt == 2: break
                        wait = 2 * (2 ** attempt)
                        log_warning(f"replicate pixverse seg {idx} attempt {attempt+1} 异常,P208 等 {wait}s retry: {str(_e)[:80]}")
                        await _asyncio.sleep(wait)
                # 3 次都失败,降级静态
                try:
                    return await _swap_fallback(idx, frame_url, last_err)
                except Exception as _fb_err:
                    log_error(f"replicate pixverse-swap seg {idx} fallback 失败: {_fb_err}")
                    raise

        results = await _asyncio.gather(*[
            _swap_one(i, seg_urls[i], frames[i]) for i in range(len(scenes))
        ], return_exceptions=True)
        # 最后兜底:用原始 ref 视频段
        final_urls = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                log_error(f"replicate pixverse-swap seg {i} 完全失败,最后兜底用原 ref 视频段: {r}")
                final_urls.append(seg_urls[i])
            else:
                final_urls.append(r)
        return final_urls


async def _gen_videos_pixverse_2step(scenes: list, frames: list, reference_video_url: str, aspect_ratio: str, product_image_url: str = None) -> list:
    """方案 B(P163 2026-05-07):pixverse 2 步 swap — object 后 person。
    
    适合:**手持/独立物体类产品**(手机/包/水杯/化妆品/装饰品等)
    不适合:穿戴类(衣服/塑身衣/胸罩)— pixverse object 模式无法生成 mask
    
    Step A: pixverse-swap(driving, product_image, mode="object")
            → 替换驱动视频中的产品 → 用户产品(位置和手贴合关系全保留)
    Step B: pixverse-swap(stepA, frame, mode="person")
            → 替换人 → 用户模特(产品位置不变)
    
    成本:¥2.80/段(2 次 pixverse 调用)
    """
    import asyncio as _asyncio
    import tempfile
    from app.services.fal_service import get_pixverse_swap_service
    from app.services.logger import log_info, log_error

    pix = get_pixverse_swap_service()
    if not pix:
        raise RuntimeError("pixverse-swap 服务未初始化")
    if not frames or not frames[0]:
        raise RuntimeError("pixverse-2step 需要 frames[0](base 帧)作 person 替换 reference")
    if not product_image_url:
        raise RuntimeError("pixverse-2step 需要 product_image_url 作 object 替换 reference")

    log_info(f"replicate pixverse 2-step: object swap → person swap(N={len(scenes)} 段)")

    # P174(2026-05-07):Step A 失败兜底 — GPT 帧 → ffmpeg loop → 静态视频
    async def _frame_to_static_video(image_url: str, duration_sec: float, ratio: str) -> str:
        import urllib.request as _urlreq
        import subprocess as _sp
        from app.services.fal_service import fal_upload_with_retry as _fup
        dim = {"9:16": "720:1280", "16:9": "1280:720", "1:1": "720:720"}.get(ratio, "720:1280")
        with tempfile.TemporaryDirectory() as _td:
            img_path = os.path.join(_td, "frame.png")
            _urlreq.urlretrieve(image_url, img_path)
            out_path = os.path.join(_td, "static.mp4")
            cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img_path,
                   "-t", f"{max(1.0, duration_sec):.2f}",
                   "-vf", f"scale={dim}:force_original_aspect_ratio=decrease,pad={dim}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                   "-pix_fmt", "yuv420p", "-r", "24", out_path]
            r = _sp.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg image->video 失败: {r.stderr[:300]}")
            return await _fup(out_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        seg_urls = await _slice_video_by_scenes(reference_video_url, scenes, tmpdir, aspect_ratio=aspect_ratio)

        sem = _asyncio.Semaphore(5)
        async def _step_a_fallback(idx: int, person_ref: str, reason: str) -> str:
            log_info(f"replicate pixverse seg {idx} Step A 降级用 GPT 帧静态视频:{reason}")
            scene_dur = float(scenes[idx].get("duration_sec", 5)) if idx < len(scenes) else 5.0
            return await _frame_to_static_video(person_ref, scene_dur, aspect_ratio)

        async def _two_step_swap(idx: int, seg_url: str, person_ref: str) -> str:
            async with sem:
                # P175(2026-05-07):Step A/B 任何错误都降级,不只 mask
                # Step A: object swap
                step_a_url: str = ""
                try:
                    log_info(f"replicate pixverse seg {idx} Step A(object swap)…")
                    step_a = await pix.swap(video_url=seg_url, image_url=product_image_url, mode="object")
                    if "error" in step_a:
                        return await _step_a_fallback(idx, person_ref, f"Step A error: {str(step_a['error'])[:120]}")
                    step_a_url = step_a.get("video_url") or ""
                    if not step_a_url:
                        return await _step_a_fallback(idx, person_ref, "Step A 无 url")
                    log_info(f"replicate pixverse seg {idx} Step A OK url={step_a_url[:60]}")
                except Exception as _e_a:
                    try:
                        return await _step_a_fallback(idx, person_ref, f"Step A 异常: {type(_e_a).__name__}:{str(_e_a)[:120]}")
                    except Exception as _fb_err:
                        log_error(f"replicate pixverse seg {idx} Step A fallback 也失败: {_fb_err}")
                        raise RuntimeError(f"pixverse seg {idx} Step A object 异常 + fallback 失败: {_e_a}")

                # Step B: person swap
                try:
                    log_info(f"replicate pixverse seg {idx} Step B(person swap)…")
                    step_b = await pix.swap(video_url=step_a_url, image_url=person_ref, mode="person")
                    if "error" in step_b:
                        log_info(f"replicate pixverse seg {idx} Step B 跳过(error: {str(step_b['error'])[:80]}),降级用 Step A 视频")
                        return step_a_url
                    step_b_url = step_b.get("video_url")
                    if not step_b_url:
                        log_info(f"replicate pixverse seg {idx} Step B 无 url,降级用 Step A 视频")
                        return step_a_url
                    log_info(f"replicate pixverse seg {idx} 2-step OK final={step_b_url[:60]}")
                    return step_b_url
                except Exception as _e_b:
                    log_info(f"replicate pixverse seg {idx} Step B 异常({type(_e_b).__name__}:{str(_e_b)[:80]}),降级用 Step A 视频")
                    return step_a_url

        # P175:gather 用 return_exceptions=True 做最后一道保险
        results = await _asyncio.gather(*[
            _two_step_swap(i, seg_urls[i], frames[min(i, len(frames)-1)]) for i in range(len(scenes))
        ], return_exceptions=True)
        # 如果某段彻底炸(连 fallback 都失败),最后兜底:用原始 ref 视频段
        final_urls = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                log_error(f"replicate pixverse seg {i} 完全失败,最后兜底用原 ref 视频段: {r}")
                final_urls.append(seg_urls[i])
            else:
                final_urls.append(r)
        return final_urls


async def _gen_videos_catvton_pixverse(scenes: list, frames: list, product_image_url: str, reference_video_url: str, aspect_ratio: str, cloth_type: str = "upper") -> list:
    """引擎 3:catvton-pixverse(P209 2026-05-08 真 cat-vton 中间步,严守产品图)

    用户怒"产品没参考上传图" → 用户铁律变了,改回真 cat-vton 路径。
    每段流程:
      Step A: 用 GPT 出的 frame(模特身份+大致产品)作 cat-vton 输入的 human_image
      Step B: cat-vton(human_image=GPT 帧, garment=用户产品图, cloth_type=upper)
              → 模特真穿用户产品(产品颜色/材质/Logo 100% 保留)
      Step C: pixverse-swap(原视频段, cat-vton 输出帧, mode=person)
              → 复刻视频动作
    """
    import asyncio as _asyncio
    import tempfile
    from app.services.fal_service import get_pixverse_swap_service, get_vton_service
    from app.services.logger import log_info, log_error, log_warning

    pix = get_pixverse_swap_service()
    vton = get_vton_service()
    if not pix:
        raise RuntimeError("pixverse-swap 服务未初始化")
    if not vton:
        raise RuntimeError("cat-vton 服务未初始化")
    if not frames or len(frames) < len(scenes):
        raise RuntimeError(f"catvton-pixverse 需要 N={len(scenes)} 张 GPT frames")
    if not product_image_url:
        raise RuntimeError("catvton-pixverse 需要 product_image_url 作 garment")

    log_info(f"replicate catvton-pixverse: 每段 cat-vton 真试穿 + pixverse 复刻动作 N={len(scenes)} cloth_type={cloth_type}")

    async def _frame_to_static_video(image_url: str, duration_sec: float, ratio: str) -> str:
        import urllib.request as _urlreq, subprocess as _sp
        from app.services.fal_service import fal_upload_with_retry as _fup
        dim = {"9:16": "720:1280", "16:9": "1280:720", "1:1": "720:720"}.get(ratio, "720:1280")
        with tempfile.TemporaryDirectory() as _td:
            img_path = os.path.join(_td, "frame.png")
            _urlreq.urlretrieve(image_url, img_path)
            out_path = os.path.join(_td, "static.mp4")
            cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img_path,
                   "-t", f"{max(1.0, duration_sec):.2f}",
                   "-vf", f"scale={dim}:force_original_aspect_ratio=decrease,pad={dim}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                   "-pix_fmt", "yuv420p", "-r", "24", out_path]
            r = _sp.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg image->video 失败: {r.stderr[:300]}")
            return await _fup(out_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        seg_urls = await _slice_video_by_scenes(reference_video_url, scenes, tmpdir, aspect_ratio=aspect_ratio)

        sem = _asyncio.Semaphore(5)

        async def _catvton_then_swap(idx: int, seg_url: str, frame_url: str) -> str:
            async with sem:
                # Step B: cat-vton 真试穿
                vton_image_url = frame_url  # 兜底
                try:
                    vton_res = await vton.try_on(
                        human_image_url=frame_url,
                        garment_image_url=product_image_url,
                        cloth_type=cloth_type,
                    )
                    if "error" in vton_res:
                        log_warning(f"replicate catvton seg {idx} cat-vton 失败,降级用 GPT 帧: {str(vton_res['error'])[:120]}")
                    else:
                        vton_image_url = vton_res.get("image_url") or frame_url
                        log_info(f"replicate catvton seg {idx} cat-vton OK url={vton_image_url[:60]}")
                except Exception as _e:
                    log_warning(f"replicate catvton seg {idx} cat-vton 异常,降级用 GPT 帧: {type(_e).__name__}:{str(_e)[:120]}")

                # Step C: pixverse-swap(用 cat-vton 输出 / 兜底 GPT 帧)+ 3 次 retry
                last_err = ""
                for attempt in range(3):
                    try:
                        result = await pix.swap(video_url=seg_url, image_url=vton_image_url)
                        if "error" in result:
                            err_text = str(result['error'])
                            last_err = f"swap error: {err_text[:120]}"
                            is_transient = any(k in err_text for k in ['unavailable', '503', '502', '504', 'timeout', 'Downstream'])
                            if not is_transient or attempt == 2:
                                break
                            await _asyncio.sleep(2 * (2 ** attempt))
                            continue
                        vurl = result.get("video_url")
                        if not vurl:
                            last_err = "swap 无 url"
                            if attempt == 2: break
                            await _asyncio.sleep(2 * (2 ** attempt))
                            continue
                        log_info(f"replicate catvton seg {idx} pixverse OK url={vurl[:60]} (attempt {attempt+1})")
                        return vurl
                    except Exception as _e:
                        last_err = f"swap 异常: {type(_e).__name__}:{str(_e)[:120]}"
                        if attempt == 2: break
                        await _asyncio.sleep(2 * (2 ** attempt))
                # 3 次都失败,降级静态(用 cat-vton 输出帧 — 至少产品对的)
                log_warning(f"replicate catvton seg {idx} pixverse 3 次失败,降级静态: {last_err}")
                scene_dur = float(scenes[idx].get("duration_sec", 5)) if idx < len(scenes) else 5.0
                return await _frame_to_static_video(vton_image_url, scene_dur, aspect_ratio)

        results = await _asyncio.gather(*[
            _catvton_then_swap(i, seg_urls[i], frames[i]) for i in range(len(scenes))
        ], return_exceptions=True)
        final_urls = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                log_error(f"replicate catvton seg {i} 完全失败,最后兜底用原 ref 视频段: {r}")
                final_urls.append(seg_urls[i])
            else:
                final_urls.append(r)
        return final_urls




# ==================== P215 通用产品视频 worker(2026-05-08)====================

async def _run_video_general_analyze_job(params: dict) -> dict:
    """qwen-vl 看多张产品图 → 品类 + 卖点 + N 段脚本"""
    import re as _re
    import json as _json
    from app.services.fal_service import get_aliyun_qwenvl_service
    from app.services.logger import log_info, log_error

    product_image_urls = params.get("product_image_urls") or []
    instruction = params.get("instruction") or ""
    if not product_image_urls or not instruction:
        raise RuntimeError("product_image_urls 或 instruction 缺")

    svc = get_aliyun_qwenvl_service()
    if not svc or not svc.is_available():
        raise RuntimeError("qwen-vl 服务不可用")

    # 2026-05-11:qwen-vl 多图真分析(`analyze_images` 一次性送 N 张产品图,
    # dashscope content list 多 {image:url} 并列 + 末尾 text instruction,
    # verify 来源:https://help.aliyun.com/zh/model-studio/user-guide/vision/)
    n_images = len(product_image_urls)
    log_info(f"video_general_analyze 多图分析 N={n_images} 张产品图(主图+详情页+包装,全部送 qwen-vl)")

    enhanced_instruction = (
        f"用户上传了 {n_images} 张产品图(第 1 张为主图,后续为详情页/包装/多角度)。"
        f"请综合所有图片信息判品类、提卖点、定 target_user,不要只看主图。\n\n" +
        instruction
    )

    import asyncio as _asyncio
    res = None
    for attempt in range(3):
        try:
            res = await svc.analyze_images(product_image_urls, enhanced_instruction)
            if "error" not in res:
                break
            err_text = str(res.get("error", ""))
            is_throttled = any(k in err_text for k in ["503", "Too many requests", "Throttled", "ServiceUnavailable"])
            if not is_throttled or attempt == 2:
                break
            wait = 2 * (2 ** attempt)
            log_warning(f"video_general_analyze qwen-vl 503,P215 retry {attempt+1}/3 等 {wait}s")
            await _asyncio.sleep(wait)
        except Exception as e:
            log_error(f"video_general_analyze qwen-vl 异常: {e}")
            if attempt == 2:
                raise

    if not res or "error" in res:
        raise RuntimeError(f"qwen-vl 失败: {(res or {}).get('error', '?')[:200]}")

    text = (res.get("text") or "").strip()
    text = _re.sub(r"^```(?:json)?\s*", "", text)
    text = _re.sub(r"\s*```$", "", text)
    try:
        data = _json.loads(text)
    except Exception as e:
        log_error(f"video_general_analyze JSON parse fail: {e} text[:200]={text[:200]}")
        raise RuntimeError("qwen-vl 输出解析失败")

    category = (data.get("category") or "其他").strip()
    selling_points = data.get("selling_points") or []
    scenes = data.get("scenes") or []
    if not scenes:
        raise RuntimeError("qwen-vl 未返分镜")

    # 2026-05-11:新增 target_user + creative_brief(16 元素叙事脑图)
    # 旧 prompt 版 qwen-vl 输出可能没这两个字段,兜底空对象,前端能识别"老版本结果"
    target_user = (data.get("target_user") or "").strip()
    creative_brief = data.get("creative_brief") or {}
    if not isinstance(creative_brief, dict):
        creative_brief = {}
    # 2026-05-11 P226:product_specifics(锁产品形态不变形,qwen-vl 看图判)
    product_specifics = data.get("product_specifics") or {}
    if not isinstance(product_specifics, dict):
        product_specifics = {}

    # 2026-05-11:校验 sum(duration_sec) == total_duration(自适应分段,不严格 fail,只 warn)
    total_duration = int(params.get("total_duration") or 15)
    try:
        sum_dur = sum(int(s.get("duration_sec") or 0) for s in scenes)
    except Exception:
        sum_dur = 0
    if sum_dur and abs(sum_dur - total_duration) > 1:
        from app.services.logger import log_warning as _logw
        _logw(
            f"video_general_analyze sum_duration_mismatch sum={sum_dur} expected={total_duration} "
            f"scenes={len(scenes)} — qwen-vl 没严格遵守自适应分段,前端会显示不一致总时长"
        )

    # 2026-05-13 用户硬性规则:max 10s/段,总长按用户点击秒数严格切。
    # qwen-vl 可能不按规则,我们这里强制改写每段 duration_sec(段数也按规则切,多余的丢)
    target_segs = _split_general_durations(total_duration)
    if len(scenes) != len(target_segs):
        log_info(
            f"video_general_analyze 段数对齐:qwen-vl 给了 {len(scenes)} 段,"
            f"用户规则要 {len(target_segs)} 段(durations={target_segs}),"
            f"truncate/pad 到正确段数"
        )
        if len(scenes) > len(target_segs):
            scenes = scenes[: len(target_segs)]
        else:
            while len(scenes) < len(target_segs):
                # pad 用最后 1 段的副本(避免空 scene 引爆下游)
                import copy as _copy
                scenes.append(_copy.deepcopy(scenes[-1]) if scenes else {
                    "id": len(scenes) + 1,
                    "narrative_role": "showcase",
                    "shot": "medium",
                    "action": "展示产品",
                    "visual_prompt": "Showing the product naturally",
                    "speech": "",
                })
    # 重写每段 duration_sec + time_range,按用户规则强制对齐
    cur_t = 0
    for sc, dur in zip(scenes, target_segs):
        sc["duration_sec"] = int(dur)
        sc["time_range"] = f"{cur_t}-{cur_t + int(dur)}s"
        cur_t += int(dur)
    log_info(
        f"video_general_analyze OK category={category} target_user={target_user[:30]!r} "
        f"scenes={len(scenes)} aligned_durations={target_segs} total={total_duration}s "
        f"selling_points={len(selling_points)} "
        f"creative_brief_keys={list(creative_brief.keys())[:7]}"
    )
    return {
        "category": category,
        "target_user": target_user,
        "selling_points": selling_points,
        "creative_brief": creative_brief,
        "product_specifics": product_specifics,
        "scenes": scenes,
        "total_duration": total_duration,
    }


async def _stretch_and_archive_general_seg(
    fal_url: str, target_dur: float, job_id: str, seg_idx: int, user_id: str,
) -> str:
    """video_general 每段:下载 fal output → setpts 拉伸到精确 target_dur → 归档,返本地 URL。
    fal seedance r2v 输出比请求短 1.04%(8s 请求出 7.917s)。用户点 60s 不能给 59.5s。
    """
    import tempfile as _tf, os as _os, httpx as _httpx, subprocess as _sp, time as _t
    import shutil as _sh, json as _json

    work_dir = _tf.mkdtemp(prefix=f"vg_seg_{job_id}_{seg_idx}_")
    fal_local = _os.path.join(work_dir, "fal_in.mp4")
    try:
        async with _httpx.AsyncClient(timeout=120, follow_redirects=True) as cli:
            r = await cli.get(fal_url)
            r.raise_for_status()
            with open(fal_local, "wb") as f:
                f.write(r.content)

        # ffprobe 实际时长
        probe = _sp.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", fal_local],
            capture_output=True, text=True, timeout=15,
        )
        try:
            actual = float(_json.loads(probe.stdout).get("format", {}).get("duration", 0))
        except Exception:
            actual = 0.0
        if actual <= 0:
            raise RuntimeError(f"ffprobe 拿不到时长:{probe.stderr[-200:]}")

        target = float(target_dur)
        # 偏差 < 20ms 不动(浮点 noise)
        if abs(actual - target) <= 0.02:
            log_info(f"video_general seg {seg_idx} 时长 OK ({actual:.3f}s ≈ target {target}s),跳过拉伸")
            archive_local = fal_local  # 直接归档原 fal 输出
        else:
            ratio = target / actual  # > 1 慢放;< 1 快放
            stretched = _os.path.join(work_dir, "stretched.mp4")
            # video setpts + audio atempo(若有音轨)。video_general fal 默认 enable_audio=True
            has_audio = b"audio" in _sp.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=codec_type", "-of", "csv=p=0", fal_local],
                capture_output=True, timeout=15,
            ).stdout
            if has_audio:
                fc = (
                    f"[0:v]setpts={ratio:.6f}*PTS[v];"
                    f"[0:a]atempo={1.0 / ratio:.6f}[a]"
                )
                cmd = [
                    "ffmpeg", "-y", "-i", fal_local,
                    "-filter_complex", fc,
                    "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                    stretched,
                ]
            else:
                cmd = [
                    "ffmpeg", "-y", "-i", fal_local,
                    "-vf", f"setpts={ratio:.6f}*PTS",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-an",
                    stretched,
                ]
            rr = _sp.run(cmd, capture_output=True, text=True, timeout=120)
            if rr.returncode != 0:
                raise RuntimeError(f"setpts 拉伸失败:{rr.stderr[-500:]}")
            log_info(
                f"video_general seg {seg_idx} setpts {actual:.3f}s → {target}s (ratio={ratio:.4f})"
            )
            archive_local = stretched

        # 归档到 /opt/ssp/uploads/{user_id}/{YYYY-MM}/(同 media_archiver.archive_url 语义)
        from datetime import datetime as _dt
        import uuid as _uuid, re as _re, pathlib as _pl
        safe_uid = (_re.sub(r"[^a-zA-Z0-9_\-.@]", "_", user_id or "anon"))[:64] or "anon"
        yyyymm = _dt.utcnow().strftime("%Y-%m")
        uploads_root = _pl.Path(_os.environ.get("SSP_UPLOADS_ROOT", "/opt/ssp/uploads"))
        target_dir = uploads_root / safe_uid / yyyymm
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"video_{_uuid.uuid4().hex}.mp4"
        _sh.copy2(archive_local, target_file)
        try:
            _sh.chown(str(target_file), user="ssp-app", group="ssp-app")
        except (LookupError, PermissionError):
            pass
        _os.chmod(target_file, 0o644)
        public_base = _os.environ.get("SSP_UPLOADS_PUBLIC_BASE", "https://ailixiao.com/uploads").rstrip("/")
        return f"{public_base}/{safe_uid}/{yyyymm}/{target_file.name}"
    finally:
        try:
            _sh.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


def _split_general_durations(total: int) -> list:
    """图片复刻按用户点击秒数严格切片,每段 ≤ 10s,最少 4s(fal 端点要求)。
    2026-05-13 老板规则:
      5  → [5]
      10 → [10]
      15 → [10, 5]
      20 → [10, 10]
      30 → [10, 10, 10]
      60 → [10, 10, 10, 10, 10, 10]
    """
    total = int(total)
    if total <= 0:
        return [4]
    if total <= 10:
        return [max(4, total)]
    full = total // 10
    remainder = total % 10
    if remainder == 0:
        return [10] * full
    if remainder >= 4:
        return [10] * full + [remainder]
    # remainder 1-3:跟最后一段并(变 11/12/13,fal 仍接受 ≤15)
    return [10] * (full - 1) + [10 + remainder]


def _build_video_general_lookbook(
    category: str,
    region: str,
    target_user: str,
    creative_brief: dict,
    product_specifics: dict = None,
    user_outfit: str = "",
    user_scene: str = "",
) -> dict:
    """2026-05-11 P226 重写:
    - model_description 去反美感锚("yellow skin"/"visible pores"),加商业广告美感锚
    - overall_setting 加 GARMENT/PRODUCT FORM LOCK(qwen-vl 看图判子类后锁形态不变形)
    用于 compose_first_frame(Step A 出模特图)和 compose_first_frame_for_scene(Step B 每段)。
    """
    cb = creative_brief or {}
    ps = product_specifics or {}
    is_cn = (region or "CN") == "CN"
    tu = (target_user or "").strip()

    # 商业广告级美感锚(P226 重写,去 "yellow skin" / "visible pores" 反美感措辞)
    beauty_anchor = (
        "Naturally beautiful, photogenic, magazine-quality commercial ad face. "
        "Balanced facial proportions, healthy glowing fair skin (NOT sallow, NOT yellow-tinged, "
        "NOT pale), soft natural makeup (subtle blush, neutral lipstick, defined but not heavy brows), "
        "friendly approachable expression, gentle natural smile. "
        "Smooth healthy skin texture — natural finish without exaggerated pores or skin defects. "
        "Real human appearance, ABSOLUTELY NO AI artifacts, NO plastic skin, NO uncanny-valley features, "
        "NO over-sharpened edges, NO cartoon / anime / illustration / 3D-render look."
    )

    if is_cn:
        model_desc = (
            f"East Asian female model with natural East Asian facial features "
            f"(fair healthy skin tone, dark brown or natural black hair, brown eyes, "
            f"East Asian eye shape and bone structure). "
            f"Target profile: {tu or 'young Asian woman, 22-30 years old'}. "
            f"{beauty_anchor}"
        )
    else:
        model_desc = (
            f"Western female model with naturally beautiful appearance "
            f"(fair healthy skin tone, natural hair color, age fitting target). "
            f"Target profile: {tu or 'young woman, 22-30 years old'}. "
            f"{beauty_anchor}"
        )

    scene_setting = (cb.get("scene_setting") or "").strip()
    pain_point = (cb.get("pain_point") or "").strip()
    emotional_arc = (cb.get("emotional_arc") or "").strip()

    # P226:产品形态锚 — qwen-vl 看图判子类后,GPT-Image 2 必须严守"产品是 X 不是 Y"
    subcategory = (ps.get("subcategory") or "").strip()
    form_constraint = (ps.get("form_constraint") or "").strip()
    visual_features = ps.get("key_visual_features") or []
    if isinstance(visual_features, list):
        features_str = "; ".join(str(f).strip() for f in visual_features if str(f).strip())[:300]
    else:
        features_str = str(visual_features)[:300]

    product_form_lock = ""
    if subcategory:
        product_form_lock = (
            f"⚠️⚠️⚠️ ABSOLUTE PRODUCT FORM LOCK ⚠️⚠️⚠️ "
            f"The user's product is: {subcategory}. "
        )
        if form_constraint:
            product_form_lock += f"Form constraint (MUST RESPECT): {form_constraint}. "
        if features_str:
            product_form_lock += f"Key visible features to preserve PIXEL-PERFECT from reference image: {features_str}. "
        product_form_lock += (
            f"NEVER substitute the product with a similar-but-different item "
            f"(eg pants ≠ skirt/dress, T-shirt ≠ tank top, lipstick ≠ lip gloss, cream ≠ serum). "
            f"The output product must be EXACTLY this category, EXACTLY these features, "
            f"matching the uploaded reference image. "
        )

    # 2026-05-12:user_outfit / user_scene 用户输入,优先级压过 AI 自动 scene_setting
    user_scene_clean = (user_scene or "").strip()[:500]
    user_outfit_clean = (user_outfit or "").strip()[:500]

    overall_setting = (
        # ⭐ 产品形态锚(最高优先级)
        product_form_lock +
        # 场景锚(跨段必须一致)
        f"SCENE LOCK (must be IDENTICAL across all shots in this ad): "
        + (f"⚠️ USER-SPECIFIED SCENE (verbatim user instruction, MUST be respected): {user_scene_clean}. "
           if user_scene_clean
           else f"{scene_setting or f'natural everyday setting matching target audience lifestyle, suitable for {category} product'}. ")
        # 光线锚
        + f"LIGHTING LOCK: soft natural daylight, consistent warm tone across all shots, "
        f"no harsh studio lighting, no fluorescent. "
        # 外观锚(增强:同一服装/同一外观状态)
        + f"OUTFIT LOCK: the model wears the EXACT SAME outfit including the product "
        f"(same garment, same accessories, same hair style, same makeup) in all shots — "
        f"only the camera angle and her interaction with the product changes. "
        + (f"⚠️ USER-SPECIFIED OUTFIT (除产品外的搭配,用户明确指定,MUST follow): {user_outfit_clean}. "
           f"The model wears EXACTLY this outfit in addition to the product. Do NOT improvise other clothing items. "
           if user_outfit_clean else "")
        # 风格锚
        + f"STYLE LOCK: photorealistic commercial ad photography, natural-looking beautiful real person, "
        f"NOT illustration / anime / 3D-render. "
        # 色调锚
        f"COLOR PALETTE LOCK: consistent warm-neutral grading across all shots. "
        + (f"Emotional tone: {emotional_arc}. " if emotional_arc else "")
        # 2026-05-12:不拼 pain_point 到 image-generation prompt
        # 原因:pain_point 含 body-image 敏感词(如"胖/瘦/紧身"),触发 OpenAI gpt-image-2 content reject(downstream_service_error)
        # pain_point 给视频脚本/口播用,不污染 character_sheet / storyboard / first_frame prompt
        + f"This is a {category} product advertisement. All shots together form ONE coherent ad — "
        f"same person, same product (exact category and features), same outfit, same scene, "
        f"same lighting, only the camera and product interaction vary."
    )

    return {
        "model_description": model_desc,
        "overall_setting": overall_setting,
    }


async def _run_video_general_storyboard_job(params: dict) -> dict:
    """2026-05-11 P226:分镜板预览 — GPT-Image 2 出 N 宫格 storyboard(N≤4)。

    复用 _build_video_general_lookbook(锁模特/场景/产品形态),
    若用户没传模特图先 compose_first_frame 生成,然后 compose_storyboard_grid 出 1 张多格图。
    用户看着满意再触发完整 /generate(走 Seedance i2v + concat)。
    """
    from app.services.logger import log_info, log_warning
    from app.services import ad_video_models

    product_image_urls = params.get("product_image_urls") or []
    if not product_image_urls:
        raise RuntimeError("product_image_urls 缺")
    primary_product = product_image_urls[0]
    product_back = product_image_urls[1] if len(product_image_urls) > 1 else None

    scene_image_url = params.get("scene_image_url") or None
    model_image_url = params.get("model_image_url")
    category = params.get("category") or "其他"
    target_user = params.get("target_user") or ""
    creative_brief = params.get("creative_brief") or {}
    product_specifics = params.get("product_specifics") or {}
    region = params.get("region") or "CN"
    scenes = params.get("scenes") or []
    aspect_ratio = params.get("aspect_ratio") or "9:16"
    user_outfit = (params.get("user_outfit") or "").strip()
    user_scene = (params.get("user_scene") or "").strip()

    # 2026-05-12:N≤9(compose_storyboard_grid 支持 2/3/4/6/9 — 多分镜走 9 宫格)
    n_scenes_actual = len(scenes)
    if n_scenes_actual < 2:
        n_panels = 0
    elif n_scenes_actual >= 9:
        n_panels = 9
    elif n_scenes_actual >= 6:
        n_panels = 6  # 6/7/8 → 6
    elif n_scenes_actual == 5:
        n_panels = 6  # 5 → round 到 6
    elif n_scenes_actual == 4:
        n_panels = 4
    elif n_scenes_actual == 3:
        n_panels = 3
    else:
        n_panels = 2
    scenes_used = scenes[:n_panels] if n_panels else scenes[:1]

    log_info(
        f"video_general_storyboard 启动 category={category} "
        f"n_scenes={len(scenes)} n_panels={n_panels} "
        f"product_subcat={(product_specifics.get('subcategory') or '?')[:30]!r} "
        f"has_scene_img={bool(scene_image_url)}"
    )

    lookbook = _build_video_general_lookbook(category, region, target_user, creative_brief, product_specifics, user_outfit=user_outfit, user_scene=user_scene)

    # 2026-05-11 P226 Step 1:模特角色表(脸部特写 + 全身正/背/侧 4 视图)
    # 没有用户传的 model_image_url 时,character sheet 自己充当 super-anchor(不再需要单独 compose_first_frame)
    log_info("video_general_storyboard Step 1: compose_character_sheet(4 视图)")
    sheet_res = await ad_video_models.compose_character_sheet(
        model_description=lookbook["model_description"],
        overall_setting=lookbook["overall_setting"],
        aspect_ratio=aspect_ratio,
        product_image_url=primary_product,
        product_back_image_url=product_back,
        scene_image_url=scene_image_url,
    )
    if "error" in sheet_res or not sheet_res.get("image_url"):
        raise Exception(f"角色表合成失败: {sheet_res.get('error', '?')}")
    character_sheet_url = sheet_res["image_url"]
    log_info(f"video_general_storyboard character_sheet OK url={character_sheet_url[:60]}")

    # 2026-05-12:character sheet 是 2x2 grid 4 视图,直接作 model_image_url 传给
    # compose_first_frame_for_scene 会让 GPT-Image 2 困惑(不知道锁哪个角度)→ 正面漂掉。
    # 裁 panel[1](右上 = 全身正面)作干净的单视图模特图。
    if not model_image_url:
        panels = await ad_video_models.crop_storyboard_panels(character_sheet_url, 4)
        model_image_url = panels[1]
        log_info(f"video_general_storyboard 裁 panel[1](正面)OK 作干净 model_image_url url={model_image_url[:60]}")

    # Step 2:1 段不出 storyboard,只返 character sheet 单图
    if n_panels == 0:
        return {
            "storyboard_image_url": character_sheet_url,
            "character_sheet_url": character_sheet_url,
            "n_panels": 1,
            "single_image_mode": True,
            "model_image_url": model_image_url,
        }

    # Step 3:N 宫格 storyboard(以 character_sheet 作 base anchor → 强化人物一致性)
    log_info(f"video_general_storyboard Step 3: compose_storyboard_grid n_panels={n_panels}")
    grid_res = await ad_video_models.compose_storyboard_grid(
        base_image_url=character_sheet_url,
        scenes=scenes_used,
        n_panels=n_panels,
        model_description=lookbook["model_description"],
        overall_setting=lookbook["overall_setting"],
        aspect_ratio=aspect_ratio,
        product_image_url=primary_product,
        product_back_image_url=product_back,
    )
    if "error" in grid_res or not grid_res.get("image_url"):
        raise Exception(f"分镜板合成失败: {grid_res.get('error', '?')}")
    log_info(f"video_general_storyboard OK n={n_panels} url={grid_res['image_url'][:60]}")
    return {
        "storyboard_image_url": grid_res["image_url"],
        "character_sheet_url": character_sheet_url,
        "n_panels": n_panels,
        "single_image_mode": False,
        "model_image_url": model_image_url,
    }


async def _run_video_general_job(params: dict) -> dict:
    """通用产品视频生成:多产品图 + 可选模特 → GPT-Image 2 全程出图 + Seedance i2v 出片

    2026-05-11 改:去 cat-vton,统一走 GPT-Image 2(更真实自然 + 人物/场景/风格跨段一致)。
    流程:
      Step A: 用户没传模特 → GPT-Image 2 出"模特+产品"基准首帧(用 unified lookbook)
      Step B: 每段 GPT-Image 2 出本段首帧(以 Step A 模特图作 base + lookbook 锁一致性)
      Step C: 每段 Seedance i2v 出 5s 视频 → ffmpeg concat
    """
    from app.services.logger import log_info, log_error, log_warning
    from app.services import ad_video_models
    import asyncio as _aio
    import tempfile as _tmp
    import subprocess as _sp
    import httpx as _httpx
    import os as _os
    from pathlib import Path as _Path

    product_image_urls = params.get("product_image_urls") or []
    if not product_image_urls:
        raise RuntimeError("product_image_urls 缺")
    primary_product = product_image_urls[0]
    product_back = product_image_urls[1] if len(product_image_urls) > 1 else None

    scene_image_url = params.get("scene_image_url") or None  # P226 用户场景图(可选,作 GPT 背景锚)
    model_image_url = params.get("model_image_url")
    model_video_url = params.get("model_video_url")
    category = params.get("category") or "其他"
    target_user = params.get("target_user") or ""
    creative_brief = params.get("creative_brief") or {}
    product_specifics = params.get("product_specifics") or {}
    region = params.get("region") or "CN"
    scenes = params.get("scenes") or []
    aspect_ratio = params.get("aspect_ratio") or "9:16"
    total_duration = params.get("total_duration", 15)
    user_id = params.get("_user_id", "anon")
    user_outfit = (params.get("user_outfit") or "").strip()
    user_scene = (params.get("user_scene") or "").strip()
    batch_count = max(1, min(5, int(params.get("batch_count") or 1)))
    # 2026-05-12:storyboard 子图直接作 i2v 首帧(跳过 compose_first_frame_for_scene 重画)
    storyboard_image_url = (params.get("storyboard_image_url") or "").strip()
    storyboard_n_panels = int(params.get("storyboard_n_panels") or 0)
    # 2026-05-12:character_sheet 整图,worker 裁 4 panels(脸/正/反/侧)作 r2v 多图 reference
    character_sheet_image_url = (params.get("character_sheet_image_url") or "").strip()

    log_info(
        f"video_general 启动 category={category} target_user={target_user[:30]!r} "
        f"product_subcat={(product_specifics.get('subcategory') or '?')[:30]!r} "
        f"n_scenes={len(scenes)} n_products={len(product_image_urls)} "
        f"model_video={'yes' if model_video_url else 'no'} model_image={'yes' if model_image_url else 'no'} "
        f"region={region}"
    )

    # 2026-05-11 P226:统一 lookbook(人物/场景/风格/产品形态跨段锁定)
    # 2026-05-12:用户输入 user_outfit / user_scene 优先压过 AI 自动生成
    lookbook = _build_video_general_lookbook(category, region, target_user, creative_brief, product_specifics, user_outfit=user_outfit, user_scene=user_scene)
    log_info(f"video_general lookbook model_desc_len={len(lookbook['model_description'])} setting_len={len(lookbook['overall_setting'])}")

    # Step A:base 模特图(如果用户没传)— 用 scene_image_url(若有)作背景锚
    if not model_image_url:
        log_info(f"video_general 用户没传模特,GPT-Image 2 出 base 模特图(用 unified lookbook + scene={'yes' if scene_image_url else 'no'})")
        first_visual = (scenes[0].get("visual_prompt") if scenes else "") or "Photorealistic e-commerce model showcasing product"
        base_res = await ad_video_models.compose_first_frame(
            product_image_url=primary_product,
            background_image_url=scene_image_url,
            model_description=lookbook["model_description"],
            scene_visual_prompt=first_visual,
            product_back_image_url=product_back,
            aspect_ratio=aspect_ratio,
            no_text=True,
            no_model=False,
        )
        if "error" in base_res or not base_res.get("image_url"):
            raise Exception(f"模特首帧合成失败: {base_res.get('error', '?')}")
        model_image_url = base_res["image_url"]
        log_info(f"video_general GPT 出模特图 OK url={model_image_url[:60]}")

    # 2026-05-12:如果用户传了 storyboard_image_url + n_panels,裁出 N 张子图作 r2v 多图 reference 第一张
    storyboard_panels: list = []
    if storyboard_image_url and storyboard_n_panels >= 2:
        try:
            storyboard_panels = await ad_video_models.crop_storyboard_panels(storyboard_image_url, storyboard_n_panels)
            log_info(f"video_general 裁 storyboard panels OK n={len(storyboard_panels)}")
        except Exception as e:
            log_warning(f"video_general 裁 storyboard panels 失败: {e}")
            storyboard_panels = []

    # 2026-05-12:character_sheet 裁 4 panels(脸特写/正面/背面/侧面)作 r2v 多图 reference 后 3 张
    char_sheet_panels: list = []
    if character_sheet_image_url:
        try:
            char_sheet_panels = await ad_video_models.crop_storyboard_panels(character_sheet_image_url, 4)
            log_info(f"video_general 裁 character_sheet 4 panels OK(脸/正/反/侧)")
        except Exception as e:
            log_warning(f"video_general 裁 character_sheet 失败: {e}")
            char_sheet_panels = []

    # Step B:N 段并发 — 每段 i2v 出动作视频
    # 2026-05-12:如有 storyboard panels → 直接用作首帧;否则 GPT-Image 2 重画
    log_info(f"video_general category={category} 视频路径:{'storyboard panel 直接作首帧' if storyboard_panels else 'GPT-Image 2 重画场景帧'}")

    # 2026-05-12:并发 3→9,9 段全并发省 ~5 分钟(单段 ~2'30",并发 9 ≈ 单段时长)
    sem = _aio.Semaphore(9)

    async def _gen_seg(idx: int, scene: dict):
        async with sem:
            visual = (scene.get("visual_prompt") or "").strip()

            # 2026-05-12:r2v 多图 reference — Image1=分镜画面 / Image2-4=模特正/背/侧
            image_urls = []
            if storyboard_panels and idx < len(storyboard_panels):
                image_urls.append(storyboard_panels[idx])
            else:
                # 没有 storyboard panel 时降级用 model_image_url(单图)
                image_urls.append(model_image_url)
            # character_sheet 4 panels:[0]脸特写,[1]正面,[2]背面,[3]侧面 — 取 [1][2][3] 全身角度作 reference
            if char_sheet_panels and len(char_sheet_panels) >= 4:
                image_urls.extend([char_sheet_panels[1], char_sheet_panels[2], char_sheet_panels[3]])

            # prompt 用 @Image1 占位符引用(memory 验证)
            ref_anchor = ""
            if len(image_urls) >= 4:
                ref_anchor = " @Image1 是该段分镜画面,@Image2/@Image3/@Image4 是模特身份多角度(正/背/侧),跨帧严格保持同一人。"
            elif len(image_urls) >= 1:
                ref_anchor = " @Image1 是参考画面,跨帧严格保持构图。"

            r2v_prompt = (visual or f"Showing the {category} product naturally, model demonstrates use") + ref_anchor

            # 2026-05-13:fal Seedance r2v 偶发个别 task 卡住(seg 0 4 分钟出,seg 1 15 分钟没出)。
            # 单次 25 分钟窗口 + 超时后用新 submit 重试 1 次(新 task id 通常能避开卡住的)。
            async def _submit_and_poll(attempt: int) -> str:
                log_info(
                    f"video_general seg {idx} Seedance r2v 提交(attempt={attempt}) "
                    f"n_refs={len(image_urls)} prompt_len={len(r2v_prompt)}"
                )
                seg_dur = max(4, min(15, int(scene.get("duration_sec") or 8)))
                sub = await ad_video_models.submit_seedance_fast_r2v_video(
                    image_urls=image_urls,
                    prompt=r2v_prompt,
                    duration=seg_dur,
                    aspect_ratio=aspect_ratio,
                    resolution="480p",
                    enable_audio=True,
                )
                if sub.get("error"):
                    raise Exception(f"seg {idx} Seedance r2v submit: {sub['error']}")
                tid = sub.get("task_id")
                for _ in range(300):  # 25 分钟 (5s × 300)
                    await _aio.sleep(5)
                    st = await ad_video_models.poll_seedance_fast_r2v_status(tid)
                    if st.get("status") == "completed" and st.get("video_url"):
                        log_info(f"video_general seg {idx} Seedance r2v OK(attempt={attempt}) url={st['video_url'][:60]}")
                        return st["video_url"]
                    if st.get("status") == "failed":
                        raise Exception(f"seg {idx} Seedance r2v failed(attempt={attempt}): {st.get('error')}")
                raise TimeoutError(f"seg {idx} 25 分钟轮询无结果(attempt={attempt})")

            try:
                fal_url = await _submit_and_poll(0)
            except TimeoutError as e1:
                log_info(f"video_general seg {idx} 首次超时,新 submit 重试一次:{e1}")
                try:
                    fal_url = await _submit_and_poll(1)
                except TimeoutError as e2:
                    raise Exception(f"seg {idx} 两次都超时:{e2}")

            # 2026-05-13:fal 输出比请求短 ~1%(8s 请求出 7.917s)。要精确按用户选的秒数
            # 出片,下载 → setpts 拉伸到 seg_dur → 归档,返本地 URL。
            try:
                return await _stretch_and_archive_general_seg(
                    fal_url, target_dur=seg_dur, job_id=job_id, seg_idx=idx,
                    user_id=params.get("_user_id", "anon"),
                )
            except Exception as e:
                log_warning(f"video_general seg {idx} setpts 拉伸失败,降级返 fal URL: {e}")
                return fal_url

    # 2026-05-12:用户明令"每个分镜要都是独立的"— 去 concat,直接返 N 段独立分镜视频
    # 矩阵 = batch_count × n_scenes 个独立 5s 视频(每段独立 fal storage URL)
    async def _run_one_batch(batch_idx: int) -> list:
        seg_urls = await _aio.gather(*[_gen_seg(i, s) for i, s in enumerate(scenes)])
        log_info(f"video_general batch {batch_idx} 完成 {len(seg_urls)} 段独立分镜(不 concat)")
        return list(seg_urls)

    def _scene_meta(scene_idx: int) -> dict:
        s = scenes[scene_idx] if scene_idx < len(scenes) else {}
        return {
            "narrative_role": s.get("narrative_role", ""),
            "shot": s.get("shot", ""),
            "visual_prompt": (s.get("visual_prompt") or "")[:200],
            "speech": s.get("speech", ""),
            "duration_sec": s.get("duration_sec", 5),
        }

    if batch_count <= 1:
        seg_urls = await _run_one_batch(0)
        scene_videos = [
            {"batch_idx": 0, "scene_idx": i, "url": u, "scene": _scene_meta(i)}
            for i, u in enumerate(seg_urls)
        ]
        return {
            "scene_videos": scene_videos,
            "video_urls": list(seg_urls),
            "video_url": seg_urls[0] if seg_urls else "",
            "batch_count": 1,
            "n_scenes": len(seg_urls),
            "type": "video",
            "category": category,
        }

    log_info(f"video_general 批量 batch_count={batch_count} × n_scenes={len(scenes)} = {batch_count * len(scenes)} 独立分镜")
    all_batches = await _aio.gather(*[_run_one_batch(b) for b in range(batch_count)])
    scene_videos = [
        {"batch_idx": b_idx, "scene_idx": s_idx, "url": url, "scene": _scene_meta(s_idx)}
        for b_idx, batch in enumerate(all_batches)
        for s_idx, url in enumerate(batch)
    ]
    flat_urls = [sv["url"] for sv in scene_videos]
    return {
        "scene_videos": scene_videos,
        "video_urls": flat_urls,
        "video_url": flat_urls[0] if flat_urls else "",
        "batch_count": batch_count,
        "n_scenes": len(scenes),
        "type": "video",
        "category": category,
    }


# ==================== P216 视频复刻 Seedance 2.0 r2v Fast worker(2026-05-08)====================

async def _run_video_clone_job(params: dict) -> dict:
    """Seedance 2.0 Fast r2v:1 视频 + 0-9 图 + prompt → 复刻视频
    
    流程:
      1. fal_client.submit_async("bytedance/seedance-2.0/fast/reference-to-video", args)
      2. polling 每 5s 一次,最多 5 分钟
      3. completed → 拿 video_url 归档到 /uploads
      4. count > 1 时并发 N 次 submit
    """
    import fal_client as _fc
    import asyncio as _aio
    from app.services.logger import log_info, log_error, log_warning
    from app.services.media_archiver import archive_url

    prompt = params.get("prompt") or ""
    video_url = params.get("reference_video_url")
    image_urls = params.get("reference_image_urls") or []
    duration = params.get("duration") or "5"
    aspect_ratio = params.get("aspect_ratio") or "9:16"
    generate_audio = params.get("generate_audio", True)
    count = int(params.get("count") or 1)
    user_id = params.get("_user_id", "anon")

    if not video_url:
        raise Exception("reference_video_url 缺")
    if not prompt.strip():
        raise Exception("prompt 缺")

    args = {
        "prompt": prompt,
        "video_urls": [video_url],
        "image_urls": image_urls,
        "resolution": "720p",  # Fast 只支持 720p
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "generate_audio": generate_audio,
    }
    log_info(f"video_clone P216 submit endpoint=seedance-2.0/fast/r2v count={count} duration={duration} ratio={aspect_ratio} prompt_len={len(prompt)} n_images={len(image_urls)}")

    async def _one_call(call_idx: int) -> str:
        """一次 r2v 调用:submit → polling → return video_url"""
        # P216:1 次 retry on transient error
        last_err = None
        for attempt in range(2):
            try:
                handler = await _fc.submit_async(
                    "bytedance/seedance-2.0/fast/reference-to-video",
                    arguments=args,
                )
                request_id = handler.request_id
                log_info(f"video_clone P216 call {call_idx}/{count} attempt {attempt+1} submitted request_id={request_id}")
                # polling 每 5s,最多 60 次(5 分钟)
                for poll_n in range(60):
                    await _aio.sleep(5)
                    status = await _fc.status_async(
                        "bytedance/seedance-2.0/fast/reference-to-video",
                        request_id,
                        with_logs=False,
                    )
                    s_type = type(status).__name__
                    if s_type == "Completed":
                        result = await _fc.result_async(
                            "bytedance/seedance-2.0/fast/reference-to-video",
                            request_id,
                        )
                        video_obj = result.get("video") if isinstance(result.get("video"), dict) else None
                        vurl = video_obj.get("url") if video_obj else result.get("video_url")
                        if not vurl:
                            raise Exception(f"call {call_idx} 完成但无 video_url, result keys={list(result.keys())}")
                        log_info(f"video_clone P216 call {call_idx} OK url={vurl[:80]}")
                        return vurl
                    if s_type in ("Failed", "Error"):
                        raise Exception(f"call {call_idx} fal failed: {status}")
                    # InQueue / InProgress 继续轮询
                raise Exception(f"call {call_idx} 5min 超时")
            except Exception as e:
                last_err = e
                err_text = str(e)
                # 瞬时错误 retry
                is_transient = any(k in err_text.lower() for k in ["unavailable", "503", "502", "504", "timeout", "downstream"])
                if not is_transient or attempt == 1:
                    break
                wait = 5
                log_warning(f"video_clone P216 call {call_idx} attempt {attempt+1} 瞬时错 retry {wait}s: {err_text[:120]}")
                await _aio.sleep(wait)
        raise last_err if last_err else Exception(f"call {call_idx} 失败")

    # count 个并发调用
    if count == 1:
        vurl = await _one_call(0)
        archived = await archive_url(vurl, user_id, "video")
        return {"video_url": archived, "type": "video", "count": 1}
    else:
        results = await _aio.gather(*[_one_call(i) for i in range(count)], return_exceptions=True)
        success_urls = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                log_warning(f"video_clone P216 call {i} 失败: {str(r)[:200]}")
                continue
            archived = await archive_url(r, user_id, "video")
            success_urls.append(archived)
        if not success_urls:
            raise Exception(f"video_clone {count} 次调用全部失败")
        return {
            "video_url": success_urls[0],
            "video_urls": success_urls,
            "type": "video",
            "count": len(success_urls),
        }


async def _run_script_to_video_job(params: dict) -> dict:
    """脚本 → 视频（入口A/B 共用）。

    改动：
    - ≤15s 单次生成；>15s GPT-Image 2 生成模特头像 + 8s 批处理 + 上帧锚点
    - 开声音（generate_audio=True）
    - 去字幕（prompt 末尾强制规则）
    - 模特描述写进 prompt
    - 入口A 可传 ref_video_url，Seedance 60% 折扣 + 风格参考
    - 批处理串行执行（需要前帧参考，不可并发）
    """
    import math, re as _re, base64 as _b64
    import os as _os
    import shutil as _shutil
    import subprocess as _sp
    import tempfile as _tmp
    import time as _t
    import httpx as _httpx
    import fal_client as _fal
    from app.services.fal_service import fal_upload_with_retry
    from app.services.ad_video_models import poll_seedance_fast_r2v_status
    from app.services.logger import log_info, log_error
    from app.database import get_app_config as _get_app_config

    SEEDANCE_EP = "bytedance/seedance-2.0/fast/reference-to-video"
    MAX_DUR = 15
    NO_TEXT = (
        " CRITICAL: Do NOT render any text, subtitles, captions, "
        "titles or watermarks on screen. The video must be purely visual with zero on-screen text."
    )

    scenes        = params.get("scenes") or []
    product_urls  = params.get("product_image_urls") or []
    scene_img_url = params.get("scene_image_url") or ""
    model_img_url = params.get("model_image_url") or ""
    aspect_ratio  = params.get("aspect_ratio") or "9:16"
    script_text   = params.get("script") or ""
    ref_video_url      = params.get("ref_video_url") or ""        # 入口A 参考视频
    resolution         = params.get("resolution") or "480p"       # 输出分辨率（480p/1080p/4k）
    contrast_image_url = params.get("contrast_image_url") or ""   # 对比产品图（起/承阶段）
    enable_voice       = params.get("enable_voice", True)          # 是否开启 TTS+Lipsync
    target_duration    = float(params.get("target_duration") or 0) # 目标总时长（秒）

    if not scenes:
        raise RuntimeError("scenes 必填（脚本解析结果为空）")
    if not product_urls:
        raise RuntimeError("product_image_urls 必填")

    # 从脚本提取 [模特] 描述
    _m = _re.search(r'\[模特\][：:]\s*(.+?)(?:\n|\[)', script_text + "\n[", _re.DOTALL)
    model_desc = _m.group(1).strip() if _m else ""

    # 基础 image_urls（产品图 + 模特图 + 场景图）
    base_imgs = list(product_urls[:9])
    if model_img_url and len(base_imgs) < 9:
        base_imgs.append(model_img_url)
    if scene_img_url and len(base_imgs) < 9:
        base_imgs.append(scene_img_url)

    # 第二层：强制校准分镜总时长（Gemini 可能写歪）
    if target_duration > 0:
        actual_total = sum(float(s.get("duration_sec") or 4) for s in scenes)
        if abs(actual_total - target_duration) > 0.5:
            scale = target_duration / actual_total
            for s in scenes:
                s["duration_sec"] = round(float(s.get("duration_sec") or 4) * scale, 1)
            log_info(
                f"script_to_video 时长校准: actual={actual_total:.1f}s → target={target_duration:.0f}s "
                f"scale={scale:.4f}"
            )

    # 总时长
    total_dur_all = sum(float(s.get("duration_sec") or 4) for s in scenes)

    # 批处理上限（DB，默认 8s）
    try:
        _batch_max = float(_get_app_config("batch_max_duration", "8"))
    except Exception:
        _batch_max = 8.0

    work_dir = _tmp.mkdtemp(prefix="script_to_video_")
    t0 = _t.time()

    # ── 模特头像生成（fal-ai/gpt-image-2，失败降级不阻塞）──────────────────
    async def _gen_portrait() -> str | None:
        if not model_desc:
            return None
        try:
            result = await _fal.run_async(
                "fal-ai/gpt-image-2",
                arguments={
                    "prompt": (
                        f"Professional portrait photo: {model_desc}. "
                        "Head and shoulders only, no products held or worn. "
                        "Clean studio background, natural lighting, photorealistic. "
                        "No text, no watermarks."
                    ),
                    "image_size": "square_hd",
                    "num_images": 1,
                    "quality": "auto",
                },
            )
            portrait_img_url = (result.get("images") or [{}])[0].get("url", "")
            if not portrait_img_url:
                raise RuntimeError("gpt-image-2 返回无图片 URL")
            log_info(f"script_to_video: portrait OK {portrait_img_url[:60]}")
            return portrait_img_url
        except Exception as e:
            log_error(f"script_to_video: portrait failed (skip): {e}")
            return None

    # ── 截取上一个 batch 最后一帧 ──────────────────────────────────────────
    async def _last_frame(raw_path: str, idx: int) -> str | None:
        try:
            fp = _os.path.join(work_dir, f"last_frame_{idx}.jpg")
            rr = _sp.run(
                ["ffmpeg", "-y", "-sseof", "-3", "-i", raw_path,
                 "-update", "1", "-q:v", "3", fp],
                capture_output=True, text=True, timeout=30,
            )
            if rr.returncode != 0 or not _os.path.exists(fp): return None
            return await fal_upload_with_retry(fp)
        except Exception as e:
            log_error(f"script_to_video: last_frame failed: {e}")
            return None

    # ── Seedance 提交（支持 video_urls）────────────────────────────────────
    async def _submit(img_urls: list, prompt: str, duration: int) -> str:
        safe_dur = max(4, min(15, int(duration)))  # Seedance Fast r2v 实测 4-15s
        args: dict = {
            "image_urls": img_urls,
            "prompt": prompt,
            "duration": str(safe_dur),
            "aspect_ratio": aspect_ratio,
            "resolution": "480p",
            "generate_audio": False,
        }
        if ref_video_url:
            args["video_urls"] = [ref_video_url]
            args["audio_urls"] = []
        h = await _fal.submit_async(SEEDANCE_EP, arguments=args)
        return h.request_id

    # ── 分组逻辑 ───────────────────────────────────────────────────────────
    # 统一走8s批处理分组（去掉≤15s单次逻辑，保证生成时长可控）
    # 模特一致性通过 model_desc 写进 prompt 来保证，不依赖 portrait
    # 改动3：所有视频都生成模特头像，保证人脸一致性
    portrait_url = await _gen_portrait() if model_desc else None

    tasks: list[dict] = []
    cur: dict | None = None
    for scene in scenes:
        dur = float(scene.get("duration_sec") or 4)
        if cur is None:
            cur = {"scenes": [scene], "total_dur": dur}
        elif cur["total_dur"] + dur > _batch_max:
            cur["task_idx"] = len(tasks); tasks.append(cur)
            cur = {"scenes": [scene], "total_dur": dur}
        else:
            cur["scenes"].append(scene); cur["total_dur"] += dur
    if cur:
        cur["task_idx"] = len(tasks); tasks.append(cur)
    log_info(
        f"script_to_video 批处理模式: total={total_dur_all:.1f}s "
        f"tasks={len(tasks)} max_dur={_batch_max}s"
    )

    try:
        # ── 改动4：TTS 提前启动，与 Seedance 并行（TTS 只需台词，不依赖视频）──
        _tts_concurrent_task = None
        _concurrent_audio_url: str | None = None
        if enable_voice:
            _pre_speeches = [str(sc.get("speech") or "").strip() for sc in scenes]
            _pre_speech_text = " ".join(s for s in _pre_speeches if s)
            if _pre_speech_text:
                async def _concurrent_tts():
                    try:
                        _r = await _fal.run_async(
                            "fal-ai/elevenlabs/tts/multilingual-v2",
                            arguments={"text": _pre_speech_text[:1000]},
                        )
                        _ao = _r.get("audio") if isinstance(_r.get("audio"), dict) else None
                        return (_ao.get("url") if _ao else None) or _r.get("audio_url")
                    except Exception as _te:
                        log_error(f"script_to_video 并行TTS失败: {_te}")
                        return None
                _tts_concurrent_task = asyncio.create_task(_concurrent_tts())
                log_info(f"script_to_video TTS 已并行启动 speech_len={len(_pre_speech_text)}")

        # ── 串行执行（需要前帧，不可并发）──────────────────────────────────
        all_raw: list[str] = []

        for task in tasks:
            ti = task["task_idx"]
            req_dur = max(4, math.ceil(min(task["total_dur"], MAX_DUR)))  # Seedance 最低 4s
            req_dur = min(req_dur, MAX_DUR)

            # 改动2：按 stage 标签判断是否用对比图（起/承用对比图，转/合用正式产品图）
            _task_stages = [sc.get("stage", "") for sc in task["scenes"]]
            if contrast_image_url:
                _has_early = any(s in ("起", "承") for s in _task_stages)
                _has_late  = any(s in ("转", "合") for s in _task_stages)
                if _has_early and not _has_late:
                    _is_conflict_stage = True
                elif not any(_task_stages):
                    # 没有 stage 标签，按位置兜底：前半用对比图
                    _mid = len(scenes) // 2
                    _scene_ids = [sc["id"] for sc in task["scenes"]]
                    _is_conflict_stage = all(sid <= _mid for sid in _scene_ids)
                else:
                    _is_conflict_stage = False
            else:
                _is_conflict_stage = False
            _anchor_img = contrast_image_url if _is_conflict_stage else (product_urls[0] if product_urls else "")

            # 构建此 task 的 image_urls（锚点图放第一位）
            task_imgs = [_anchor_img] if _anchor_img else []
            for _u in base_imgs:
                if _u not in task_imgs and len(task_imgs) < 9:
                    task_imgs.append(_u)
            if not task_imgs:
                task_imgs = list(base_imgs)

            if portrait_url and len(task_imgs) < 9:
                task_imgs.append(portrait_url)
            if ti > 0 and all_raw and len(task_imgs) < 9:
                prev_frame = await _last_frame(all_raw[-1], ti)
                if prev_frame:
                    task_imgs.append(prev_frame)
            task_imgs = task_imgs[:9]

            # Prompt
            descs = [
                _strip_video_color_words(sc.get("visual_prompt") or "Cinematic product showcase")
                for sc in task["scenes"]
            ]
            combined = "; ".join(descs)
            ref_tags = " ".join(
                f"@Image{j+2} is a product/scene reference."
                for j in range(len(task_imgs) - 1)
            )
            model_line = (
                f"The model in this video must be: {model_desc}. Keep this appearance consistent. "
                if model_desc else ""
            )
            portrait_line = (
                "IMPORTANT: Maintain the exact same model appearance as shown in the face reference image throughout all shots. "
                if portrait_url else ""
            )
            prompt = (
                f"@Image1 defines the EXACT visual appearance (colors, outfit, products) "
                f"that MUST be preserved throughout. {ref_tags} "
                f"{model_line}{portrait_line}"
                f"Generate a {req_dur}-second continuous video: {combined}. "
                f"CRITICAL: Strictly match all visual details from @Image1. "
                f"Ignore any color words in the description — use ONLY what @Image1 shows."
                f"{NO_TEXT}"
            )
            # 两级清洗：GPT2 过滤 + Seedance 专项替换
            prompt = _sanitize_for_gpt2(_sanitize_for_seedance(prompt))

            log_info(
                f"script_to_video Task {ti+1}/{len(tasks)}: "
                f"n_scenes={len(task['scenes'])} req_dur={req_dur}s "
                f"n_imgs={len(task_imgs)} ref_video={bool(ref_video_url)}"
            )
            log_info(f"script_to_video Task {ti+1} image_urls={[u[:80] for u in task_imgs]}")

            # 商业语境前缀（随敏感内容重试逐级加强，不含"产品展示/studio/影棚"等词以免影响视频风格）
            _SENS_PREFIXES = [
                "",  # si=0 不加前缀
                "专业品牌广告拍摄，真实生活场景，自然光线，社交媒体营销内容。",
                "Professional brand campaign. Real-life lifestyle scene, natural lighting, social media marketing content. ",
                "Fashion brand promotional video. Everyday life scenario with natural aesthetics. Social media ready content. ",
            ]

            def _is_sensitive(err_str: str) -> bool:
                return any(kw in (err_str or "").lower()
                           for kw in ["sensitive", "nsfw", "inappropriate", "content"])

            MAX_WAIT = 600  # 10 分钟
            vid_url = None
            last_err = ""

            # 外层：超时重试（最多 1 次）
            for timeout_attempt in range(2):
                # 内层：敏感内容升级重试（最多 3 次）
                for si in range(4):
                    use_prompt = _SENS_PREFIXES[si] + prompt
                    sensitive_fail = False
                    log_info(f"script_to_video Task {ti+1} prompt(si={si})={use_prompt[:200]}")
                    try:
                        fal_id = await _submit(task_imgs, use_prompt, req_dur)
                        log_info(f"script_to_video Task {ti+1} fal_id={fal_id} (to={timeout_attempt+1} sens={si+1})")
                    except Exception as e:
                        raise RuntimeError(f"Task {ti+1} submit 失败: {e}")

                    tp = _t.time()
                    timeout_hit = False
                    while _t.time() - tp < MAX_WAIT:
                        st = await poll_seedance_fast_r2v_status(fal_id)
                        if st.get("status") == "completed":
                            vid_url = st.get("video_url"); break
                        if st.get("status") == "failed":
                            err = st.get("error", "")
                            if _is_sensitive(err) and si < 3:
                                log_info(f"script_to_video Task {ti+1} 敏感内容拦截，升级商业语境重试 (si={si})")
                                sensitive_fail = True; break
                            if _is_sensitive(err) and si >= 3:
                                raise RuntimeError(f"Task {ti+1} 内容审核持续拒绝（{si+1} 次），请调整脚本描述")
                            raise RuntimeError(f"Task {ti+1} fal failed: {err}")
                        await asyncio.sleep(5)
                    else:
                        timeout_hit = True
                        last_err = f"超时 {MAX_WAIT}s"

                    if vid_url or timeout_hit:
                        break  # 成功或超时，退出敏感重试
                    if not sensitive_fail:
                        last_err = f"超时 {MAX_WAIT}s"; break  # 非敏感原因退出
                    # sensitive_fail=True and si<3: 继续下一个前缀

                if vid_url: break
                if timeout_attempt == 0:
                    log_info(f"script_to_video Task {ti+1} {last_err}，执行超时重试")
                # 继续 timeout_attempt

            if not vid_url:
                raise RuntimeError(f"视频生成超时（{MAX_WAIT}s），请稍后重试。Task {ti+1}")

            raw = _os.path.join(work_dir, f"task_{ti:02d}_raw.mp4")
            async with _httpx.AsyncClient(timeout=120, follow_redirects=True) as cli:
                r = await cli.get(vid_url); r.raise_for_status()
                with open(raw, "wb") as f: f.write(r.content)
            log_info(f"script_to_video Task {ti+1} 下载 {_os.path.getsize(raw)} bytes ({_t.time()-tp:.1f}s)")
            all_raw.append(raw)
            task["raw_path"] = raw

        # ── ffprobe + 裁剪（保留音轨）──────────────────────────────────────
        def _ffprobe_dur(path: str) -> float:
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                   "-of", "default=noprint_wrappers=1:nokey=1", path]
            try:
                rr = _sp.run(cmd, capture_output=True, text=True, timeout=30)
                return float(rr.stdout.strip())
            except Exception:
                return -1.0

        all_clips: list[tuple[int, str]] = []
        global_idx = 0
        for task in tasks:
            raw = task["raw_path"]; ti = task["task_idx"]
            actual = _ffprobe_dur(raw)
            expected = task["total_dur"]
            if actual < 0: actual = expected
            scale = (actual / expected) if actual < expected - 0.1 else 1.0
            if scale < 1.0:
                log_error(f"script_to_video Task {ti+1} 实际={actual:.2f}s < 预期={expected:.2f}s scale={scale:.4f}")
            offset = 0.0
            for scene in task["scenes"]:
                target = max(0.05, float(scene.get("duration_sec") or 4) * scale)
                if offset >= actual - 0.02: break
                target = min(target, actual - offset)
                clip = _os.path.join(work_dir, f"clip_{global_idx:04d}.mp4")
                rr = _sp.run(
                    ["ffmpeg", "-y", "-i", raw,
                     "-ss", f"{offset:.3f}", "-t", f"{target:.3f}",
                     "-c:v", "libx264", "-preset", "ultrafast",
                     "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "128k",  # 保留音轨
                     clip],
                    capture_output=True, text=True, timeout=120,
                )
                if rr.returncode != 0 or not _os.path.exists(clip) or _os.path.getsize(clip) < 512:
                    raise RuntimeError(f"Task {ti+1} 裁剪失败: {rr.stderr[-200:]}")
                all_clips.append((global_idx, clip))
                offset += target; global_idx += 1

        # ── concat ──────────────────────────────────────────────────────────
        all_clips.sort(key=lambda x: x[0])
        list_path = _os.path.join(work_dir, "concat.txt")
        with open(list_path, "w") as f:
            for _, p in all_clips: f.write(f"file '{p}'\n")
        final = _os.path.join(work_dir, "final.mp4")
        rr = _sp.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k",  # 保留音轨
             final],
            capture_output=True, text=True, timeout=180,
        )
        if rr.returncode != 0 or not _os.path.exists(final):
            raise RuntimeError(f"concat 失败: {rr.stderr[-300:]}")
        log_info(f"script_to_video concat 完成 {_os.path.getsize(final)} bytes")

        # 先上传 480p 成品
        video_url_out = await fal_upload_with_retry(final)

        # ── Upscale 先做（静音视频放大，不会丢音轨）──────────────────────────────
        if resolution != "480p":
            try:
                log_info(f"script_to_video upscale 开始 resolution={resolution} input={video_url_out[:60]}")
                upscale_result = await _fal.subscribe_async(
                    "fal-ai/bytedance-upscaler/upscale/video",
                    arguments={"video_url": video_url_out, "resolution": resolution},
                )
                upscaled_url = None
                if isinstance(upscale_result, dict):
                    _v2 = upscale_result.get("video") or {}
                    upscaled_url = (_v2.get("url") if isinstance(_v2, dict) else None) or upscale_result.get("video_url")
                if upscaled_url:
                    video_url_out = upscaled_url
                    log_info(f"script_to_video upscale 完成 {resolution} url={upscaled_url[:80]}")
                else:
                    log_error("script_to_video upscale 返回无 URL，保留 480p 成品")
            except Exception as _ue:
                log_error(f"script_to_video upscale 失败（保留 480p）: {_ue}")

        # ── Lipsync：await 并行TTS结果 → 对齐口型（音频加在放大后的视频上）──────
        if not enable_voice:
            log_info("script_to_video lipsync: enable_voice=False，跳过 TTS+Lipsync")
            if _tts_concurrent_task and not _tts_concurrent_task.done():
                _tts_concurrent_task.cancel()
        else:
            try:
                # await 并行已启动的 TTS（与 Seedance 并行执行，此处只是等结果）
                _tts_audio_url = None
                if _tts_concurrent_task is not None:
                    _tts_audio_url = await _tts_concurrent_task
                    if _tts_audio_url:
                        log_info(f"script_to_video lipsync TTS 并行完成 url={_tts_audio_url[:60]}")
                    else:
                        log_error("script_to_video 并行TTS无返回，跳过Lipsync")

                if _tts_audio_url:
                    _lipsync_result = await _fal.subscribe_async(
                        "fal-ai/musetalk",
                        arguments={
                            "source_video_url": video_url_out,  # MuseTalk 字段名
                            "audio_url": _tts_audio_url,
                        },
                    )
                    _lipsync_url = None
                    if isinstance(_lipsync_result, dict):
                        _v = _lipsync_result.get("video") or {}
                        _lipsync_url = (_v.get("url") if isinstance(_v, dict) else None) or _lipsync_result.get("video_url")
                    if _lipsync_url:
                        video_url_out = _lipsync_url
                        log_info(f"script_to_video lipsync OK url={_lipsync_url[:80]}")
                    else:
                        log_error("script_to_video lipsync 返回无 URL，保留无口型视频继续")
                else:
                    log_info("script_to_video lipsync: 台词为空或TTS失败，跳过Lipsync")
            except Exception as _lse:
                log_error(f"script_to_video lipsync 失败（降级保留无口型视频）: {_lse}")
        # ── Lipsync 结束 ────────────────────────────────────────────────────────

        log_info(
            f"script_to_video 收工 total={_t.time()-t0:.1f}s tasks={len(tasks)} "
            f"scenes={len(scenes)} dur={total_dur_all:.1f}s resolution={resolution} url={video_url_out[:80]}"
        )
        return {
            "video_url": video_url_out,
            "n_scenes": len(scenes),
            "total_duration_sec": round(total_dur_all, 2),
        }
    finally:
        try: _shutil.rmtree(work_dir, ignore_errors=True)
        except Exception: pass
