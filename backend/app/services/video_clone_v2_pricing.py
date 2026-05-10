"""P221 视频复刻 V2 — 价格 + 常量 + hash 工具(4 道红线)

详见 docs/P221-API-SCHEMA.md(v4)§2 / §5.6 / §6。

所有数字常量改要走 PR + 用户审。
"""
from __future__ import annotations
import hashlib
import re
from typing import Final, Mapping, Sequence


# ⭐ 全局单档(2026-05-10 砍 economy/standard,只留一档)
# 决策依据:
# - fal 端点 duration 最低 4 + input<output 触发 hallucinate(2026-05-10 probe 验证)
#   → 全段 input=output=N 秒(N ∈ [4,8],plan_segments_v2 决定)
# - 砍单档后 economy/standard 行为已等价(同端点/同分辨率/同参数),分档只是 UI 噱头
# - 占位定价用原 standard 档上限值(20 积分 / ¥19.9),commit 5 真测 fal cost 后重定
SEGMENT_CREDITS:       Final[int] = 20      # 每个 ai 段固定扣 20 积分
SEGMENT_DISPLAY_RMB:   Final[str] = "19.9"  # 前端营销展示价
SEGMENT_LABEL:         Final[str] = "AI 替换"  # 前端段卡片显示名
SEGMENT_INPUT_SECONDS_MAX: Final[int] = 8   # worst-case 估算上限,实际段长 4-8s


# fal 端固定参数(改要测过)
FAL_ENDPOINT:        Final[str] = "bytedance/seedance-2.0/fast/reference-to-video"
FAL_RESOLUTION:      Final[str] = "480p"
# fal 端点接受 duration ∈ {'auto', '4', '5', ..., '15'}(2026-05-10 probe 验证)
# processor 会按段实际秒数对齐到 [4, 15] 区间,不再用此固定值;保留作 fallback
FAL_OUTPUT_DURATION: Final[int] = 8
FAL_GENERATE_AUDIO:  Final[bool] = False   # 用原视频音轨拼回
FAL_SAFETY_CHECKER:  Final[bool] = True    # 必开


# 全能档限制
MAX_ULTIMATE_SECONDS:  Final[int] = 64
MAX_ULTIMATE_SEGMENTS: Final[int] = 8


# ⭐ 功能 1:替换模式
REPLACEMENT_MODES: Final[tuple[str, ...]] = ("partial", "full")


# ⭐ 功能 3:image role 枚举(prompt @ 语法用)
IMAGE_ROLES: Final[tuple[str, ...]] = ("product", "person", "scene", "reference")
ROLE_TO_AT_LABEL: Final[Mapping[str, str]] = {
    "product":   "产品",   # @产品1
    "person":    "人物",   # @人物1
    "scene":     "场景",   # @场景1
    "reference": "图",     # @图1(默认/兜底)
}


# ⭐ 功能 4:5 个 prompt 模板(前端按钮 → 填入 prompt 框 → 用户可改)
PROMPT_TEMPLATES: Final[Sequence[Mapping[str, str]]] = (
    {"id": "baby_goods",      "label": "婴儿用品带货",
     "template": "婴儿安静地玩耍/睡觉/学习抬头,展示婴儿用品的安全和舒适,柔光卧室或客厅"},
    {"id": "clothing_try",    "label": "服装试穿",
     "template": "模特展示服装的合身度和质感,镜头自然过渡,光线明亮简洁"},
    {"id": "food_making",     "label": "美食制作",
     "template": "食材新鲜陈列,烹饪过程清晰展示,光泽诱人,配文火慢炖的氛围"},
    {"id": "digital_unbox",   "label": "数码开箱",
     "template": "产品开箱展示,细节特写,质感金属/玻璃反光,简约工业风背景"},
    {"id": "beauty_skincare", "label": "美妆护肤",
     "template": "产品近景,质地细腻,模特肤感清透,柔光梳妆台或大理石背景"},
)


# ⭐ 双版本下载水印规格(用户最终决议 v3 → 六审撤回到纯文字)
# 60% 不透明度 + 黑色描边是法律"显著标识"门槛
# logo + 小字方案(五审)在 8% 短边下糊成一片,六审撤回纯文字方案
# logo 文件保留在 /opt/ssp/uploads/brand/,将来 V3 视觉升级再用
WATERMARK_TEXT:               Final[str]   = "xiaoLi ai · AI 生成"  # 品牌 + 法规第 17 条显著标识合一
WATERMARK_FONT_SIZE_PCT:      Final[float] = 0.03   # 画面较短边的 3%(向上取整,只增不减)
WATERMARK_OPACITY:            Final[float] = 0.60   # 60%(法定"显著标识"门槛)
WATERMARK_BORDER_OPACITY:     Final[float] = 0.60   # 黑色描边 60%
WATERMARK_BORDER_WIDTH:       Final[int]   = 1      # 描边像素宽度
WATERMARK_EDGE_PAD:           Final[int]   = 10     # 距右下角边距(六审固定 10px)
WATERMARK_POSITION:           Final[str]   = "bottom-right"
WATERMARK_FONT_FAMILY:        Final[str]   = "Noto Sans CJK SC Bold"  # 六审升级到 Bold


