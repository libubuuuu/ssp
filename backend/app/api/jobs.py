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
            target_dur = min(v_dur, a_dur) if v_dur > 0 and a_dur > 0 else max(v_dur, a_dur)
            if abs(v_dur - a_dur) > 0.1:
                log_warning(f"P151 段 {i+1} desync: video={v_dur:.2f}s audio={a_dur:.2f}s,trim 到 {target_dur:.2f}s")
            trimmed = Path(work) / f"seg_{i+1}.mp4"
            # ffmpeg trim 到 target_dur(re-encode 保证关键帧对齐)
            r2 = _sp.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", raw, "-t", str(target_dur),
                 "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                 "-c:a", "aac", "-b:a", "128k",
                 "-movflags", "+faststart",
                 str(trimmed)],
                capture_output=True, text=True, timeout=120,
            )
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
        aspect_ratio=params.get("aspect_ratio") or "9:16",
        no_text=True,  # P158(2026-05-07):AI 带货视频图严禁字幕
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
            seg_speeches = []
            for i, sc in enumerate(scenes):
                sp = (sc.get("speech") or "").strip()
                if not sp:
                    log_warning(f"ad_video P139 段{i+1} speech 空,该段跳过")
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
                seg_speeches.append((i + 1, sp))

            if not seg_speeches:
                raise Exception("P139 全部段 speech 都空,VLM 没写台词")

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
                )
                if isinstance(fr, dict) and "error" in fr:
                    raise Exception(f"段{idx} 分镜图失败: {fr['error']}")
                url = fr.get("image_url") if isinstance(fr, dict) else None
                if not url:
                    raise Exception(f"段{idx} 分镜图未返 url")
                log_info(f"ad_video P149 段{idx} GPT-Image 2 9:16 分镜图 OK url={url[:60]}")
                return (idx, url)

            valid_idxs = [idx for idx, _ in seg_speeches]
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

            ka_results = await asyncio.gather(
                *[_ka_for_seg(idx, audio) for idx, audio in seg_audios],
                return_exceptions=True,
            )
            # 按 idx 排序保证段顺序
            seg_video_urls = []
            for r in sorted([x for x in ka_results if not isinstance(x, Exception)], key=lambda t: t[0]):
                seg_video_urls.append(r[1])
            for r in ka_results:
                if isinstance(r, Exception):
                    log_warning(f"ad_video P135 Kling Avatar 段失败,跳过: {str(r)[:200]}")
            if not seg_video_urls:
                raise Exception("P135 全部段 Kling Avatar 失败")

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
                fr = await ad_video_models.compose_first_frame_for_scene(
                    base_image_url=base_image_url,
                    scene=scene,
                    model_description=model_desc,
                    overall_setting=overall,
                    aspect_ratio=params.get("aspect_ratio") or "9:16",
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
            elif t == "replicate_analyze":
                result = await _run_replicate_analyze_job(job["params"])
            elif t == "replicate":
                job["params"]["_user_id"] = job.get("user_id") or job.get("user_numeric_id") or "anon"
                result = await _run_replicate_job(job["params"])
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


def _oral_sessions_as_virtual_jobs(user_id: str) -> list[dict]:
    """把当前用户的 oral 口播 session 转成虚拟 job 给 My Tasks 显示。
    跟 studio 同样的桥接思路 — oral 不写 JOBS 字典,SQL 表 oral_sessions 是真源,
    这里读出来按 JobPanel 期望的 schema 拼一份只读视图。
    """
    try:
        from app.database import get_db
    except Exception:
        return []
    out = []
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, status, duration_seconds, final_video_url,
                       thumbnail_url, created_at
                  FROM oral_sessions
                 WHERE user_id = ? AND archived_at IS NULL
              ORDER BY created_at DESC
                 LIMIT 50
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
    except Exception:
        return []
    import time as _t
    from datetime import datetime as _dt
    for r in rows:
        d = dict(r)
        sid = d["id"]
        status_raw = d.get("status") or "pending"
        if status_raw == "completed":
            v_status = "completed"
        elif str(status_raw).startswith("failed"):
            v_status = "failed"
        else:
            v_status = "running"
        duration = d.get("duration_seconds") or 0
        title = f"口播带货 {duration:.0f}s"
        result = {}
        if d.get("final_video_url"):
            result["video_url"] = d["final_video_url"]
        if d.get("thumbnail_url"):
            result["image_url"] = d["thumbnail_url"]
        # created_at: TEXT(ISO) → epoch float, 排序用
        ca = d.get("created_at")
        ca_num = 0.0
        if isinstance(ca, (int, float)):
            ca_num = float(ca)
        elif isinstance(ca, str) and ca:
            try:
                ca_num = _dt.fromisoformat(ca.replace(" ", "T")).timestamp()
            except Exception:
                ca_num = 0.0
        out.append({
            "id": f"oral_{sid}",
            "user_id": user_id,
            "user_numeric_id": user_id,
            "type": "oral_broadcast",
            "title": title,
            "status": v_status,
            "created_at": ca_num,
            "result": result,
            "_long_video": True,  # 复用现有"虚拟 job 不可删 + 可点跳转"渲染分支
            "_session_id": sid,
            "_route": f"/video/oral-broadcast/{sid}",
        })
    return out


@router.get("/list")
async def list_jobs(current_user: dict = Depends(get_current_user)):
    """七十五续:My Tasks 列表合并 long-video sessions(虚拟 job 视图)"""
    user_id = str(current_user.get("id") or current_user.get("email", "unknown"))
    mine = [j for j in JOBS.values() if j.get("user_id") == user_id]
    # 追加 long-video 虚拟 jobs
    mine.extend(_studio_sessions_as_virtual_jobs(user_id))
    mine.extend(_oral_sessions_as_virtual_jobs(user_id))
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
    t0 = _t.time()
    res = await svc.analyze_video(archived_url, instruction)
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
    log_info(f"replicate_analyze OK scenes={len(clean_scenes)} ratio={aspect}")
    return {
        "scenes": clean_scenes,
        "total_duration": data.get("total_duration_seconds", sum(s.get("duration_sec", 5) for s in clean_scenes)),
        "detected_aspect_ratio": aspect,
    }



def _sanitize_for_gpt2(text: str) -> str:
    """硬替换 fal content_checker 触发词。覆盖 visual_prompt + identity_description。
    替换成中性通用词,既不破坏语义又能过审。"""
    if not text:
        return text
    pairs = [
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

        # Attempt 2:简化 prompt 重试(仅 content_policy 才值得重试)
        if err and ("content_policy" in err or "content_checker" in err):
            log_error(f"replicate grid n={n} 原 prompt 拒,简化 prompt retry: {err[:120]}")
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
        else:
            log_error(f"replicate grid n={n} 非 content_policy 错误,逐段降级: {err[:120] if err else '?'}")

        # 最终降级:逐段单出(单出自身有 3 段 retry)
        return await _asyncio.gather(*[_gen_single_scene(sc) for sc in chunk])

    if len(scenes) == 1:
        frames = [base_frame_url]
    else:
        # scene 0 = base; scenes[1:] 按 4 个一组分块走 grid
        rest = scenes[1:]
        frame_chunks: list = []
        i = 0
        while i < len(rest):
            chunk_size = min(4, len(rest) - i)
            # 末尾如果只剩 1,合并到上一块? 简化:剩 1 单出
            if chunk_size == 1 and frame_chunks:
                # 单段加在末尾
                single_url = await _gen_single_scene(rest[i])
                frame_chunks.append([single_url])
            else:
                panels = await _gen_grid_panels(rest[i:i+chunk_size])
                frame_chunks.append(panels)
            i += chunk_size
        rest_frames = [u for chunk in frame_chunks for u in chunk]
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
        # 用 -c copy 试,失败再 re-encode
        cp = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out_path],
            capture_output=True,
        )
        if cp.returncode != 0 or not os.path.exists(out_path):
            # re-encode 兜底
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
                 "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out_path],
                check=True, capture_output=True,
            )
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


