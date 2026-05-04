"""P110 周更 scraper:从公开博客拉爆款话术句子,INSERT IGNORE 入 viral_scripts 表

跑法:
  /opt/ssp/backend/venv/bin/python /opt/ssp/scripts/scrape_viral_scripts.py

cron 每周一 03:30 跑(避开 03:00 backup),日志写到 /var/log/scrape_viral.log。

策略:
- requests 拉 HTML(带 User-Agent + 重试)
- 纯正则提取引号包住的句子(避开 BS4 依赖)
- 启发式过滤:长度 + 关键词(必须含至少一个直播话术信号词)
- UNIQUE(text) 约束自动去重,新句子 INSERT,旧句子 IGNORE

源:
- 国内 opp2.com 多个真材料 url
- 国内 27sem.com / yumaochuhai.com / yydashu.com / iyunying.org
- 海外 heyorca / minta / lyfemarketing / sendshort

故意保守:每个 url 失败不 raise,继续下一个。爬不到也没关系,seed 已 258 条。
"""
import os
import re
import sqlite3
import sys
import time
from typing import Optional

import requests

DB_PATH = os.environ.get("DATABASE_PATH", "/opt/ssp/backend/dev.db")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
TIMEOUT = 15


# ============== 源 URL 配置 ==============
# (url, region, kind 推断 hint:None=自动按句子内容分类,优先按 url 默认 hint)
SOURCES_CN = [
    ("https://www.opp2.com/348214.html", "hook"),       # 33 个开头模板
    ("https://www.opp2.com/360571.html", "hook"),       # 9 大类 41 条
    ("https://www.opp2.com/350800.html", "cta"),        # 标准话术模板
    ("https://www.opp2.com/351178.html", "hook"),
    ("https://www.opp2.com/356582.html", "hook"),
    ("https://www.opp2.com/361531.html", "selling"),    # 2025 技巧多品类
    ("https://www.opp2.com/343864.html", "hook"),
    ("https://www.opp2.com/310534.html", "selling"),
    ("https://www.opp2.com/329945.html", "selling"),
    ("https://www.27sem.com/article/8355", "hook"),     # 100 条
    ("https://www.doukeplus.com/9696.html", "hook"),    # 400 条
]

SOURCES_GLOBAL = [
    ("https://www.heyorca.com/blog/best-tiktok-hooks", "hook"),
    ("https://www.minta.ai/blog-post/tiktok-hooks", "hook"),
    ("https://www.lyfemarketing.com/blog/tiktok-hook-ideas/", "hook"),
    ("https://sendshort.ai/guides/tiktok-hooks/", "hook"),
    ("https://usevisuals.com/blog/scroll-stopping-tiktok-hook-examples", "hook"),
]


# ============== 句子提取 ==============

# 中文引号或英文引号包住的句子;长度 12-260
# 4 种引号都覆盖:"""英中"
QUOTE_PATTERN_CN = re.compile(r'["""]([^"""]{12,260})["""]')
QUOTE_PATTERN_EN = re.compile(r'"([^"]{15,260})"')

# 直播话术信号词(中文):句子至少含一个才入库,过滤无关引文
CN_SIGNALS = [
    "姐妹", "宝子", "宝妈", "宝宝", "亲", "大家", "直播间", "公屏", "链接",
    "下单", "立减", "包邮", "赠", "送", "抢", "限时", "秒杀", "9.9", "29.9",
    "便宜", "卖完", "库存", "现货", "上架", "拍下", "拍立", "运费", "正品",
    "抽奖", "福袋", "福利", "倒数", "倒计时", "关注", "点击", "跟主播", "厂家",
    "源头", "官方", "返", "全场", "新人", "老粉", "回购", "看上身", "凡是",
    "拼手速", "手慢", "想要", "卖了", "我家", "我妈", "我老公", "试用", "买它",
    # 卖点/痛点信号
    "穿一天", "用一年", "效果", "对比", "原价", "现价", "省", "亏",
]

# 信号词(英文)
EN_SIGNALS = [
    "stop scroll", "POV", "if you", "babe", "girl", "link in bio",
    "code ", "off live", "% off", "buy 2", "buy 1", "free shipping",
    "selling out", "dont sleep", "trust me", "TikTok made me",
    "i swear", "literally", "wait until", "you won't believe",
    "i tried every", "watch before", "RUN ", " run!", "$",
    "no kidding", "no joke", "i bet you", "here's why",
    "secret", "5 ft", "lbs", "got me",
]

# 显式排除关键字:句子含这些立刻丢(导航/广告/评论/博客介绍文)
NEGATIVE = [
    # 站点导航
    "上一篇", "下一篇", "评论", "推荐阅读", "广告", "首页",
    "subscribe", "newsletter", "cookie", "©", "all rights",
    "<", ">", "{{", "}}", "function", "var ", "<!--",
    # 博客作者教学口吻(不是真 hook,容易混入)
    "Let me know", "Pay attention", "Don't bait", "Analyze ",
    "creating a broader", "ecosystem", "algorithm", "in the comments",
    "delivers on", "your content", "your audience", "your videos",
    "for instance", "for example", "make sure", "this article",
    "in this post", "let's dive", "step by step", "framework",
    # escape 残留
    "\\\\", "\\\"", "&nbsp;", "&amp;", "&quot;",
]
# 海外句子最大长度收紧(超过 = 段落而非 hook/CTA)
EN_MAX_LEN = 180

CN_RE = re.compile(r"[一-鿿]")  # 含中文字符


