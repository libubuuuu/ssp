"""video_frame_extraction skill 异常树。"""


class VideoFrameError(Exception):
    """skill 通用异常基类。"""


class SceneDetectionError(VideoFrameError):
    """PySceneDetect 检测失败(视频损坏 / 编码不支持 / 太短)。"""


class KeyframeExtractionError(VideoFrameError):
    """ffmpeg 抽帧失败(returncode != 0 / 输出帧损坏 / scene 时间戳越界)。"""


class GridCompositionError(VideoFrameError):
    """PIL 拼合九宫格失败(input 数量 < layout / 图损坏 / 写文件失败)。"""