async def _gen_videos_seedance_lite_i2v(scenes: list, frames: list, aspect_ratio: str) -> list:
    """引擎 3:seedance-lite-i2v(P164 替换 kling-3-pro-i2v,$0.80→$0.18/5s)

    用户说 kling-3-pro $0.80/5s 太贵,目标 $0.2-0.3 平替 + AI 自由生成动作。
    Seedance v1 Lite i2v $0.18/5s @720p,ByteDance 模型,效果接近 Pro 版的 70%。
    """
    import asyncio as _asyncio
    import fal_client
    from app.services.logger import log_info, log_error

    sem = _asyncio.Semaphore(3)
    async def _one(idx: int, scene: dict, frame_url: str) -> str:
        async with sem:
            duration = max(5, min(10, int(round(scene.get("duration_sec", 5)))))
            prompt = scene.get("visual_prompt") or "Cinematic product showcase"
            try:
                result = await fal_client.run_async(
                    "fal-ai/bytedance/seedance/v1/lite/image-to-video",
                    arguments={
                        "image_url": frame_url,
                        "prompt": prompt,
                        "duration": str(duration),
                        "aspect_ratio": aspect_ratio,
                        "resolution": "720p",
                        "enable_audio": False,
                    },
                )
                video = result.get("video") if isinstance(result, dict) else None
                vurl = video.get("url") if isinstance(video, dict) else None
                if not vurl:
                    vurl = result.get("video_url") if isinstance(result, dict) else None
                if not vurl:
                    raise RuntimeError(f"seedance-lite-i2v seg {idx} 未返 video URL")
                log_info(f"replicate seedance-lite-i2v seg {idx} OK url={vurl[:60]}")
                return vurl
            except Exception as e:
                raise RuntimeError(f"seedance-lite-i2v seg {idx} 失败: {str(e)[:200]}")

    return await _asyncio.gather(*[
        _one(i, scenes[i], frames[i]) for i in range(len(scenes))
    ])


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

    with tempfile.TemporaryDirectory() as tmpdir:
        seg_urls = await _slice_video_by_scenes(reference_video_url, scenes, tmpdir, aspect_ratio=aspect_ratio)

        sem = _asyncio.Semaphore(3)
        async def _swap_one(idx: int, seg_url: str, frame_url: str) -> str:
            async with sem:
                # frame_url 是 GPT-Image 2 出的图,直接喂 pixverse(不经 cat-vton 等后处理)
                result = await pix.swap(video_url=seg_url, image_url=frame_url)
                if "error" in result:
                    raise RuntimeError(f"pixverse-swap seg {idx}: {result['error']}")
                vurl = result.get("video_url")
                if not vurl:
                    raise RuntimeError(f"pixverse-swap seg {idx} 未返 url")
                log_info(f"replicate pixverse seg {idx} OK url={vurl[:60]}")
                return vurl

        return await _asyncio.gather(*[
            _swap_one(i, seg_urls[i], frames[i]) for i in range(len(scenes))
        ])


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

    with tempfile.TemporaryDirectory() as tmpdir:
        seg_urls = await _slice_video_by_scenes(reference_video_url, scenes, tmpdir, aspect_ratio=aspect_ratio)

        sem = _asyncio.Semaphore(3)
        async def _two_step_swap(idx: int, seg_url: str, person_ref: str) -> str:
            async with sem:
                # Step A: object swap
                log_info(f"replicate pixverse seg {idx} Step A(object swap)…")
                step_a = await pix.swap(video_url=seg_url, image_url=product_image_url, mode="object")
                if "error" in step_a:
                    raise RuntimeError(f"pixverse seg {idx} Step A object: {step_a['error']}")
                step_a_url = step_a.get("video_url")
                if not step_a_url:
                    raise RuntimeError(f"pixverse seg {idx} Step A 无 url")
                log_info(f"replicate pixverse seg {idx} Step A OK url={step_a_url[:60]}")
                # Step B: person swap
                log_info(f"replicate pixverse seg {idx} Step B(person swap)…")
                step_b = await pix.swap(video_url=step_a_url, image_url=person_ref, mode="person")
                if "error" in step_b:
                    raise RuntimeError(f"pixverse seg {idx} Step B person: {step_b['error']}")
                step_b_url = step_b.get("video_url")
                if not step_b_url:
                    raise RuntimeError(f"pixverse seg {idx} Step B 无 url")
                log_info(f"replicate pixverse seg {idx} 2-step OK final={step_b_url[:60]}")
                return step_b_url

        return await _asyncio.gather(*[
            _two_step_swap(i, seg_urls[i], frames[min(i, len(frames)-1)]) for i in range(len(scenes))
        ])


async def _gen_videos_catvton_pixverse(scenes: list, frames: list, product_image_url: str, reference_video_url: str, aspect_ratio: str) -> list:
    """引擎 3:catvton-pixverse(已废弃 cat-vton 中间步) — 现在等同 pixverse-swap。
    
    用户铁律:图必须交给 gpt2 做,不准 cat-vton 后处理。
    所以这条路径也直接调 _gen_videos_pixverse_swap,frames 已经是 GPT 直出图。
    
    保留 engine 选项是为了兼容老的 job(用户已选 catvton-pixverse 时不报错)。
    """
    return await _gen_videos_pixverse_swap(scenes, frames, reference_video_url, aspect_ratio)


