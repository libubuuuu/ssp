"""jobs.py 内部函数覆盖(P7 覆盖率补齐)

原 48% — 缺 _execute_job 异步路径 / _run_image_job / _run_video_job / _module_from_type / fcntl 持久化。
本文件直接调内部函数,mock fal_service 避免真打 FAL。

策略:
- 保留 conftest 的 _execute_job=noop 不动(老测试用它走 submit 端点)
- 本文件**不**用 conftest 的 client fixture(那是 test_app 把 _execute_job 替成 noop 了),
  改成直接 import + 操作 module-level state + 调内部函数
"""
import asyncio
import json
import time
import uuid as _uuid
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from app.api import jobs as jobs_module


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_jobs(monkeypatch, tmp_path):
    """每个测试独立 JOBS_FILE + JOBS dict + 还原真 _execute_job"""
    monkeypatch.setattr(jobs_module, "JOBS_FILE", tmp_path / "jobs.json")
    monkeypatch.setattr(jobs_module, "JOBS_DIR", tmp_path)
    jobs_module.JOBS.clear()
    yield
    jobs_module.JOBS.clear()


# === _module_from_type 单元 ===

def test_module_from_type_image_with_refs():
    assert jobs_module._module_from_type("image", {"reference_images": ["a", "b"]}) == "image/multi-reference"


def test_module_from_type_image_no_refs():
    assert jobs_module._module_from_type("image", {}) == "image/style"


def test_module_from_type_video_variants():
    assert jobs_module._module_from_type("video_i2v", {}) == "video/image-to-video"
    assert jobs_module._module_from_type("video_edit", {}) == "video/replace/element"
    assert jobs_module._module_from_type("video_clone", {}) == "video/clone"


def test_module_from_type_unknown_falls_back():
    assert jobs_module._module_from_type("totally_unknown", {}) == "image/style"


# === _save_jobs / _load_jobs(fcntl 锁 + 文件持久化)===

def test_save_then_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "JOBS_FILE", tmp_path / "jobs.json")
    jobs_module.JOBS["job-1"] = {"id": "job-1", "status": "pending", "cost": 2}
    jobs_module._save_jobs()
    # 文件落盘
    assert (tmp_path / "jobs.json").exists()
    loaded = jobs_module._load_jobs()
    assert "job-1" in loaded
    assert loaded["job-1"]["cost"] == 2


def test_load_returns_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "JOBS_FILE", tmp_path / "nonexistent.json")
    assert jobs_module._load_jobs() == {}


def test_load_handles_corrupted_json(tmp_path, monkeypatch):
    """损坏的 JSON 文件 → 返回 {} 不抛"""
    bad = tmp_path / "jobs.json"
    bad.write_text("{not valid json")
    monkeypatch.setattr(jobs_module, "JOBS_FILE", bad)
    assert jobs_module._load_jobs() == {}


def test_load_handles_empty_file(tmp_path, monkeypatch):
    """空文件 → {}"""
    empty = tmp_path / "jobs.json"
    empty.write_text("")
    monkeypatch.setattr(jobs_module, "JOBS_FILE", empty)
    assert jobs_module._load_jobs() == {}


# === _run_image_job(mock fal)===

def test_run_image_job_simple_path(monkeypatch):
    """无 reference_images:走 image_service.generate"""
    mock_service = MagicMock()
    mock_service.generate = AsyncMock(return_value={"image_url": "https://x/a.png", "model": "nano-banana-2"})
    monkeypatch.setattr(jobs_module, "get_image_service", lambda: mock_service)

    result = _run(jobs_module._run_image_job({"prompt": "a sunset"}))
    assert result["image_url"] == "https://x/a.png"
    assert result["type"] == "image"
    mock_service.generate.assert_awaited_once()


def test_run_image_job_propagates_error(monkeypatch):
    mock_service = MagicMock()
    mock_service.generate = AsyncMock(return_value={"error": "fal exploded"})
    monkeypatch.setattr(jobs_module, "get_image_service", lambda: mock_service)

    with pytest.raises(Exception, match="fal exploded"):
        _run(jobs_module._run_image_job({"prompt": "x"}))


