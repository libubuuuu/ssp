"""PIL 拼九宫格 + 编号水印封装。

输入:帧路径 list + layout(rows, cols)
输出:网格 PNG 路径

依赖:Pillow
"""
import os
import tempfile
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .exceptions import GridCompositionError


def compose_grid(
    frame_paths: List[str],
    layout: Tuple[int, int] = (3, 3),
    output_path: Optional[str] = None,
    canvas_size: int = 1024,
    border: int = 4,
    show_index: bool = True,
) -> str:
    """把 N 张帧图按 layout 拼成网格大图 + 编号水印。

    Args:
        frame_paths: 帧文件路径 list
        layout: (rows, cols),(3,3)=9 宫格,(2,3)=6 宫格,(2,2)=4,(1,2)=2
        output_path: 输出 PNG 路径,None → /tmp/v3_grid_xxx.png
        canvas_size: 网格图边长(像素),默认 1024
        border: 格子间白边(像素),默认 4
        show_index: 左上角是否画 1..N 编号水印

    Returns:
        网格图 PNG 绝对路径

    Raises:
        GridCompositionError: 帧 list 空 / 图损坏 / 写文件失败
    """
    rows, cols = layout
    expected = rows * cols
    actual = len(frame_paths)

    if actual == 0:
        raise GridCompositionError("frame_paths is empty")
    # 不够格子 → 重复最后一张填充;超过 → 截断
    if actual < expected:
        frame_paths = list(frame_paths) + [frame_paths[-1]] * (expected - actual)
    elif actual > expected:
        frame_paths = frame_paths[:expected]

    cell_w = (canvas_size - (cols + 1) * border) // cols
    cell_h = (canvas_size - (rows + 1) * border) // rows

    canvas = Image.new("RGB", (canvas_size, canvas_size), color="white")

    # 标签字体:优先 DejaVu Sans Bold,降级 PIL 默认字体
    try:
        font_size = max(20, cell_h // 12)
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            size=font_size,
        )
    except Exception:
        font = ImageFont.load_default()

    draw = ImageDraw.Draw(canvas)

    for i, fp in enumerate(frame_paths):
        if not os.path.exists(fp):
            raise GridCompositionError(f"frame missing: {fp}")
        try:
            img = Image.open(fp).convert("RGB")
        except Exception as e:
            raise GridCompositionError(f"open frame {fp} failed: {e}") from e

        # 等比缩放到 cover cell + 居中裁剪
        img_ratio = img.width / img.height if img.height > 0 else 1.0
        cell_ratio = cell_w / cell_h if cell_h > 0 else 1.0
        if img_ratio > cell_ratio:
            new_h = cell_h
            new_w = max(1, int(img.width * cell_h / max(1, img.height)))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            left = max(0, (new_w - cell_w) // 2)
            img = img.crop((left, 0, left + cell_w, cell_h))
        else:
            new_w = cell_w
            new_h = max(1, int(img.height * cell_w / max(1, img.width)))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            top = max(0, (new_h - cell_h) // 2)
            img = img.crop((0, top, cell_w, top + cell_h))

        row = i // cols
        col = i % cols
        x = border + col * (cell_w + border)
        y = border + row * (cell_h + border)
        canvas.paste(img, (x, y))

        if show_index:
            label = str(i + 1)
            pad = 6
            try:
                bbox = draw.textbbox((0, 0), label, font=font)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            except AttributeError:
                # 老版本 PIL fallback
                tw, th = draw.textsize(label, font=font)
            box_xy = (x + 4, y + 4, x + 4 + tw + 2 * pad, y + 4 + th + 2 * pad)
            draw.rectangle(box_xy, fill="black")
            draw.text((x + 4 + pad, y + 4 + pad - 2), label, fill="white", font=font)

    if output_path is None:
        fd, output_path = tempfile.mkstemp(prefix="v3_grid_", suffix=".png")
        os.close(fd)

    try:
        canvas.save(output_path, "PNG", optimize=True)
    except Exception as e:
        raise GridCompositionError(f"save grid {output_path} failed: {e}") from e

    return output_path
