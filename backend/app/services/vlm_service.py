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
import re
from typing import Optional
import fal_client

from .circuit_breaker import get_circuit_breaker
from .logger import log_info, log_error


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


def _build_analysis_prompt(total_duration: int = 15, region: str = "CN") -> str:
    """P31:按 total_duration 动态生成 N 段分镜。
    每段独立可作为单次 Seedance 调用,共享 overall_setting + model_description
    锁角色场景(简版段间一致性)。
    P99:加 region 参数(CN=国内抖音风/亚洲模特/中文话术,Global=TikTok 风/西方模特/英文话术)
    """
    seg_durs = split_segments(total_duration)
    n = len(seg_durs)
    if n == 1:
        purpose_hint = "单段:开场 → 产品展示 → 促单 CTA 一气呵成"
    elif n == 2:
        purpose_hint = "镜头一开场+产品展示 / 镜头二促单 CTA"
    elif n == 3:
        purpose_hint = "镜头一开场吸引 / 镜头二产品展示 / 镜头三促单 CTA"
    else:
        purpose_hint = f"前 1 段开场,中间 {n-2} 段从不同角度展示卖点,最后 1 段促单 CTA"

    # 拼时间戳
    time_lines = []
    cum = 0
    for i, d in enumerate(seg_durs):
        time_lines.append(f"  - 镜头{i+1}({cum}-{cum+d}s,共 {d} 秒)")
        cum += d

    scenes_example_parts = []
    cum2 = 0
    for i, d in enumerate(seg_durs[:3]):  # 仅展示前 3 段示例
        scenes_example_parts.append(
            f'      {{"id": {i+1}, "time_range": "{cum2}-{cum2+d}s", "purpose": "...", '
            f'"shot_language": "...", "content": "...", '
            f'"visual_prompt": "English prompt", "speech": "English speech"}}'
        )
        cum2 += d
    if n > 3:
        scenes_example_parts.append("      ... 共 {n} 段 ...".replace("{n}", str(n)))
    scenes_example = ",\n".join(scenes_example_parts)

    return f"""你是抖音/TikTok 顶级带货达人,学习过上千条爆款脚本(单条带货过千万 GMV)。请分析用户上传的产品图,完成两件事:

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

**P99+P106 抖音/TikTok 真爆款带货话术学习({"国内抖音(中文)" if region == "CN" else "海外 TikTok(英文)"})**:
{"国内抖音爆款话术(speech 中文,model_description 亚洲面孔)" if region == "CN" else "海外 TikTok 爆款话术(speech 英文,model_description 西方/多元)"}:
{'''
- 模特特征:亚洲面孔(东方五官)/ 自然黄皮肤 / 黑色或棕黑色头发 / 真实素颜或淡妆 / 22-30 岁
- speech 中文爆款话术铁律:
  ⭐ **每 2-3 秒一个 punch** — 不要通用形容词("好/超棒/不错"),要具体数字+具体动作+具体场景
  ⭐ **钩子(开场 0-2s)必须勾人**:疑问/反差/痛点/悬念/直接结论
  ⭐ **卖点必须 3 件套**:【具体数据】+【场景化】+【对比/反差】
  ⭐ **CTA 必须紧迫感**:数量限制/时间限制/价格优惠/不买后悔

- ✅ 真爆款示例(原句逐字学习,不要改成通用):

  【塑身/束腰类】
  "宝子们我跟你们说,这个束腰真的!颠覆了我对塑身的认知!穿一天腰小三公分,关键是不勒不卡,我办公一天都没感觉,链接姐妹们抢吧,卖完没有了!"

  "你们是不是穿别的束腰勒一晚上印子第二天还在?这个真的!整整 8 小时不勒不卷边,我老公都说看不出来我穿了!直播间下单送两件,先到先得!"

  【内衣/胸罩类】
  "胸大姐妹的福音终于来了!穿了二十多年内衣,这是第一件没勒红肩膀的,深 V 还显小,我现在天天就穿这一件,直播间下单立减 30,先到先得啊!"

  "宝妈们听我一句!喂奶之后胸下垂的这条收!这件软钢圈一穿提升 5 厘米,弯腰也不掉,薄到夏天透气,我闺蜜抢了 5 件!"

  【裤子/牛仔类】
  "我这条裤子穿过三十多种,最后只回购了这一条,腿粗的、屁股大的、平胯的,统统能驾驭!神奇的是它居然透气,夏天穿不闷,链接挂上面,真的不亏!"

  "姐妹们直接看上身!158 苹果型穿出 1m65 的腿!关键这弹力是真的绝,蹲下不开线!天猫旗舰店现在才 89,买亏算我的!"

  【短款/上衣类】
  "姐妹们看这版型!今年最显瘦的就是这种短款,我 130 斤穿出 105 斤的效果,关键它真的不显肚子,谁穿谁好看!现在直播间还能再减 20!"

  "天呐这个版型!微胖姐妹必入!腰带一系直接显腰精,胃凸肚腩通通看不见!纯棉透气,夏天穿不闷,69 块买不了吃亏!"

  【美妆/口红类】
  "姐妹们这支口红绝绝子!黄黑皮亲妈色!涂上去显白显气色,关键持久 8 小时,我吃饭喝水都不掉色!淘宝旗舰店买二送一,冲冲冲!"

  【家居/收纳类】
  "宝子们我家这个抽屉简直外星科技!以前打开找袜子翻箱倒柜,现在 1 秒拿到!分隔灵活,毛衣袜子统统能装,直播间满 2 件免邮!"

- ⚠️ 反面例子(不要写这种):
  ✗ "这件束腰超级好看舒服,推荐大家购买"(空洞/没数据/没场景)
  ✗ "Get that hourglass shape!"(英文混杂或者翻译腔)
  ✗ "买它买它买它"(过时网梗,显得 LOW)

- ❌ 严禁:绝对化用词(最好/第一/绝对/100%)、夸大医疗(瘦/治愈/丰胸效果)、明显违规
''' if region == "CN" else '''
- 模特特征:Western/Caucasian/Black/Latina/diverse / 自然肤色 / 22-32 岁 / 真实 UGC
- speech 英文 TikTok 爆款话术铁律:
  ⭐ **每 2-3 秒一个punch** — no generic adjectives, use specific numbers + actions + scenes
  ⭐ **Hook (0-2s) MUST grab**: question/contrast/pain/cliffhanger/direct conclusion
  ⭐ **Selling point** = 3-piece: specific data + scenario + contrast
  ⭐ **CTA** with urgency: limited stock/time/discount/regret-not-buying

- ✅ Real viral examples (learn verbatim, do NOT generic-ify):

  [Waist trainer]
  "Babe I am telling you — this waist trainer literally CHANGED my life. I lost 3 inches off my waist in a week, sitting at my desk all day, no chafing, no marks. Link in bio, RUN — they sold out twice already!"

  "POV you are tired of waist trainers leaving marks all day. THIS one — 8 hours straight, NO marks, NO rolling. My husband cant tell I am wearing one. Link in bio, run!"

  [Bra]
  "If you have big boobs and gaping bras have ruined your life — this is THE one. No straps digging, no spillage, deep V looks insane on, I literally wear this every day now. $30 off in my bio, dont sleep!"

  "Moms after breastfeeding this is the ONE. Soft underwire lifts 2 inches, no slipping when you bend, breathable for summer. My friend grabbed 5 of them, link!"

  [Shorts/pants]
  "I have tried 30+ pairs of biker shorts and only re-bought THESE. They work for thick thighs, big bum, no whale tail. Breathable in summer, linked, you wont regret!"

  "5 ft 2 apple shape over here looking like 5 ft 5 in these jeans. Stretch is INSANE, can squat, no rip. $89 only on tmall now, take my word for it!"

  [Top/crop]
  "Look at this fit girl! The most slimming crop top of the year, I am 130 lbs and look like 105 in this, somehow zero tummy, EVERYBODY looks good in it. $20 off live now!"

  [Beauty/lipstick]
  "Babe THIS lipstick — yellow undertone friendly, 8 hour wear, you can EAT and DRINK and it stays. Buy 2 get 1 free in my bio, run!"

- ⚠️ Bad examples (do NOT write):
  ✗ "This waist trainer is super cute and comfy, recommend!" (empty/no data/no scene)
  ✗ "买它买它"(Chinese mixed with English or translation-feel)

- ❌ Forbidden: absolutes (best/first/100%), medical claims (lose weight/cure/enhance), violations
'''}

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
    "fences, prose, or explanation. The JSON must match the schema requested in the user prompt."
)


# 八十四续 P7:严禁敏感词 — 旧示例含"年轻女性 + 身穿"导致 fal kling 100%
# content_policy_violation,改为商品摄影中性描述。
_QUICK_PROMPT_INSTRUCTION = """你是商品摄影视频提示词专家。看产品图,生成一个**专业商品摄影视频提示词**,150 字以内。

