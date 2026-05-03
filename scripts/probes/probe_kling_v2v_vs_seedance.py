"""
Probe Group 11:Kling o3 v2v 复刻 vs Seedance 2.0 r2v vs 现有 kling-3-pro i2v

用 14c390bb session 真实素材(driving 视频 + 用户原模特图)实测三引擎,
并发跑 + similarity 评分,数据驱动决定 P49 模型 B 换不换 v2v。

目标:
  - kling-o3-v2v/reference 真复刻 driving 动作的 similarity 是不是真比 i2v 高
  - seedance-2-r2v(用户问的对比项)真值 similarity
  - 跟 kling-3-pro-i2v(0.4096 基线)对比
"""
from __future__ import annotations
import os, time, json, asyncio, urllib.request, subprocess
from pathlib import Path
import fal_client

REF_FACE = "https://v3b.fal.media/files/b/0a98aee9/TSfPqBlIwUoey6rbVPUuL_ref_frame.jpg"
DRIVING_VIDEO = "https://v3b.fal.media/files/b/0a98aee9/FADIvK--d7hR-nv2ogcIr_driving_8s.mp4"
SRC_FACE_LOCAL = "/opt/ssp/uploads/probe-results/sd-enterprise-pixverse/ref_frame.jpg"

OUT_DIR = Path("/opt/ssp/uploads/probe-results/kling-v2v-vs-seedance")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def submit_and_poll(endpoint, args, timeout=900):
    t0 = time.time()
    try:
        handler = await fal_client.submit_async(endpoint, arguments=args)
        tid = handler.request_id
        for _ in range(timeout // 10):
            await asyncio.sleep(10)
            s = await fal_client.status_async(endpoint, tid, with_logs=False)
            if type(s).__name__ == "Completed":
                final = await fal_client.result_async(endpoint, tid)
                v = final.get("video") if isinstance(final, dict) else None
                url = v.get("url") if isinstance(v, dict) else None
                return {"ok": bool(url), "url": url, "elapsed_s": time.time() - t0}
        return {"ok": False, "elapsed_s": time.time() - t0, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "elapsed_s": time.time() - t0, "error": str(e)[:300]}


def score_similarity(video_local: str) -> float:
    """跑 face_similarity_worker 评分"""
    try:
        r = subprocess.run([
            "/opt/ssp/face_venv/bin/python",
            "/opt/ssp/scripts/face_similarity_worker.py",
            SRC_FACE_LOCAL, video_local, "0.5",
        ], capture_output=True, text=True, timeout=180)
        for ln in (r.stdout or "").splitlines():
            if ln.startswith("SCORE="):
                return float(ln[len("SCORE="):])
        return -1.0
    except Exception:
        return -1.0


async def main():
    # 三个引擎并发跑(节省时间)
    tasks = []

    # 1) kling-o3-v2v/reference(真 v2v 复刻 driving)
    PROMPT = (
        "Primary identity anchor: @Element1. Do NOT alter facial proportions, "
        "eye spacing, nose shape, jawline, hair, or skin tone. "
        "Replace the person in @Video1 with @Element1. "
        "Preserve the original motion, gestures, and camera movement. "
        "No face distortion, no wardrobe changes."
    )
    elements = [{"frontal_image_url": REF_FACE, "reference_image_urls": [REF_FACE]}]
    tasks.append(("kling-o3-v2v", "fal-ai/kling-video/o3/pro/video-to-video/reference", {
        "prompt": PROMPT,
        "video_url": DRIVING_VIDEO,
        "image_urls": [REF_FACE],
        "elements": elements,
        "duration": "5",
        "aspect_ratio": "9:16",
        "keep_audio": False,
    }))

    # 2) seedance-2-r2v(用户对比项)
    SD_PROMPT = (
        "Primary identity anchor: @Image1. Do NOT alter facial proportions, "
        "eye spacing, nose shape, jawline, hair, or skin tone. "
        "@Image1 performing the same actions and movements as in @Video1. "
        "Preserve the original background and camera angle from @Video1 exactly. "
        "No face distortion."
    )
    tasks.append(("seedance-2-r2v", "fal-ai/bytedance/seedance-2.0/reference-to-video", {
        "prompt": SD_PROMPT,
        "image_urls": [REF_FACE],
        "video_urls": [DRIVING_VIDEO],
        "duration": "5",
        "resolution": "720p",
        "aspect_ratio": "auto",
        "generate_audio": False,
    }))

    # 3) kling-3-pro i2v(基线对比)
    tasks.append(("kling-3-pro-i2v", "fal-ai/kling-video/v3/pro/image-to-video", {
        "image_url": REF_FACE,
        "prompt": "A young woman naturally showcasing herself, smooth body movement, photorealistic UGC selfie style. Do NOT alter facial proportions or hair.",
        "duration": "5",
        "aspect_ratio": "9:16",
    }))

    print(f"=== 并发跑 3 引擎 ===")
    results_async = await asyncio.gather(*[
        submit_and_poll(ep, args) for _, ep, args in tasks
    ])

    final_results = []
    for (name, endpoint, _), r in zip(tasks, results_async):
        out = {"engine": name, "endpoint": endpoint, **r}
        if r.get("ok") and r.get("url"):
            local = OUT_DIR / f"{name}.mp4"
            try:
                urllib.request.urlretrieve(r["url"], str(local))
                out["local"] = str(local)
                out["size_mb"] = round(local.stat().st_size / 1e6, 2)
            except Exception as e:
                out["download_err"] = str(e)[:100]
        final_results.append(out)
        print(f"\n--- {name} ---")
        print(f"  {'✅' if r.get('ok') else '❌'} elapsed={r.get('elapsed_s', 0):.1f}s")
        if r.get("error"):
            print(f"  err: {r['error'][:200]}")

    # similarity 评分(串行,因为 inswapper 单进程内存占用)
    print(f"\n=== similarity 评分 ===")
    for r in final_results:
        if r.get("local"):
            s = score_similarity(r["local"])
            r["similarity"] = s
            print(f"  {r['engine']}: SCORE={s:.4f}")
        else:
            r["similarity"] = -1.0

    # 总结
    print(f"\n=== 真值对比表 ===")
    print(f"{'Engine':<25} {'Time':>8} {'Score':>8}  {'Strength'}")
    for r in sorted(final_results, key=lambda x: -x.get("similarity", -1)):
        s = r.get("similarity", -1)
        strength = "⭐⭐" if s >= 0.45 else "⭐" if s >= 0.40 else "·" if s >= 0.30 else "❌"
        print(f"{r['engine']:<25} {r.get('elapsed_s', 0):>7.1f}s {s:>8.4f}  {strength}")

    out_path = Path("/root/ssp/scripts/probes/kling_v2v_vs_seedance_results.json")
    out_path.write_text(json.dumps({"results": final_results}, ensure_ascii=False, indent=2))
    print(f"\n报告:{out_path}")


if __name__ == "__main__":
    asyncio.run(main())
