"""
VLM 视觉服务 - AI 带货视频专用

替代 v2 的 claude_vision.py:
- 不再依赖 anthropic SDK + ANTHROPIC_API_KEY
- 改用 fal-ai 的 OpenRouter Vision 端点(openrouter/router/vision)
- 复用现有 FAL_KEY,零新成本

模型选型:
- 默认 qwen/qwen3-vl-235b-a22b-instruct (中文理解最强,带货脚本撰写优势明显)
- 可降级到 google/gemini-2.5-flash (便宜快,质量稍弱)
- 可升级到 anthropic/claude-sonnet-4.5 (质量最高,价格高)

调用方式:
- 用 fal_client.run_async(已有依赖)
- 入参 image_urls (list of URL 字符串)
- 出参 result["output"] 是 LLM 返回的纯文本
- 我们 prompt 让它输出 JSON,然后在 Python 层解析
"""
from __future__ import annotations

import json
import os
import random
import re
import sqlite3
from typing import Optional
import fal_client

from .circuit_breaker import get_circuit_breaker
from .logger import log_info, log_error, log_warning


# P110: viral_scripts db 抽样配置 — 每次 build prompt 时从 db 取 N 条新样本
# 库可以无限增长(scraper 周更),prompt 长度恒定,LLM 接触多样化样本不会风格固化
_VIRAL_SAMPLE_N = {
    "hook": 18,      # 钩子模板:每次抽 18 条(覆盖各风格)
    "selling": 12,   # 卖点 3 件套:抽 12 条
    "cta": 12,       # 促单 CTA:抽 12 条
    "example": 18,   # 多品类示例:抽 18 条
}

# 兜底:db 0 行或异常时,prompt 走极简模式(只保留规则,无 few-shot 示例)
# 实际生产 db 不会空,fallback 仅为单元测试 / 全新部署初始化前的安全网
_VIRAL_FALLBACK_NOTE_CN = "(本次未拉到爆款样本库,LLM 自行发挥;请严格按上述铁律写)"
_VIRAL_FALLBACK_NOTE_GLOBAL = "(no viral samples available; LLM follow the rules above)"


def _get_db_path() -> str:
    """跟 app/database.py 同源。优先 env 覆盖。"""
    return os.environ.get("DATABASE_PATH", "/opt/ssp/backend/dev.db")


def _sample_viral_scripts(region: str) -> dict:
    """从 viral_scripts 表按 region 随机抽样 hook/selling/cta/example 各 N 条。

    返回 dict[kind][list[(text, category)]]。
    db 异常或 0 行时返回空 dict,调用方走 fallback。
    """
    out: dict = {"hook": [], "selling": [], "cta": [], "example": []}
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return out
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout=2000")
        cur = conn.cursor()
        for kind, n in _VIRAL_SAMPLE_N.items():
            # SQLite 用 ORDER BY RANDOM() 抽样,数据规模 < 万级性能 OK
            cur.execute(
                "SELECT text, category FROM viral_scripts WHERE region = ? AND kind = ? ORDER BY RANDOM() LIMIT ?",
                (region, kind, n),
            )
            out[kind] = list(cur.fetchall())
        conn.close()
    except Exception as e:
        log_warning(f"_sample_viral_scripts({region}) 失败,prompt 走 fallback: {e}")
    return out


