"""
Probe Group 6: fal 上的 face swap / identity preservation / restoration 端点

目的:为"残差身份漂移"找到 fal hosted 解,工作流末尾加身份强化
  - codeformer:出片后修脸(已知存在,只接图)
  - face-swap:把用户上传模特脸 swap 到生成视频(若支持视频)
  - instant-id / ip-adapter:生成阶段强化身份(配合 r2v 用)
  - reactor / inswapper:开源 face swap 是否 host 了

候选(全部 submit 试,不许 pattern-match 推断):
"""
from __future__ import annotations
import asyncio, time, json
from pathlib import Path
import fal_client

REF_FACE = "https://v3b.fal.media/files/b/0a98aee9/TSfPqBlIwUoey6rbVPUuL_ref_frame.jpg"
DRIVING_VIDEO = "https://v3b.fal.media/files/b/0a98aee9/FADIvK--d7hR-nv2ogcIr_driving_8s.mp4"

OUT_DIR = Path("/opt/ssp/uploads/probe-results/face-endpoints")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 一组端点 + 一组试出来 schema 的 args
CANDIDATES = [
    # CodeFormer:已知存在,验证 video_url 是否支持(若支持,工作流不用拆帧)
    {"name": "codeformer (image only?)", "endpoint": "fal-ai/codeformer",
     "args": {"image_url": REF_FACE, "fidelity_weight": 0.7}},
    {"name": "codeformer + video_url 试", "endpoint": "fal-ai/codeformer",
     "args": {"video_url": DRIVING_VIDEO, "fidelity_weight": 0.7}},

    # face-swap 系列(image)
    {"name": "face-swap (image)", "endpoint": "fal-ai/face-swap",
     "args": {"source_image_url": REF_FACE, "target_image_url": REF_FACE}},
    {"name": "face-swap (alt fields)", "endpoint": "fal-ai/face-swap",
     "args": {"face_image_url": REF_FACE, "target_image_url": REF_FACE}},

    # InsightFace 系列
    {"name": "insightface-face-swap", "endpoint": "fal-ai/insightface-face-swap",
     "args": {"source_image_url": REF_FACE, "target_image_url": REF_FACE}},
    {"name": "easel/advanced-face-swap", "endpoint": "easel-ai/advanced-face-swap",
     "args": {"source_image_url": REF_FACE, "target_image_url": REF_FACE}},

    # InstantID
    {"name": "instant-id", "endpoint": "fal-ai/instant-id",
     "args": {"face_image_url": REF_FACE, "prompt": "a portrait, photorealistic"}},
    {"name": "instant-character", "endpoint": "fal-ai/instant-character",
     "args": {"image_url": REF_FACE, "prompt": "a portrait, photorealistic"}},

    # IP-Adapter Face ID
    {"name": "ip-adapter-face-id", "endpoint": "fal-ai/ip-adapter-face-id",
     "args": {"face_image_url": REF_FACE, "prompt": "a portrait, photorealistic"}},

    # PuLID(身份保持新 SOTA)
    {"name": "pulid", "endpoint": "fal-ai/pulid",
     "args": {"reference_image_url": REF_FACE, "prompt": "a portrait, photorealistic"}},
    {"name": "flux-pulid", "endpoint": "fal-ai/flux-pulid",
     "args": {"reference_image_url": REF_FACE, "prompt": "a portrait, photorealistic"}},

    # PhotoMaker
    {"name": "photomaker", "endpoint": "fal-ai/photomaker",
     "args": {"image_archive_url": REF_FACE, "prompt": "a portrait img, photorealistic"}},

    # ReActor
    {"name": "reactor", "endpoint": "fal-ai/reactor",
     "args": {"source_image_url": REF_FACE, "target_image_url": REF_FACE}},

    # 视频 face swap?
    {"name": "video-face-swap", "endpoint": "fal-ai/video-face-swap",
     "args": {"source_image_url": REF_FACE, "target_video_url": DRIVING_VIDEO}},
    {"name": "easel/face-swap-video", "endpoint": "easel-ai/face-swap-video",
     "args": {"source_image_url": REF_FACE, "target_video_url": DRIVING_VIDEO}},
]


async def probe(eng: dict, timeout: int = 240) -> dict:
    t0 = time.time()
    out = {"engine": eng["name"], "endpoint": eng["endpoint"]}
    try:
        res = await asyncio.wait_for(
            fal_client.subscribe_async(eng["endpoint"], arguments=eng["args"]),
            timeout=timeout,
        )
        out["elapsed_s"] = round(time.time() - t0, 1)
        out["ok"] = True
        out["raw_keys"] = list(res.keys()) if isinstance(res, dict) else type(res).__name__
        out["raw_sample"] = json.dumps(res, default=str)[:600]
    except asyncio.TimeoutError:
        out["ok"] = False
        out["reason"] = "timeout"
    except Exception as e:
        out["ok"] = False
        msg = str(e)
        if "not found" in msg.lower():
            out["reason"] = "endpoint_not_found"
        elif "missing" in msg.lower() or "field required" in msg.lower():
            out["reason"] = "schema_mismatch"
        elif "policy" in msg.lower() or "validation" in msg.lower():
            out["reason"] = "content_policy"
        else:
            out["reason"] = "other"
        out["error"] = msg[:400]
    return out


async def main():
    results = []
    for eng in CANDIDATES:
        print(f"\n=== {eng['name']} ({eng['endpoint']}) ===")
        r = await probe(eng)
        results.append(r)
        if r.get("ok"):
            print(f"  ✅ {r['elapsed_s']}s  keys={r.get('raw_keys')}")
            print(f"  raw: {r.get('raw_sample','')[:300]}")
        else:
            print(f"  ❌ {r.get('reason','?')}: {r.get('error','')[:200]}")

    out_path = Path("/root/ssp/scripts/probes/face_endpoints_results.json")
    out_path.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    print(f"\n报告:{out_path}")

    # 总结
    ok_list = [r for r in results if r.get("ok")]
    print(f"\n=== 真存在且通过的端点({len(ok_list)}个)===")
    for r in ok_list:
        print(f"  ✅ {r['endpoint']}  ({r['elapsed_s']}s)  keys={r.get('raw_keys')}")


if __name__ == "__main__":
    asyncio.run(main())
