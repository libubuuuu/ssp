"""P110 seed: 把抖音/TikTok 真爆款话术素材 INSERT 到 viral_scripts 表

数据来源:
- opp2.com (33开头模板 / 9大类41条 / 标准模板 / 2025技巧 / 高客单 / 多品类)
- 27sem.com (100条直播话术)
- doukeplus.com (400条话术)
- heyorca.com / minta.ai / lyfemarketing.com / sendshort.ai / usevisuals.com (TikTok hook 库)
- kinship.com (TikTok pet products)

INSERT OR IGNORE 防重复,可重跑。
"""
import sqlite3
import os

DB = os.environ.get("DATABASE_PATH", "/opt/ssp/backend/dev.db")

CN = "CN"
GLOBAL = "GLOBAL"


# ============ 国内中文 ============

CN_HOOKS = [
    # 提问钩
    "如何才能穿的显高?",
    "你知道为什么你容易长胖吗?",
    "你有没有发现:十元的快餐,只有男人在吃?",
    "你是不是也这样,有微信没人聊?",
    "有哪些手机适合学生党用?",
    "到底选电视,还是投影仪?",
    "为什么男孩更容易惹妈妈生气?",
    "有和我一样不喜欢夏天吹头发的吗?",
    # 警告/反差钩
    "都夏天了怎么还穿这种裤子跑步?",
    "千万不要相信任何生发产品,否则就没人会花 20 万去种头发",
    "这4种玩具千万不要买,尤其是第2个",
    "现在,把家里的黑垃圾袋都扔了吧",
    "听我说,这才是 XX 的核心真相!",
    "我一直说,不要买便宜的热风烘干吹风机",
    "警告:看完这篇文章前,不要在文案上花一分钱!",
    # 悬念钩
    "这个视频可能颠覆你之前所有的认知",
    "你敢相信吗?3 句话就能拐走一个大学生!",
    "宝子们我跟你们说,这个真的!颠覆了我对 XX 的认知!",
    "求求各位妈妈们,不要再上当了",
    # 干货钩
    "1 个合格妈妈,必须要秒懂的 3 种孩子饿的信号",
    "客厅装修的 12 条避坑细节,一定不要踩!",
    "准到吓人的 7 条识人术,你一定要知道",
    "厨房装修做好这几点,一定不后悔",
    "初入职场时,必须要知道的10种办公室禁忌!",
    # 直播留人
    "进来的朋友们不要走,马上进入抽奖环节",
    "今天进我直播间的都瘦三斤",
    "欢迎xx的女人们回家",
    "新进直播间的朋友们,左上角帮主播点点关注!点点关注大家不迷路",
    "新进的姐妹左上角点关注,马上有福利",
    "凡是在直播间呆超过 20 分钟的新人朋友,待会我会抽取两位送惊喜礼品",
    "新进来我们直播间的宝贝左上角的福袋还有 X 分钟就要开抢了",
    "进来的人不要走,主播要给大家发红包了",
    "话不多说,咱们先来抽波奖",
    "妮妮家的宝宝们晚上好",
    "这里是美妆达人某某某的直播间,专注美妆15年",
    # 直接展示/精准人群
    "姐妹们直接看上身!",
    "宝妈们听我一句!",
    "胸大姐妹的福音终于来了!",
    "宝贝们今天是我们的品牌宠粉日,整场福利炸不停",
    # 福利/价格
    "想要 1499 的茅台,在公屏上打 1499",
    "刚刚谁在等腰果的?我们将赞点到X的时候直接上链接",
    "新进的家人公屏上打个 111 报名,主播马上给你们上",
    # 痛点共鸣
    "你们是不是穿别的束腰勒一晚上印子第二天还在?",
    "宝妈姐妹们,喂奶之后胸下垂的这条收!",
    "宝子们我家这个抽屉简直外星科技!以前打开找袜子翻箱倒柜……",
    "好多宝宝向我咨询化妆的问题,因为直播的节奏问题,没办法全部回复",
    "教大家避雷,买最合适的化妆品,每年至少省个几千块",
]