def _format_viral_block(region: str) -> str:
    """组装 prompt 的 'P110 真爆款话术' 块(钩子 + 卖点 + CTA + 品类示例)。

    每次调用都重新抽样 → prompt 永远不重复 → LLM 学习多样化样本不会风格固化。
    """
    samples = _sample_viral_scripts(region)
    total = sum(len(v) for v in samples.values())
    if total == 0:
        # db 空或挂,只保留规则 hint
        return _VIRAL_FALLBACK_NOTE_CN if region == "CN" else _VIRAL_FALLBACK_NOTE_GLOBAL

    lines = []
    if region == "CN":
        lines.append(f"- ✅ 钩子模板(本次随机抽 {len(samples['hook'])} 条,逐字学习风格):")
        for txt, _ in samples["hook"]:
            lines.append(f'  • "{txt}"')
        lines.append("")
        lines.append(f"- ✅ 卖点 3 件套真句(数据+场景+对比),抽 {len(samples['selling'])} 条:")
        for txt, _ in samples["selling"]:
            lines.append(f'  • "{txt}"')
        lines.append("")
        lines.append(f"- ✅ CTA / 促单真句(紧迫感),抽 {len(samples['cta'])} 条:")
        for txt, _ in samples["cta"]:
            lines.append(f'  • "{txt}"')
        lines.append("")
        lines.append(f"- ✅ 多品类真爆款示例(逐字学习风格,不要改成通用),抽 {len(samples['example'])} 条:")
        for txt, cat in samples["example"]:
            cat_label = f"【{cat}】" if cat else ""
            lines.append(f'  {cat_label}"{txt}"')
    else:  # GLOBAL
        lines.append(f"- ✅ Hook templates (random {len(samples['hook'])} this run, learn the cadence verbatim):")
        for txt, _ in samples["hook"]:
            lines.append(f'  • "{txt}"')
        lines.append("")
        lines.append(f"- ✅ Real selling-point lines (data + scenario + contrast), {len(samples['selling'])} samples:")
        for txt, _ in samples["selling"]:
            lines.append(f'  • "{txt}"')
        lines.append("")
        lines.append(f"- ✅ Real CTA / urgency lines, {len(samples['cta'])} samples:")
        for txt, _ in samples["cta"]:
            lines.append(f'  • "{txt}"')
        lines.append("")
        lines.append(f"- ✅ Multi-category viral examples (verbatim style learning, do NOT generic-ify), {len(samples['example'])} samples:")
        for txt, cat in samples["example"]:
            cat_label = f"[{cat}] " if cat else ""
            lines.append(f'  {cat_label}"{txt}"')
    return "\n".join(lines)


# ============== 配置 ==============

# 默认模型 — 中文带货场景首选 Qwen3-VL,中文理解 + 中文输出双优
DEFAULT_MODEL = "qwen/qwen3-vl-235b-a22b-instruct"

# 备选模型(熔断时降级用)
FALLBACK_MODEL = "google/gemini-2.5-flash"

VISION_ENDPOINT = "openrouter/router/vision"


# ============== Prompt 模板 ==============

# P31:split_segments 跟 jobs.py 的逻辑保持一致(单源)
# total<=15 单段;否则每段 10s,total 整除 10
def split_segments(total_duration: int) -> list[int]:
    """把 total_duration 拆成 Seedance 单次能跑的段长(5/10/15s)。
    简化版:<=15 单段直接返;>15 每段 10s 整除。"""
    # P40 (2026-05-01):v1.5/pro 实测 duration 只接 4-12,>12 fal queue 静默死
    if total_duration <= 12:
        return [max(4, total_duration)]
    n = total_duration // 10
    rem = total_duration - n * 10
    segs = [10] * n
    if rem == 0:
        return segs
    if rem >= 4:
        segs.append(rem)
        return segs
    # rem 1-3:并到最后一段(上限 12)
    if segs[-1] + rem <= 12:
        segs[-1] += rem
        return segs
    # 极端:摊到前段(罕见,total 是 10 倍数走不到这分支)
    return segs


# P119(2026-05-05):5-12s 多镜头叙事 — VLM 输出 N 段 micro-scenes
# P121(2026-05-05):每段从 1.5-2s 拉长到 2.5-3s,N 段适当减少 — 解决 speech 字数太短
# 写不出爆款话术(英文 21 字符根本没法说话)。Seedance 跑 4s 然后 ffmpeg trim 到设计长。
def split_segments_micro(total_duration: int) -> list[float]:
    """P137(2026-05-05):用户敲"5 秒 1 段就够,10 秒 2 段(钩子+CTA),15 秒 3 段"。
    fal 后台不要再"2s 2s 出视频"(P121 拆碎了),改成每段固定 5 秒爆款 punch。

    设计:每段固定 5s 一个完整爆款 punch(钩子/卖点/CTA),
    段数 = ceil(total/5),最后一段吸收余数(3-7s 弹性)。
    """
    if total_duration <= 5:
        return [float(total_duration)]  # 5s = [5] 1 段
    if total_duration <= 7:
        return [float(total_duration)]  # 6-7s = 1 段(避免最后一段太短)
    if total_duration <= 10:
        return [5.0, float(total_duration - 5)]  # 8-10s = [5, 3-5]
    if total_duration <= 12:
        return [5.0, float(total_duration - 5)]  # 11-12s = [5, 6-7]
    if total_duration <= 15:
        return [5.0, 5.0, float(total_duration - 10)]  # 13-15s = [5, 5, 3-5]
    if total_duration <= 20:
        return [5.0, 5.0, 5.0, float(total_duration - 15)]  # 16-20s
    # 20s+ 走老 split_segments(>15s) 路径,这里不应进入
    n = (total_duration + 4) // 5
    base = [5.0] * (n - 1)
    base.append(float(total_duration - 5 * (n - 1)))
    return base