def has_cn(s: str) -> bool:
    return bool(CN_RE.search(s))


def is_chinese(s: str) -> bool:
    """中文字符占比 > 30% 算中文句子"""
    if not s:
        return False
    cn_count = len(CN_RE.findall(s))
    return cn_count / len(s) > 0.3


def has_signal(s: str, signals: list) -> bool:
    low = s.lower()
    return any(sig.lower() in low for sig in signals)


def has_negative(s: str) -> bool:
    low = s.lower()
    return any(neg.lower() in low for neg in NEGATIVE)


def classify_kind(s: str, default_hint: Optional[str], is_cn: bool) -> str:
    """启发式判断 kind: hook/selling/cta/example。
    优先按句子特征匹配,匹配不到走 default_hint。
    """
    low = s.lower()
    if is_cn:
        # CTA: 含价格 / 数量 / 时间限制
        if re.search(r"\d+[米块元¥]|包邮|立减|秒杀|抢|福袋|运费|限时|手慢|送|赠|减 \d+|9\.9", s):
            return "cta"
        # Hook: 含问号 / 召唤 / 警告
        if re.search(r"[?？]|姐妹们|宝子们|宝妈们|进直播间|不要走|新进", s):
            return "hook"
        # Selling: 含数字+ unit / 对比
        if re.search(r"\d+[斤天年小时]|穿了|用了|这条|这件|这台", s):
            return "selling"
    else:
        if re.search(r"\$\d+|% off|free|code |link in bio|buy \d|sold out", low):
            return "cta"
        if re.search(r"if you|stop scroll|pov|babe|wait until|you won|i bet", low):
            return "hook"
        if re.search(r"\d+ (lbs|inches|hours|days|hour wear|years)|i (carried|wore|wear)", low):
            return "selling"
    return default_hint or "hook"


def extract(html: str, region: str) -> list:
    """从 HTML 提取候选句子,返回 [(text, kind),...]。"""
    out = []
    seen_local = set()
    if region == "CN":
        candidates = QUOTE_PATTERN_CN.findall(html)
    else:
        candidates = QUOTE_PATTERN_EN.findall(html)

    for s in candidates:
        s = s.strip()
        if len(s) < 12 or len(s) > 260:
            continue
        if has_negative(s):
            continue
        if region == "CN":
            if not is_chinese(s):
                continue
            if not has_signal(s, CN_SIGNALS):
                continue
        else:  # GLOBAL
            if has_cn(s):  # 英文页若混进中文丢
                continue
            if len(s) > EN_MAX_LEN:  # 段落级别 (>180) = 不是 hook/CTA,丢
                continue
            if not has_signal(s, EN_SIGNALS):
                continue
        if s in seen_local:
            continue
        seen_local.add(s)
        out.append(s)
    return out


# ============== 抓取 + 入库 ==============

def fetch(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.encoding = r.apparent_encoding or "utf-8"
        if r.status_code == 200 and len(r.text) > 500:
            return r.text
        print(f"  [skip] {url} status={r.status_code} len={len(r.text)}")
    except Exception as e:
        print(f"  [err]  {url} {e}")
    return None


def insert(conn, region: str, text: str, kind: str, source_url: str) -> bool:
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO viral_scripts (region, kind, category, text, source_url) VALUES (?, ?, NULL, ?, ?)",
            (region, kind, text, source_url),
        )
        return True
    except sqlite3.IntegrityError:
        return False  # UNIQUE 重复


def scrape_region(conn, sources: list, region: str) -> tuple:
    new_count = 0
    skip_count = 0
    err_count = 0
    for url, default_hint in sources:
        print(f"\n[{region}] {url} (default={default_hint})")
        html = fetch(url)
        if not html:
            err_count += 1
            continue
        sentences = extract(html, region)
        page_new = 0
        page_skip = 0
        for s in sentences:
            kind = classify_kind(s, default_hint, region == "CN")
            ok = insert(conn, region, s, kind, url)
            if ok:
                new_count += 1
                page_new += 1
            else:
                skip_count += 1
                page_skip += 1
        print(f"  candidates={len(sentences)} new={page_new} dup={page_skip}")
        time.sleep(2)  # 礼貌爬,避免被反爬
    return new_count, skip_count, err_count


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    # 抓前总数
    pre = conn.execute("SELECT COUNT(*) FROM viral_scripts").fetchone()[0]
    print(f"\n=== 抓取开始,db 当前 {pre} 条 ===")

    cn_new, cn_skip, cn_err = scrape_region(conn, SOURCES_CN, "CN")
    gl_new, gl_skip, gl_err = scrape_region(conn, SOURCES_GLOBAL, "GLOBAL")

    conn.commit()
    post = conn.execute("SELECT COUNT(*) FROM viral_scripts").fetchone()[0]

    print(f"\n=== 完成 ===")
    print(f"CN  新增 {cn_new} / 跳过 {cn_skip} / url err {cn_err}")
    print(f"GL  新增 {gl_new} / 跳过 {gl_skip} / url err {gl_err}")
    print(f"db: {pre} → {post} (净增 {post-pre})")

    # 最近 5 条新增预览
    cur = conn.execute(
        "SELECT region, kind, text FROM viral_scripts ORDER BY id DESC LIMIT 5"
    )
    print("\n--- 最新 5 条预览 ---")
    for r in cur.fetchall():
        print(f"  [{r[0]}/{r[1]}] {r[2][:80]}")
    conn.close()


if __name__ == "__main__":
    main()