CN_SELLING = [
    "这条裤子穿过三十多种,最后只回购了这一条,腿粗的、屁股大的、平胯的,统统能驾驭",
    "穿一天腰小三公分,关键是不勒不卡,我办公一天都没感觉",
    "整整 8 小时不勒不卷边,我老公都说看不出来我穿了",
    "穿了二十多年内衣,这是第一件没勒红肩膀的",
    "软钢圈一穿提升 5 厘米,弯腰也不掉,薄到夏天透气",
    "我 130 斤穿出 105 斤的效果,关键它真的不显肚子",
    "158 苹果型穿出 1m65 的腿,蹲下不开线",
    "纹眉的功能是让你不用自己画眉毛,早上可以多睡十分钟,素颜也很有气色",
    "你喷的香水就是别人眼中你的味道",
    "精致大气的女人一定要拥有一件羊绒大衣",
    "你买的是家人的健康和安心,你买的是对家人的关爱与保护",
    "里面是 40 级加厚衬、3D 立体剪裁的收腰设计,衬托你的腰就是水蛇腰",
    "荔枝纹的看起来就很高级,摸上去能感觉到它的纹路质感",
    "我家有一只同样材质的包包就背了好几年,越背越喜欢",
    "夏天穿这件防晒衣,轻薄得像没穿一样!",
    "我们家是仓库直播,是供应链的源头,不经过任何中间周转",
    "早上来杯有粉,暖暖的很舒服,整个上午都不带饿的",
    "0 添加防腐剂,孩子吃也放心",
    "通勤地铁噪音直接消失,续航 30 小时",
    "负离子风一吹头发顺到反光",
    "源头货品,最低折扣,正品保证",
    "凤凰单丛属于乌龙茶里面的一种",
    "支持大家到货试喝三泡茶,喜欢了茶留下,不喜欢可以退换",
    "我们的奶瓶宝宝从两个月用到现在,从不胀气不溢奶,半夜泡奶不用醒",
]

CN_CTA = [
    "正常价格要 99 米,今天只要 29.9 米,不到一杯奶茶价格",
    "一份泡面的钱/一顿外卖的钱/一杯奶茶的钱,就可以吃到双人餐",
    "某宝 399 米,今天在我直播间,直接 299!带回家",
    "专柜价近三千,直播间给一个惊喜价!只要 899",
    "门店价 150 块,今天只需 75 块,平时玩一次的价格",
    "只准备了 30 件,放完为止",
    "就 500 份,抢完就没有了,上次 30 秒不到就没了",
    "明天有没有,明天没有;下周有没有,下周没有;什么时候再有,不知道",
    "买了 10 万件的爆款,库存不多了",
    "我们最后 1 分钟就要下播了,这个价格下播就没了",
    "买二送一,冲冲冲!",
    "链接挂上面了,姐妹冲!",
    "先到先得,手慢就没了",
    "10 秒钟准备就上车,10、9、8、7、6、5、4、3、2、1",
    "还剩最后的 50 单了,拼手速了",
    "前 50 名下单的朋友享受折扣和额外赠品",
    "前 100 名下单享受九折优惠",
    "1 分钱的 xxx,抢到就是赚到",
    "四个九块九,连个运费都不要,放 50 单",
    "7 天无理由退换货,运费险我出",
    "收到货不满意,拆开可以退回来的,运费我出",
    "买一瓶送一套飞天酒具,数量有限",
    "我们直播间专属价啊,而且你去门店是没有这个价格的",
    "粉丝团成员有专属优惠价,赶紧加入",
]