def _build_analysis_prompt(total_duration: int = 15, region: str = "CN") -> str:
    """P31:按 total_duration 动态生成 N 段分镜。
    每段独立可作为单次 Seedance 调用,共享 overall_setting + model_description
    锁角色场景(简版段间一致性)。
    P99:加 region 参数(CN=国内抖音风/亚洲模特/中文话术,Global=TikTok 风/西方模特/英文话术)
    """
    # P119(2026-05-05):5-12s 也用多镜头(split_segments_micro),>12s 走老逻辑
    if total_duration <= 12:
        seg_durs = split_segments_micro(total_duration)
        is_p119 = True
    else:
        seg_durs = split_segments(total_duration)
        is_p119 = False
    n = len(seg_durs)
    if n == 1:
        purpose_hint = "单段:开场 → 产品展示 → 促单 CTA 一气呵成"
    elif n == 2:
        purpose_hint = "镜头一开场+产品展示 / 镜头二促单 CTA"
    elif n == 3:
        purpose_hint = "镜头一开场吸引 / 镜头二产品展示 / 镜头三促单 CTA"
    else:
        purpose_hint = f"前 1 段开场,中间 {n-2} 段从不同角度展示卖点,最后 1 段促单 CTA"

    # 拼时间戳 + P112 speech 字数硬上限(elevenlabs 实测速率:CN 5 字/s, EN 14 字符/s)
    # P120(2026-05-05):5-12s 也按段独立 speech(每段独立 TTS,画外音 concat),
    # 不再"段 1 全说话其他段沉默"— 爆款抖音是每段不同话术连贯播
    char_per_sec = 5 if region == "CN" else 14
    char_unit = "字" if region == "CN" else "字符"
    total_max_chars = int(total_duration * char_per_sec)
    time_lines = []
    cum = 0.0
    for i, d in enumerate(seg_durs):
        max_chars = int(d * char_per_sec)
        time_lines.append(
            f"  - 镜头{i+1}({cum}-{cum+d}s,共 {d} 秒,**speech 严格 ≤ {max_chars} {char_unit}**,超字会被截断)"
        )
        cum += d

    # P120/P121 多镜头叙事强提示(P134:按 region 分语言示例,防 VLM 看中文示例输出中文)
    # P135(2026-05-05):后端架构说明 — 让 VLM 知道每段是独立 talking head,按爆款节奏定段数
    if is_p119:
        time_lines.insert(0, f"  ⚠️ **P121 爆款多镜头叙事**:总时长 {total_duration}s 拆成 {n} 个 2-3s 镜头,**每段画面+话术都不同**(钩子→卖点→对比→CTA)。**每段独立 speech**(全段加起来 ≤ {total_max_chars} {char_unit}),后端按段独立 TTS 后画外音 concat 出爆款主播节奏。**严禁段 2-N speech 留空** — 那样会沉默。")
        time_lines.insert(1, f"  📐 **P135 后端架构**:每段 speech → 独立 TTS → 独立 Kling Avatar talking head 视频 → 全部段 ffmpeg concat 完整拼接(不剪辑)。所以**每段是 1 个独立的爆款 punch**(钩子 / 卖点 / 对比 / CTA),按抽样的爆款 example 天然节奏写,段时长是参考(段时长 = 该段实际 audio 长度,Kling Avatar 自动匹配)。")
        # P121 爆款话术质量约束:每段 speech 必须含具体细节,不要"360 sculpting / zero squeeze" 这种通用空话
        if region == "CN":
            time_lines.insert(1, "  ⚠️ **每段 speech 必须包含至少 2 个**:[具体数字(2 寸/50 件/3 天/4 层)] / [具体场景(上班/坐下/聚会/穿衣搭配)] / [痛点反差(腰粗→收/勒→不勒/卷边→服帖)] / [具体动作(穿上/绑紧/转身/拉一下)]。")
            time_lines.insert(2, "  ⚠️ **严禁通用空话** — \"good quality / amazing / love it / so comfortable / 360 sculpting\" 这种无信息词全部禁用。要写真主播带货那种\"姐妹们我跟你说\"\"穿上立刻瘦 2 寸\"\"坐下都不卷边\"具体感受。")
        else:  # GLOBAL — 全英文示例,VLM 才会输出英文 speech
            time_lines.insert(1, "  ⚠️ **Each speech MUST include at least 2 of**: [specific numbers (2 inches/50 left/3 days/4 layers)] / [specific scene (work/sit down/party/styling)] / [pain contrast (saggy→snatched/tight→smooth/rolling→flat)] / [specific action (slip on/zip up/turn/tug)].")
            time_lines.insert(2, "  ⚠️ **NO generic filler** — banned: \"good quality / amazing / love it / so comfortable / 360 sculpting\". Write like a real TikTok creator: \"girl let me tell you\" \"snatches my waist instantly 2 inches\" \"sit down and it doesn't roll up\".")
            time_lines.insert(3, "  🚨 **CRITICAL — speech LANGUAGE = ENGLISH ONLY**. No Chinese characters allowed in any speech field. If you write any Chinese in speech, the entire output is invalid.")

    # P134:scenes 示例的 speech 也按 region 切语言(VLM 看示例学语言!)
    scenes_example_parts = []
    cum2 = 0.0
    if region == "CN":
        speech_examples = ['"开场钩子一句话"', '"卖点一句话"', '"CTA 紧迫感一句话"']
    else:
        speech_examples = ['"opening hook in English"', '"selling point in English"', '"CTA with urgency in English"']
    for i, d in enumerate(seg_durs[:3]):
        speech_eg = speech_examples[i] if i < len(speech_examples) else '"本段对应话术"'
        scenes_example_parts.append(
            f'      {{"id": {i+1}, "time_range": "{cum2}-{cum2+d}s", "purpose": "...", '
            f'"shot_language": "...", "content": "...", '
            f'"visual_prompt": "English prompt", "speech": {speech_eg}}}'
        )
        cum2 += d
    if n > 3:
        scenes_example_parts.append("      ... 共 {n} 段 ...".replace("{n}", str(n)))
    scenes_example = ",\n".join(scenes_example_parts)

    # P110: 从 db 实时抽样真爆款话术注入 prompt(每次抽不同样本,避免 LLM 风格固化)
    viral_block = _format_viral_block(region)

    # f-string 不支持 \n,所以 region 相关文本块在外面拼好再传进去
    # P214(2026-05-08):viral_model_desc 升级为 MUST 强约束(用户:选 CN 出来 caucasian)
    if region == "CN":
        viral_lang_label = "国内抖音爆款话术(speech 中文,model_description 亚洲面孔)"
        viral_model_desc = (
            "- ⚠️⚠️⚠️ MUST 铁律(region=CN):model_description **必须**以 'Asian woman' 开头,"
            "**必须**包含 'East Asian features' / 'natural yellow skin' / 'black or dark brown hair'。"
            "**严禁**写 caucasian / blonde / Western / European / red hair / blue eyes 等西方特征,"
            "否则后端会直接拒(P214 兜底)。\n"
            "- 模特特征示例:'Asian woman, East Asian features, natural yellow skin tone, "
            "straight black or dark brown shoulder-length hair, natural makeup, 22-30 yrs'"
        )
        viral_rules = (
            "- speech 中文爆款话术 4 条铁律:\n"
            "  ⭐ 每 2-3 秒一个 punch — 不要通用形容词(\"好/超棒/不错\"),要具体数字+具体动作+具体场景\n"
            "  ⭐ 钩子(开场 0-2s)必须勾人:提问/反差/痛点/悬念/直接结论\n"
            "  ⭐ 卖点必须 3 件套:【具体数据】+【场景化】+【对比/反差】\n"
            "  ⭐ CTA 必须紧迫感:数量限制/时间限制/价格优惠/不买后悔"
        )
        viral_bad_and_forbidden = (
            "- ⚠️ 反面例子(不要写这种):\n"
            "  ✗ \"这件束腰超级好看舒服,推荐大家购买\"(空洞/没数据/没场景)\n"
            "  ✗ \"Get that hourglass shape!\"(英文混杂或翻译腔)\n"
            "  ✗ \"买它买它买它\"(过时网梗,显得 LOW)\n"
            "  ✗ \"亲爱的家人们大家好欢迎来到我的直播间\"(开场太冗长无信息)\n"
            "  ✗ \"这款产品对我来说意义非凡,它的XX特性彻底征服了我\"(空洞文绉绉)\n"
            "\n"
            "- ❌ 严禁:绝对化用词(最好/第一/绝对/100%)、夸大医疗(瘦/治愈/丰胸效果)、明显违规"
        )
    else:  # GLOBAL
        viral_lang_label = "海外 TikTok 爆款话术(speech English, model_description Western/diverse)"
        viral_model_desc = (
            "- ⚠️⚠️⚠️ MUST RULE (region=Global): model_description **MUST** start with "
            "'Western woman' or 'Caucasian woman' or 'Black woman' or 'Latina woman'. "
            "**FORBIDDEN** to write 'Asian' / 'Chinese' / 'Korean' / 'Japanese' / "
            "'East Asian' / 'yellow skin' (region=Global)."
            " Backend will reject (P214) if Asian keywords appear. \n"
            "- Model traits example: 'Western/Caucasian/Black/Latina woman, natural skin, "
            "22-32 yrs, authentic UGC look, diverse Western features'"
        )
        viral_rules = (
            "- speech English TikTok 爆款话术 4 rules:\n"
            "  ⭐ Punch every 2-3 sec — no generic adjectives, use specific numbers + actions + scenes\n"
            "  ⭐ Hook (0-2s) MUST grab: question/contrast/pain/cliffhanger/direct conclusion\n"
            "  ⭐ Selling point = 3-piece: specific data + scenario + contrast\n"
            "  ⭐ CTA with urgency: limited stock/time/discount/regret-not-buying"
        )
        viral_bad_and_forbidden = (
            "- ⚠️ Bad examples (do NOT write):\n"
            "  ✗ \"This waist trainer is super cute and comfy, recommend!\" (empty/no data/no scene)\n"
            "  ✗ \"买它买它\" (Chinese mixed with English or translation-feel)\n"
            "  ✗ \"Hi everyone welcome to my video today I'm going to show you...\" (intro too long, no hook)\n"
            "  ✗ \"This product is amazing and I love it\" (zero specifics)\n"
            "\n"
            "- ❌ Forbidden: absolutes (best/first/100%), medical claims (lose weight/cure/enhance), violations"
        )

    return f"""你是抖音/TikTok 顶级带货达人,学习过上千条爆款脚本(单条带货过千万 GMV)。请分析用户上传的图片,完成两件事:

**输入图说明**(用户最多上传 1-2 张):
- 第 1 张:产品图(必有)— 重点识别品类/颜色/材质/卖点
- 第 2 张:背景场景图(可选,不一定有)— 这是用户期望的拍摄场景/使用环境/氛围,**写脚本时 overall_setting 必须基于第 2 张场景图来定**(光线/地点/氛围),speech 话术里也可以引用场景元素(比如"在这种场景下穿"),让脚本和场景对得上。如果没有第 2 张图,overall_setting 自由发挥。

【任务一:审核】
- 图片质量(清晰度/光线/白底)
- 是否有违规内容(侵权 logo / 违禁品 / 不雅内容 / 政治敏感)
- 识别产品品类、颜色、材质、目标人群

【任务二:生成 {total_duration} 秒带货视频脚本(共 {n} 段)】
拆成 {n} 段分镜,每段长度如下,**严格按这个段长输出 N 段 scene**:
{chr(10).join(time_lines)}

整体节奏:{purpose_hint}

**关键约束(P31 段间一致性)**:
1. overall_setting + model_description 是 N 段共享的锁定描述(模特长相、发型、肤色、服装风格、拍摄场景、灯光),N 段 visual_prompt 不要写跟它们冲突的内容
2. 每段 visual_prompt 只写"这段独有的"动作/构图/卖点,**不要重复写模特外貌或场景**(由 overall + model 锁住)
3. 每段 speech 是这段独立的口播台词,串起来要逻辑连贯

**P112 speech 字数硬约束(必须严格遵守,否则视频时长会跑飞)**:
- TTS 实测速率:中文 elevenlabs ≈ 5 字/秒, 英文 ≈ 14 字符/秒
- 每段 speech **必须** ≤ 上面"镜头时间戳"里标注的字数上限
- 超字 = 视频时长 > 用户选的总时长,体验严重错误
- 宁可 speech 短一点(留点呼吸感),也不要写满或超出

**P98 产品穿戴位置强制约束(否则 AI 默认贴胸口出错)**:
visual_prompt **必须**显式指明产品穿戴/使用位置(英文),按产品类型严格遵守:
- 胸罩/bra/内衣 → "worn on chest, properly covering the bust"
- 束腰/塑身衣/corset/shapewear/waist trainer → "worn around the waist/torso, NOT on chest"
- 内裤/panties/三角裤 → "worn on hips/lower body, NOT on chest"
- 袜子/socks/丝袜 → "worn on feet/legs, NOT on body"
- 项链/necklace → "worn around neck"
- 鞋/shoes → "worn on feet, NOT held in hand"
- 包/bag → "held in hand or on shoulder, NOT worn"
- 衣服/T 恤/dress → "worn on body, properly fitted"
- 帽子/hat → "worn on head"
不写位置 = AI 随机贴胸口 = 失败。**必须写**!示例:`"model wearing the waist trainer correctly around her waist, not on chest, smiling at camera, hands on hips"`

每段字段:shot_language(中文镜头语言) / content(中文场景内容) / visual_prompt(英文视频模型提示词,**必含穿戴位置**) / speech({"中文带货话术(国内抖音风)" if region == "CN" else "英文 TikTok 带货话术"})

**P113/P116 爆款镜头/动作公式(每段 visual_prompt + shot_language 必须组合 1-2 个公式,不要每段都"模特微笑站着"这种单调描述)**:

⚠️ **P116 重要约束:visual_prompt 严禁写驱动模特嘴/脸的指令**(因为这个 prompt 后续会喂给
talking head 模型,模型按指令驱动嘴脸 → 嘴张大、表情夸张失真)。
- ❌ 禁止:"shocked / concerned / surprised / amazed / open mouth / mouth open / smiling
  widely / yelling / screaming / dramatic expression / 惊讶 / 痛苦 / 大笑 / 张嘴" 等驱动
  嘴脸的描述
- ❌ 禁止:"face close-up / 大特写脸部 / extreme close-up of face" 让 talking head 模型
  把整张嘴放大
- ✅ 允许:**镜头运动 / 构图 / 字幕 / 道具 / 服装 / 颜色 / 灯光 / 产品特写 / 动作(走 / 转身 /
  弯腰 / 手势 / 推近 / 平移)** 这些不驱动嘴脸的元素

- 钩子镜头(0-1s,通常用在第 1 段):
  ⭐ 大特写产品 + 推近:`extreme macro close-up of the product itself (not the face), smooth camera push-in revealing texture / material / detail` + "产品大特写微距,镜头推近凸显材质"
  ⭐ 反差对比 split-screen:`split-screen contrast: left side wide shot of pain point context, right side same scene with product solving it` + "split 分屏对比,左痛点场景右解决方案"
  ⭐ 数字/痛点字幕硬冲:`bold text overlay flashing big number or shocking claim, like '23%↓ INSTANT' or '5 mins to fix', with neutral model B-roll behind` + "大字幕硬冲,加粗数字闪烁,模特中性 B-roll 在后"
  ⭐ 动作冲击:`model gestures throwing away old/competitor product (off-screen direction), then product reveal centered` + "甩开旧物(出框方向),新品居中亮出"

- 卖点镜头(中段,通常第 2-N-1 段):
  ⭐ Product macro 360°:`smooth camera orbit 360 around product, macro close-up showing material/texture/details, model body partial in background` + "镜头环绕产品 360°,微距特写材质,模特身体局部作背景"
  ⭐ 数字数据字幕悬浮:`floating text bubbles around model body (NOT face) showing data points like '+2 inches slimmer', '0 chemicals', '5x stronger'` + "数据气泡悬浮在模特身体周围(不挡脸),具体数字"
  ⭐ 用前/用后对比:`split-frame before/after using the product, with arrow or 'AFTER' label, body/waist focus` + "用前用后对比,带箭头或 AFTER 标签,聚焦身体不聚焦脸"
  ⭐ 模特动作演示:`model dynamic body movement (walking confidently, twirling, bending to show fit, hand gestures pointing at product), camera follows from chest down` + "模特动态身体动作(走台/转身/弯腰展贴合度/手势点产品),镜头从胸部以下跟随"
  ⭐ 用户证言镜头:`split-screen showing customer reviews / 5-star ratings / before-after photos popping in, no model close-up` + "客户评价/五星好评/对比图弹入,无模特脸部特写"

- CTA 镜头(末段,最后 1 段):
  ⭐ 字幕硬上 + 倒计时:`big text overlay 'LIMITED 50 LEFT' or 'TODAY ONLY' with red/yellow color and pulse animation, plus countdown timer ticking, model upper-body in background` + "大字幕硬上,红黄色脉冲,带倒计时,模特上半身在后景"
  ⭐ 模特看镜头 + 字幕同步:`model facing camera with relaxed natural look (NO exaggerated expression), finger pointing toward camera, with text overlay matching speech keywords` + "模特直视镜头自然放松(不要夸张表情),手指观众,字幕同步关键词"
  ⭐ 产品居中 + 加购按钮特写:`product centered on screen, animated 'ADD TO CART' or 'SHOP NOW' button pulsing below, price tag flashing, model in soft-blur background` + "产品居中,加购按钮脉冲动画,价签闪烁,模特软虚化在后景"

⚠️ 反面例子(不要写这种 visual_prompt):
- ✗ "model wearing the waist trainer, smiling at camera, standing in studio"(只有静态站姿,看一眼就划走)
- ✗ "extreme close-up of model's face shocked expression"(P116 严禁:talking head 会按"shocked"驱动嘴张大失真)
- ✗ "model with open mouth / dramatic facial expression"(同上,驱动嘴脸夸张)
✅ 正例:"smooth camera orbit 360 around the waist trainer, macro close-up of fabric texture, with bold '−2 inches' text flashing in red, model upper-body softly visible in background"

**P110 抖音/TikTok 真爆款话术(从 db 实时抽样,每次不同样本,库可无限扩展)**:
{viral_lang_label}

{viral_model_desc}

{viral_rules}

{viral_block}

{viral_bad_and_forbidden}

【输出格式】
严格按以下 JSON 返回,不要任何 markdown 标记或额外说明:

{{
  "audit": {{
    "is_valid": true,
    "category": "产品品类",
    "color": "主色",
    "material": "材质",
    "quality_score": 8.5,
    "issues": [],
    "violations": [],
    "target_audience": "目标人群"
  }},
  "script": {{
    "overall_setting": "整体设定(N 段共享的拍摄风格/场景/灯光,中文)",
    "model_description": "N 段共享的模特特征(年龄/发型/肤色/服装风格,英文,给视频模型锁角色)",
    "scenes": [
{scenes_example}
    ]
  }}
}}

如果图片有严重违规(色情 / 暴力 / 政治敏感),audit.is_valid 设为 false,violations 列出原因,scene 数组返回空 []。

不要输出 ```json 或任何 markdown 标记,直接输出纯 JSON。**scenes 数组必须正好 {n} 段**。"""


