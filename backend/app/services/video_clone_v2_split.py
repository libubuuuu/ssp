"""P221 视频复刻 V2 — 切片算法 + check_duration(智能切片提示)

详见 docs/P221-API-SCHEMA.md(v4)§6 + docs/P221-CHECK-DURATION-UI.md(智能切片设计)。

切片规则(2026-05-10 砍单档后):
- total < 4     → 拒绝(fal duration 最低 4 秒,< 4 段无法处理)
- [4, 8]        → 单段
- (8, 64],非 8 倍数 → 弹窗让用户选丢哪段(target = floor(total/8)*8)
- 8 / 16 / 24 / ... / 64 整数倍 → 完美分段
- > 64          → 拒绝
- 末段 < 4 秒并到前一段(最多 12 秒;fal 接受 4-15s 输入)

⚠ 历史:本模块原有 _allowed_tiers + allowed_tiers 字段,2026-05-10 砍 economy/standard 单档后删除。
       所有段统一单价,无需 tier 选项。
"""
from __future__ import annotations
import asyncio
import re
from typing import List, Dict, Any

from .video_clone_v2_pricing import (
    MAX_ULTIMATE_SECONDS,
    MAX_ULTIMATE_SEGMENTS,
)


def plan_segments_v2(total_sec: float) -> List[Dict[str, Any]]:
    """切片(不带 source_type — 那是前端用户选完才知道)。

    Returns:
        [{"idx", "start", "duration"}, ...]
    Raises:
        ValueError: total_sec < 4 / > 64 / 段数 > MAX_ULTIMATE_SEGMENTS
    """
    if total_sec < 4:
        raise ValueError("视频太短,最少 4 秒")
    # ── 单段化(新模型单次最长 15 秒,不再自动切段拼接)─────────────────────
    # >15s 直接拦下,引导用户自己分段截取后分别复刻;≤15s 整段一次复刻。
    # 下方多段切片逻辑保留(当前阈值下不会执行),日后要恢复多段把这里的上限调高即可。
    _SINGLE_PASS_MAX = 15.0
    if total_sec > _SINGLE_PASS_MAX:
        raise ValueError(
            "单次复刻最长 15 秒。视频较长时,请分段截取(每段 ≤15 秒)后分别复刻,"
            "再拼接为完整视频,以保证生成质量。"
        )
    return [{
        "idx": 0,
        "start": 0.0,
        "duration": float(total_sec),
    }]

    # ── 以下多段切片逻辑(dormant:>15s 已在上方拦截,不会触达)─────────────
    if total_sec > MAX_ULTIMATE_SECONDS:
        raise ValueError(
            f"视频太长,最多 {MAX_ULTIMATE_SECONDS} 秒(请截取 60 秒以内视频)"
        )

    segments: List[Dict[str, Any]] = []
    idx = 0
    cur = 0.0
    while cur < total_sec:
        seg_dur = min(8.0, total_sec - cur)
        segments.append({
            "idx": idx,
            "start": cur,
            "duration": seg_dur,
        })
        cur += seg_dur
        idx += 1

    # 末段 < 4 秒并到前一段(最多 12 秒;fal 接受 4-15s 输入)
    if len(segments) > 1 and segments[-1]["duration"] < 4.0:
        last = segments.pop()
        segments[-1]["duration"] += last["duration"]

    if len(segments) > MAX_ULTIMATE_SEGMENTS:
        raise ValueError(f"段数超限({len(segments)} > {MAX_ULTIMATE_SEGMENTS})")

    return segments


# ─── ⭐ 智能切片提示(check_duration)──────────────────────────────────

# 视为"完美 8 倍数"的容差(防浮点误差,如 16.001 / 15.999 都视为 16)
_TARGET_TOLERANCE = 0.05


def check_duration(duration_sec: float) -> Dict[str, Any]:
    """检查视频时长是否需要 trim。

    Seedance 2.0 支持每段 4-15 秒，plan_segments_v2 按 ≤8s 切段。
    末段 < 4s 时并入前段（合并后 ≤ 12s，在 fal 15s 限制内）。
    因此 4-64 秒内任何时长都能合法切段，极少需要 trim。

    需要 trim 的唯一情况：末段 < 4s 且无法合并（理论上不出现）。

    Returns:
        needs_trim=False: 直接切段即可,target_duration=duration_sec
        needs_trim=True:  末段不合法需丢弃,含 drop_seconds 供弹窗展示

    Raises:
        ValueError: < 4s / > 64s
    """
    if duration_sec < 4.0:
        raise ValueError(f"视频太短(<4 秒),最少 4 秒")
    # 单段化:单次复刻最长 15 秒。>15s 拦下引导用户分段;≤15s 整段一次复刻,无需 trim。
    if duration_sec > 15.0:
        raise ValueError(
            "单次复刻最长 15 秒。视频较长时,请分段截取(每段 ≤15 秒)后分别复刻,"
            "再拼接为完整视频,以保证生成质量。"
        )
    return {"needs_trim": False, "current_duration": round(duration_sec, 2),
            "target_duration": round(duration_sec, 2)}


# ─── ffmpeg 运动量计算(motion_score)───────────────────────────────────

