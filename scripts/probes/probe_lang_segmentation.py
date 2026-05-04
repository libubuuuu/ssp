"""
P86 probe — 用语言自动分割定位"内层胸罩"区域,替代 SAM2 box(白衣浅景失败)。

测试端点(假设可能存在,实际跑试):
  fal-ai/evf-sam-2 / fal-ai/evf-sam2
  fal-ai/grounded-sam-2 / fal-ai/grounded-sam
  fal-ai/sam-2 (with text)
  fal-ai/birefnet
  fal-ai/lang-sam
  fal-ai/sam2/auto

输入:user 真实 driving 视频(穿白T 拉起露胸罩,5s 段)
预期:输出"胸罩"二值 mask 视频(胸罩可见时白,T恤盖住时黑)
"""
from __future__ import annotations
import asyncio
import json
import os
import time
import fal_client


DRIVING_URL = "https://v3b.fal.media/files/b/0a98c690/TvvnB_ZkVX5vHEz7bD8FS_seg_00.mp4"
PROMPTS = [
    "bra",
    "inner garment / bra",
    "lingerie / underwear",
    "the visible bra",
]


CANDIDATES = [
    # (endpoint, args_template_fn)
    ("fal-ai/evf-sam-2", lambda p: {"video_url": DRIVING_URL, "prompt": p}),
    ("fal-ai/evf-sam2", lambda p: {"video_url": DRIVING_URL, "prompt": p}),
    ("fal-ai/grounded-sam-2", lambda p: {"video_url": DRIVING_URL, "prompt": p}),
    ("fal-ai/grounded-sam", lambda p: {"video_url": DRIVING_URL, "prompt": p}),
    ("fal-ai/sam-2/text", lambda p: {"video_url": DRIVING_URL, "prompt": p}),
    ("fal-ai/sam2/auto", lambda p: {"video_url": DRIVING_URL}),
    ("fal-ai/lang-sam", lambda p: {"video_url": DRIVING_URL, "prompt": p}),
    ("fal-ai/birefnet/v2", lambda p: {"video_url": DRIVING_URL}),
    ("fal-ai/segment-anything-2", lambda p: {"video_url": DRIVING_URL, "prompt": p}),
    ("fal-ai/segment-anything-2-video", lambda p: {"video_url": DRIVING_URL, "prompt": p}),
    ("fal-ai/yolov11-segmentation", lambda p: {"video_url": DRIVING_URL}),
    ("fal-ai/cascadepsp", lambda p: {"video_url": DRIVING_URL}),
]


async def try_one(endpoint: str, args: dict, max_wait_s: int = 180) -> dict:
    """Submit + poll up to max_wait_s. Return rich result dict."""
    t0 = time.time()
    out = {"endpoint": endpoint, "args_keys": list(args.keys()), "ok": False}
    try:
        handler = await fal_client.submit_async(endpoint, arguments=args)
        req_id = handler.request_id
        out["request_id"] = req_id

        # poll
        for _ in range(max_wait_s // 5):
            await asyncio.sleep(5)
            try:
                st = await fal_client.status_async(endpoint, req_id)
            except Exception as st_err:
                out["status_error"] = str(st_err)[:200]
                break
            from fal_client import Completed
            if isinstance(st, Completed):
                res = await fal_client.result_async(endpoint, req_id)
                out["ok"] = True
                out["elapsed_s"] = round(time.time() - t0, 1)
                out["result_keys"] = list(res.keys()) if isinstance(res, dict) else type(res).__name__
                # find any url-like field
                if isinstance(res, dict):
                    out["result_sample"] = json.dumps(res, ensure_ascii=False)[:500]
                return out
        out["reason"] = "timeout"
        out["elapsed_s"] = round(time.time() - t0, 1)
    except Exception as e:
        s = str(e)
        out["error"] = s[:300]
        if "not found" in s.lower() or "404" in s:
            out["reason"] = "endpoint_not_found"
        elif "validation" in s.lower() or "missing" in s.lower() or "type" in s.lower():
            out["reason"] = "schema_mismatch"
        else:
            out["reason"] = "other"
    return out


async def main():
    if not os.environ.get("FAL_KEY"):
        print("ERR: FAL_KEY env required")
        return

    results = []
    print(f"# probe lang-segmentation, driving={DRIVING_URL}\n")
    for endpoint, mk_args in CANDIDATES:
        # try first prompt for prompt-based, no prompt for prompt-less
        args = mk_args(PROMPTS[0])
        print(f"→ {endpoint} args={list(args.keys())}")
        r = await try_one(endpoint, args, max_wait_s=120)
        # quick decision
        marker = "✅" if r.get("ok") else ("🔴" if r.get("reason") == "endpoint_not_found" else "⚠️")
        print(f"  {marker} reason={r.get('reason','')} err={r.get('error','')[:120]}")
        if r.get("ok"):
            print(f"  result_keys={r.get('result_keys')}")
            print(f"  sample={r.get('result_sample','')}")
        results.append(r)

    out_file = "/root/ssp/scripts/probes/lang_segmentation_results.json"
    with open(out_file, "w") as f:
        json.dump({"results": results}, f, ensure_ascii=False, indent=2)
    print(f"\nresults → {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
