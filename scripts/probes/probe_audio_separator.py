"""
Probe Group 4: 音轨分离能力(BGM ⊥ 人声)

目的:为"复刻工作流"找一个能把原视频音轨拆成
  - 人声(口播,后续直喂 lipsync)
  - BGM(背景音,后续混回成片 + 喂 seedance-2-r2v audio_urls 做节奏参考)
的 fal 端点。

候选(全部 submit 试一遍,真存在/schema 真兼容才作数,不许 pattern-match 推断):
  - fal-ai/playai/audio-isolator
  - fal-ai/sieve/audio-separator
  - fal-ai/audio-separator
  - fal-ai/playht/audio-isolator
  - fal-ai/playht/sieve/audio-separator

输入:14c390bb session 的原视频音轨(已知 60s+,带人声 + BGM)
"""
from __future__ import annotations
import asyncio, os, time, json, urllib.request
from pathlib import Path

import fal_client

# 用户原视频(WebM,带 BGM + 人声) — orig.webm 60s+
INPUT_LOCAL = "/tmp/probe_audio_15s.wav"  # 15s wav,够测分离 + 不被 180s timeout 卡

OUT_DIR = Path("/opt/ssp/uploads/probe-results/audio-sep")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CANDIDATES = [
    # 主候选(已确认存在,前轮超时)
    {"name": "PlayHT audio-isolator", "endpoint": "fal-ai/playht/audio-isolator", "field": "audio_url"},
    # ElevenLabs 系列
    {"name": "ElevenLabs audio-isolation", "endpoint": "fal-ai/elevenlabs/audio-isolation", "field": "audio_url"},
    # 通用 separator 候选
    {"name": "demucs", "endpoint": "fal-ai/demucs", "field": "audio_url"},
    {"name": "demucs-v4", "endpoint": "fal-ai/demucs-v4", "field": "audio_url"},
    {"name": "spleeter", "endpoint": "fal-ai/spleeter", "field": "audio_url"},
    {"name": "stable-audio/source-separation", "endpoint": "fal-ai/stable-audio/source-separation", "field": "audio_url"},
    {"name": "voice-isolator", "endpoint": "fal-ai/voice-isolator", "field": "audio_url"},
    {"name": "lalal-ai", "endpoint": "fal-ai/lalal-ai", "field": "audio_url"},
    {"name": "lalal-ai/stem-splitter", "endpoint": "fal-ai/lalal-ai/stem-splitter", "field": "audio_url"},
    {"name": "audio-extract-vocals", "endpoint": "fal-ai/audio-extract-vocals", "field": "audio_url"},
    {"name": "vocal-remover", "endpoint": "fal-ai/vocal-remover", "field": "audio_url"},
    {"name": "moseca", "endpoint": "fal-ai/moseca", "field": "audio_url"},
    {"name": "mdx-net", "endpoint": "fal-ai/mdx-net", "field": "audio_url"},
]


async def upload_with_retry(local: str) -> str:
    last = None
    for i in range(3):
        try:
            return await fal_client.upload_file_async(local)
        except Exception as e:
            last = e
            await asyncio.sleep(2 ** i)
    raise last


async def probe_one(eng: dict, audio_url: str) -> dict:
    t0 = time.time()
    endpoint = eng.get("endpoint") or eng.get("endpoke")  # typo-tolerant
    args = {eng["field"]: audio_url}
    out = {"engine": eng["name"], "endpoint": endpoint}
    try:
        res = await asyncio.wait_for(
            fal_client.subscribe_async(endpoint, arguments=args),
            timeout=120,
        )
        elapsed = round(time.time() - t0, 1)
        out["elapsed_s"] = elapsed
        out["ok"] = True
        out["raw_keys"] = list(res.keys()) if isinstance(res, dict) else type(res).__name__
        out["raw_sample"] = json.dumps(res, default=str)[:1500]
        # 尝试下载它返回的 vocal/bg 文件保存
        if isinstance(res, dict):
            for k, v in res.items():
                if isinstance(v, dict) and v.get("url"):
                    fname = f"{eng['name'].replace(' ', '_')}__{k}.{v.get('content_type','audio/wav').split('/')[-1].split(';')[0]}"
                    fpath = OUT_DIR / fname
                    try:
                        urllib.request.urlretrieve(v["url"], str(fpath))
                        out.setdefault("downloads", []).append({"key": k, "url": v["url"], "local": str(fpath)})
                    except Exception as e:
                        out.setdefault("download_errors", []).append(f"{k}: {e}")
                elif isinstance(v, list):
                    for i, item in enumerate(v):
                        if isinstance(item, dict) and item.get("url"):
                            fname = f"{eng['name'].replace(' ', '_')}__{k}_{i}.wav"
                            fpath = OUT_DIR / fname
                            try:
                                urllib.request.urlretrieve(item["url"], str(fpath))
                                out.setdefault("downloads", []).append({"key": f"{k}[{i}]", "url": item["url"], "local": str(fpath)})
                            except Exception as e:
                                out.setdefault("download_errors", []).append(f"{k}[{i}]: {e}")
    except asyncio.TimeoutError:
        out["ok"] = False
        out["reason"] = "timeout_180s"
    except Exception as e:
        out["ok"] = False
        out["reason"] = "exception"
        out["error"] = str(e)[:600]
    return out


async def main():
    if not Path(INPUT_LOCAL).exists():
        print(f"INPUT 不存在: {INPUT_LOCAL}")
        return
    print(f"上传输入: {INPUT_LOCAL}")
    audio_url = await upload_with_retry(INPUT_LOCAL)
    print(f"  → {audio_url}")

    results = []
    for eng in CANDIDATES:
        print(f"\n=== 试 {eng['name']} ===")
        r = await probe_one(eng, audio_url)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:500])

    out_path = Path("/root/ssp/scripts/probes/audio_sep_results.json")
    out_path.write_text(json.dumps({
        "input_url": audio_url,
        "input_local": INPUT_LOCAL,
        "results": results,
    }, ensure_ascii=False, indent=2))
    print(f"\n报告:{out_path}")


if __name__ == "__main__":
    asyncio.run(main())