# 向后兼容:模块加载时构造 15s 默认 prompt
_ANALYSIS_PROMPT = _build_analysis_prompt(15)


_SYSTEM_PROMPT = (
    "You are a JSON-only API. Output strict valid JSON without any markdown "
    "fences, prose, or explanation. The JSON must match the schema requested in the user prompt. "
    # P136(2026-05-05):防"几次脚本都一样" — 强约束每次输出不同 hook 风格
    "IMPORTANT — VARIETY: Each generation MUST use a DIFFERENT hook style and word choice "
    "from previous runs. Pick from {question / contrast / pain / cliffhanger / direct conclusion / "
    "shocking number / before-after / personal story / category-comparison} and rotate. "
    "Even with the same product image, the speech content must feel fresh and varied across runs."
)


class VLMService:
    """VLM 视觉服务 - 单例,通过 fal OpenRouter 端点调用"""

    SERVICE_KEY = "fal/openrouter-vision"  # 熔断器 key

    def __init__(self):
        # 不需要单独 key,fal_client 会从环境变量 FAL_KEY 读
        pass

    async def analyze_product(
        self,
        image_url: str,
        model: Optional[str] = None,
        total_duration: int = 15,
        region: str = "CN",
        background_image_url: Optional[str] = None,
    ) -> dict:
        """
        分析产品图 + 生成脚本

        参数:
            image_url: 产品图的 fal storage URL
            model: 可选,指定 VLM 模型;默认走 DEFAULT_MODEL
            total_duration: P31 总时长(秒),默认 15 维持向后兼容。
                            > 15 时 VLM 会按 split_segments(total) 出 N 段 scenes。
            background_image_url: P111 可选 — 背景场景图 URL,VLM 用第 2 张图
                            决定 overall_setting / speech 话术情境,让脚本和用户期望
                            的拍摄场景对得上(否则脚本完全脱离用户上传的场景图)

        返回:
            {"audit": {...}, "script": {...}}  成功
            {"error": "..."}                    失败
        """
        circuit_breaker = get_circuit_breaker()
        if not circuit_breaker.is_available(self.SERVICE_KEY):
            return {"error": "VLM 视觉服务暂时不可用,请稍后再试"}

        chosen_model = model or DEFAULT_MODEL
        prompt = _build_analysis_prompt(total_duration, region=region)

        # P111: 拼图列表(产品图必有 + 背景图可选作为第 2 张)
        image_urls = [image_url]
        if background_image_url:
            image_urls.append(background_image_url)

        # 调用 fal OpenRouter Vision
        try:
            result = await fal_client.run_async(
                VISION_ENDPOINT,
                arguments={
                    "image_urls": image_urls,
                    "prompt": prompt,
                    "system_prompt": _SYSTEM_PROMPT,
                    "model": chosen_model,
                    # P136(2026-05-05):加 temperature 让每次输出更随机,防"几次脚本都一样"
                    "temperature": 0.9,
                },
            )
        except Exception as e:
            await circuit_breaker.record_failure(self.SERVICE_KEY)
            log_error(f"VLM 调用失败 (model={chosen_model}): {e}")
            # 主模型失败时尝试降级模型一次
            if chosen_model != FALLBACK_MODEL:
                log_info(f"尝试降级到 {FALLBACK_MODEL}")
                try:
                    result = await fal_client.run_async(
                        VISION_ENDPOINT,
                        arguments={
                            "image_urls": image_urls,
                            "prompt": prompt,
                            "system_prompt": _SYSTEM_PROMPT,
                            "model": FALLBACK_MODEL,
                        },
                    )
                except Exception as e2:
                    return {"error": f"VLM 主备模型均失败: {str(e2)[:200]}"}
            else:
                return {"error": f"VLM 调用失败: {str(e)[:200]}"}

        # 解析响应
        text = result.get("output", "")
        if not text:
            await circuit_breaker.record_failure(self.SERVICE_KEY)
            return {"error": "VLM 返回为空"}

        try:
            # 去 markdown 标记(以防模型不听话)
            cleaned = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                text.strip(),
                flags=re.MULTILINE,
            )
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            await circuit_breaker.record_failure(self.SERVICE_KEY)
            log_error(f"VLM 响应解析失败: {e}, 原文前 500 字: {text[:500]}")
            return {"error": "AI 输出格式异常,请重试"}

        # 校验结构
        if "audit" not in data or "script" not in data:
            await circuit_breaker.record_failure(self.SERVICE_KEY)
            return {"error": "AI 输出结构不完整,请重试"}

        await circuit_breaker.record_success(self.SERVICE_KEY)
        log_info(
            f"VLM 分析完成: model={chosen_model} "
            f"category={data['audit'].get('category')} "
            f"valid={data['audit'].get('is_valid')}"
        )
        return data

    async def regenerate_scene(
        self,
        original_scene: dict,
        instruction: str,
        model: Optional[str] = None,
    ) -> dict:
        """
        重新生成单个分镜(用户在编辑器里点'重新生成此镜头'时调用)

        这个不需要图片,纯文本对话。但还是走 OpenRouter Vision 端点(它也兼容纯文本)。
        实际上 fal 还有个纯文本的 openrouter/router 端点,但为了简化代码我们都走同一个。

        参数:
            original_scene: 原 scene dict
            instruction: 用户给的修改指令(中文)
            model: 可选

        返回:
            新 scene dict 或 {"error": "..."}
        """
        circuit_breaker = get_circuit_breaker()
        if not circuit_breaker.is_available(self.SERVICE_KEY):
            return {"error": "VLM 服务暂时不可用"}

        chosen_model = model or DEFAULT_MODEL

        prompt = f"""根据用户指令修改以下分镜,严格按原 JSON 格式输出(不要 markdown,直接输出纯 JSON):

原分镜:
{json.dumps(original_scene, ensure_ascii=False, indent=2)}

用户修改指令:
{instruction}

输出修改后的 JSON,字段保持一致(id / time_range / purpose / shot_language / content / visual_prompt / speech)。"""

        try:
            # 即便没图片,这个端点也要 image_urls 字段。
            # 我们传一个 1x1 透明占位图(fal 自家 CDN)规避 schema 校验。
            # 模型会忽略这张图,只看 prompt。
            placeholder = "https://fal.media/files/placeholder/blank-1x1.png"

            result = await fal_client.run_async(
                VISION_ENDPOINT,
                arguments={
                    "image_urls": [placeholder],
                    "prompt": prompt,
                    "system_prompt": _SYSTEM_PROMPT,
                    "model": chosen_model,
                },
            )
            text = result.get("output", "")
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
            new_scene = json.loads(cleaned)
            await circuit_breaker.record_success(self.SERVICE_KEY)
            return new_scene
        except Exception as e:
            await circuit_breaker.record_failure(self.SERVICE_KEY)
            return {"error": f"重新生成失败: {str(e)[:200]}"}


# ============== 单例 ==============

_vlm_service: Optional[VLMService] = None


def init_vlm_service():
    """在 main.py 启动时调用(无参,从 fal_client 拿 FAL_KEY)"""
    global _vlm_service
    _vlm_service = VLMService()
    log_info(f"VLM 视觉服务已初始化(默认模型: {DEFAULT_MODEL},端点: {VISION_ENDPOINT})")


def get_vlm_service() -> Optional[VLMService]:
    return _vlm_service