async def calc_motion_score(video_path_or_url: str, start: float, duration: float) -> float:
    """算一段视频的运动量(YDIF 平均值,0-100)。

    支持本地路径 / HTTPS URL 直读(ffmpeg 4.4.2 自带 HTTPS 流支持)。
    实测 ailixiao.com 上 2s 视频 0.8s 完成,fal CDN 同协议适用。

    用 ffmpeg signalstats 算相邻帧亮度差(YDIF):
    - YDIF 大 = 帧间变化大 = 运动多
    - YDIF 小 = 帧间几乎不变 = 静止/运动少

    实测 0.4-30 是常见区间(婴儿安静镜头 ~0.5,快动作 >10)。

    实现细节:`-f null -` 会把 stdout 当输出文件,所以 metadata=print:file=-
    会被压在 stdout 里跟 null 输出混。改用临时文件接 metadata。
    """
    import os
    import tempfile
    fd, meta_path = tempfile.mkstemp(prefix="vc2_motion_", suffix=".txt")
    os.close(fd)
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-ss", str(start),
            "-i", video_path_or_url,
            "-t", str(duration),
            "-vf", f"signalstats,metadata=print:file={meta_path}",
            "-an", "-f", "null", "/dev/null",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"motion_score ffmpeg 失败:{stderr.decode(errors='replace')[-500:]}"
            )
        with open(meta_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        ydifs = [float(m) for m in re.findall(r"YDIF=([\d.]+)", content)]
        if not ydifs:
            return 0.0
        return round(sum(ydifs) / len(ydifs), 2)
    finally:
        try: os.unlink(meta_path)
        except OSError: pass


# ─── ⭐ 镜头切换检测(scene cut detection,用于多镜头视频识别)──────────

# 切换检测阈值(0-1,scene_score > 此值视为切换)
# 实测老板手机视频在 0.3 阈值下检测出 3 个切换(scene_score 0.487-0.646),
# 实证经验值;过低假阳性多(过场镜头被算成切换),过高漏检温和切换
SCENE_CUT_THRESHOLD: float = 0.3


async def detect_scene_cuts(video_path_or_url: str) -> List[float]:
    """检测视频内的镜头切换时间戳(秒)。

    用 ffmpeg select='gt(scene,N)' filter + metadata print,返回切换发生的 pts_time。
    实测老板 8s 4 镜头手机视频检测出 3 个切换:[3.70, 5.90, 7.13]。

    Returns:
        切换时间戳列表(秒),按时间升序;空 list = 单镜头视频
    """
    import os
    import tempfile
    fd, meta_path = tempfile.mkstemp(prefix="vc2_scene_", suffix=".txt")
    os.close(fd)
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i", video_path_or_url,
            "-filter_complex",
            f"select='gt(scene,{SCENE_CUT_THRESHOLD})',metadata=print:file={meta_path}",
            "-an", "-f", "null", "/dev/null",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"detect_scene_cuts ffmpeg 失败:{stderr.decode(errors='replace')[-500:]}"
            )
        with open(meta_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        # parse pts_time:3.7 / pts_time:5.9 等行
        cuts = [float(m) for m in re.findall(r"pts_time:([\d.]+)", content)]
        return sorted(cuts)
    finally:
        try: os.unlink(meta_path)
        except OSError: pass


async def detect_scene_count(video_path_or_url: str) -> int:
    """返回视频镜头数 = 切换数 + 1(用于多镜头判断 / 前端弹窗触发)。

    单镜头视频 = 1(无切换);老板视频 4 镜头 = 3 切换。
    """
    cuts = await detect_scene_cuts(video_path_or_url)
    return len(cuts) + 1


async def suggest_trim_candidates(
    video_path_or_url: str,
    duration_sec: float,
    target: float,
) -> List[Dict[str, Any]]:
    """对需要 trim 的视频生成 3 个候选丢弃位置 + 各自运动量评分。

    候选:
    - 末尾:[target, duration]
    - 开头:[0, drop]
    - 中间:[duration/2 - drop/2, duration/2 + drop/2]

    返回按 motion_score 升序(运动量最少的优先推荐)。
    """
    drop = duration_sec - target
    candidates: List[Dict[str, Any]] = []

    # 末尾
    candidates.append({
        "label": f"丢末尾 {drop:.1f} 秒",
        "position": "tail",
        "start": round(target, 2),
        "end": round(duration_sec, 2),
    })
    # 开头
    candidates.append({
        "label": f"丢开头 {drop:.1f} 秒",
        "position": "head",
        "start": 0.0,
        "end": round(drop, 2),
    })
    # 中间(只在 duration > drop*2 时才有意义,避免跟 head/tail 重合)
    mid_start = duration_sec / 2 - drop / 2
    mid_end = duration_sec / 2 + drop / 2
    if mid_start > drop and (duration_sec - mid_end) > drop:
        candidates.append({
            "label": f"丢中间 {drop:.1f} 秒",
            "position": "middle",
            "start": round(mid_start, 2),
            "end": round(mid_end, 2),
        })

    # 算 motion_score(并发 + 各自异常吞掉)
    async def _score(c):
        try:
            c["motion_score"] = await calc_motion_score(
                video_path_or_url, c["start"], c["end"] - c["start"]
            )
        except Exception:
            c["motion_score"] = -1.0   # 算不出 → 排最后
        return c

    candidates = await asyncio.gather(*[_score(c) for c in candidates])
    # 升序:motion 越小越推荐丢(影响小)
    candidates_list = sorted(
        candidates,
        key=lambda c: (c["motion_score"] < 0, c["motion_score"]),
    )
    if candidates_list:
        candidates_list[0]["recommended"] = True
    return candidates_list
