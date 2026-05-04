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

**P109 抖音/TikTok 真爆款带货话术大全({"国内抖音(中文)" if region == "CN" else "海外 TikTok(英文)"})**:
{"国内抖音爆款话术(speech 中文,model_description 亚洲面孔)" if region == "CN" else "海外 TikTok 爆款话术(speech 英文,model_description 西方/多元)"}:
{'''
- 模特特征:亚洲面孔(东方五官)/ 自然黄皮肤 / 黑色或棕黑色头发 / 真实素颜或淡妆 / 22-30 岁

- speech 中文爆款话术 4 条铁律:
  ⭐ **每 2-3 秒一个 punch** — 不要通用形容词("好/超棒/不错"),要具体数字+具体动作+具体场景
  ⭐ **钩子(开场 0-2s)必须勾人**:提问/反差/痛点/悬念/直接结论
  ⭐ **卖点必须 3 件套**:【具体数据】+【场景化】+【对比/反差】
  ⭐ **CTA 必须紧迫感**:数量限制/时间限制/价格优惠/不买后悔

- ✅ 钩子模板 30+ 条(真爆款抖音/直播间扒来,逐字学习风格):
  · 提问钩 ─
  • "如何才能穿的显高?"
  • "你知道为什么你容易长胖吗?"
  • "你有没有发现:十元的快餐,只有男人在吃?"
  • "你是不是也这样,有微信没人聊?"
  · 警告/反差钩 ─
  • "都夏天了怎么还穿这种裤子跑步?"
  • "千万不要相信任何生发产品,否则就没人会花 20 万去种头发"
  • "这4种玩具千万不要买,尤其是第2个"
  • "现在,把家里的黑垃圾袋都扔了吧"
  • "听我说,这才是 XX 的核心真相!"
  · 悬念钩 ─
  • "这个视频可能颠覆你之前所有的认知"
  • "你敢相信吗?3 句话就能拐走一个大学生!"
  • "宝子们我跟你们说,这个真的!颠覆了我对 XX 的认知!"
  · 干货钩 ─
  • "1 个合格妈妈,必须要秒懂的 3 种孩子饿的信号"
  • "客厅装修的 12 条避坑细节,一定不要踩!"
  • "准到吓人的 7 条识人术,你一定要知道"
  · 直播留人钩 ─
  • "进来的朋友们不要走,马上进入抽奖环节"
  • "今天进我直播间的都瘦三斤"
  • "欢迎 xx 的女人们回家"
  • "新进直播间的朋友们,左上角帮主播点点关注!点点关注大家不迷路"
  • "新进的姐妹左上角点关注,马上有福利"
  • "凡是在直播间呆超过 20 分钟的新人朋友,待会我会抽取两位送惊喜礼品"
  · 直接展示钩 ─
  • "姐妹们直接看上身!"
  • "宝妈们听我一句!"
  • "胸大姐妹的福音终于来了!"
  · 福利/价格钩 ─
  • "想要 1499 的茅台,在公屏上打 1499"
  • "新进来的宝贝左上角的福袋还有 X 分钟就要开抢了"
  • "进来的人不要走,主播要给大家发红包了"
  • "话不多说,咱们先来抽波奖"
  · 痛点共鸣钩 ─
  • "你们是不是穿别的束腰勒一晚上印子第二天还在?"
  • "宝妈姐妹们,喂奶之后胸下垂的这条收!"
  • "宝子们我家这个抽屉简直外星科技!以前打开找袜子翻箱倒柜……"

- ✅ 卖点 3 件套真句(数据+场景+对比),20 条:
  • "这条裤子穿过三十多种,最后只回购了这一条,腿粗的、屁股大的、平胯的,统统能驾驭"
  • "穿一天腰小三公分,关键是不勒不卡,我办公一天都没感觉"
  • "整整 8 小时不勒不卷边,我老公都说看不出来我穿了"
  • "穿了二十多年内衣,这是第一件没勒红肩膀的"
  • "软钢圈一穿提升 5 厘米,弯腰也不掉,薄到夏天透气"
  • "我 130 斤穿出 105 斤的效果,关键它真的不显肚子"
  • "158 苹果型穿出 1m65 的腿,蹲下不开线"
  • "纹眉的功能是让你不用自己画眉毛,早上可以多睡十分钟,素颜也很有气色"
  • "你喷的香水就是别人眼中你的味道"
  • "精致大气的女人一定要拥有一件羊绒大衣"
  • "你买的是家人的健康和安心,你买的是对家人的关爱与保护"
  • "里面是 40 级加厚衬、3D 立体剪裁的收腰设计,衬托你的腰就是水蛇腰"
  • "荔枝纹的看起来就很高级,摸上去能感觉到它的纹路质感"
  • "我家有一只同样材质的包包就背了好几年,越背越喜欢"
  • "夏天穿这件防晒衣,轻薄得像没穿一样!"
  • "我们家是仓库直播,是供应链的源头,不经过任何中间周转"
  • "早上来杯有粉,暖暖的很舒服,整个上午都不带饿的"
  • "0 添加防腐剂,孩子吃也放心"
  • "通勤地铁噪音直接消失,续航 30 小时"
  • "负离子风一吹头发顺到反光"

- ✅ CTA / 促单真句(紧迫感)20 条:
  • "正常价格要 99 米,今天只要 29.9 米,不到一杯奶茶价格"
  • "一份泡面的钱/一顿外卖的钱/一杯奶茶的钱,就可以吃到双人餐"
  • "某宝 399 米,今天在我直播间,直接 299!带回家"
  • "专柜价近三千,直播间给一个惊喜价!只要 899"
  • "门店价 150 块,今天只需 75 块,平时玩一次的价格"
  • "只准备了 30 件,放完为止"
  • "就 500 份,抢完就没有了,上次 30 秒不到就没了"
  • "明天有没有,明天没有;下周有没有,下周没有;什么时候再有,不知道"
  • "买了 10 万件的爆款,库存不多了"
  • "我们最后 1 分钟就要下播了,这个价格下播就没了"
  • "买二送一,冲冲冲!"
  • "链接挂上面了,姐妹冲!"
  • "先到先得,手慢就没了"
  • "10 秒钟准备就上车,10、9、8、7、6、5、4、3、2、1"
  • "还剩最后的 50 单了,拼手速了"
  • "前 50 名下单的朋友享受折扣和额外赠品"
  • "前 100 名下单享受九折优惠"
  • "1 分钱的 xxx,抢到就是赚到"
  • "四个九块九,连个运费都不要,放 50 单"
  • "7 天无理由退换货,运费险我出"

- ✅ 多品类真爆款示例 25+ 类(逐字学习风格,不要改成通用):

  【塑身/束腰】"宝子们我跟你们说,这个束腰真的!颠覆了我对塑身的认知!穿一天腰小三公分,不勒不卡,我办公一天都没感觉,链接姐妹们抢吧,卖完没有了!"

  【内衣/胸罩】"胸大姐妹的福音终于来了!穿了二十多年内衣,这是第一件没勒红肩膀的,深 V 还显小,我现在天天就穿这一件,直播间下单立减 30!"

  【内衣/哺乳】"宝妈们听我一句!喂奶之后胸下垂的这条收!软钢圈一穿提升 5 厘米,弯腰也不掉,我闺蜜抢了 5 件!"

  【裤子/牛仔】"姐妹们直接看上身!158 苹果型穿出 1m65 的腿!弹力是真的绝,蹲下不开线!天猫旗舰店现在才 89!"

  【短款/上衣】"姐妹们看这版型!最显瘦的就是这种短款,我 130 斤穿出 105 斤的效果,关键不显肚子,谁穿谁好看!"

  【连衣裙】"各位姐妹看一下这款水雾蓝的设计款连衣裙,40 级加厚衬、3D 立体剪裁的收腰,衬托你的腰就是水蛇腰,闭眼下单!"

  【外套/羽绒】"零下 20 度都不冷的羽绒服!90 白鸭绒充绒量 200,机洗不变形,我妈穿了三年还跟新的一样!直播间立减 200!"

  【T恤/打底】"姐妹们这件打底我囤了 5 件!冰丝凉感不黏身,薄到能塞口袋,洗 30 次不变形,夏天通勤神器!"

  【羊绒大衣】"精致大气的女人一定要拥有一件羊绒大衣!90% 山羊绒,垂坠有版型,我穿了 3 年没起球,专柜 5800 直播 1980!"

  【鞋】"姐妹们这双鞋绝了!久站一天脚不痛不肿,内里软到像踩棉花,我做销售站 12 小时回家不用泡脚!满 199 减 50!"

  【箱包】"姐妹们看过来,我手里这款包,荔枝纹一看就高级,我家有一只同款背了好几年,越背越喜欢,专柜 1280 直播 299!"

  【珠宝/首饰】"这条 18K 金项链是闺蜜结婚送我的同款,锁骨位刚好,洗澡睡觉都不用摘,带几年也不褪色,直播价比专柜便宜一半!"

  【手表】"上班的姐妹这块手表必须收!陶瓷表带亲肤不夹毛,夜光表盘加班看时间不用开灯,质保 3 年!"

  【美妆/口红】"姐妹们这支口红绝绝子!黄黑皮亲妈色!涂上去显白显气色,持久 8 小时,吃饭喝水都不掉色!买二送一!"

  【美妆/护肤】"姐妹们干皮换季烂脸的看这条!这瓶精华我用了 28 天,毛孔小一半、上妆服帖到爆,素颜出门都不慌!送同款面膜 5 片!"

  【美妆/底妆】"姐妹们这粉底液太懂我了!油皮 12 小时不脱妆不浮粉,持妆能扛一整天会议,直播间专享 6 折!"

  【美容仪】"姐妹们这台美容仪用 21 天我表妹问我去医美了!射频提拉法令纹真的浅了,医美级在家做,直播立减 800!"

  【母婴/奶瓶】"宝妈们这款奶瓶我家娃从两个月用到现在,不胀气不溢奶,半夜泡奶不用醒,我老公都夸!立减 50 送奶嘴!"

  【母婴/玩具】"宝妈姐妹这个绘画机器人陪我家娃画画两小时,我终于能喝杯热咖啡了!教 200 多个步骤,3-8 岁通用,直播 199!"

  【母婴/纸尿裤】"宝妈来听!这款纸尿裤吸水性是平时的 2 倍,我家娃睡整夜不漏不返渗,腰间无勒痕,直播 1 包送 1 包!"

  【食品/零食】"姐妹们这袋我抢了三轮才抢到!外面酥里面爆汁,一袋一晚干掉,0 添加防腐剂,孩子吃也放心!9.9 抢一袋!"

  【食品/水果】"福建桃子甜到爆汁的来了!果园直发,树上熟才摘,我一口气吃了 5 个,孕妈也能放心吃,5 斤装才 39.9!"

  【食品/茶】"凤凰单丛属于乌龙茶里的一种,我们家是茶园直发,支持到货试喝三泡,不喜欢可以退换,这款蜜兰香买一送一!"

  【酒】"60 度纯粮老酒,陶坛窖藏 5 年,送礼倍有面子,我家老爸喝了直夸,5 斤装专享价 198 还包邮!"

  【家居/收纳】"宝子们我家这个抽屉简直外星科技!打开找袜子翻箱倒柜的日子结束了,1 秒拿到!满 2 件免邮!"

  【家居/床品】"姐妹这套四件套真的舒服!100% 长绒棉,睡上去像被云包裹,洗 50 次不起球不掉色,直播立减 100!"

  【家电/吹风机】"姐妹们看这台吹风机!十年没换过,这次必须升级,负离子风一吹头发顺到反光,直播 199 比天猫便宜 100!"

  【家电/破壁机】"打豆浆 8 分钟出热饮,免泡免煮,自动清洗,我老公都夸我会挑,适合三口之家,直播立减 200!"

  【家电/洗碗机】"懒妈福音!三餐碗筷一键搞定,杀菌烘干一体,孩子奶瓶也能洗,我每周省 7 小时洗碗时间,直播立减 500!"

  【家电/扫地机】"3 室 2 厅扫地拖地一次清,自动回充自动倒尘,我家猫毛狗毛通通搞定,直播间专享 1599!"

  【数码/耳机】"姐妹们这副耳机我戴一周不想换!通勤地铁噪音直接消失,续航 30 小时,学生党闭眼冲 99!"

  【数码/平板/手机】"今天给大家带这台平板,8+256 大内存高刷屏,看剧办公画画都流畅,学生党的开学神器,直播 1499 比官网便宜 300!"

  【数码/迷你投影】"投在墙上 100 寸,夜里和老公在床上看电影超有氛围,自动对焦不刺眼,直播 999 包安装!"

  【健身/瑜伽垫】"姐妹们练瑜伽久站站痛膝盖的来!这款 8mm TPE 跪着不疼,防滑下犬式不打滑,环保无味,直播 89 包邮!"

  【健身/筋膜枪】"我健身完用 5 分钟,第二天不酸不痛!10 档力度 4 个按摩头,办公族也能用,直播立减 200!"

  【宠物用品】"狗子姐妹这个自动喂食器我用半年,出差 3 天娃也吃得规律,APP 远程加餐,直播立减 80 送一袋粮!"

  【宠物零食】"猫主子尝过都回头的小鱼干!野生鳀鱼低盐烘焙,我家两只追着我屁股要,直播买二送一!"

  【办公文具】"姐妹这支笔太顺手了!考研党必囊!0.5 速干不晕墨,500 米连续书写不卡墨,5 支装才 19.9!"

  【户外/旅行】"姐妹这只行李箱拉杆超丝滑!万向轮带刹车,海关锁防撬,出差 5 天的衣服全装下,直播 269 比 28 寸还便宜!"

- ⚠️ 反面例子(不要写这种):
  ✗ "这件束腰超级好看舒服,推荐大家购买"(空洞/没数据/没场景)
  ✗ "Get that hourglass shape!"(英文混杂或翻译腔)
  ✗ "买它买它买它"(过时网梗,显得 LOW)
  ✗ "亲爱的家人们大家好欢迎来到我的直播间"(开场太冗长无信息)
  ✗ "这款产品对我来说意义非凡,它的XX特性彻底征服了我"(空洞文绉绉)

- ❌ 严禁:绝对化用词(最好/第一/绝对/100%)、夸大医疗(瘦/治愈/丰胸效果)、明显违规
''' if region == "CN" else '''
- 模特特征:Western/Caucasian/Black/Latina/diverse / 自然肤色 / 22-32 岁 / 真实 UGC

- speech 英文 TikTok 爆款话术 4 rules:
  ⭐ **Punch every 2-3 sec** — no generic adjectives, use specific numbers + actions + scenes
  ⭐ **Hook (0-2s) MUST grab**: question/contrast/pain/cliffhanger/direct conclusion
  ⭐ **Selling point = 3-piece**: specific data + scenario + contrast
  ⭐ **CTA with urgency**: limited stock/time/discount/regret-not-buying

- ✅ Hook templates 30+ (verbatim from real TikTok viral hits, learn the cadence):
  · "Stop scrolling" hooks ─
  • "If you're a foodie, stop scrolling!"
  • "If you're tired of [pain], stop scrolling!"
  • "Stop scrolling if you love [X]"
  • "If you have [body type/problem], you NEED to see this"
  • "Stop scrolling if you've been doing [X] wrong"
  · POV hooks ─
  • "POV: you found the [perfect product]"
  • "POV: you finally [achieved X]"
  • "POV: a random stranger asks where you got [your item]"
  • "POV: you stop spending $$$ on [old solution]"
  • "POV: you find the [product] you'll Amazon-subscribe forever"
  · Question hooks ─
  • "Are YOU wearing SPF?"
  • "Did you know Europe once had a mini ice age?"
  • "What's your full skincare routine now?"
  • "What is this?"
  • "Have you heard of [obscure benefit]?"
  · Confession/personal hooks ─
  • "I threw all of my [old product] in the bin..."
  • "I tried every [product] so you don't have to"
  • "Babe, this [product] literally CHANGED my life"
  • "Watch before you buy: [product]"
  • "Here's why I never buy [old thing] again"
  · Contrarian/secret hooks ─
  • "I bet you didn't know that 80% of people make this mistake when..."
  • "Here's why you've been doing [X] all wrong"
  • "Only 1% of people know this..."
  • "Unpopular opinion: ..."
  • "This may be controversial but ___"
  • "Everything you knew about ___ is 100% WRONG"
  · Cliffhanger/curiosity ─
  • "Wait until you see this..."
  • "You won't believe what happened next..."
  • "I can't believe what I just found!"
  • "Watch this in the next 3 seconds..."
  · Body-type / problem-solver ─
  • "If [body type/situation], this is THE one"
  • "If you have big boobs and gaping bras have ruined your life..."
  • "If you stand all day at work, RUN to grab these"

- ✅ Real selling-point lines (data + scenario + contrast) 18+:
  • "I lost 3 inches off my waist in a week, sitting at my desk all day, no chafing"
  • "8 hours straight, NO marks, NO rolling"
  • "5 ft 2 apple shape over here looking like 5 ft 5 in these jeans"
  • "I am 130 lbs and look like 105 in this, somehow zero tummy"
  • "yellow undertone friendly, 8 hour wear, you can EAT and DRINK and it stays"
  • "Soft underwire lifts 2 inches, no slipping when you bend, breathable for summer"
  • "Stretch is INSANE, can squat, no rip"
  • "Breathable in summer, no whale tail"
  • "I do 12-hour shifts, zero swelling, the insole feels like a cloud"
  • "Negative ions = glassy hair, my friend bought it after one use"
  • "I have carried this bag every single day for 6 months — leather still pristine"
  • "Subway noise GONE, 30-hour battery, I forget I'm wearing them"
  • "28 days, pores cut in half, my makeup sits PERFECT"
  • "Vented base, no air swallowed, my babe sleeps 6 hours straight now"
  • "100-inch screen on my wall, auto-focus, no eye strain"
  • "ZERO preservatives, my kid begs daily"
  • "10 hours of cordless run time, dust gone in one pass"
  • "My dog stays full and happy when I'm at work all day"

- ✅ Real CTA / urgency lines 18+:
  • "Link in bio, RUN — they sold out twice already!"
  • "$30 off in my bio, dont sleep!"
  • "Buy 2 get 1 free in my bio, run!"
  • "$20 off live now!"
  • "Take my word for it!"
  • "You wont regret!"
  • "Linked, run!"
  • "Selling out fast — get yours before they're gone"
  • "Code TIKTOK20 for 20% off — first 100 only"
  • "Free shipping over 2 — go!"
  • "Last 50 in stock, drop the 🛒 emoji to grab one"
  • "Half price for the next 10 minutes only"
  • "I'm not gatekeeping — link is right there"
  • "Tap the yellow basket NOW"
  • "Bundle deal: buy 3 save 25%"
  • "Subscribe & save 15% — never run out again"
  • "Use code FIRST15 at checkout — new buyers only"
  • "Add to cart before midnight — flash deal"

- ✅ Multi-category viral examples 25+ (verbatim style learning, do NOT generic-ify):

  [Waist trainer] "Babe I am telling you — this waist trainer literally CHANGED my life. I lost 3 inches off my waist in a week, sitting at my desk all day, no chafing, no marks. Link in bio, RUN — they sold out twice already!"

  [Bra] "If you have big boobs and gaping bras have ruined your life — this is THE one. No straps digging, no spillage, deep V looks insane on, I literally wear this every day now. $30 off in my bio, dont sleep!"

  [Bra / nursing] "Moms after breastfeeding this is the ONE. Soft underwire lifts 2 inches, no slipping when you bend, breathable for summer. My friend grabbed 5 of them, link!"

  [Jeans / pants] "5 ft 2 apple shape over here looking like 5 ft 5 in these jeans. Stretch is INSANE, can squat, no rip. $89 only, take my word for it!"

  [Crop top / fashion] "Look at this fit girl! The most slimming crop top of the year, I am 130 lbs and look like 105 in this, somehow zero tummy, EVERYBODY looks good in it. $20 off live now!"

  [Dress] "POV: a random stranger knew this dress was from your favorite brand because of the silhouette. THIS is the most flattering bodycon of 2026 — link in bio!"

  [Coat / outerwear] "Negative-20 weather and I'm WARM. 90% goose down, 200g fill, machine washable, my mom has worn hers for 3 winters and it looks new. $200 off live!"

  [Tee / basic] "I bought 5 of these tees, no joke. Cooling fabric, doesn't cling, fits in your pocket, washes 30+ times no shrink. $19 each!"

  [Shoes] "If you stand all day at work, RUN to grab these. I do 12-hour shifts, zero swelling, the insole feels like a cloud. $50 off live now!"

  [Bag] "I have carried this bag every single day for 6 months — leather still pristine, holds my entire life, fits a 13-inch laptop. Designer dupe energy at $89, linked!"

  [Jewelry / necklace] "This 18k gold necklace is exactly what I wear in the shower, to the gym, to bed — never tarnishes, never tangles. Half the boutique price, code TT15!"

  [Watch] "Ceramic strap watch — no hair pull, no skin reaction, lume hands so I can read it in bed without flipping a light. 3-year warranty, $79 live!"

  [Lipstick] "Babe THIS lipstick — yellow undertone friendly, 8 hour wear, you can EAT and DRINK and it stays. Buy 2 get 1 free in my bio, run!"

  [Skincare / serum] "I threw all my serums out — this ONE is doing the work of five. 28 days, pores cut in half, my makeup sits PERFECT. Code TIKTOK20 in bio!"

  [Foundation] "Oily girls THIS foundation — 12 hours, NO transfer, NO settling into pores. I wore it through a sweaty wedding, photo perfect. 40% off live!"

  [Beauty device / LED mask] "21 days with this LED mask — strangers asking if I had a facial. Red light, NIR, 3 modes, I do 10 min while scrolling. $200 off!"

  [Mom & baby / bottle] "Mama if your baby has colic — get this bottle. Vented base, no air swallowed, my babe sleeps 6 hours straight now. Link in bio!"

  [Mom & baby / toy] "This sketch robot kept my 4-year-old quiet for TWO HOURS. 200+ guided drawings, ages 3-8, my coffee stayed hot for the first time in years. $39!"

  [Mom & baby / diapers] "These diapers absorb 2x more, my baby sleeps through, no leak, no red marks. Buy 1 pack get 1 free this hour only!"

  [Snack / food] "POV: you find the snack you'll Amazon-subscribe forever. Crispy outside, juicy bite, ZERO preservatives, my kid begs daily. 30% off live!"

  [Tea / coffee] "This single-origin oolong is what I drink EVERY morning now. Honey notes, no bitter, free 3-cup tasting if you don't love it. Buy 1 get 1!"

  [Home / organizer] "Babe my drawer used to be CHAOS. This organizer fits all my socks/sweaters by section, I find anything in 1 second. Free shipping over 2!"

  [Home / bedding] "This 4-piece set — 100% long-staple cotton, sleeping in clouds, washed 50 times zero pilling. $80 off live now!"

  [Home decor / sunset lamp] "POV: golden hour at 2 AM. This sunset lamp gives my apartment a glow my exes paid for in their entire wardrobes. $35!"

  [Home appliance / hair dryer] "10 years with my old hair dryer — this one cuts my time in HALF. Negative ions = glassy hair, my friend bought it after one use. $99 live!"

  [Kitchen appliance / blender] "8 minute hot soy milk, no soak, no boil, self-cleaning. My husband says I'm the wife of the year. $200 off!"

  [Kitchen appliance / dishwasher] "Mom hack: this countertop dishwasher saves me 7 hours a WEEK. Sanitizes, dries, fits a baby bottle set. $500 off live!"

  [Cleaning / robot vacuum] "3-bedroom apartment cleaned hands-free. Vacuum + mop in one pass, auto-empties, eats my cat's hair. Live price $1,599 — half retail!"

  [Tech / earbuds] "These earbuds are scary good. Subway noise GONE, 30-hour battery, I forget I'm wearing them. Student-budget at $99, linked!"

  [Tech / mini projector] "100-inch movie on my wall in 30 seconds. Auto-focus, my boyfriend and I do bed-cinema every Friday now. Setup-free, $299 with mount!"

  [Tech / phone stand] "Hands-free Zoom, hands-free recipes, hands-free everything. Folds flat, fits in my purse, $19 take my money!"

  [Fitness / yoga mat] "If your knees hurt during yoga, RUN to grab this. 8mm TPE, downward-dog grip, eco zero-smell. $45 free shipping today!"

  [Fitness / massage gun] "5 minutes after gym = ZERO soreness next day. 10 levels, 4 heads, fits in my office drawer. $50 off live!"

  [Pet / auto feeder] "I'm out of town for 3 days and my dog stayed on schedule. App control, portion-perfect, 2 yrs no breakdown. Bag of kibble FREE today!"

  [Pet / treat] "My cat literally chases me for these. Wild-caught anchovies, low salt, baked-not-fried. Buy 2 get 1 free!"

  [Office / pen] "If you're studying for an exam, you NEED these. 0.5 quick-dry, 500m of writing, no smudge. 5-pack for $5!"

  [Travel / luggage] "Smoothest wheels of my LIFE, TSA lock, packed 5 days of outfits. $90, 28-inch, this is the one!"

- ⚠️ Bad examples (do NOT write):
  ✗ "This waist trainer is super cute and comfy, recommend!" (empty/no data/no scene)
  ✗ "买它买它"(Chinese mixed with English or translation-feel)
  ✗ "Hi everyone welcome to my video today I'm going to show you..." (intro too long, no hook)
  ✗ "This product is amazing and I love it" (zero specifics)

- ❌ Forbidden: absolutes (best/first/100%), medical claims (lose weight/cure/enhance), violations

  [Tech / earbuds] "These earbuds are scary good. Subway noise GONE, 30-hour battery, I forget I'm wearing them. Student-budget at $99, linked!"

- ⚠️ Bad examples (do NOT write):
  ✗ "This waist trainer is super cute and comfy, recommend!" (empty/no data/no scene)
  ✗ "买它买它"(Chinese mixed with English or translation-feel)
  ✗ "Hi everyone welcome to my video today I'm going to show you..." (intro too long, no hook)

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
