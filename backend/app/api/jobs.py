"""全局任务队列 API - 统一管理图片/视频生成，5 并发上限，JSON 持久化"""
import os
import json
import uuid
import time
import asyncio
import fcntl
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


class SubmitJobRequest(BaseModel):
    type: str
    params: Dict[str, Any]
    title: Optional[str] = None


async def _run_image_job(params: dict):
    service = get_image_service()
    if params.get("reference_images"):
        # 八十四续 P6:nano-banana-2/edit 是 Google 系列对内衣/塑身/紧身衣等
        # NSFW 拦截极严(实测豹纹比基尼直接拒,和 prompt 无关)。
        # 切字节 Seedream 4 edit:国产对带货类宽容,实测同图能成,且支持多图合成。
        import fal_client
        result = await fal_client.run_async(
            "fal-ai/bytedance/seedream/v4/edit",
            arguments={
                "prompt": params["prompt"],
                "image_urls": params["reference_images"],
                "image_size": "square_hd",
            }
        )
        images = result.get("images", [])
        if not images:
            raise Exception("no image generated")
        return {"image_url": images[0].get("url"), "type": "image"}
    else:
        result = await service.generate(
            params["prompt"], params.get("size", "1024x1024"), params.get("model", "nano-banana-2")
        )
        if "error" in result:
            raise Exception(result["error"])
        result["type"] = "image"
        return result


