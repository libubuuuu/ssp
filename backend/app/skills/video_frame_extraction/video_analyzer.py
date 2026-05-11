"""AI 视觉分析 stub — 等 DeepSeek-VL / nano-banana 接入后实装。

当前仅占位,所有方法 raise NotImplementedError。
"""
from typing import List

from .exceptions import VideoFrameError


class _NotWiredError(VideoFrameError):
    """AI 模型尚未接入。"""


def analyze_keyframes_with_ai(
    frame_paths: List[str],
    model: str = "deepseek-vl",
) -> List[dict]:
    """用 vision-LLM 分析每张关键帧 → 镜头描述 / 景别 / 动作。

    Args:
        frame_paths: 关键帧路径 list
        model: 选哪个 VLM ("deepseek-vl" / "nano-banana" / "qwen-vl")

    Returns:
        每张帧一个 dict: {idx, description, shot_type, action, ...}

    Raises:
        _NotWiredError: 当前所有模型都未接入,详见 SKILL.md "未来扩展"
    """
    raise _NotWiredError(
        f"video_analyzer.{model} 尚未接入,见 SKILL.md 未来扩展章节"
    )
