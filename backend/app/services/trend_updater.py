"""每日趋势自动更新：凌晨3点搜索各平台品类趋势，存入 trend_cache 表。"""
from __future__ import annotations

import asyncio
import datetime

from .logger import log_info, log_error

SEARCH_QUERIES = [
    {"platform": "tiktok", "category": "general", "query": "TikTok Shop trending products this week 2026 top selling"},
    {"platform": "tiktok", "category": "fashion",  "query": "TikTok viral fashion clothing video trends this week"},
    {"platform": "tiktok", "category": "beauty",   "query": "TikTok viral beauty skincare product video trends this week"},
    {"platform": "douyin", "category": "general",  "query": "抖音带货 本周爆款视频 热门商品 2026"},
    {"platform": "douyin", "category": "fashion",  "query": "抖音 服装穿搭 带货爆款 本周趋势"},
    {"platform": "douyin", "category": "beauty",   "query": "抖音 美妆护肤 带货爆款 本周趋势"},
]


async def run_trend_update() -> int:
    """执行一次趋势更新，返回成功写入条数。"""
    from app.config import get_settings
    from app.database import get_db
    from openai import AsyncOpenAI

    s = get_settings()
    # 用联网搜索专用 key；未配置时降级为普通 key
    _key = s.SEARCH_API_KEY or s.LINGMENG_API_KEY
    _base = s.SEARCH_BASE_URL or s.LINGMENG_BASE_URL
    client = AsyncOpenAI(base_url=_base, api_key=_key)

    # 清理 7 天前的旧数据
    with get_db() as conn:
        conn.execute("DELETE FROM trend_cache WHERE created_at < datetime('now', '-7 days')")
        conn.commit()

    success = 0
    for item in SEARCH_QUERIES:
        try:
            resp = await client.chat.completions.create(
                model="gpt-4o-search-preview",
                messages=[{"role": "user", "content": item["query"]}],
                max_tokens=800,
            )
            content = (resp.choices[0].message.content or "").strip()
            if content:
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO trend_cache (platform, category, content, source) VALUES (?, ?, ?, ?)",
                        (item["platform"], item["category"], content, "gpt-4o-search-preview"),
                    )
                    conn.commit()
                success += 1
                log_info(f"trend_updater: {item['platform']}/{item['category']} 更新成功 len={len(content)}")
        except Exception as e:
            log_error(f"trend_updater: {item['platform']}/{item['category']} 失败: {e}")

    log_info(f"trend_updater: 完成 {success}/{len(SEARCH_QUERIES)} 条")
    return success


def _seconds_until_3am() -> float:
    """计算距下一个凌晨3点的秒数。"""
    now = datetime.datetime.now()
    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now >= target:
        target += datetime.timedelta(days=1)
    return (target - now).total_seconds()


async def trend_updater_loop() -> None:
    """后台循环：每天凌晨3点执行一次趋势更新。"""
    while True:
        wait = _seconds_until_3am()
        log_info(f"trend_updater: 下次更新在 {wait/3600:.1f} 小时后（凌晨3点）")
        await asyncio.sleep(wait)
        try:
            await run_trend_update()
        except Exception as e:
            log_error(f"trend_updater_loop 未捕获异常: {e}")
