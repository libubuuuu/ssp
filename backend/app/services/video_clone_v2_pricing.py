"""P221 视频复刻 V2 — 价格 + 常量 + hash 工具(4 道红线)

详见 docs/P221-API-SCHEMA.md(v4)§2 / §5.6 / §6。

所有数字常量改要走 PR + 用户审。
"""
from __future__ import annotations
import hashlib
from typing import Final, Mapping, Sequence


# ⭐ 全局单档(2026-05-10 砍 economy/standard,只留一档)
# 决策依据:
# - fal 端点 duration 最低 4 + input<output 触发 hallucinate(2026-05-10 probe 验证)
#   → 全段 input=output=N 秒(N ∈ [4,8],plan_segments_v2 决定)
# - 砍单档后 economy/standard 行为已等价(同端点/同分辨率/同参数),分档只是 UI 噱头
#
# 2026-05-14 视频复刻定价:60 积分/秒(¥1.2/s)。按段 duration 计费,
# 不再 "每段固定 20"(老规则等于 4s 段 5 积分/秒、8s 段 2.5 积分/秒,跟新口径不一致)。
CREDITS_PER_SEC:       Final[int] = 60      # 60 积分 / 秒(¥1.2/秒,视频复刻,2026-05-14 重定)
CREDITS_PER_YUAN:      Final[int] = 50      # 50 积分 = 1 元(2026-05-13 锁定汇率)
SEGMENT_LABEL:         Final[str] = "AI 替换"  # 前端段卡片显示名
SEGMENT_INPUT_SECONDS_MAX: Final[int] = 8   # worst-case 估算上限,实际段长 4-8s


# fal 端固定参数(改要测过)
FAL_ENDPOINT:        Final[str] = "bytedance/seedance-2.0/fast/reference-to-video"
# 2026-05-11:480p。老板 playground 实证 (request 019e1331) 也用 480p 跑出大体对的结果。
# Agent 调研说 fal schema 默认 720p,但 playground 实际跑 480p,实证为准。
FAL_RESOLUTION:      Final[str] = "480p"
# fal 端点接受 duration ∈ {'auto', '4', '5', ..., '15'}(2026-05-10 probe 验证)
# processor 会按段实际秒数对齐到 [4, 15] 区间,不再用此固定值;保留作 fallback
FAL_OUTPUT_DURATION: Final[int] = 8
# 2026-05-11:对齐 playground 默认 = generate_audio=True(老板真测确认 playground 开)。
# fal 模型在 generate_audio=False 时走不同 code path,实测产品图替换效果失效(prod 920b2000 只换 1 帧)。
# 后端 mux 逻辑会用原视频音轨覆盖 fal 生成的音频(processor L883-918),最终成品仍是原音轨,
# 仅 fal 浪费一次音频生成时间(微小成本)。
FAL_GENERATE_AUDIO:  Final[bool] = False  # 2026-05-13 改 False:fal 自生成音频会被 fal 内容策略检查,
                                          # 偶发 "Output audio has sensitive content" 整段失败。改用原视频音轨。
                                          # _build_segment_clip 检测 fal 无音轨自动 fallback mux 原视频音轨。
# 删除 FAL_SAFETY_CHECKER:fal 官方 schema 不存在 enable_safety_checker 字段,
# 之前 payload 里这个 unknown key 可能干扰 fal 内部 routing。


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


def calc_segment_credits(duration_sec: float) -> int:
    """单段积分 = round(duration_sec × CREDITS_PER_SEC),下限 1 秒。"""
    if duration_sec <= 0:
        return 0
    return max(CREDITS_PER_SEC, int(round(float(duration_sec) * CREDITS_PER_SEC)))


def calc_total_credits(
    segments: Sequence[Mapping],
    plan: Sequence[Mapping] | None = None,
) -> int:
    """算订单总积分:只对 source_type='ai' 段按 duration × CREDITS_PER_SEC 计。

    Args:
        segments: [{"idx", "source_type": "ai"|"original", ...}, ...]
                  (用户选择;source_type 在这里)
        plan:     plan_segments_v2 输出 [{"idx", "start", "duration"}, ...]
                  (后端切片;duration 在这里)
                  如果 segments 元素自身已携带 duration / duration_sec,可省略 plan
    Returns:
        总积分(int)= Σ round(ai 段 duration × CREDITS_PER_SEC),下限 CREDITS_PER_SEC
    """
    # 1) 优先 segments 自带 duration (老调用方传 plan 的合并 dict)
    plan_by_idx: dict[int, float] = {}
    if plan:
        for p in plan:
            try:
                plan_by_idx[int(p["idx"])] = float(p.get("duration") or p.get("duration_sec") or 0)
            except (KeyError, TypeError, ValueError):
                continue

    total = 0
    for seg in segments:
        if seg.get("source_type") != "ai":
            continue
        dur = float(seg.get("duration") or seg.get("duration_sec") or 0)
        if dur <= 0 and plan_by_idx:
            try:
                dur = plan_by_idx.get(int(seg["idx"]), 0.0)
            except (KeyError, TypeError, ValueError):
                dur = 0.0
        total += calc_segment_credits(dur)

    # 至少 1 秒下限,避免空订单走通
    return max(CREDITS_PER_SEC, total) if total > 0 else 0


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
    """直接透传用户 prompt 给 fal,不做任何转换或追加。

    2026-05-11 简化决策:
    - 老板 fal playground 实测成功配方就是裸 prompt(eg "视频中的裤子换成上传的图片")
      + image_urls 数组。fal Fast 端点自己看 image_urls 就知道用图,prompt 里不需要 @ 引用。
    - commit 4 引入的"中文 @ → @Image{N} 转换 + 末尾追加(参考素材:...)" 复杂化反而干扰
      模型(老板 2026-05-10 真测 4 次均失败,playground 同端点同输入跑出完美效果)。
    - 简化等价 playground 体验。

    2026-05-13 prompt 可选:用户不填时给个中性默认,seedance 端点空 prompt 行为未定,给个兜底。

    Args:
        user_prompt: 用户填的 prompt(原样透传;空时用默认)
        image_urls:  [{"url": "...", "role": "..."}, ...](保留参数兼容签名,不使用)
    Returns:
        user_prompt 原样,空时返默认参考生成提示
    """
    if not (user_prompt or "").strip():
        return "Recreate the scene from the reference video using the reference images as visual guidance for style, composition, and motion."
    return user_prompt