CN_EXAMPLES = [
    ("塑身/束腰", "宝子们我跟你们说,这个束腰真的!颠覆了我对塑身的认知!穿一天腰小三公分,不勒不卡,我办公一天都没感觉,链接姐妹们抢吧,卖完没有了!"),
    ("内衣/胸罩", "胸大姐妹的福音终于来了!穿了二十多年内衣,这是第一件没勒红肩膀的,深 V 还显小,我现在天天就穿这一件,直播间下单立减 30!"),
    ("内衣/哺乳", "宝妈们听我一句!喂奶之后胸下垂的这条收!软钢圈一穿提升 5 厘米,弯腰也不掉,我闺蜜抢了 5 件!"),
    ("裤子/牛仔", "姐妹们直接看上身!158 苹果型穿出 1m65 的腿!弹力是真的绝,蹲下不开线!天猫旗舰店现在才 89!"),
    ("短款/上衣", "姐妹们看这版型!最显瘦的就是这种短款,我 130 斤穿出 105 斤的效果,关键不显肚子,谁穿谁好看!"),
    ("连衣裙", "各位姐妹看一下这款水雾蓝的设计款连衣裙,40 级加厚衬、3D 立体剪裁的收腰,衬托你的腰就是水蛇腰,闭眼下单!"),
    ("外套/羽绒", "零下 20 度都不冷的羽绒服!90 白鸭绒充绒量 200,机洗不变形,我妈穿了三年还跟新的一样!直播间立减 200!"),
    ("T恤/打底", "姐妹们这件打底我囤了 5 件!冰丝凉感不黏身,薄到能塞口袋,洗 30 次不变形,夏天通勤神器!"),
    ("羊绒大衣", "精致大气的女人一定要拥有一件羊绒大衣!90% 山羊绒,垂坠有版型,我穿了 3 年没起球,专柜 5800 直播 1980!"),
    ("鞋", "姐妹们这双鞋绝了!久站一天脚不痛不肿,内里软到像踩棉花,我做销售站 12 小时回家不用泡脚!满 199 减 50!"),
    ("箱包", "姐妹们看过来,我手里这款包,荔枝纹一看就高级,我家有一只同款背了好几年,越背越喜欢,专柜 1280 直播 299!"),
    ("珠宝/首饰", "这条 18K 金项链是闺蜜结婚送我的同款,锁骨位刚好,洗澡睡觉都不用摘,带几年也不褪色,直播价比专柜便宜一半!"),
    ("手表", "上班的姐妹这块手表必须收!陶瓷表带亲肤不夹毛,夜光表盘加班看时间不用开灯,质保 3 年!"),
    ("美妆/口红", "姐妹们这支口红绝绝子!黄黑皮亲妈色!涂上去显白显气色,持久 8 小时,吃饭喝水都不掉色!买二送一!"),
    ("美妆/护肤", "姐妹们干皮换季烂脸的看这条!这瓶精华我用了 28 天,毛孔小一半、上妆服帖到爆,素颜出门都不慌!送同款面膜 5 片!"),
    ("美妆/底妆", "姐妹们这粉底液太懂我了!油皮 12 小时不脱妆不浮粉,持妆能扛一整天会议,直播间专享 6 折!"),
    ("美容仪", "姐妹们这台美容仪用 21 天我表妹问我去医美了!射频提拉法令纹真的浅了,医美级在家做,直播立减 800!"),
    ("母婴/奶瓶", "宝妈们这款奶瓶我家娃从两个月用到现在,不胀气不溢奶,半夜泡奶不用醒,我老公都夸!立减 50 送奶嘴!"),
    ("母婴/玩具", "宝妈姐妹这个绘画机器人陪我家娃画画两小时,我终于能喝杯热咖啡了!教 200 多个步骤,3-8 岁通用,直播 199!"),
    ("母婴/纸尿裤", "宝妈来听!这款纸尿裤吸水性是平时的 2 倍,我家娃睡整夜不漏不返渗,腰间无勒痕,直播 1 包送 1 包!"),
    ("食品/零食", "姐妹们这袋我抢了三轮才抢到!外面酥里面爆汁,一袋一晚干掉,0 添加防腐剂,孩子吃也放心!9.9 抢一袋!"),
    ("食品/水果", "福建桃子甜到爆汁的来了!果园直发,树上熟才摘,我一口气吃了 5 个,孕妈也能放心吃,5 斤装才 39.9!"),
    ("食品/茶", "凤凰单丛属于乌龙茶里的一种,我们家是茶园直发,支持到货试喝三泡,不喜欢可以退换,这款蜜兰香买一送一!"),
    ("酒", "60 度纯粮老酒,陶坛窖藏 5 年,送礼倍有面子,我家老爸喝了直夸,5 斤装专享价 198 还包邮!"),
    ("家居/收纳", "宝子们我家这个抽屉简直外星科技!打开找袜子翻箱倒柜的日子结束了,1 秒拿到!满 2 件免邮!"),
    ("家居/床品", "姐妹这套四件套真的舒服!100% 长绒棉,睡上去像被云包裹,洗 50 次不起球不掉色,直播立减 100!"),
    ("家电/吹风机", "姐妹们看这台吹风机!十年没换过,这次必须升级,负离子风一吹头发顺到反光,直播 199 比天猫便宜 100!"),
    ("家电/破壁机", "打豆浆 8 分钟出热饮,免泡免煮,自动清洗,我老公都夸我会挑,适合三口之家,直播立减 200!"),
    ("家电/洗碗机", "懒妈福音!三餐碗筷一键搞定,杀菌烘干一体,孩子奶瓶也能洗,我每周省 7 小时洗碗时间,直播立减 500!"),
    ("家电/扫地机", "3 室 2 厅扫地拖地一次清,自动回充自动倒尘,我家猫毛狗毛通通搞定,直播间专享 1599!"),
    ("数码/耳机", "姐妹们这副耳机我戴一周不想换!通勤地铁噪音直接消失,续航 30 小时,学生党闭眼冲 99!"),
    ("数码/平板/手机", "今天给大家带这台平板,8+256 大内存高刷屏,看剧办公画画都流畅,学生党的开学神器,直播 1499 比官网便宜 300!"),
    ("数码/迷你投影", "投在墙上 100 寸,夜里和老公在床上看电影超有氛围,自动对焦不刺眼,直播 999 包安装!"),
    ("健身/瑜伽垫", "姐妹们练瑜伽久站站痛膝盖的来!这款 8mm TPE 跪着不疼,防滑下犬式不打滑,环保无味,直播 89 包邮!"),
    ("健身/筋膜枪", "我健身完用 5 分钟,第二天不酸不痛!10 档力度 4 个按摩头,办公族也能用,直播立减 200!"),
    ("宠物用品", "狗子姐妹这个自动喂食器我用半年,出差 3 天娃也吃得规律,APP 远程加餐,直播立减 80 送一袋粮!"),
    ("宠物零食", "猫主子尝过都回头的小鱼干!野生鳀鱼低盐烘焙,我家两只追着我屁股要,直播买二送一!"),
    ("办公文具", "姐妹这支笔太顺手了!考研党必囊!0.5 速干不晕墨,500 米连续书写不卡墨,5 支装才 19.9!"),
    ("户外/旅行", "姐妹这只行李箱拉杆超丝滑!万向轮带刹车,海关锁防撬,出差 5 天的衣服全装下,直播 269 比 28 寸还便宜!"),
]


