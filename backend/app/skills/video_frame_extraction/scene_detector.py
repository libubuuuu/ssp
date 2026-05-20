"""PySceneDetect 场景检测封装。

主检测器:AdaptiveDetector(adaptive_threshold=3.0) — 适合带货/软变化视频。
兜底检测器:ContentDetector(threshold=12.0) — AdaptiveDetector 场景数不足时补充。

输入:视频路径
输出:Scene list,每个 scene 含 idx / start_seconds / end_seconds

依赖:scenedetect[opencv] >= 0.6
"""
import os
from dataclasses import dataclass
from typing import List

from .exceptions import SceneDetectionError


def _log_info(msg: str) -> None:
    try:
        from app.services.logger import log_info
        log_info(msg)
    except Exception:
        pass


@dataclass
class Scene:
    """单个场景片段。"""
    idx: int
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    @property
    def midpoint_seconds(self) -> float:
        return (self.start_seconds + self.end_seconds) / 2

    def to_dict(self) -> dict:
        return {
            "idx": self.idx,
            "start_seconds": round(self.start_seconds, 3),
            "end_seconds": round(self.end_seconds, 3),
            "duration_seconds": round(self.duration_seconds, 3),
        }


def _scene_list_to_scenes(scene_list) -> List[Scene]:
    return [
        Scene(idx=i, start_seconds=float(s.get_seconds()), end_seconds=float(e.get_seconds()))
        for i, (s, e) in enumerate(scene_list)
    ]


def _probe_duration(video_path: str) -> float:
    import cv2
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    return float(n) / float(fps) if n > 0 else 0.0


def detect_scenes(
    video_path: str,
    threshold: float = 27.0,   # 保留参数兼容老调用，内部不再使用
    min_scene_len: int = 15,
) -> List[Scene]:
    """场景检测：AdaptiveDetector 主检 + ContentDetector 兜底。

    1. AdaptiveDetector(adaptive_threshold=3.0) — 适合软变化/带货视频
    2. 若检测到场景数 < 3，用 ContentDetector(threshold=12.0) 补充
    3. 两次都没结果，整段当 1 个 scene

    Returns:
        Scene list 按时间排序，至少 1 个。
    Raises:
        SceneDetectionError: 文件不存在 / 视频读取失败
    """
    if not os.path.exists(video_path):
        raise SceneDetectionError(f"video not found: {video_path}")

    try:
        from scenedetect import detect, AdaptiveDetector, ContentDetector

        # 主检：AdaptiveDetector 捕捉软变化
        scene_list = detect(
            video_path,
            AdaptiveDetector(adaptive_threshold=3.0, min_scene_len=min_scene_len),
        )
        _log_info(f"scene_detect AdaptiveDetector: {len(scene_list)} cuts in {video_path[-40:]}")

        # 兜底：场景太少时再用 ContentDetector(threshold=12) 补一次
        if len(scene_list) < 3:
            scene_list2 = detect(
                video_path,
                ContentDetector(threshold=12.0, min_scene_len=min_scene_len),
            )
            _log_info(f"scene_detect ContentDetector fallback: {len(scene_list2)} cuts")
            if len(scene_list2) > len(scene_list):
                scene_list = scene_list2

    except Exception as e:
        raise SceneDetectionError(f"PySceneDetect failed on {video_path}: {e}") from e

    if not scene_list:
        # 硬切换都检测不到：整段当 1 个 scene
        try:
            dur = _probe_duration(video_path)
            if dur <= 0:
                raise SceneDetectionError(f"video has 0 frames: {video_path}")
            _log_info(f"scene_detect: no cuts found, 1 scene duration={dur:.1f}s")
            return [Scene(idx=0, start_seconds=0.0, end_seconds=dur)]
        except SceneDetectionError:
            raise
        except Exception as e:
            raise SceneDetectionError(f"duration probe failed: {e}") from e

    scenes = _scene_list_to_scenes(scene_list)
    _log.info(f"scene_detect: final {len(scenes)} scenes")
    return scenes
