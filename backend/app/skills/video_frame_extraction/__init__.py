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
        # 第一轮:标准阈值检测
        scenes = self.detect_scenes(video_path, threshold=27.0)
        # 第二轮:镜头变化少时降低阈值,捕捉景别/机位细微变化
        if len(scenes) < 9:
            scenes2 = self.detect_scenes(video_path, threshold=15.0)
            if len(scenes2) > len(scenes):
                scenes = scenes2
        # 第三轮:还是不够,再降
        if len(scenes) < 5:
            scenes3 = self.detect_scenes(video_path, threshold=8.0)
            if len(scenes3) > len(scenes):
                scenes = scenes3

        if not scenes:
            return {"scenes": [], "keyframe_paths": [], "grid_paths": [], "n_frames": 0, "n_grids": 0, "layout": (3, 3)}

        frame_paths = self.extract_keyframes(video_path, scenes, output_dir=output_dir)

        # 兜底:三轮降阈值后还不足 5 帧,用画面差异最大化选帧
        # 每秒抽 1 帧,用颜色直方图差值贪心选出 9 张视觉差异最大的帧
        if len(frame_paths) < 5:
            try:
                r = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                    capture_output=True, text=True, timeout=15,
                )
                duration = float(r.stdout.strip()) if r.returncode == 0 else 0
            except Exception:
                duration = 0
            if duration > 0:
                # 每秒抽 1 帧
                n_sample = max(18, int(duration))
                sample_frames = []
                sample_times = []
                for i in range(n_sample):
                    ts = duration * i / n_sample
                    out_path = os.path.join(output_dir, f"sample_{i:03d}.jpg")
                    ret = subprocess.run(
                        ["ffmpeg", "-y", "-ss", f"{ts:.3f}", "-i", video_path,
                         "-vframes", "1", "-q:v", "3", "-vf", "scale=64:64", out_path],
                        capture_output=True, timeout=15,
                    )
                    if ret.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 50:
                        sample_frames.append(out_path)
                        sample_times.append(ts)

                if len(sample_frames) >= 5:
                    # 计算各帧直方图
                    try:
                        from PIL import Image
                        import numpy as np

                        def hist(p):
                            img = Image.open(p).convert("RGB")
                            arr = np.array(img).reshape(-1, 3)
                            h = []
                            for c in range(3):
                                h.extend(np.histogram(arr[:, c], bins=16, range=(0, 256))[0])
                            return np.array(h, dtype=float)

                        hists = [hist(p) for p in sample_frames]
                        # 贪心:每次选与已选集合差异最大的帧
                        n_pick = min(9, len(sample_frames))
                        picked = [0]  # 从第一帧开始
                        while len(picked) < n_pick:
                            best_i, best_d = -1, -1.0
                            for i in range(len(sample_frames)):
                                if i in picked:
                                    continue
                                d = min(np.linalg.norm(hists[i] - hists[j]) for j in picked)
                                if d > best_d:
                                    best_d, best_i = d, i
                            if best_i == -1:
                                break
                            picked.append(best_i)
                        picked.sort()

                        # 用真实分辨率重新抽这 9 帧
                        diverse_frames = []
                        diverse_scenes = []
                        for rank, si in enumerate(picked):
                            ts = sample_times[si]
                            out_path = os.path.join(output_dir, f"diverse_{rank:02d}.jpg")
                            ret = subprocess.run(
                                ["ffmpeg", "-y", "-ss", f"{ts:.3f}", "-i", video_path,
                                 "-vframes", "1", "-q:v", "2", out_path],
                                capture_output=True, timeout=30,
                            )
                            if ret.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                                diverse_frames.append(out_path)
                                diverse_scenes.append(Scene(idx=rank, start_seconds=ts,
                                                            end_seconds=min(ts + duration / n_pick, duration)))
                        if len(diverse_frames) >= 5:
                            frame_paths = diverse_frames
                            scenes = diverse_scenes
                    except Exception:
                        pass

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
