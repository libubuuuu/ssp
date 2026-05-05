"""
分镜图工作台 API
================

流程:
  1. /upload/image  上传参考图(产品/模特+产品/场景图),返 fal storage URL
  2. /generate      VLM 看图+描述 → N 段分镜 → 并发 Kontext multi-edit → N 张分镜图

设计:
- 同步等(总耗时 30-60s),不入 task_queue,简化前端无需轮询
- @require_credits 装饰器统一处理扣费/退款/消费记录
- VLM 阶段失败 → 抛 HTTPException → 装饰器自动退款
- Kontext 部分失败 → 返回 success_count + 各段 error 标注(不退款,因 VLM 已花)
"""
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from app.api.auth import get_current_user
from app.services.decorators import require_credits
from app.services.fal_service import fal_upload_with_retry
from app.services.storyboard_service import generate_storyboard
from app.services.logger import log_info, log_error

router = APIRouter()


@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传参考图(同 ad-video 上传逻辑:Pillow 标准化 + 宽高比矫正 + fal storage)"""
    import tempfile
    import os
    from PIL import Image
    import io
    from app.services.upload_guard import read_bounded, IMAGE_MIMES

    contents = await read_bounded(file, 10 * 1024 * 1024, IMAGE_MIMES, "storyboard 参考图")
    img = Image.open(io.BytesIO(contents))

    if img.mode in ("RGBA", "P", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode in ("RGBA", "LA"):
            bg.paste(img, mask=img.split()[-1])
        else:
            bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    ratio = w / h
    if ratio < 0.40:
        new_w = int(h * 0.45)
        new_img = Image.new("RGB", (new_w, h), (255, 255, 255))
        new_img.paste(img, ((new_w - w) // 2, 0))
        img = new_img
    elif ratio > 2.50:
        new_h = int(w / 2.45)
        new_img = Image.new("RGB", (w, new_h), (255, 255, 255))
        new_img.paste(img, (0, (new_h - h) // 2))
        img = new_img

    w, h = img.size
    if w < 300 or h < 300:
        scale = max(300 / w, 300 / h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        img.save(tmp.name, "JPEG", quality=90, optimize=True)
        if os.path.getsize(tmp.name) > 10 * 1024 * 1024:
            img.save(tmp.name, "JPEG", quality=75, optimize=True)
        tmp_path = tmp.name
    try:
        url = await fal_upload_with_retry(tmp_path)
        return {"url": url}
    finally:
        os.unlink(tmp_path)


@router.post("/generate")
@require_credits("storyboard/generate")
async def generate(
    image_url: str = Form(...),
    description: str = Form(""),
    n_frames: int = Form(5),
    aspect_ratio: str = Form("9:16"),
    current_user: dict = Depends(get_current_user),
):
    """生成 N 段分镜图(同步等 30-60s)

    Form params:
      image_url:      /upload/image 返回的 fal storage URL
      description:    用户输入的产品+用途描述(可空,VLM 仅看图)
      n_frames:       2-12,默认 5
      aspect_ratio:   "9:16" / "16:9" / "1:1",默认 9:16

    返回:
      {overall_theme, frames: [{idx, title, purpose, shot_type, image_url|None, error|None}],
       success_count, total_count, cost}

    扣费:storyboard/generate 8 积分/单,VLM 阶段失败自动退款,Kontext 部分失败不退。
    """
    if n_frames < 2 or n_frames > 12:
        raise HTTPException(status_code=400, detail="分镜数必须在 2-12 之间")
    if aspect_ratio not in ("9:16", "16:9", "1:1"):
        raise HTTPException(status_code=400, detail="比例只支持 9:16 / 16:9 / 1:1")

    user_id = current_user["id"]
    log_info(f"storyboard/generate user={user_id} n={n_frames} ar={aspect_ratio} desc_len={len(description)}")

    result = await generate_storyboard(
        reference_image_url=image_url,
        description=description.strip(),
        n_frames=n_frames,
        aspect_ratio=aspect_ratio,
    )

    if "error" in result:
        # 抛出让 @require_credits 自动退款
        log_info(f"storyboard/generate user={user_id} VLM 阶段失败 → 触发退款: {result['error']}")
        raise HTTPException(status_code=500, detail=result["error"])

    log_info(
        f"storyboard/generate user={user_id} OK "
        f"success={result.get('success_count')}/{result.get('total_count')}"
    )
    # description 字段供 @require_credits 写消费记录
    result["description"] = (
        f"分镜图 {n_frames} 段 {aspect_ratio} 主题={result.get('overall_theme', '')[:30]}"
    )
    return result
