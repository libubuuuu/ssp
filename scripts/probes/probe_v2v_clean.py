"""
P86 — 回到 v2v_dual 配方,只改 driving OCR + prompt 精确度
v2v_dual 的成功:fal-ai/kling-video/o1/video-to-video/reference
                + driving + reference_image_urls=[模特,产品] + 时序 prompt
本次只动:
  - driving:用之前 ffmpeg delogo 去 OCR 的 drv_no_ocr
  - prompt:精确写黑色 V-neck triangle wireless bra + 反向锁 balconette/underwire
其他维持 v2v_dual 不变(端点/reference 顺序/双图 ref)
"""
import asyncio
import os
import time
import urllib.request
import fal_client


# 干净 driving (上轮 ffmpeg delogo 已生成)
DRV_CLEAN = "https://v3b.fal.media/files/b/0a98d64e/xnavBB0isCoaEym16xqtu_driving_no_ocr.mp4"
MODEL_URL = "https://v3b.fal.media/files/b/0a98c674/5Ykt_GqOkGRDd1HuISlEf_tmp0i7f9q7o.jpg"
PRODUCT_URL = "https://v3b.fal.media/files/b/0a98c675/89Dg_cYoBjv0-DjbSLr6v_tmp_w3_2wz2.jpg"

# v2v_dual 端点(用户认可这条)
V2V_REF = "fal-ai/kling-video/o1/video-to-video/reference"

# 精确产品款式 + 时序动作 + 反向锁误导
PROMPT = (
    "The woman in image 1 wears a white tank top. She uses both hands to firmly grasp "
    "the bottom edge of the white tank top and pull it upward, gradually exposing the "
    "exact black item shown in image 2 (black soft V-neck triangle wireless bra with "
    "thick athletic straps, plain black fabric, no underwire, no balconette, no lace). "
    "Preserve original background, lighting, composition. Single take, smooth motion."
)


async def main():
    if not os.environ.get("FAL_KEY"):
        return

    print(f"endpoint: {V2V_REF}")
    print(f"driving (clean OCR): {DRV_CLEAN[:80]}")
    print(f"refs: model={MODEL_URL[:60]} | product={PRODUCT_URL[:60]}")
    print(f"prompt({len(PROMPT)} chars):\n{PROMPT}\n")

    t0 = time.time()
    h = await fal_client.submit_async(V2V_REF, arguments={
        "video_url": DRV_CLEAN,
        "reference_image_urls": [MODEL_URL, PRODUCT_URL],
        "prompt": PROMPT,
    })
    rid = h.request_id
    print(f"rid={rid}")

    for _ in range(120):  # 20min cap
        await asyncio.sleep(10)
        try:
            s = await fal_client.status_async(V2V_REF, rid)
        except Exception as e:
            print(f"  poll err {e}")
            continue
        elapsed = int(time.time()-t0)
        print(f"  [{elapsed:4d}s] {type(s).__name__}")
        from fal_client import Completed
        if isinstance(s, Completed):
            try:
                res = await fal_client.result_async(V2V_REF, rid)
                v = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
                print(f"\n✅ total={elapsed}s url={v}")
                if v:
                    urllib.request.urlretrieve(v, "/tmp/seg0_inspect/v2v_dual_v2.mp4")
                    print("→ /tmp/seg0_inspect/v2v_dual_v2.mp4")
                return
            except Exception as re:
                print(f"❌ result err {str(re)[:300]}")
                return
    print("⏱ timeout 20min")


asyncio.run(main())
