"""
Probe Group 9:阿里云通义万相 Wan 系列(P47-B 主路候选)

用 14c390bb session 真实素材测试:
  - wan2.7-r2v          参考生视频(主推)
  - wan2.2-animate-mix  视频换人(替代 fal wan-2.2-animate-replace)
  - wanx2.1-vace-plus   VACE 视频编辑(image_reference 模式,5s 段)

DashScope 异步任务模式:
  POST create task → 200 + task_id
  GET tasks/{task_id} → status PENDING/RUNNING/SUCCEEDED/FAILED
  SUCCEEDED 时取 video_url(24h 有效)

key 通过 env DASHSCOPE_API_KEY 读取,不写代码。
"""
from __future__ import annotations
import os
import time
import json
import urllib.request
import asyncio
from pathlib import Path
import httpx

API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not API_KEY:
    raise SystemExit("DASHSCOPE_API_KEY 未设置")

BASE = "https://dashscope.aliyuncs.com/api/v1"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "X-DashScope-Async": "enable",
}

# 公开素材(P44 probe 已上传到 fal storage,URL 公开)
REF_FACE = "https://v3b.fal.media/files/b/0a98aee9/TSfPqBlIwUoey6rbVPUuL_ref_frame.jpg"
DRIVING_VIDEO = "https://v3b.fal.media/files/b/0a98aee9/FADIvK--d7hR-nv2ogcIr_driving_8s.mp4"

OUT_DIR = Path("/opt/ssp/uploads/probe-results/aliyun-wan")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def submit_and_wait(client: httpx.AsyncClient, model: str, input_payload: dict, parameters: dict, timeout_s: int = 900) -> dict:
    """DashScope 异步任务通用逻辑"""
    submit_url = f"{BASE}/services/aigc/video-generation/video-synthesis"
    body = {"model": model, "input": input_payload, "parameters": parameters}
    t0 = time.time()
    r = await client.post(submit_url, headers=HEADERS, json=body, timeout=60)
    if r.status_code != 200:
        return {"error": f"submit {r.status_code}: {r.text[:400]}", "elapsed_s": time.time() - t0}
    task_id = r.json().get("output", {}).get("task_id")
    if not task_id:
        return {"error": f"no task_id: {r.text[:400]}", "elapsed_s": time.time() - t0}

    # poll
    poll_url = f"{BASE}/tasks/{task_id}"
    poll_headers = {"Authorization": f"Bearer {API_KEY}"}
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        await asyncio.sleep(10)
        pr = await client.get(poll_url, headers=poll_headers, timeout=60)
        if pr.status_code != 200:
            continue
        data = pr.json().get("output", {})
        status = data.get("task_status")
        if status == "SUCCEEDED":
            # video_url 在 output.video_url 或 output.results[0].url
            video_url = data.get("video_url") or (data.get("results", [{}])[0].get("url") if data.get("results") else None)
            return {"ok": True, "task_id": task_id, "video_url": video_url, "elapsed_s": time.time() - t0, "raw": json.dumps(data, default=str)[:1500]}
        if status == "FAILED":
            return {"error": f"FAILED: {data.get('message', '?')[:300]}", "task_id": task_id, "elapsed_s": time.time() - t0, "raw": json.dumps(data, default=str)[:1000]}
    return {"error": f"timeout {timeout_s}s", "task_id": task_id, "elapsed_s": time.time() - t0}


async def main():
    async with httpx.AsyncClient() as client:
        results = []

        # 1) wan2.7-r2v(参考生视频,主推)
        print("\n=== wan2.7-r2v ===")
        r = await submit_and_wait(
            client, "wan2.7-r2v",
            input_payload={
                "media": [
                    {"type": "reference_image", "url": REF_FACE},
                    {"type": "reference_video", "url": DRIVING_VIDEO},
                ],
                "prompt": "图1中的女性按照视频1中的动作和姿态自然展示,保留视频1的背景和镜头角度。",
            },
            parameters={"resolution": "720P", "duration": 5, "ratio": "9:16"},
        )
        if r.get("ok"):
            print(f"  ✅ {r['elapsed_s']:.1f}s, video={r.get('video_url','?')[:80]}")
            if r.get("video_url"):
                local = OUT_DIR / "wan2.7-r2v.mp4"
                urllib.request.urlretrieve(r["video_url"], str(local))
                print(f"  saved {local} ({local.stat().st_size//1024}KB)")
                r["local"] = str(local)
        else:
            print(f"  ❌ {r.get('error', '?')[:300]}")
        results.append({"engine": "wan2.7-r2v", **r})

        # 2) wan2.2-animate-mix(视频换人,角色图 + 动作视频)
        print("\n=== wan2.2-animate-mix ===")
        r = await submit_and_wait(
            client, "wan2.2-animate-mix",
            input_payload={
                "image_url": REF_FACE,
                "video_url": DRIVING_VIDEO,
            },
            parameters={"resolution": "720P"},
        )
        if r.get("ok"):
            print(f"  ✅ {r['elapsed_s']:.1f}s, video={r.get('video_url','?')[:80]}")
            if r.get("video_url"):
                local = OUT_DIR / "wan2.2-animate-mix.mp4"
                try:
                    urllib.request.urlretrieve(r["video_url"], str(local))
                    print(f"  saved {local} ({local.stat().st_size//1024}KB)")
                except Exception as e:
                    print(f"  download failed: {e}")
        else:
            print(f"  ❌ {r.get('error', '?')[:300]}")
        results.append({"engine": "wan2.2-animate-mix", **r})

        # 3) wanx2.1-vace-plus(VACE image_reference 模式,5s)
        print("\n=== wanx2.1-vace-plus image_reference ===")
        r = await submit_and_wait(
            client, "wanx2.1-vace-plus",
            input_payload={
                "function": "image_reference",
                "image_reference_urls": [REF_FACE],
                "prompt": "一位女性自然展示,保留参考图人物特征,自然真实",
            },
            parameters={"size": "720*1280"},
        )
        if r.get("ok"):
            print(f"  ✅ {r['elapsed_s']:.1f}s, video={r.get('video_url','?')[:80]}")
        else:
            print(f"  ❌ {r.get('error', '?')[:300]}")
        results.append({"engine": "wanx2.1-vace-plus", **r})

        # 写报告
        out = Path("/root/ssp/scripts/probes/aliyun_wan_results.json")
        # 不落盘 raw(可能含敏感信息),只 elapsed + ok + error
        clean = [{k: v for k, v in r.items() if k != "raw"} for r in results]
        out.write_text(json.dumps({"results": clean}, ensure_ascii=False, indent=2))
        print(f"\n报告:{out}")


if __name__ == "__main__":
    asyncio.run(main())