# ============ 海外英文 ============

GLOBAL_HOOKS = [
    # Stop scrolling
    "If you're a foodie, stop scrolling!",
    "If you're tired of [pain], stop scrolling!",
    "Stop scrolling if you love [X]",
    "If you have [body type/problem], you NEED to see this",
    "Stop scrolling if you've been doing [X] wrong",
    "If you want to travel more, stop scrolling!",
    "If you're tired of watching your houseplants die, stop scrolling!",
    "Ladies, stop scrolling and listen to these 5 [niche] secrets",
    # POV
    "POV: you found the [perfect product]",
    "POV: you finally [achieved X]",
    "POV: a random stranger asks where you got [your item]",
    "POV: you stop spending $$$ on [old solution]",
    "POV: you find the [product] you'll Amazon-subscribe forever",
    "POV: you're tired of [pain] all day",
    # Question
    "Are YOU wearing SPF?",
    "Did you know Europe once had a mini ice age?",
    "What's your full skincare routine now?",
    "What is this?",
    "Have you heard of [obscure benefit]?",
    "Is it just me, or [observation]?",
    "What is their morning routine and how do we follow it?",
    # Confession/personal
    "I threw all of my [old product] in the bin...",
    "I tried every [product] so you don't have to",
    "Babe, this [product] literally CHANGED my life",
    "Watch before you buy: [product]",
    "Here's why I never buy [old thing] again",
    "I just found the perfect product that helps with [pain]",
    "I can't believe what I just discovered!",
    "I promise you've never seen anything like this before!",
    # Contrarian/secret
    "I bet you didn't know that 80% of people make this mistake when...",
    "Here's why you've been doing [X] all wrong",
    "Only 1% of people know this...",
    "Unpopular opinion: ...",
    "This may be controversial but ___",
    "Everything you knew about ___ is 100% WRONG",
    "Instead of buying this, buy this!",
    # Cliffhanger/curiosity
    "Wait until you see this...",
    "You won't believe what happened next...",
    "I can't believe what I just found!",
    "Watch this in the next 3 seconds...",
    "Here's a secret you didn't know...",
    "Only 1% of People Know...",
    # Body-type / problem-solver
    "If [body type/situation], this is THE one",
    "If you have big boobs and gaping bras have ruined your life...",
    "If you stand all day at work, RUN to grab these",
    "5 things you can do right now to improve [outcome]",
    "Here are [N] tips to get rid of [problem]",
]

