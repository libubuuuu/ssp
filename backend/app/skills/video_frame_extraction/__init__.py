"""video_frame_extraction skill:视频拆帧 → 关键帧 → 九宫格 → AI 描述。

公共入口:
    from app.skills.video_frame_extraction import VideoFrameSkill
    skill = VideoFrameSkill()
    result = skill.process(video_path, grid_size=9)

具体见 SKILL.md
"""
from .scene_detector import detect_scenes, Scene
from .keyframe_extractor import extract_keyframes
from .grid_composer import compose_grid
from .exceptions import (
    VideoFrameError,
    SceneDetectionError,
    KeyframeExtractionError,
    GridCompositionError,
)


class VideoFrameSkill:
    """video_frame_extraction skill 公共入口。

    封装 scene_detector + keyframe_extractor + grid_composer 三个核心模块,
    提供组合调用 process() 和分步调用接口。
    """

    def detect_scenes(self, video_path: str, threshold: float = 27.0):
        return detect_scenes(video_path, threshold=threshold)

    def extract_keyframes(self, video_path: str, scenes, output_dir: str = "/tmp/v3_frames"):
        return extract_keyframes(video_path, scenes, output_dir=output_dir)

    def compose_grid(self, frame_paths, layout=(3, 3), output_path: str = None):
        return compose_grid(frame_paths, layout=layout, output_path=output_path)

    def analyze_with_ai(self, frame_paths):
        # 2026-05-12:stub — 等 DeepSeek-VL / nano-banana 接入后实装,见 SKILL.md "未来扩展"
        raise NotImplementedError("AI 视觉分析尚未接入,见 SKILL.md")

    def transcribe_speech(self, video_path: str):
        # 2026-05-12:stub — 等 Whisper 接入后实装,见 SKILL.md "未来扩展"
        raise NotImplementedError("语音转文字尚未接入,见 SKILL.md")

    def process(
        self,
        video_path: str,
        grid_size: int = 9,
        include_speech: bool = False,
        use_ai_keyframe_selection: bool = False,
        output_dir: str = "/tmp/v3_frames",
    ) -> dict:
        """端到端流程:scene 检测 → 关键帧抽取 → 多九宫格拼合(每 9 帧一张)。

        长视频自动分页:scenes > 9 时自动出多张九宫格(每张 3x3)。
        最后一张如果不满 9 格,compose_grid 会自动用最后一帧 padding 填满。

        AI 选帧 / 语音转文字 当前 stub,留参数兼容未来扩展。
        grid_size 参数保留向后兼容,实际固定走 (3, 3) 多张分页。

        Returns:
          scenes:        全部 N 个 scene dict
          keyframe_paths: 全部 N 张关键帧路径
          grid_paths:    M 张九宫格路径,M = ceil(N / 9)
          n_frames:      N
          n_grids:       M
          layout:        (3, 3) 固定
        """
        import os, subprocess

        # 1. 获取视频时长
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=15,
        )
        try:
            duration = float(r.stdout.strip())
        except Exception:
            duration = 0.0
        if duration <= 0:
            return {"scenes": [], "keyframe_paths": [], "grid_paths": [],
                    "n_frames": 0, "n_grids": 0, "layout": (3, 3)}

        # 2. PySceneDetect 场景切换检测，降级为整段单场景
        from app.skills.video_frame_extraction.scene_detector import detect_scenes
        try:
            raw_scenes = detect_scenes(video_path)
        except Exception:
            raw_scenes = [Scene(idx=0, start_seconds=0.0, end_seconds=duration)]

        # 3. 计算目标帧数：每15s 1张九宫格，最多4张
        n_grids_target = max(1, min(4, round(duration / 15)))
        n_target = n_grids_target * 9

        # 4. 按场景时长比例分配帧数，确保每个场景至少1帧
        #    scene 太多时取最长的 n_target 个；scene 太少时按比例给长场景补帧
        if len(raw_scenes) >= n_target:
            # 场景过多：按时长降序保留 n_target 个，再按时间重排
            kept = sorted(raw_scenes, key=lambda s: s.duration_seconds, reverse=True)[:n_target]
            kept.sort(key=lambda s: s.start_seconds)
            allocation = {s.idx: 1 for s in kept}
            selected_scenes = kept
        else:
            # 场景不足：按时长比例将剩余帧分给各场景
            selected_scenes = raw_scenes
            allocation = {s.idx: 1 for s in selected_scenes}
            remaining = n_target - len(selected_scenes)
            while remaining > 0:
                # 每轮把1帧分给"时长/已分配帧数"最大的场景（贪心）
                best = max(selected_scenes,
                           key=lambda s: s.duration_seconds / allocation[s.idx])
                allocation[best.idx] += 1
                remaining -= 1

        # 5. 根据分配展开时间戳列表（每场景内均匀分布）
        timestamps: list[tuple[float, Scene]] = []
        for s in selected_scenes:
            n = allocation[s.idx]
            for k in range(n):
                t = s.start_seconds + s.duration_seconds * (k + 0.5) / n
                timestamps.append((t, s))
        timestamps.sort(key=lambda x: x[0])

        # 6. ffmpeg 按时间戳逐帧抽取（精确 seek，比 1fps 全量快）
        frames_dir = os.path.join(output_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        frame_paths: list[str] = []
        out_scenes: list[Scene] = []
        for i, (ts, s) in enumerate(timestamps):
            out_path = os.path.join(frames_dir, f"f{i:04d}.jpg")
            res = subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{ts:.3f}", "-i", video_path,
                 "-vframes", "1", "-q:v", "2", out_path],
                capture_output=True, timeout=30,
            )
            if res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                frame_paths.append(out_path)
                out_scenes.append(Scene(idx=i, start_seconds=s.start_seconds,
                                        end_seconds=s.end_seconds))

        if not frame_paths:
            return {"scenes": [], "keyframe_paths": [], "grid_paths": [],
                    "n_frames": 0, "n_grids": 0, "layout": (3, 3)}

        # 7. 多九宫格分页：每9帧一张
        layout = (3, 3)
        per_grid = 9
        grid_paths = []
        for chunk_idx, start in enumerate(range(0, len(frame_paths), per_grid)):
            chunk = frame_paths[start:start + per_grid]
            out_path = os.path.join(output_dir, f"grid_{chunk_idx:02d}.png")
            self.compose_grid(chunk, layout=layout, output_path=out_path)
            grid_paths.append(out_path)

        return {
            "scenes": [s.to_dict() for s in out_scenes],
            "keyframe_paths": frame_paths,
            "grid_paths": grid_paths,
            "n_frames": len(frame_paths),
            "n_grids": len(grid_paths),
            "layout": layout,
        }


def _layout_for_grid_size(grid_size: int):
    if grid_size <= 2:
        return (1, 2)
    if grid_size <= 4:
        return (2, 2)
    if grid_size <= 6:
        return (2, 3)
    return (3, 3)


__all__ = [
    "VideoFrameSkill",
    "Scene",
    "detect_scenes",
    "extract_keyframes",
    "compose_grid",
    "VideoFrameError",
    "SceneDetectionError",
    "KeyframeExtractionError",
    "GridCompositionError",
]
