"""P221 V2 Path B 本地缓存测试。

覆盖:
- cache_path SHA256 校验(防路径注入)
- store 移文件 + dedupe
- try_get hit / miss
- clean_old 过期清理
"""
import hashlib
import os
import time
from pathlib import Path

import pytest

from app.services import video_clone_v2_cache as cache_mod


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    """每个 case 用独立 tmp 目录,免得撞到真 /tmp/v2_cache。"""
    monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path / "v2_cache")
    return tmp_path / "v2_cache"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ─── cache_path 校验 ──────────────────────────────────────────

class TestCachePath:
    def test_valid_sha256(self, tmp_cache):
        sha = _sha256(b"hello")
        p = cache_mod.cache_path(sha)
        assert p.name == f"{sha}.mp4"
        assert p.parent == tmp_cache

    def test_uppercase_normalized(self, tmp_cache):
        sha = _sha256(b"x").upper()
        p = cache_mod.cache_path(sha)
        # 内部小写
        assert p.name == f"{sha.lower()}.mp4"

    def test_too_short_rejected(self, tmp_cache):
        with pytest.raises(ValueError):
            cache_mod.cache_path("abc")

    def test_path_traversal_rejected(self, tmp_cache):
        # 长度 ok 但有非 hex → 拒绝
        with pytest.raises(ValueError):
            cache_mod.cache_path("../../etc/passwd" + "0" * 48)
        with pytest.raises(ValueError):
            cache_mod.cache_path("/" * 64)

    def test_empty_rejected(self, tmp_cache):
        with pytest.raises(ValueError):
            cache_mod.cache_path("")


# ─── try_get ──────────────────────────────────────────────────

class TestTryGet:
    def test_miss_returns_none(self, tmp_cache):
        sha = _sha256(b"missing")
        assert cache_mod.try_get(sha) is None

    def test_hit_returns_path(self, tmp_cache):
        sha = _sha256(b"hit")
        tmp_cache.mkdir(parents=True, exist_ok=True)
        p = tmp_cache / f"{sha}.mp4"
        p.write_bytes(b"fake video")
        got = cache_mod.try_get(sha)
        assert got == str(p)

    def test_invalid_sha256_returns_none_not_raises(self, tmp_cache):
        # 防 endpoint 拿到坏数据时 500
        assert cache_mod.try_get("not-a-hash") is None
        assert cache_mod.try_get("") is None
        assert cache_mod.try_get(None) is None  # type: ignore


# ─── store ────────────────────────────────────────────────────

class TestStore:
    def test_moves_src_to_cache(self, tmp_cache, tmp_path):
        src = tmp_path / "upload.tmp"
        src.write_bytes(b"video bytes")
        sha = _sha256(b"video bytes")
        result = cache_mod.store(sha, str(src))
        assert result == str(tmp_cache / f"{sha}.mp4")
        # src 已搬走
        assert not src.exists()
        # 目标内容对
        assert (tmp_cache / f"{sha}.mp4").read_bytes() == b"video bytes"

    def test_dedupe_when_target_exists(self, tmp_cache, tmp_path):
        sha = _sha256(b"dup")
        # 预置缓存
        tmp_cache.mkdir(parents=True, exist_ok=True)
        existing = tmp_cache / f"{sha}.mp4"
        existing.write_bytes(b"dup")
        existing_mtime = existing.stat().st_mtime

        time.sleep(0.01)
        src = tmp_path / "second-upload.tmp"
        src.write_bytes(b"dup")
        result = cache_mod.store(sha, str(src))

        assert result == str(existing)
        assert not src.exists()  # 源被清掉
        # 缓存原本的没被覆盖(mtime 没变)
        assert existing.stat().st_mtime == existing_mtime

    def test_invalid_sha256_returns_none(self, tmp_cache, tmp_path):
        src = tmp_path / "u.tmp"
        src.write_bytes(b"x")
        result = cache_mod.store("not-a-hash", str(src))
        assert result is None
        # 源没被搬(让 caller 自己 unlink)
        assert src.exists()


# ─── clean_old ────────────────────────────────────────────────

class TestCleanOld:
    def test_no_root_returns_zero(self, tmp_cache):
        # CACHE_ROOT 不存在
        result = cache_mod.clean_old()
        assert result == {"scanned": 0, "deleted": 0, "freed_bytes": 0, "errors": []}

    def test_keeps_fresh_deletes_stale(self, tmp_cache):
        tmp_cache.mkdir(parents=True, exist_ok=True)
        fresh_sha = _sha256(b"fresh")
        stale_sha = _sha256(b"stale")
        fresh = tmp_cache / f"{fresh_sha}.mp4"
        stale = tmp_cache / f"{stale_sha}.mp4"
        fresh.write_bytes(b"fresh content")
        stale.write_bytes(b"stale content")

        # stale 改 mtime 到 1h 前
        old = time.time() - 3600
        os.utime(stale, (old, old))

        result = cache_mod.clean_old(max_age_seconds=1800)
        assert result["scanned"] == 2
        assert result["deleted"] == 1
        assert result["freed_bytes"] == len(b"stale content")
        assert fresh.exists()
        assert not stale.exists()

    def test_zero_age_deletes_all(self, tmp_cache):
        tmp_cache.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            sha = _sha256(f"v{i}".encode())
            (tmp_cache / f"{sha}.mp4").write_bytes(b"x" * 10)
        # max_age=0 → cutoff 是现在,所有文件都"过期"
        # 但刚写的可能 mtime > cutoff(浮点),手动 backdate
        for p in tmp_cache.iterdir():
            os.utime(p, (time.time() - 1, time.time() - 1))
        result = cache_mod.clean_old(max_age_seconds=0)
        assert result["deleted"] == 3
