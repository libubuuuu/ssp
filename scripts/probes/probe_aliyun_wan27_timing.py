"""
P86 真路 — 阿里 wan2.7-r2v 用"时序动作 prompt + 多图 reference" 跑用户真实素材

输入:
  - 模特图(用户上传)
  - 产品图(用户上传,黑色 bra)
  - prompt:0-2s 穿白色T恤站着 / 2-3s 拉起T恤 / 3-5s 露出胸罩
输出:
  - 5s 视频(720P 9:16)
  - 验证模型能否真按时序生成动作序列
"""
from __future__ import annotations
import asyncio
import os
import sys
import time
sys.path.insert(0, "/opt/ssp/backend")
from app.services.fal_service import AliyunWanService


MODEL_URL = "https://v3b.fal.media/files/b/0a98c674/5Ykt_GqOkGRDd1HuISlEf_tmp0i7f9q7o.jpg"
PRODUCT_URL = "https://v3b.fal.media/files/b/0a98c675/89Dg_cYoBjv0-DjbSLr6v_tmp_w3_2wz2.jpg"

# 中文时序 prompt(阿里通义万相是中文模型,中文效果更好)
PROMPT_CN = (
    "图1的女子穿着白色无袖背心,站在明亮的客厅,白墙背景,自然光线。"
    "0-2 秒:她保持原姿势,双手举起。"
    "2-3 秒:她双手缓慢抓住白色背心下摆,慢慢向上拉起。"
    "3-5 秒:背心完全拉起,露出图2 中的黑色蕾丝胸罩(内层物品)。"
    "整段一镜到底,9:16 竖屏,光线一致,真实人像视频风格。"
)


async def main():
    api_key = os.environ.get("DASHSCOPE_API_KEY") or ""
    if not api_key:
        print("ERR: DASHSCOPE_API_KEY required")
        return
    print(f"DASHSCOPE_API_KEY={api_key[:15]}...")

    svc = AliyunWanService()
    if svc.api_key != api_key:
        # service uses settings; force-set if needed
        svc.api_key = api_key

    print(f"\nprompt({len(PROMPT_CN)} chars):\n{PROMPT_CN}\n")
    print(f"refs: model={MODEL_URL[:80]}\n      product={PRODUCT_URL[:80]}\n")

    t0 = time.time()
    print("submit...")
    sub = await svc.wan27_r2v_submit(
        reference_image_url="",
        reference_video_url=None,
        prompt=PROMPT_CN,
        duration=5,
        resolution="720P",
        ratio="9:16",
        reference_image_urls=[MODEL_URL, PRODUCT_URL],
    )
    if "error" in sub:
        print(f"SUBMIT FAIL: {sub['error']}")
        return
    task_id = sub["task_id"]
    print(f"task_id={task_id} submit_elapsed={time.time()-t0:.1f}s")

    # poll up to 25min
    for i in range(150):
        await asyncio.sleep(10)
        st = await svc.poll_task(task_id)
        elapsed = int(time.time()-t0)
        s = st.get("status", "?")
        print(f"  [{elapsed:4d}s] status={s}")
        if s == "SUCCEEDED":
            print(f"\n✅ video_url={st.get('video_url')}")
            return
        if s == "FAILED":
            print(f"\n❌ failed: {st.get('error')}")
            return
        if "error" in st:
            print(f"  poll error: {st['error']}")
            await asyncio.sleep(10)
    print("⏱ TIMEOUT 25min")


asyncio.run(main())
