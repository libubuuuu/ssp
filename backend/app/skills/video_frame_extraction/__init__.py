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
        from PIL import Image
        import numpy as np

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

        # 2. ffmpeg 一次 pass 抽缩略图(1fps 64x64),比 PySceneDetect 快 5-10x
        thumb_dir = os.path.join(output_dir, "thumbs")
        os.makedirs(thumb_dir, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-vf", "fps=1,scale=64:64", "-q:v", "5",
             os.path.join(thumb_dir, "t%04d.jpg")],
            capture_output=True, timeout=60,
        )
        thumbs = sorted(
            os.path.join(thumb_dir, f) for f in os.listdir(thumb_dir) if f.endswith(".jpg")
        )
        if not thumbs:
            return {"scenes": [], "keyframe_paths": [], "grid_paths": [],
                    "n_frames": 0, "n_grids": 0, "layout": (3, 3)}

        n_total = len(thumbs)
        # 每张缩略图对应视频中 (i+0.5)/n_total * duration 时刻
        sample_times = [duration * (i + 0.5) / n_total for i in range(n_total)]

        # 3. 颜色直方图差异最大化选帧(贪心)
        n_grids_target = max(1, min(6, round(duration / 10)))
        n_pick = min(n_grids_target * 9, n_total)

        def _hist(p: str) -> np.ndarray:
            img = Image.open(p).convert("RGB")
            arr = np.array(img).reshape(-1, 3)
            return np.concatenate([
                np.histogram(arr[:, c], bins=16, range=(0, 256))[0]
                for c in range(3)
            ]).astype(float)

        hists = [_hist(p) for p in thumbs]
        picked = [0]
        while len(picked) < n_pick:
            best_i, best_d = -1, -1.0
            for i in range(n_total):
                if i in picked:
                    continue
                d = min(float(np.linalg.norm(hists[i] - hists[j])) for j in picked)
                if d > best_d:
                    best_d, best_i = d, i
            if best_i == -1:
                break
            picked.append(best_i)
        picked.sort()

        # 4. 用全分辨率重新抽选中的帧
        frame_paths: list[str] = []
        scenes: list[Scene] = []
        seg = duration / max(n_pick, 1)
        for rank, si in enumerate(picked):
            ts = sample_times[si]
            out_path = os.path.join(output_dir, f"frame_{rank:03d}.jpg")
            ret = subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{ts:.3f}", "-i", video_path,
                 "-vframes", "1", "-q:v", "2", out_path],
                capture_output=True, timeout=30,
            )
            if ret.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                frame_paths.append(out_path)
                scenes.append(Scene(idx=rank, start_seconds=ts,
                                    end_seconds=min(ts + seg, duration)))

        # 多九宫格分页:每 9 帧一张
        layout = (3, 3)
        per_grid = layout[0] * layout[1]  # 9
        grid_paths = []
        for chunk_idx, start in enumerate(range(0, len(frame_paths), per_grid)):
            chunk = frame_paths[start:start + per_grid]
            # 用具名 PNG,而不是 mkstemp 随机名 — 便于调试 + 跟 chunk idx 对齐
            out_path = os.path.join(output_dir, f"grid_{chunk_idx:02d}.png")
            self.compose_grid(chunk, layout=layout, output_path=out_path)
            grid_paths.append(out_path)

        return {
            "scenes": [s.to_dict() for s in scenes],
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
