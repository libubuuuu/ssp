"""P221 视频复刻 V2 — 切片算法 + allowed_tiers + check_duration(智能切片提示)

详见 docs/P221-API-SCHEMA.md(v4)§6 + docs/P221-CHECK-DURATION-UI.md(智能切片设计)。

切片规则:
- total < 2     → 拒绝
- [2, 4)        → 单段,仅 economy
- [4, 8]        → 单段,两档可选
- (8, 64],非 8 倍数 → 弹窗让用户选丢哪段(target = floor(total/8)*8)
- 8 / 16 / 24 / ... / 64 整数倍 → 完美分段
- > 64          → 拒绝

allowed_tiers:
- < 2.0s:[]
- [2.0, 4.0):["economy"]
- ≥ 4.0s:    ["economy", "standard"]
"""
from __future__ import annotations
import asyncio
import re
from typing import List, Dict, Any

from .video_clone_v2_pricing import (
    MAX_ULTIMATE_SECONDS,
    MAX_ULTIMATE_SEGMENTS,
)


def _allowed_tiers(seg_duration: float) -> List[str]:
    """根据段长决定可选 tier 列表(全局两档,详见 §5.5)。"""
    if seg_duration < 2.0:
        return []
    if seg_duration < 4.0:
        return ["economy"]
    return ["economy", "standard"]


def plan_segments_v2(total_sec: float) -> List[Dict[str, Any]]:
    """切片(不带 source_type / tier — 那是前端用户选完才知道)。

    Returns:
        [{"idx", "start", "duration", "allowed_tiers"}, ...]
    Raises:
        ValueError: total_sec < 4 / > 64 / 段数 > MAX_ULTIMATE_SEGMENTS
    """
    if total_sec < 4:
        raise ValueError("视频太短,最少 4 秒")
    if total_sec > MAX_ULTIMATE_SECONDS:
        raise ValueError(
            f"视频太长,最多 {MAX_ULTIMATE_SECONDS} 秒(请截取 60 秒以内视频)"
        )

    if total_sec <= 8:
        return [{
            "idx": 0,
            "start": 0.0,
            "duration": float(total_sec),
            "allowed_tiers": _allowed_tiers(total_sec),
        }]

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

    # 末段 < 4 秒并到前一段(最多 12 秒;fal 接受 ~15s 输入)
    if len(segments) > 1 and segments[-1]["duration"] < 4.0:
        last = segments.pop()
        segments[-1]["duration"] += last["duration"]

    if len(segments) > MAX_ULTIMATE_SEGMENTS:
        raise ValueError(f"段数超限({len(segments)} > {MAX_ULTIMATE_SEGMENTS})")

    for seg in segments:
        seg["allowed_tiers"] = _allowed_tiers(seg["duration"])

    return segments


# ─── ⭐ 智能切片提示(check_duration)──────────────────────────────────

# 视为"完美 8 倍数"的容差(防浮点误差,如 16.001 / 15.999 都视为 16)
_TARGET_TOLERANCE = 0.05


def check_duration(duration_sec: float) -> Dict[str, Any]:
    """检查视频时长是否需要 trim 到 8 整倍数。

    Returns:
        needs_trim=False:无需 trim(单段或完美 8 倍数);只含 current/target
        needs_trim=True:需要 trim;含 current/target/drop_seconds(供前端弹窗展示候选)

    Raises:
        ValueError: < 2 / > 64
    """
    if duration_sec < 2.0:
        raise ValueError(f"视频太短(<2 秒),最少 2 秒")
    if duration_sec > MAX_ULTIMATE_SECONDS:
        raise ValueError(f"视频太长,最多 {MAX_ULTIMATE_SECONDS} 秒")

    # 单段路径:duration ≤ 8 不需要 trim(直接当一整段处理)
    if duration_sec <= 8.0 + _TARGET_TOLERANCE:
        return {
            "needs_trim": False,
            "current_duration": round(duration_sec, 2),
            "target_duration": round(min(duration_sec, 8.0), 2),
        }

    # 多段路径:target 是 ≤ duration 的最大 8 整倍数
    target = int(duration_sec // 8) * 8
    if duration_sec - target < _TARGET_TOLERANCE:
        # 完美 8 倍数(容差内)
        return {
            "needs_trim": False,
            "current_duration": round(duration_sec, 2),
            "target_duration": float(target),
        }

    drop = duration_sec - target
    return {
        "needs_trim": True,
        "current_duration": round(duration_sec, 2),
        "target_duration": float(target),
        "drop_seconds": round(drop, 2),
    }


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
