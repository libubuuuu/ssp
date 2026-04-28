"""七十一续:studio_workspace/ 自动 GC — cron 每天 04:00 跑

清理:
1. studio_workspace/_uploading/ 下超 24h 老分片目录(用户上传一半弃 / 网断)
2. studio_workspace/<session_id>/ 下超 24h 老 session 目录(原视频 + segments + 成品)

设计:**不 import 任何业务模块**(防 JWT_SECRET 等 env 依赖,cron 直接跑),
只用标准库 + 文件系统 mtime 判定。
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path


def gc_dir(parent: Path, hours: int, label: str, only_subdirs_pattern=None) -> dict:
    """删 parent 下 mtime 超 hours 的子目录"""
    cutoff = time.time() - hours * 3600
    scanned = deleted = freed = 0
    if not parent.exists():
        return {"scanned": 0, "deleted": 0, "freed_bytes": 0}
    for d in parent.iterdir():
        if not d.is_dir():
            continue
        if only_subdirs_pattern and not only_subdirs_pattern(d):
            continue
        scanned += 1
        try:
            if d.stat().st_mtime < cutoff:
                size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
                shutil.rmtree(d, ignore_errors=True)
                deleted += 1
                freed += size
        except OSError as e:
            print(f"  {label} 跳过 {d.name}: {e}", file=sys.stderr)
    return {"scanned": scanned, "deleted": deleted, "freed_bytes": freed}


def main(studio_dir: str, hours: int) -> int:
    studio_root = Path(studio_dir)
    if not studio_root.exists():
        print(f"❌ studio_dir 不存在: {studio_root}")
        return 1

    print(f"=== studio GC [{time.strftime('%Y-%m-%d %H:%M:%S')}] ===")
    print(f"工作区: {studio_root} (hours={hours})")

    # 1. _uploading/ 老分片
    uploading = studio_root / "_uploading"
    up_res = gc_dir(uploading, hours, "uploads") if uploading.exists() else {"scanned": 0, "deleted": 0, "freed_bytes": 0}
    print(f"_uploading/: scan={up_res['scanned']} del={up_res['deleted']} freed={up_res['freed_bytes']/1024/1024:.1f}MB")

    # 2. session 目录(直接 STUDIO_DIR 下,排除 _ 开头 + sessions.json)
    sess_res = gc_dir(studio_root, hours, "sessions",
                      only_subdirs_pattern=lambda d: not d.name.startswith("_"))
    print(f"sessions: scan={sess_res['scanned']} del={sess_res['deleted']} freed={sess_res['freed_bytes']/1024/1024:.1f}MB")

    total_mb = (up_res["freed_bytes"] + sess_res["freed_bytes"]) / 1024 / 1024
    print(f"总释放: {total_mb:.1f}MB")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="studio_workspace 自动 GC")
    parser.add_argument("--studio-dir", default="/opt/ssp/studio_workspace",
                        help="工作区根目录,默认 /opt/ssp/studio_workspace")
    parser.add_argument("--hours", type=int, default=24, help="保留 N 小时,默认 24")
    args = parser.parse_args()
    sys.exit(main(args.studio_dir, args.hours))