GLOBAL_SELLING = [
    "I lost 3 inches off my waist in a week, sitting at my desk all day, no chafing",
    "8 hours straight, NO marks, NO rolling",
    "5 ft 2 apple shape over here looking like 5 ft 5 in these jeans",
    "I am 130 lbs and look like 105 in this, somehow zero tummy",
    "yellow undertone friendly, 8 hour wear, you can EAT and DRINK and it stays",
    "Soft underwire lifts 2 inches, no slipping when you bend, breathable for summer",
    "Stretch is INSANE, can squat, no rip",
    "Breathable in summer, no whale tail",
    "I do 12-hour shifts, zero swelling, the insole feels like a cloud",
    "Negative ions = glassy hair, my friend bought it after one use",
    "I have carried this bag every single day for 6 months — leather still pristine",
    "Subway noise GONE, 30-hour battery, I forget I'm wearing them",
    "28 days, pores cut in half, my makeup sits PERFECT",
    "Vented base, no air swallowed, my babe sleeps 6 hours straight now",
    "100-inch screen on my wall, auto-focus, no eye strain",
    "ZERO preservatives, my kid begs daily",
    "10 hours of cordless run time, dust gone in one pass",
    "My dog stays full and happy when I'm at work all day",
    "Cooling fabric, doesn't cling, fits in your pocket, washes 30+ times no shrink",
    "Never tarnishes, never tangles, shower-proof, gym-proof",
]

GLOBAL_CTA = [
    "Link in bio, RUN — they sold out twice already!",
    "$30 off in my bio, dont sleep!",
    "Buy 2 get 1 free in my bio, run!",
    "$20 off live now!",
    "Take my word for it!",
    "You wont regret!",
    "Linked, run!",
    "Selling out fast — get yours before they're gone",
    "Code TIKTOK20 for 20% off — first 100 only",
    "Free shipping over 2 — go!",
    "Last 50 in stock, drop the cart emoji to grab one",
    "Half price for the next 10 minutes only",
    "I'm not gatekeeping — link is right there",
    "Tap the yellow basket NOW",
    "Bundle deal: buy 3 save 25%",
    "Subscribe & save 15% — never run out again",
    "Use code FIRST15 at checkout — new buyers only",
    "Add to cart before midnight — flash deal",
    "Live price drops every 5 minutes — set a reminder",
    "Restocked tonight — not staying up for round 4",
]

