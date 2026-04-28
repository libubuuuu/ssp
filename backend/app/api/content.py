"""
内容增强 API
- 根据提示词生成卖点和场景描述
- 为生成的图片/视频提供商业文案
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from app.api.auth import get_current_user

router = APIRouter()


class ContentEnhanceRequest(BaseModel):
    prompt: str
    style: Optional[str] = "advertising"
    content_type: Optional[str] = "image"  # image | video


# 预设的场景和卖点模板
SCENE_TEMPLATES = {
    "advertising": {
        "scenes": [
            "社交媒体广告投放（小红书、抖音、微信朋友圈）",
            "电商平台产品主图（淘宝、京东、拼多多）",
            "品牌宣传物料（海报、展架、易拉宝）",
            "线上营销活动页面（Banner、落地页）",
        ],
        "selling_points": [
            "高视觉冲击力，快速吸引用户注意力",
            "专业级画质，提升品牌形象与信任度",
            "精准传达产品核心卖点，提高转化率",
            "可批量生成多风格素材，适配不同投放渠道",
        ],
    },
    "minimalist": {
        "scenes": [
            "品牌官网与产品展示页",
            "高端产品画册与宣传册",
            "企业VI设计素材",
            "杂志内页与 editorial 内容",
        ],
        "selling_points": [
            "极简美学设计，突出产品质感",
            "高级感配色与构图，传递品牌调性",
            "留白得当，视觉呼吸感强",
            "适合高端定位产品，提升溢价能力",
        ],
    },
    "default": {
        "scenes": [
            "社交媒体内容创作（公众号配图、短视频封面）",
            "个人创作与灵感参考",
            "PPT 演示与报告配图",
            "博客与文章插图",
        ],
        "selling_points": [
            "AI 智能生成，节省拍摄与设计成本",
            "创意无限，满足多样化内容需求",
            "快速迭代，支持多次调整优化",
            "版权清晰，商用无忧",
        ],
    },
}


@router.post("/enhance")
async def generate_content_enhancement(
    req: ContentEnhanceRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    根据提示词生成卖点和场景描述
    用于生成结果详情页展示

    五十八续:加鉴权防匿名扫(纯模板返回但仍是 attack surface)。
    """
    style = req.style or "default"
    template = SCENE_TEMPLATES.get(style, SCENE_TEMPLATES["default"])

    # 根据提示词智能匹配相关场景
    prompt_lower = req.prompt.lower()
    extra_scenes = []
    extra_selling_points = []

    # 智能检测内容类型，补充场景
    if any(kw in prompt_lower for kw in ["food", "食物", "食品", "餐厅", "restaurant", "cake", "蛋糕"]):
        extra_scenes.extend(["外卖平台店铺装修", "美食博主内容配图", "菜单设计素材"])
        extra_selling_points.extend(["诱人食欲的色彩搭配", "突出食材新鲜与品质感"])
    elif any(kw in prompt_lower for kw in ["fashion", "服装", "衣服", "dress", "shoe", "鞋"]):
        extra_scenes.extend(["服装电商平台详情页", "穿搭博主社交媒体内容", "时尚杂志内页"])
        extra_selling_points.extend(["展现穿着效果与搭配灵感", "突出面料质感与版型"])
    elif any(kw in prompt_lower for kw in ["house", "home", "家居", "室内", "interior", "room"]):
        extra_scenes.extend(["房产平台房源展示", "家居品牌产品图", "室内设计方案展示"])
        extra_selling_points.extend(["真实感空间渲染效果", "展现居住氛围与生活品质"])
    elif any(kw in prompt_lower for kw in ["product", "产品", "商品", "goods"]):
        extra_scenes.extend(["产品详情页主图", "直播带货素材准备", "跨境电商平台素材"])
        extra_selling_points.extend(["突出产品核心功能与卖点", "适配多平台尺寸要求"])
    elif any(kw in prompt_lower for kw in ["logo", "brand", "品牌", "标志"]):
        extra_scenes.extend(["品牌VI系统设计", "名片与办公用品", "线上线下品牌物料"])
        extra_selling_points.extend(["强化品牌识别度", "统一视觉形象传递"])

    # 合并场景和卖点
    all_scenes = template["scenes"] + extra_scenes
    all_selling_points = template["selling_points"] + extra_selling_points

    # 生成标题
    title = _generate_title(req.prompt, req.content_type)

    # 生成简短描述
    description = _generate_description(req.prompt, style, req.content_type)

    return {
        "success": True,
        "title": title,
        "description": description,
        "selling_points": all_selling_points[:6],
        "scenes": all_scenes[:6],
        "tags": _generate_tags(req.prompt, style),
    }


def _generate_title(prompt: str, content_type: str) -> str:
    """生成内容标题"""
    # 截取提示词前20个字符作为标题基础
    base = prompt[:30].strip()
    if len(prompt) > 30:
        base += "..."

    suffix = "AI创意图片" if content_type == "image" else "AI创意视频"
    return f"{base} - {suffix}"


def _generate_description(prompt: str, style: str, content_type: str) -> str:
    """生成内容描述"""
    type_text = "图片" if content_type == "image" else "视频"
    style_text = {
        "advertising": "广告级视觉效果",
        "minimalist": "极简美学风格",
    }.get(style, "专业级AI生成")

    return (
        f"本{type_text}由AI根据\"{prompt[:50]}\"智能生成，"
        f"采用{style_text}，适用于品牌营销、社交媒体、电商运营等多种商业场景。"
        f"可直接下载使用，支持高清大图输出。"
    )


def _generate_tags(prompt: str, style: str) -> list[str]:
    """生成标签"""
    tags = ["AI生成", style]
    prompt_lower = prompt.lower()

    tag_map = {
        "product": "产品图", "产品": "产品图",
        "food": "美食", "食物": "美食",
        "fashion": "时尚", "服装": "时尚",
        "house": "家居", "家居": "家居",
        "logo": "品牌", "品牌": "品牌",
        "nature": "自然", "自然": "自然",
        "portrait": "人像", "人": "人像",
        "advertising": "广告", "广告": "广告",
    }

    for kw, tag in tag_map.items():
        if kw in prompt_lower and tag not in tags:
            tags.append(tag)

    return tags[:5]


from fastapi import UploadFile, File, Depends
from app.api.auth import get_current_user
from app.services.upload_guard import read_bounded, IMAGE_MIMES
import fal_client, tempfile, os

@router.post("/upload")
async def upload_content(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """通用图片上传(传给 fal storage 拿 URL)。

    五十六续:加 upload_guard 守卫(原 await file.read() 无 size 限制,
    nginx 给 500MB 上限,一次读到内存 = OOM 攻击面)。
    """
    contents = await read_bounded(file, 10 * 1024 * 1024, IMAGE_MIMES, "content 上传图")
    suffix = os.path.splitext(file.filename)[1] or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    try:
        url = await fal_client.upload_file_async(tmp_path)
        return {"url": url, "image_url": url}
    finally:
        os.unlink(tmp_path)
