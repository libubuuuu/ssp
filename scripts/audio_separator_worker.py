#!/usr/bin/env python3
"""
P47-D 本地 demucs 音轨分离 worker(替代 fal-ai/demucs,省 ¥0.35/session)

CPU 推理(htdemucs 4-stem 模型,80MB),--two-stems vocals 模式直接输出:
  vocals.mp3:用户口播(送 lipsync 用)
  no_vocals.mp3:BGM(其他乐器,后处理 ffmpeg amix 进 final)

调用约定(由 backend subprocess.run 发起):
  python audio_separator_worker.py AUDIO_INPUT VOCALS_OUT BGM_OUT

环境变量(必须):
  TORCH_HOME=/opt/ssp/face_models/torch_hub_cache(模型缓存,ssp-app 可读)

退出码:
  0  成功(VOCALS_OUT 和 BGM_OUT 都生成)
  1  参数 / 文件错误
  2  demucs 推理异常

实测(2 vCPU + 3.6G RAM):
  17.5s 音频 → 31s 处理(1.77x realtime)
  30s 音频预计 ~55s 处理
"""
from __future__ import annotations
import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path

# 必须在 import torch 之前设置 TORCH_HOME
os.environ.setdefault("TORCH_HOME", "/opt/ssp/face_models/torch_hub_cache")


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: audio_separator_worker.py AUDIO_IN VOCALS_OUT BGM_OUT", file=sys.stderr)
        return 1

    audio_in = sys.argv[1]
    vocals_out = sys.argv[2]
    bgm_out = sys.argv[3]

    if not Path(audio_in).is_file():
        print(f"audio_in 不存在: {audio_in}", file=sys.stderr)
        return 1

    Path(vocals_out).parent.mkdir(parents=True, exist_ok=True)
    Path(bgm_out).parent.mkdir(parents=True, exist_ok=True)

    work_dir = Path(tempfile.mkdtemp(prefix="demucs_"))
    try:
        # demucs --two-stems vocals 输出 vocals.mp3 + no_vocals.mp3
        cmd = [
            "/opt/ssp/face_venv/bin/demucs",
            "-d", "cpu",
            "-n", "htdemucs",
            "--two-stems", "vocals",
            "--mp3",
            "--mp3-bitrate", "128",
            "-o", str(work_dir),
            audio_in,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            print("demucs timeout 600s", file=sys.stderr)
            return 2

        if r.returncode != 0:
            print(f"demucs rc={r.returncode}: {r.stderr[:500]}", file=sys.stderr)
            return 2

        # 输出在 work_dir/htdemucs/<basename>/vocals.mp3 + no_vocals.mp3
        basename = Path(audio_in).stem
        out_root = work_dir / "htdemucs" / basename
        v = out_root / "vocals.mp3"
        nv = out_root / "no_vocals.mp3"

        if not v.exists() or not nv.exists():
            print(f"demucs 输出文件缺失: vocals={v.exists()} bgm={nv.exists()}", file=sys.stderr)
            return 2

        shutil.copy2(str(v), vocals_out)
        shutil.copy2(str(nv), bgm_out)
        os.chmod(vocals_out, 0o644)
        os.chmod(bgm_out, 0o644)
        print(f"OK vocals={Path(vocals_out).stat().st_size//1024}KB bgm={Path(bgm_out).stat().st_size//1024}KB")
        return 0
    finally:
        shutil.rmtree(str(work_dir), ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"unexpected: {e}", file=sys.stderr)
        sys.exit(2)