GLOBAL_EXAMPLES = [
    ("Waist trainer", "Babe I am telling you — this waist trainer literally CHANGED my life. I lost 3 inches off my waist in a week, sitting at my desk all day, no chafing, no marks. Link in bio, RUN — they sold out twice already!"),
    ("Bra", "If you have big boobs and gaping bras have ruined your life — this is THE one. No straps digging, no spillage, deep V looks insane on, I literally wear this every day now. $30 off in my bio, dont sleep!"),
    ("Bra/nursing", "Moms after breastfeeding this is the ONE. Soft underwire lifts 2 inches, no slipping when you bend, breathable for summer. My friend grabbed 5 of them, link!"),
    ("Jeans/pants", "5 ft 2 apple shape over here looking like 5 ft 5 in these jeans. Stretch is INSANE, can squat, no rip. $89 only, take my word for it!"),
    ("Crop top/fashion", "Look at this fit girl! The most slimming crop top of the year, I am 130 lbs and look like 105 in this, somehow zero tummy, EVERYBODY looks good in it. $20 off live now!"),
    ("Dress", "POV: a random stranger knew this dress was from your favorite brand because of the silhouette. THIS is the most flattering bodycon of 2026 — link in bio!"),
    ("Coat/outerwear", "Negative-20 weather and I'm WARM. 90% goose down, 200g fill, machine washable, my mom has worn hers for 3 winters and it looks new. $200 off live!"),
    ("Tee/basic", "I bought 5 of these tees, no joke. Cooling fabric, doesn't cling, fits in your pocket, washes 30+ times no shrink. $19 each!"),
    ("Shoes", "If you stand all day at work, RUN to grab these. I do 12-hour shifts, zero swelling, the insole feels like a cloud. $50 off live now!"),
    ("Bag", "I have carried this bag every single day for 6 months — leather still pristine, holds my entire life, fits a 13-inch laptop. Designer dupe energy at $89, linked!"),
    ("Jewelry/necklace", "This 18k gold necklace is exactly what I wear in the shower, to the gym, to bed — never tarnishes, never tangles. Half the boutique price, code TT15!"),
    ("Watch", "Ceramic strap watch — no hair pull, no skin reaction, lume hands so I can read it in bed without flipping a light. 3-year warranty, $79 live!"),
    ("Lipstick", "Babe THIS lipstick — yellow undertone friendly, 8 hour wear, you can EAT and DRINK and it stays. Buy 2 get 1 free in my bio, run!"),
    ("Skincare/serum", "I threw all my serums out — this ONE is doing the work of five. 28 days, pores cut in half, my makeup sits PERFECT. Code TIKTOK20 in bio!"),
    ("Foundation", "Oily girls THIS foundation — 12 hours, NO transfer, NO settling into pores. I wore it through a sweaty wedding, photo perfect. 40% off live!"),
    ("Beauty device/LED mask", "21 days with this LED mask — strangers asking if I had a facial. Red light, NIR, 3 modes, I do 10 min while scrolling. $200 off!"),
    ("Mom & baby/bottle", "Mama if your baby has colic — get this bottle. Vented base, no air swallowed, my babe sleeps 6 hours straight now. Link in bio!"),
    ("Mom & baby/toy", "This sketch robot kept my 4-year-old quiet for TWO HOURS. 200+ guided drawings, ages 3-8, my coffee stayed hot for the first time in years. $39!"),
    ("Mom & baby/diapers", "These diapers absorb 2x more, my baby sleeps through, no leak, no red marks. Buy 1 pack get 1 free this hour only!"),
    ("Snack/food", "POV: you find the snack you'll Amazon-subscribe forever. Crispy outside, juicy bite, ZERO preservatives, my kid begs daily. 30% off live!"),
    ("Tea/coffee", "This single-origin oolong is what I drink EVERY morning now. Honey notes, no bitter, free 3-cup tasting if you don't love it. Buy 1 get 1!"),
    ("Home/organizer", "Babe my drawer used to be CHAOS. This organizer fits all my socks/sweaters by section, I find anything in 1 second. Free shipping over 2!"),
    ("Home/bedding", "This 4-piece set — 100% long-staple cotton, sleeping in clouds, washed 50 times zero pilling. $80 off live now!"),
    ("Home decor/sunset lamp", "POV: golden hour at 2 AM. This sunset lamp gives my apartment a glow my exes paid for in their entire wardrobes. $35!"),
    ("Home appliance/hair dryer", "10 years with my old hair dryer — this one cuts my time in HALF. Negative ions = glassy hair, my friend bought it after one use. $99 live!"),
    ("Kitchen appliance/blender", "8 minute hot soy milk, no soak, no boil, self-cleaning. My husband says I'm the wife of the year. $200 off!"),
    ("Kitchen appliance/dishwasher", "Mom hack: this countertop dishwasher saves me 7 hours a WEEK. Sanitizes, dries, fits a baby bottle set. $500 off live!"),
    ("Cleaning/robot vacuum", "3-bedroom apartment cleaned hands-free. Vacuum + mop in one pass, auto-empties, eats my cat's hair. Live price $1,599 — half retail!"),
    ("Tech/earbuds", "These earbuds are scary good. Subway noise GONE, 30-hour battery, I forget I'm wearing them. Student-budget at $99, linked!"),
    ("Tech/mini projector", "100-inch movie on my wall in 30 seconds. Auto-focus, my boyfriend and I do bed-cinema every Friday now. Setup-free, $299 with mount!"),
    ("Tech/phone stand", "Hands-free Zoom, hands-free recipes, hands-free everything. Folds flat, fits in my purse, $19 take my money!"),
    ("Fitness/yoga mat", "If your knees hurt during yoga, RUN to grab this. 8mm TPE, downward-dog grip, eco zero-smell. $45 free shipping today!"),
    ("Fitness/massage gun", "5 minutes after gym = ZERO soreness next day. 10 levels, 4 heads, fits in my office drawer. $50 off live!"),
    ("Pet/auto feeder", "I'm out of town for 3 days and my dog stayed on schedule. App control, portion-perfect, 2 yrs no breakdown. Bag of kibble FREE today!"),
    ("Pet/treat", "My cat literally chases me for these. Wild-caught anchovies, low salt, baked-not-fried. Buy 2 get 1 free!"),
    ("Office/pen", "If you're studying for an exam, you NEED these. 0.5 quick-dry, 500m of writing, no smudge. 5-pack for $5!"),
    ("Travel/luggage", "Smoothest wheels of my LIFE, TSA lock, packed 5 days of outfits. $90, 28-inch, this is the one!"),
]