def test_run_image_job_multi_reference_path(monkeypatch):
    """有 reference_images:走 fal_client.run_async"""
    fake_fal = MagicMock()
    fake_fal.run_async = AsyncMock(return_value={"images": [{"url": "https://multi.png"}]})
    # patch 让 import fal_client 时拿到 fake
    monkeypatch.setitem(__import__("sys").modules, "fal_client", fake_fal)

    result = _run(jobs_module._run_image_job({
        "prompt": "merged",
        "reference_images": ["a", "b"],
    }))
    assert result["image_url"] == "https://multi.png"
    assert result["type"] == "image"


def test_run_image_job_multi_reference_no_images_raises(monkeypatch):
    fake_fal = MagicMock()
    fake_fal.run_async = AsyncMock(return_value={"images": []})  # 空
    monkeypatch.setitem(__import__("sys").modules, "fal_client", fake_fal)

    with pytest.raises(Exception, match="no image generated"):
        _run(jobs_module._run_image_job({"prompt": "x", "reference_images": ["a"]}))


# === _run_video_job(mock fal,且短路 polling 循环)===

def test_run_video_job_unknown_type_raises():
    with pytest.raises(Exception, match="unknown video type"):
        _run(jobs_module._run_video_job({}, "video_unknown"))


def test_run_video_job_no_task_id_raises(monkeypatch):
    mock_service = MagicMock()
    mock_service.generate_from_image = AsyncMock(return_value={"task_id": None})
    monkeypatch.setattr(jobs_module, "get_video_service", lambda: mock_service)
    with pytest.raises(Exception, match="no task_id"):
        _run(jobs_module._run_video_job({"image_url": "x"}, "video_i2v"))


def test_run_video_job_completed_immediately(monkeypatch):
    """polling 第一轮就 completed → 立刻返回"""
    mock_service = MagicMock()
    mock_service.generate_from_image = AsyncMock(return_value={"task_id": "t1", "endpoint_tag": "i2v"})
    mock_service.get_task_status = AsyncMock(return_value={"status": "completed", "video_url": "https://v.mp4"})
    monkeypatch.setattr(jobs_module, "get_video_service", lambda: mock_service)
    # 短路 sleep 让测试不真等 5s
    monkeypatch.setattr(jobs_module.asyncio, "sleep", AsyncMock(return_value=None))

    result = _run(jobs_module._run_video_job({"image_url": "x"}, "video_i2v"))
    assert result["video_url"] == "https://v.mp4"
    assert result["type"] == "video"


def test_run_video_job_failed_raises(monkeypatch):
    mock_service = MagicMock()
    mock_service.replace_element = AsyncMock(return_value={"task_id": "t2"})
    mock_service.get_task_status = AsyncMock(return_value={"status": "failed", "error": "fal upstream"})
    monkeypatch.setattr(jobs_module, "get_video_service", lambda: mock_service)
    monkeypatch.setattr(jobs_module.asyncio, "sleep", AsyncMock(return_value=None))

    with pytest.raises(Exception, match="fal upstream"):
        _run(jobs_module._run_video_job(
            {"video_url": "v", "element_image_url": "e", "instruction": "swap"},
            "video_edit",
        ))


def test_run_video_job_clone_path(monkeypatch):
    """video_clone 也走 polling"""
    mock_service = MagicMock()
    mock_service.clone_video = AsyncMock(return_value={"task_id": "t3"})
    mock_service.get_task_status = AsyncMock(return_value={"status": "completed", "video_url": "https://c.mp4"})
    monkeypatch.setattr(jobs_module, "get_video_service", lambda: mock_service)
    monkeypatch.setattr(jobs_module.asyncio, "sleep", AsyncMock(return_value=None))

    result = _run(jobs_module._run_video_job(
        {"reference_video_url": "r", "model_image_url": "m"},
        "video_clone",
    ))
    assert result["video_url"] == "https://c.mp4"


# === _execute_job(端到端,mock _run_image_job)===

