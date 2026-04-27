"""uploads GC 测试(隐藏雷 #1)"""
import os
import time
from pathlib import Path

import pytest

from app.services import uploads_gc


@pytest.fixture
def tmp_uploads(tmp_path, monkeypatch):
    """每个测试一份独立 uploads 目录"""
    monkeypatch.setattr(uploads_gc, "UPLOADS_ROOT", tmp_path / "uploads")
    return tmp_path / "uploads"


def _touch_file(path: Path, age_days: float, content: bytes = b"x" * 100):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    mtime = time.time() - age_days * 86400
    os.utime(path, (mtime, mtime))


def test_clean_keeps_fresh_files(tmp_uploads):
    p = tmp_uploads / "u1" / "2026-04" / "fresh.png"
    _touch_file(p, age_days=30)  # 30 天 < 90 天保留
    res = uploads_gc.clean_old_uploads(days=90, dry_run=False)
    assert res["scanned"] == 1
    assert res["deleted"] == 0
    assert p.exists()


def test_clean_deletes_old_files(tmp_uploads):
    fresh = tmp_uploads / "u1" / "2026-04" / "fresh.png"
    old = tmp_uploads / "u1" / "2025-12" / "old.png"
    _touch_file(fresh, age_days=10)
    _touch_file(old, age_days=120)  # > 90 天
    res = uploads_gc.clean_old_uploads(days=90, dry_run=False)
    assert res["scanned"] == 2
    assert res["deleted"] == 1
    assert res["freed_bytes"] == 100
    assert fresh.exists()
    assert not old.exists()


def test_dry_run_does_not_delete(tmp_uploads):
    old = tmp_uploads / "u" / "old.png"
    _touch_file(old, age_days=200)
    res = uploads_gc.clean_old_uploads(days=90, dry_run=True)
    assert res["deleted"] == 1  # 计数仍报
    assert old.exists()  # 但文件还在


def test_clean_removes_empty_dirs(tmp_uploads):
    """删完文件后,空目录也清(否则目录树膨胀)"""
    old1 = tmp_uploads / "u1" / "2024-01" / "a.png"
    old2 = tmp_uploads / "u1" / "2024-01" / "b.png"
    _touch_file(old1, age_days=200)
    _touch_file(old2, age_days=200)
    uploads_gc.clean_old_uploads(days=90, dry_run=False)
    # 2024-01 目录下文件全删,目录应该也消失
    assert not (tmp_uploads / "u1" / "2024-01").exists()


def test_clean_returns_zero_when_no_uploads_dir(tmp_path, monkeypatch):
    """uploads 目录不存在 → 返回 zero counts,不抛"""
    monkeypatch.setattr(uploads_gc, "UPLOADS_ROOT", tmp_path / "nonexistent")
    res = uploads_gc.clean_old_uploads(days=90, dry_run=False)
    assert res == {"scanned": 0, "deleted": 0, "freed_bytes": 0, "errors": []}


# === delete_archived 测试 ===

def test_delete_archived_happy_path(tmp_uploads):
    target = tmp_uploads / "u123" / "2026-04" / "abc.jpg"
    _touch_file(target, age_days=1)
    url = "https://ailixiao.com/uploads/u123/2026-04/abc.jpg"
    assert uploads_gc.delete_archived(url) is True
    assert not target.exists()


def test_delete_archived_path_traversal_rejected(tmp_uploads):
    """url 路径含 .. 被拒"""
    bad_url = "https://ailixiao.com/uploads/../../../etc/passwd"
    assert uploads_gc.delete_archived(bad_url) is False
    # /etc/passwd 显然没动


def test_delete_archived_non_uploads_url_ignored(tmp_uploads):
    assert uploads_gc.delete_archived("https://fal.media/files/abc.png") is False
    assert uploads_gc.delete_archived("") is False
    assert uploads_gc.delete_archived(None) is False


def test_delete_archived_missing_file_returns_false(tmp_uploads):
    """路径合法但文件不存在 → False(不抛)"""
    assert uploads_gc.delete_archived("https://ailixiao.com/uploads/u/missing.jpg") is False


# === disk_usage_pct 测试 ===

def test_disk_usage_pct_returns_int_when_dir_exists(tmp_uploads):
    tmp_uploads.mkdir(parents=True, exist_ok=True)
    pct = uploads_gc.disk_usage_pct()
    assert pct is not None and 0 <= pct <= 100


def test_disk_usage_pct_returns_none_when_no_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads_gc, "UPLOADS_ROOT", tmp_path / "nope")
    assert uploads_gc.disk_usage_pct() is None