def seed():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    inserted = 0
    skipped = 0

    def ins(region, kind, category, text, source="seed_p110"):
        nonlocal inserted, skipped
        try:
            cur.execute(
                "INSERT INTO viral_scripts (region, kind, category, text, source_url) VALUES (?, ?, ?, ?, ?)",
                (region, kind, category, text, source),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1  # UNIQUE conflict: 已存在,跳过

    for h in CN_HOOKS:
        ins(CN, "hook", None, h)
    for s in CN_SELLING:
        ins(CN, "selling", None, s)
    for c in CN_CTA:
        ins(CN, "cta", None, c)
    for cat, txt in CN_EXAMPLES:
        ins(CN, "example", cat, txt)

    for h in GLOBAL_HOOKS:
        ins(GLOBAL, "hook", None, h)
    for s in GLOBAL_SELLING:
        ins(GLOBAL, "selling", None, s)
    for c in GLOBAL_CTA:
        ins(GLOBAL, "cta", None, c)
    for cat, txt in GLOBAL_EXAMPLES:
        ins(GLOBAL, "example", cat, txt)

    conn.commit()

    cur.execute("SELECT region, kind, COUNT(*) FROM viral_scripts GROUP BY region, kind ORDER BY region, kind")
    rows = cur.fetchall()
    print(f"\n--- 入库统计(insert={inserted}, skip={skipped}) ---")
    for r in rows:
        print(f"  {r[0]:8s} {r[1]:8s}: {r[2]}")
    cur.execute("SELECT COUNT(*) FROM viral_scripts")
    print(f"  总计: {cur.fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    seed()