def test_execute_job_happy_path_image(monkeypatch):
    """成功路径:status running → completed,result.image_url 被归档"""
    job_id = "job-happy-img"
    jobs_module.JOBS[job_id] = {
        "id": job_id, "type": "image",
        "params": {"prompt": "x"},
        "status": "pending",
        "user_numeric_id": "user-x",  # 触发 history 写入分支
        "cost": 2,
        "module": "image/style",
        "title": "test",
    }
    # mock _run_image_job 返预期结果
    monkeypatch.setattr(
        jobs_module, "_run_image_job",
        AsyncMock(return_value={"image_url": "https://fal/orig.png", "type": "image"})
    )
    # mock archive_url 返回固定 URL,验证被调
    archive_calls = []
    async def fake_archive(url, uid, kind):
        archive_calls.append((url, uid, kind))
        return f"https://ailixiao.com/uploads/{uid}/m/x.png"
    import app.services.media_archiver as ma
    monkeypatch.setattr(ma, "archive_url", fake_archive)
    # mock create_consumption_record 看是否被调
    cr_calls = []
    monkeypatch.setattr(jobs_module, "create_consumption_record", lambda **kw: cr_calls.append(kw) or True)

    _run(jobs_module._execute_job_original(job_id))

    job = jobs_module.JOBS[job_id]
    assert job["status"] == "completed"
    assert job["result"]["image_url"].startswith("https://ailixiao.com/uploads/")
    assert "started_at" in job and "finished_at" in job
    # 归档真被调
    assert len(archive_calls) == 1
    # consumption record 真被写
    assert len(cr_calls) == 1
    assert cr_calls[0]["module"] == "image/style"
    assert cr_calls[0]["cost"] == 2


def test_execute_job_failure_refunds_credits(monkeypatch):
    """失败路径:_run_image_job 抛异常 → status=failed + add_credits 退积分"""
    job_id = "job-failed"
    jobs_module.JOBS[job_id] = {
        "id": job_id, "type": "image",
        "params": {"prompt": "x"},
        "status": "pending",
        "user_numeric_id": "user-y",
        "cost": 5,
    }
    monkeypatch.setattr(
        jobs_module, "_run_image_job",
        AsyncMock(side_effect=Exception("fal returned error"))
    )
    refund_calls = []
    monkeypatch.setattr(jobs_module, "add_credits", lambda uid, amt: refund_calls.append((uid, amt)) or True)

    _run(jobs_module._execute_job_original(job_id))

    job = jobs_module.JOBS[job_id]
    assert job["status"] == "failed"
    assert "fal returned error" in job["error"]
    # 退还 5 积分给 user-y
    assert refund_calls == [("user-y", 5)]


def test_execute_job_unknown_type_fails(monkeypatch):
    job_id = "job-unknown"
    jobs_module.JOBS[job_id] = {
        "id": job_id, "type": "totally_alien",
        "params": {},
        "status": "pending",
        "user_numeric_id": "user-z",
        "cost": 0,
    }
    _run(jobs_module._execute_job_original(job_id))
    assert jobs_module.JOBS[job_id]["status"] == "failed"
    assert "unknown type" in jobs_module.JOBS[job_id]["error"]


def test_execute_job_missing_id_returns_silently(monkeypatch):
    """job_id 不在 JOBS dict 中 → 静默返回(防 race)"""
    # 不会抛
    _run(jobs_module._execute_job("nonexistent-job"))


def test_execute_job_archive_failure_continues_with_fal_url(monkeypatch):
    """归档失败不让任务挂掉:result 仍是 fal URL"""
    job_id = "job-arch-fail"
    jobs_module.JOBS[job_id] = {
        "id": job_id, "type": "image",
        "params": {}, "status": "pending",
        "user_numeric_id": "u", "cost": 2,
    }
    monkeypatch.setattr(
        jobs_module, "_run_image_job",
        AsyncMock(return_value={"image_url": "https://fal/a.png", "type": "image"})
    )
    import app.services.media_archiver as ma
    async def boom(*a, **kw):
        raise Exception("nginx 503")
    monkeypatch.setattr(ma, "archive_url", boom)

    _run(jobs_module._execute_job_original(job_id))

    job = jobs_module.JOBS[job_id]
    # 任务仍 completed(归档失败不影响主流程)
    assert job["status"] == "completed"
    # image_url 退到原 fal URL
    assert job["result"]["image_url"] == "https://fal/a.png"