async def _run_video_job(params: dict, job_type: str):
    service = get_video_service()
    if job_type == "video_i2v":
        r = await service.generate_from_image(params["image_url"], params.get("prompt", ""), params.get("tail_image_url"))
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

    # P39 (2026-05-01):回 i2v 架构 + Flux Kontext 合首帧。
    # 痛点:ref2vid 改产品(用户实测产品被改) → 切 image edit SOTA 锁产品。
    # 路线:产品图 → Flux Kontext 17s 合"模特+产品+背景"首帧 → Seedance v1.5/pro
    # i2v 70s 出视频。5s 视频总用时 ~90 秒,产品保真度高,真人质感强。

    # 第 1 步:用产品图 + 第 1 段 visual 调 Flux Kontext 合共享 base 首帧
    first_scene = scenes[0] if scenes else {}
    base_result = await ad_video_models.compose_first_frame(
        product_image_url=params.get("product_image_url") or params.get("image_url"),
        background_image_url=params.get("background_image_url"),
        model_description=model_desc,
        scene_visual_prompt=first_scene.get("visual_prompt", ""),
        product_back_image_url=params.get("product_back_image_url"),
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
    if duration <= 12 or len(scenes) <= 1:
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

        # Step 2: P115 Kling 通道 — talking head 喂 reframed 图(若用 Kling)
        # 注意 Seedance 永远喂 base_image(同模特同产品同背景,保证身份一致)
        talking_endpoint = params.get("talking_head_endpoint", "fal-ai/bytedance/omnihuman")
        log_info(f"ad_video P104 talking_head endpoint={talking_endpoint}")
        talking_image_url = base_image_url
        if "kling" in talking_endpoint:
            try:
                log_info("ad_video P115 Kling 通道:GPT-Image 2 reframe → portrait")
                _kontext = await _fc.run_async(
                    "openai/gpt-image-2/edit",
                    arguments={
                        "prompt": (
                            # P117:不替换背景、不弱化产品。仅做"镜头视角调整 + 模特上半身居前"
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
                    # P128:reframe 失败用 base_image_url 不切端点(用户选啥跑啥)
                    log_warning("ad_video P115 reframe 无 image,继续用 base_image 跑用户选的 talking 端点")
                    talking_image_url = base_image_url
            except Exception as e:
                log_warning(f"ad_video P115 reframe 失败,继续用 base_image 跑用户选的 talking 端点: {str(e)[:200]}")
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

        # P129(2026-05-05):用户教的真正架构 —
        # GPT-Image 2 出 N 张分镜首帧 + i2v 模型(用户选 Seedance 2.0 / Kling v3 pro / v2.5-turbo pro)
        # 自带 generate_audio=true,模型自己生成"模特说话+lipsync+演示动作",一步到位。
        # 砍:elevenlabs TTS / talking head 端点 / 独立 audio concat / 双轨。
        if len(scenes) >= 2:
            from app.services.ad_video_models import compose_first_frame_for_scene

            # 解析 time_range 拿每段时长
            seg_durs = []
            for s in scenes:
                seg_durs.append(_parse_time_range(s.get("time_range"), fallback=duration / len(scenes)))
            total_seg = sum(seg_durs)
            if total_seg > duration:
                ratio = duration / total_seg
                seg_durs = [d * ratio for d in seg_durs]

            # 用户前端选的视频引擎(默认 Seedance 2.0 i2v)。
            # 兼容前端旧字段名 talking_head_endpoint(用户当前还在传)+ 新字段 video_model_endpoint
            user_video_endpoint = (
                params.get("video_model_endpoint")
                or params.get("talking_head_endpoint")
                or "bytedance/seedance-2.0/image-to-video"
            )
            log_info(f"ad_video P129 多镜头叙事 scenes={len(scenes)} duration={duration}s "
                     f"video_endpoint={user_video_endpoint}")

            # P129 阶段 A:并发 N 张分镜首帧(GPT-Image 2 - 共享 base_image 锁模特+产品)
            log_info(f"ad_video P129 阶段 A:并发 {len(scenes)} 张分镜首帧合成(GPT-Image 2)")
            frame_tasks = [
                compose_first_frame_for_scene(
                    base_image_url=base_image_url,
                    scene=scenes[i],
                    model_description=model_desc,
                    overall_setting=overall,
                )
                for i in range(len(scenes))
            ]
            frame_results = await asyncio.gather(*frame_tasks, return_exceptions=True)
            seg_frames = []
            for i, fr in enumerate(frame_results):
                if isinstance(fr, Exception) or (isinstance(fr, dict) and "error" in fr):
                    err = str(fr)[:120] if isinstance(fr, Exception) else fr.get("error", "?")
                    log_warning(f"ad_video P129 段{i+1} 分镜首帧失败,用 base_image 兜底: {err}")
                    seg_frames.append(base_image_url)
                else:
                    seg_frames.append(fr.get("image_url") or base_image_url)

            # P129 阶段 B:并发 N 段 i2v(用户选的端点,generate_audio=true,visual_prompt 含台词)
            log_info(f"ad_video P129 阶段 B:并发 {len(scenes)} 段 {user_video_endpoint}(generate_audio)")

            def _build_i2v_prompt_with_speech(scene: dict, idx: int) -> str:
                """合成 i2v prompt:视觉描述 + 模特要说的话(让 i2v 生成 lipsync audio)。"""
                visual = (scene.get("visual_prompt") or "").strip()
                speech = (scene.get("speech") or "").strip()
                # 段时长 → 字数限制(防 i2v 内置 TTS 说不完)
                _has_cn2 = bool(_re.search(r"[一-鿿]", speech))
                _max = int(seg_durs[idx - 1] * (5 if _has_cn2 else 14))
                if len(speech) > _max:
                    log_warning(f"ad_video P129 段{idx} speech 超长 {len(speech)} > {_max},截断")
                    speech = speech[:_max]
                parts = [visual] if visual else []
                if speech:
                    parts.append(
                        f"The model is speaking enthusiastically to the camera. "
                        f"She says: \"{speech}\". "
                        f"Her lips and mouth move naturally in sync with the words."
                    )
                return " ".join(parts) or "Model presenting product naturally to the camera."

            async def _run_i2v_for_seg(image_url: str, scene: dict, idx: int) -> str:
                """P129:跑用户选的 i2v 端点(失败直接 raise,不偷换端点)。"""
                ep = user_video_endpoint
                prompt = _build_i2v_prompt_with_speech(scene, idx)
                # i2v schema(seedance-2.0 / kling-video/v3-pro / v2.5-turbo-pro 共用)
                args = {
                    "image_url": image_url,
                    "prompt": prompt,
                    "duration": "4",  # 跑 4s,ffmpeg trim 到设计段长(seg_durs[i])
                    "resolution": "720p",
                    "aspect_ratio": aspect_ratio,
                    "generate_audio": True,  # 关键:模型自己生成 lipsync audio
                }
                log_info(f"ad_video P129 段{idx} i2v start prompt_len={len(prompt)}")
                res = await _fc.subscribe_async(ep, arguments=args)
                v = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else res.get("video_url")
                if not v:
                    raise Exception(f"段 {idx} {ep} 未返 video_url")
                log_info(f"ad_video P129 段{idx} i2v OK url={v[:80]}")
                return v

            i2v_results = await asyncio.gather(
                *[
                    _run_i2v_for_seg(seg_frames[i], scenes[i], i + 1)
                    for i in range(len(scenes))
                ],
                return_exceptions=True,
            )
            seg_video_urls = []
            seg_video_durs = []
            for i, vr in enumerate(i2v_results):
                if isinstance(vr, Exception):
                    log_warning(f"ad_video P129 段{i+1} i2v 失败,该段跳过: {str(vr)[:200]}")
                    continue
                seg_video_urls.append(vr)
                seg_video_durs.append(seg_durs[i])
            if not seg_video_urls:
                raise Exception(f"P129 全部 i2v({user_video_endpoint})失败,无视频可拼接")

            # P129 阶段 C:ffmpeg xfade 拼接(各段视频自带 audio,无独立 audio concat)
            try:
                user_id = params.get("_user_id", "anon")
                final_url = await _p125_concat_omnihuman(
                    seg_video_urls=seg_video_urls,
                    seg_durs=seg_video_durs,
                    user_id=user_id,
                    aspect_ratio=aspect_ratio,
                )
                return {"video_url": final_url, "type": "video"}
            except Exception as e:
                log_warning(f"ad_video P129 ffmpeg 拼接失败,降级返第 1 段: {str(e)[:200]}")
                return {"video_url": seg_video_urls[0], "type": "video"}

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
                fr = await ad_video_models.compose_first_frame_for_scene(
                    base_image_url=base_image_url,
                    scene=scene,
                    model_description=model_desc,
                    overall_setting=overall,
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
        try:
            t = job["type"]
            if t == "image":
                result = await _run_image_job(job["params"])
            elif t.startswith("video_"):
                result = await _run_video_job(job["params"], t)
            elif t == "ad_video":
                # P118: 把 user_id 透传给 _run_ad_video_job(用于 ffmpeg 拼接产物落 uploads)
                job["params"]["_user_id"] = job.get("user_id") or job.get("user_numeric_id") or "anon"
                result = await _run_ad_video_job(job["params"])
            else:
                raise Exception(f"unknown type: {t}")

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
                    add_credits(uid, job.get("cost", 0))
            except:
                pass
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
    asyncio.create_task(_execute_job(job_id))
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
    """七十五续:My Tasks 列表合并 long-video sessions(虚拟 job 视图)"""
    user_id = str(current_user.get("id") or current_user.get("email", "unknown"))
    mine = [j for j in JOBS.values() if j.get("user_id") == user_id]
    # 追加 long-video 虚拟 jobs
    mine.extend(_studio_sessions_as_virtual_jobs(user_id))
    mine.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return {"jobs": mine[:50]}


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
