"""PySceneDetect ContentDetector 封装。

输入:视频路径
输出:Scene list,每个 scene 含 idx / start_seconds / end_seconds

依赖:scenedetect[opencv] >= 0.6
"""
import os
from dataclasses import dataclass
from typing import List

from .exceptions import SceneDetectionError


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


def detect_scenes(
    video_path: str,
    threshold: float = 27.0,
    min_scene_len: int = 15,
) -> List[Scene]:
    """用 PySceneDetect ContentDetector 检测视频场景切换。

    Args:
        video_path: 视频文件路径(本地)
        threshold: 内容变化阈值(默认 27.0),越低越敏感
        min_scene_len: 最小场景长度(帧),避免抖动碎片

    Returns:
        Scene list 按时间排序。若视频是单镜头(无切换),返回 1 个全片 scene。

    Raises:
        SceneDetectionError: 文件不存在 / PySceneDetect 抛错
    """
    if not os.path.exists(video_path):
        raise SceneDetectionError(f"video not found: {video_path}")

    try:
        from scenedetect import detect, ContentDetector
        scene_list = detect(
            video_path,
            ContentDetector(threshold=threshold, min_scene_len=min_scene_len),
        )
    except Exception as e:
        raise SceneDetectionError(f"PySceneDetect failed on {video_path}: {e}") from e

    if not scene_list:
        # 单镜头视频:整段当一个 scene
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
            n_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            cap.release()
            if n_frames <= 0:
                raise SceneDetectionError(f"video has 0 frames: {video_path}")
            duration = float(n_frames) / float(fps)
            return [Scene(idx=0, start_seconds=0.0, end_seconds=duration)]
        except SceneDetectionError:
            raise
        except Exception as e:
            raise SceneDetectionError(
                f"no scene cuts and duration probe failed: {e}"
            ) from e

    scenes = []
    for i, (start, end) in enumerate(scene_list):
        scenes.append(Scene(
            idx=i,
            start_seconds=float(start.get_seconds()),
            end_seconds=float(end.get_seconds()),
        ))
    return scenes
