"""
Probe Group 10:阿里通义万相 P47-C 全免费工作流 — lipsync / ASR / image-edit

3 路 verify(全部用 14c390bb session 真实素材):
1) wan2.2-s2v(视频 lipsync)— 替代 fal veed lipsync
2) paraformer-v2(录音文件识别)— 替代 fal whisper
3) qwen-image-edit(图像编辑/VTON-like)— 替代 fal cat-vton

key 通过 env DASHSCOPE_API_KEY,probe 失败不计成功秒数(免费配额不扣)
"""
from __future__ import annotations
import os, time, json, asyncio
from pathlib import Path
import httpx

API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not API_KEY:
    raise SystemExit("DASHSCOPE_API_KEY 未设置")

BASE = "https://dashscope.aliyuncs.com"
HEADERS_ASYNC = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "X-DashScope-Async": "enable",
}
HEADERS_SYNC = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

# 公开素材
REF_FACE = "https://v3b.fal.media/files/b/0a98aee9/TSfPqBlIwUoey6rbVPUuL_ref_frame.jpg"
DRIVING_VIDEO = "https://v3b.fal.media/files/b/0a98aee9/FADIvK--d7hR-nv2ogcIr_driving_8s.mp4"
# 用户原音轨(P44 demucs 已分离)
USER_AUDIO = "/opt/ssp/uploads/probe-results/audio-sep/demucs__vocals.mpeg"


async def submit_async_task(client, url, body, timeout_s=900):
    t0 = time.time()
    r = await client.post(url, headers=HEADERS_ASYNC, json=body, timeout=60)
    if r.status_code != 200:
        return {"error": f"submit {r.status_code}: {r.text[:300]}"}
    task_id = r.json().get("output", {}).get("task_id")
    if not task_id:
        return {"error": f"no task_id: {r.text[:300]}"}
    poll_url = f"{BASE}/api/v1/tasks/{task_id}"
    poll_headers = {"Authorization": f"Bearer {API_KEY}"}
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        await asyncio.sleep(10)
        pr = await client.get(poll_url, headers=poll_headers, timeout=60)
        if pr.status_code != 200: continue
        data = pr.json().get("output", {})
        status = data.get("task_status")
        if status == "SUCCEEDED":
            return {"ok": True, "elapsed_s": time.time() - t0, "data": data}
        if status == "FAILED":
            return {"error": f"FAILED: {data.get('message','?')[:200]}", "elapsed_s": time.time() - t0}
    return {"error": f"timeout {timeout_s}s", "elapsed_s": time.time() - t0}


async def main():
    async with httpx.AsyncClient() as client:
        # 1) wan2.2-s2v 视频 lipsync(图 + 音 → 视频)
        print("\n=== 1) wan2.2-s2v 视频 lipsync ===")
        # 上传 user audio 到公网(借 fal storage)
        import fal_client
        if Path(USER_AUDIO).exists():
            audio_url = await fal_client.upload_file_async(USER_AUDIO)
            print(f"  audio uploaded → {audio_url[:80]}")
        else:
            audio_url = "https://v3b.fal.media/files/b/0a98aea4/m-wnO-nzguIuII0Z9oDH3_probe_audio_15s.wav"
            print(f"  fallback audio: {audio_url[:80]}")
        r = await submit_async_task(client,
            f"{BASE}/api/v1/services/aigc/video-generation/video-synthesis",
            body={
                "model": "wan2.2-s2v",
                "input": {"image_url": REF_FACE, "audio_url": audio_url},
                "parameters": {"resolution": "720P"},
            },
            timeout_s=600,
        )
        if r.get("ok"):
            v = r["data"].get("video_url") or (r["data"].get("results", [{}])[0].get("url") if r["data"].get("results") else None)
            print(f"  ✅ {r['elapsed_s']:.1f}s,video={str(v)[:80]}")
        else:
            print(f"  ❌ {r.get('error','?')[:300]}")

        # 2) paraformer-v2 录音文件识别(ASR)
        print("\n=== 2) paraformer-v2 ASR ===")
        t0 = time.time()
        r = await client.post(
            f"{BASE}/api/v1/services/audio/asr/transcription",
            headers=HEADERS_ASYNC,
            json={"model": "paraformer-v2", "input": {"file_urls": [audio_url]}, "parameters": {"language_hints": ["zh"]}},
            timeout=60,
        )
        if r.status_code == 200:
            tid = r.json().get("output", {}).get("task_id")
            print(f"  submit OK,task_id={tid}")
            for _ in range(30):
                await asyncio.sleep(5)
                pr = await client.get(f"{BASE}/api/v1/tasks/{tid}", headers={"Authorization": f"Bearer {API_KEY}"}, timeout=30)
                if pr.status_code != 200: continue
                d = pr.json().get("output", {})
                if d.get("task_status") == "SUCCEEDED":
                    print(f"  ✅ {time.time()-t0:.1f}s,results={json.dumps(d.get('results', []), default=str)[:300]}")
                    break
                if d.get("task_status") == "FAILED":
                    print(f"  ❌ FAILED: {d.get('message','?')[:200]}")
                    break
            else:
                print(f"  ⚠ timeout 150s")
        else:
            print(f"  ❌ submit {r.status_code}: {r.text[:300]}")

        # 3) qwen-image-edit(VTON-like 模特穿衣)
        print("\n=== 3) qwen-image-edit ===")
        # 同步调用方式
        product_url = "https://v3b.fal.media/files/b/0a98ae8d/kj0lQTF_pka0WPDK-S_p6_orig.webm"  # 用户视频(测试用,实际应该是产品图)
        r = await client.post(
            f"{BASE}/api/v1/services/aigc/multimodal-generation/generation",
            headers=HEADERS_SYNC,
            json={
                "model": "qwen-image-edit",
                "input": {"messages": [{"role": "user", "content": [
                    {"image": REF_FACE},
                    {"text": "把这位女性的服装换成红色短裙,保留面部特征,自然真实"},
                ]}]},
                "parameters": {"size": "1024*1024"},
            },
            timeout=120,
        )
        if r.status_code == 200:
            print(f"  ✅ {r.text[:400]}")
        else:
            print(f"  ❌ {r.status_code}: {r.text[:300]}")


if __name__ == "__main__":
    asyncio.run(main())
