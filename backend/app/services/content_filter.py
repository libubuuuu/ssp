"""
内容审核(精简版,Phase 1)

设计:
- 200 词左右黑名单(中英对照),三类:政治敏感 / 色情 / 暴力
- check_prompt(prompt) -> (is_safe, reason)
- 中文用子串匹配(中文无 word boundary 概念)
- 英文用单词边界匹配(避免 "kill" 误伤 "skill" / "skillful")
- 大小写不敏感

⚠ 这是"够用"的底线,不是合规级。Phase 4 必须接阿里云内容安全 / 腾讯云
CMS 才能满足"深度合成"监管要求。当前实现只挡明显违规,降低法律风险。

接入点:
- image.py /style, /realistic, /multi-reference — 调 FAL 前过滤 req.prompt
- video.py /image-to-video — 过滤 req.prompt
- video.py /replace/element — 过滤 req.instruction

不过滤:
- voice clone / TTS(text 字段)— 暂留下次,优先处理图像/视频
- 已 503 的端点(不会真到 FAL)
"""
from __future__ import annotations

import re
from typing import Optional, Tuple


# === 政治敏感(中文) ===
_BLACK_POL_CN = [
    "习近平", "毛泽东", "共产党", "国家主席", "中央委员", "中央政治局",
    "胡锦涛", "温家宝", "江泽民", "李克强", "李强",
    "六四", "天安门事件", "天安门屠杀",
    "法轮功", "李洪志",
    "藏独", "疆独", "台独", "港独",
    "达赖", "达赖喇嘛",
    "民运", "反贼",
    "新疆集中营", "再教育营",
    "镇压", "政变",
]

# === 政治敏感(英文) ===
_BLACK_POL_EN = [
    "xi jinping", "mao zedong", "tiananmen square",
    "falun gong", "dalai lama",
    "tibet independence", "xinjiang camp", "uyghur camp",
    "ccp dictator",
]

# === 色情(中文) ===
_BLACK_PORN_CN = [
    "裸体", "裸照", "裸露", "全裸", "赤身", "赤裸",
    "色情", "黄色片", "色情片", "做爱", "性交", "口交", "肛交",
    "自慰", "手淫",
    "阴茎", "阴道", "阴蒂", "阴部", "乳头", "下体",
    "操逼", "干逼", "鸡巴", "肉棒",
    "情色", "情趣用品", "成人片",
    "妓女", "卖淫", "嫖娼", "援交",
    "毛片", "A片", "AV片", "三级片",
    "小穴",
]

# === 色情(英文) ===
_BLACK_PORN_EN = [
    "nude", "naked", "topless", "bottomless",
    "sex", "sexy", "sexual", "porn", "pornography", "porno",
    "fuck", "fucking", "fucker", "motherfucker",
    "dick", "cock", "penis", "vagina", "pussy", "boobs", "tits",
    "masturbation", "masturbate", "masturbating",
    "hentai", "erotic", "erotica", "xxx",
    "anal", "blowjob", "handjob", "cumshot",
    "nsfw",
]

# === 暴力(中文) ===
_BLACK_VIOLENCE_CN = [
    "杀人", "凶杀", "谋杀", "刺杀", "屠杀",
    "血腥", "血浆", "鲜血淋漓", "血流成河",
    "虐杀", "自杀", "自残", "割腕", "上吊",
    "斩首", "砍头", "断头", "断肢",
    "虐待", "暴打", "凌迟",
    "炸死", "炸毁", "爆炸", "炸弹", "恐怖袭击", "恐怖分子",
    "枪击", "枪杀", "中弹", "枪决",
    "尸体", "死尸", "腐尸",
    "强奸", "轮奸",
]

# === 暴力(英文) ===
_BLACK_VIOLENCE_EN = [
    "kill", "killing", "murder", "murdering", "assassinate",
    "blood", "bloody", "gore", "gory", "carnage",
    "suicide", "self-harm", "self harm",
    "behead", "beheading", "decapitate", "decapitation",
    "slaughter", "torture", "tortured",
    "bomb", "bombing", "explosion", "terrorist", "terrorism",
    "gun", "shoot", "shooting", "shooter",
    "corpse", "dead body", "dismember",
    "rape", "raping", "rapist",
]


# 编译为单一搜索结构,加 reason 标签
_CN_RULES: list[tuple[str, str]] = (
    [(w, "政治敏感") for w in _BLACK_POL_CN]
    + [(w, "色情") for w in _BLACK_PORN_CN]
    + [(w, "暴力") for w in _BLACK_VIOLENCE_CN]
)

# 英文用单词边界正则,避免 "kill" 误伤 "skill" / "skillful"
_EN_RULES: list[tuple[re.Pattern, str]] = (
    [(re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE), "political") for w in _BLACK_POL_EN]
    + [(re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE), "explicit") for w in _BLACK_PORN_EN]
    + [(re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE), "violence") for w in _BLACK_VIOLENCE_EN]
)


def check_prompt(prompt: Optional[str]) -> Tuple[bool, Optional[str]]:
    """检查 prompt 是否合规

    返回 (is_safe, reason)
    - is_safe=True 时 reason 为 None
    - is_safe=False 时 reason 为命中的中文类别("政治敏感"/"色情"/"暴力"),
      不返回具体词避免给攻击者反馈

    空 prompt 视为安全(由调用方决定是否拒绝空输入)。
    """
    if not prompt:
        return True, None

    # 中文:子串匹配
    for word, category in _CN_RULES:
        if word in prompt:
            return False, category

    # 英文:单词边界
    for pat, cat in _EN_RULES:
        if pat.search(prompt):
            cn = {"political": "政治敏感", "explicit": "色情", "violence": "暴力"}[cat]
            return False, cn

    return True, None


def assert_safe_prompt(prompt: Optional[str]) -> None:
    """便捷接口:不安全直接 raise HTTPException(400)

    在 FastAPI handler 调 FAL 前一行调用即可:
        from app.services.content_filter import assert_safe_prompt
        assert_safe_prompt(req.prompt)
    """
    from fastapi import HTTPException

    is_safe, reason = check_prompt(prompt)
    if not is_safe:
        raise HTTPException(
            status_code=400,
            detail=f"您的请求包含不允许的内容({reason}),请修改后重试",
        )
