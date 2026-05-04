"""
P86 — kling-3-pro-i2v 用时序 prompt + 用户模特图 verify
  - 看模型能不能按 0-2s/2-3s/3-5s 时序生成动作
  - 看 NSFW(露出胸罩内层物品)是否通过
"""
import asyncio
import os
import time
import fal_client


MODEL_URL = "https://v3b.fal.media/files/b/0a98c674/5Ykt_GqOkGRDd1HuISlEf_tmp0i7f9q7o.jpg"
ENDPOINT = "fal-ai/kling-video/v2.1-master/image-to-video"  # kling 3 pro i2v 老路
ENDPOINT_FALLBACK = "fal-ai/kling-video/o3/standard/image-to-video"

# 中文 prompt(memory:阿里通义万相中文好,kling 也接受中文)
PROMPT_CN = (
    "图中的女子开始时双手举高,穿着白色无袖背心。"
    "1 秒后,她双手缓慢下移到胸前,抓住背心下摆。"
    "2-3 秒,她慢慢向上拉起背心。"
    "4-5 秒,背心拉到胸口位置,露出里面穿的黑色蕾丝胸罩。"
    "整段一镜到底,9:16 竖屏,客厅背景,自然光线。"
)

# 英文 prompt 备用
PROMPT_EN = (
    "The woman in the image starts with hands raised wearing a white tank top. "
    "After 1 second, she slowly lowers her hands to her chest. "
    "2-3 seconds in, she grasps the bottom of the tank top with both hands. "
    "3-5 seconds, she slowly lifts the tank top up to chest level, revealing a black lace bra underneath. "
    "Single continuous shot, 9:16 portrait, living room background, natural lighting."
)


async def try_endpoint(endpoint, prompt, label):
    print(f"\n=== {label} === {endpoint}")
    print(f"prompt({len(prompt)} chars):\n{prompt}\n")
    args = {
        "image_url": MODEL_URL,
        "prompt": prompt,
        "duration": "5",
        "aspect_ratio": "9:16",
    }
    t0 = time.time()
    try:
        handler = await fal_client.submit_async(endpoint, arguments=args)
        rid = handler.request_id
        print(f"  request_id={rid} submit={time.time()-t0:.1f}s")

        for i in range(90):  # 15 min
            await asyncio.sleep(10)
            try:
                st = await fal_client.status_async(endpoint, rid)
            except Exception as se:
                print(f"  [poll err] {se}")
                continue
            elapsed = int(time.time()-t0)
            print(f"  [{elapsed:4d}s] {type(st).__name__}")
            from fal_client import Completed
            if isinstance(st, Completed):
                res = await fal_client.result_async(endpoint, rid)
                v = (res.get("video") or {}).get("url") if isinstance(res, dict) else None
                print(f"\n  ✅ COMPLETED total={elapsed}s url={v}")
                return v
        print("  ⏱ timeout 15min")
    except Exception as e:
        s = str(e)
        print(f"  ❌ {type(e).__name__}: {s[:300]}")
    return None


async def main():
    if not os.environ.get("FAL_KEY"):
        print("ERR: FAL_KEY required")
        return

    # 中文 prompt 在 v2.1-master
    url1 = await try_endpoint(ENDPOINT, PROMPT_CN, "kling 2.1-master + 中文时序")
    # 如果挂了 fallback 英文 prompt
    if not url1:
        await try_endpoint(ENDPOINT, PROMPT_EN, "kling 2.1-master + 英文时序")

    # 也试 o3/standard 看时序响应
    await try_endpoint(ENDPOINT_FALLBACK, PROMPT_CN, "kling o3/standard + 中文时序")


asyncio.run(main())