要求:
1. 只描述产品本身 + 摄影场景 + 镜头运动 + 灯光,**不描述模特/人物**
2. 风格定位:工作室商品摄影 / 户外生活方式 / 极简白底 / 高端质感 等
3. 中文输出,可混入英文摄影术语(如 "close-up shot" / "natural lighting" / "product showcase" / "smooth camera push-in")

【绝对禁止】(违反会被内容审核拒绝,任务失败):
- 不写人物年龄、外貌、身材、性别(如"年轻女性"、"25-30岁"、"身材匀称"、"长发")
- 不写身体部位描述(腰部、臀部、胸部、腿部)
- 不写营销词与身体词组合(塑形、收腹、提臀、紧身、性感、贴身)
- 不写"突出 X 卖点"+ 身体词

直接输出提示词正文,**不要任何解释、引号、markdown 标记**。

示例输出格式:
产品工作室商品摄影,纯白背景,自然柔光,close-up shot 展示产品细节与材质,smooth camera push-in 强化质感,商业广告风格,产品色彩饱和度高,边缘锐利清晰,无文字水印
"""

_QUICK_PROMPT_SYSTEM = (
    "You output a single line plain-text prompt for a video generation model. "
    "No JSON, no markdown, no quotes, no preamble. Just the prompt body in Chinese with English term mixins."
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
    ) -> dict:
        """
        分析产品图 + 生成脚本

        参数:
            image_url: 产品图的 fal storage URL
            model: 可选,指定 VLM 模型;默认走 DEFAULT_MODEL
            total_duration: P31 总时长(秒),默认 15 维持向后兼容。
                            > 15 时 VLM 会按 split_segments(total) 出 N 段 scenes。

        返回:
            {"audit": {...}, "script": {...}}  成功
            {"error": "..."}                    失败
        """
        circuit_breaker = get_circuit_breaker()
        if not circuit_breaker.is_available(self.SERVICE_KEY):
            return {"error": "VLM 视觉服务暂时不可用,请稍后再试"}

        chosen_model = model or DEFAULT_MODEL
        prompt = _build_analysis_prompt(total_duration, region=region)

        # 调用 fal OpenRouter Vision
        try:
            result = await fal_client.run_async(
                VISION_ENDPOINT,
                arguments={
                    "image_urls": [image_url],
                    "prompt": prompt,
                    "system_prompt": _SYSTEM_PROMPT,
                    "model": chosen_model,
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
                            "image_urls": [image_url],
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

    async def generate_quick_prompt(
        self,
        image_url: str,
        model: Optional[str] = None,
    ) -> dict:
        """七十续:简化版 — 看产品图直接吐一个完整带货视频提示词字符串。

        相比 analyze_product 的 4 步重流程(audit + 3 镜头脚本),这个返一个
        即用即改的 prompt,用户可在前端 textarea 里编辑,然后送给视频生成模型。

        返回:
            {"prompt": "..."} 成功
            {"error": "..."} 失败
        """
        circuit_breaker = get_circuit_breaker()
        if not circuit_breaker.is_available(self.SERVICE_KEY):
            return {"error": "VLM 视觉服务暂时不可用,请稍后再试"}

        chosen_model = model or DEFAULT_MODEL

        try:
            result = await fal_client.run_async(
                VISION_ENDPOINT,
                arguments={
                    "image_urls": [image_url],
                    "prompt": _QUICK_PROMPT_INSTRUCTION,
                    "system_prompt": _QUICK_PROMPT_SYSTEM,
                    "model": chosen_model,
                },
            )
        except Exception as e:
            await circuit_breaker.record_failure(self.SERVICE_KEY)
            log_error(f"VLM quick-prompt 失败 (model={chosen_model}): {e}")
            if chosen_model != FALLBACK_MODEL:
                try:
                    result = await fal_client.run_async(
                        VISION_ENDPOINT,
                        arguments={
                            "image_urls": [image_url],
                            "prompt": _QUICK_PROMPT_INSTRUCTION,
                            "system_prompt": _QUICK_PROMPT_SYSTEM,
                            "model": FALLBACK_MODEL,
                        },
                    )
                except Exception as e2:
                    return {"error": f"VLM 主备模型均失败: {str(e2)[:200]}"}
            else:
                return {"error": f"VLM 调用失败: {str(e)[:200]}"}

        text = (result.get("output") or "").strip()
        if not text:
            await circuit_breaker.record_failure(self.SERVICE_KEY)
            return {"error": "VLM 返回为空"}

        # 模型偶尔加引号 / 多余前缀,简单清洗
        cleaned = text.strip().strip('"').strip("'")
        # 去 markdown / 多余换行
        cleaned = re.sub(r"^```.*?\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
        cleaned = cleaned.strip()

        # 限长(保护:VLM 偶尔超 prompt 限制返大段)
        if len(cleaned) > 500:
            cleaned = cleaned[:500].rstrip("。.,, ") + "..."

        await circuit_breaker.record_success(self.SERVICE_KEY)
        log_info(f"VLM quick-prompt 完成: model={chosen_model} len={len(cleaned)}")
        return {"prompt": cleaned}

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