def calc_total_credits(segments: Sequence[Mapping]) -> int:
    """算订单总积分(忽略 source_type='original' 段,只对 ai 段累加单档单价)。

    Args:
        segments: [{"source_type": "ai"|"original", ...}, ...]
    Returns:
        总积分(int)= ai 段数 × SEGMENT_CREDITS
    """
    ai_count = sum(1 for seg in segments if seg.get("source_type") == "ai")
    return ai_count * SEGMENT_CREDITS


# alias 对齐 A2 任务清单命名("calc_credits");新代码调用方推荐用 calc_credits
calc_credits = calc_total_credits


# ─── hash 工具(4 道红线用)─────────────────────────────────────────────

def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    """文件 SHA256(流式,大文件友好)。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_url_first8(url: str) -> str:
    """URL 字符串 SHA256 前 8 位(fal 调用日志用,不是文件本体)。"""
    return hashlib.sha256(url.encode()).hexdigest()[:8]


def build_prompt(user_prompt: str, image_urls: Sequence[Mapping]) -> str:
    """把 image_urls 拼成 fal 标准 @Image{N} 占位符附加到用户 prompt 末尾。

    fal seedance r2v(fast/reference-to-video)占位符约定:@Image1 / @Image2 / ... 引用
    image_urls 数组中对应位置(1-based)。中文 @ 写法 fal 不识别 → 当作普通文本 → 模型不看图。

    本函数行为:
    1. user_prompt 里的中文 @ 引用(@产品N / @人物N / @场景N / @图N)按 role 内序号
       查 image_urls 中对应全局位置 → 替换成 @Image{global_idx}
    2. 末尾追加(参考素材:@Image1, @Image2, ...)列出所有图

    设计选择(2026-05-10 commit 4):
    - 前端 / 用户层保持中文 @ 友好,后端透明转 fal 标准 — 前端 prompt 模板不动
    - 用户引用不存在的图(@产品3 但只上传 2 张 product)→ 保留原文,不擅自重定向

    Args:
        user_prompt: 用户填的 prompt(可能含中文 @ 引用)
        image_urls:  [{"url": "...", "role": "product"}, ...](role ∈ IMAGE_ROLES)
    Returns:
        fal 端能识别的 prompt(中文 @ 已转 @Image{N})

    示例:
        user_prompt="@产品1 替换视频中的裤子"
        image_urls=[{role:"product"}, {role:"person"}]
        → "@Image1 替换视频中的裤子(参考素材:@Image1, @Image2)"
    """
    if not image_urls:
        return user_prompt

    # 1. 算每张图的 (role, role_inner_seq) → 全局 1-based idx
    fal_idx_by_role_seq: dict[tuple[str, int], int] = {}
    role_seen = {role: 0 for role in IMAGE_ROLES}
    for global_idx, img in enumerate(image_urls, start=1):
        role = img.get("role", "reference")
        if role not in IMAGE_ROLES:
            role = "reference"
        role_seen[role] += 1
        fal_idx_by_role_seq[(role, role_seen[role])] = global_idx

    # 2. user_prompt 里的中文 @{label}{N?} → @Image{global_idx}
    transformed = user_prompt
    for role, label in ROLE_TO_AT_LABEL.items():
        pattern = re.compile(re.escape(f"@{label}") + r"(\d*)")

        def _make_replacer(_role: str):
            def _replace(m: re.Match) -> str:
                num_str = m.group(1)
                n = int(num_str) if num_str else 1
                global_idx = fal_idx_by_role_seq.get((_role, n))
                if global_idx is None:
                    return m.group(0)  # 用户引用不存在的图 — 保留原文
                return f"@Image{global_idx}"
            return _replace

        transformed = pattern.sub(_make_replacer(role), transformed)

    # 3. 末尾追加(参考素材:@Image1, @Image2, ...)
    refs = [f"@Image{i}" for i in range(1, len(image_urls) + 1)]
    return f"{transformed}(参考素材:{', '.join(refs)})"
