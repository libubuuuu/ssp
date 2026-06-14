"""视频复刻 V2 人脸打码回归用例。

覆盖第二步上线的"必改"回归点:
1. mask_face=False(及默认)→ 行为完全不变:不打码、上传原视频、审计 sha256 用原始。
2. mask_face=True → 走 nice 子进程打码;命中人脸用打码版,无脸/失败回退原视频(不阻断上传)。
3. /upload/video/status 归属校验:404(不存在)/ 403(查别人的)。
4. face_mask_pro "全程无脸 → any_face=False" 契约(依赖装好时才跑,否则 skip)。

真实人脸 det=3 覆盖率 94~96% 已在服务器 2vCPU 实测(见上线报告),CI 无人脸 fixture 不在此断言。
"""
import os
import tempfile

import pytest
from fastapi import HTTPException

import app.api.video_clone_v2 as v2


@pytest.fixture
def patched(monkeypatch):
    """把上传/缓存/哈希/SSRF 校验都换成可控桩,隔离掉外部副作用。"""
    monkeypatch.setattr(v2, "_guard_enabled", lambda: None)
    monkeypatch.setattr(v2, "sha256_file", lambda p: "ORIGSHA256")
    monkeypatch.setattr(v2, "upload_to_cos", lambda p: "https://cos-test/clone.mp4")
    monkeypatch.setattr(v2, "validate_video_url", lambda u, **k: u)
    monkeypatch.setattr(v2, "cache_store", lambda sha, p: True)
    monkeypatch.setattr(v2, "cache_clean_old", lambda: None)
    v2._blur_jobs.clear()
    yield
    v2._blur_jobs.clear()


def _mk_tmp_video() -> str:
    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.write(fd, b"\x00\x00\x00\x18ftypmp42fakebytes")
    os.close(fd)
    return path


async def test_mask_false_uses_original_and_no_subprocess(patched, monkeypatch):
    """mask_face=False:不调打码子进程,上传原视频,sha256=原始,masked=False。"""
    called = {"sub": False}

    async def _never(*a, **k):
        called["sub"] = True
        return {"ok": True}
    monkeypatch.setattr(v2, "_run_mask_subprocess", _never)

    path = _mk_tmp_video()
    bid = "b1"
    v2._blur_jobs[bid] = {"status": "processing", "user_id": "u1", "ts": 0}
    await v2._blur_video_task(bid, path, "u1", mask_face=False)

    job = v2._blur_jobs[bid]
    assert job["status"] == "done"
    assert job["sha256"] == "ORIGSHA256"
    assert job["masked"] is False
    assert job["video_url"] == "https://cos-test/clone.mp4"
    assert called["sub"] is False, "mask_face=False 绝不能起打码子进程"
    assert not os.path.exists(path), "临时文件应被清理"


async def test_mask_true_masks_when_face_found(patched, monkeypatch):
    """mask_face=True 且检到脸:用打码版,masked=True,带 coverage;sha256 仍是原始。"""
    async def _sub(in_path, out_path, detect_every=3):
        with open(out_path, "wb") as f:           # 模拟生成打码文件
            f.write(b"masked-bytes")
        return {"ok": True, "any_face": True, "coverage": 0.95,
                "detected_frames": 110, "frames": 330, "out": out_path}
    monkeypatch.setattr(v2, "_run_mask_subprocess", _sub)

    path = _mk_tmp_video()
    bid = "b2"
    v2._blur_jobs[bid] = {"status": "processing", "user_id": "u1", "ts": 0}
    await v2._blur_video_task(bid, path, "u1", mask_face=True)

    job = v2._blur_jobs[bid]
    assert job["status"] == "done"
    assert job["masked"] is True
    assert job["coverage"] == 0.95
    assert job["sha256"] == "ORIGSHA256"   # 审计仍用原始指纹


async def test_mask_true_no_face_falls_back_to_original(patched, monkeypatch):
    """mask_face=True 但全程无脸:用原视频,masked=False,仍 done(不阻断)。"""
    async def _sub(in_path, out_path, detect_every=3):
        return {"ok": True, "any_face": False, "out": None}
    monkeypatch.setattr(v2, "_run_mask_subprocess", _sub)

    path = _mk_tmp_video()
    bid = "b3"
    v2._blur_jobs[bid] = {"status": "processing", "user_id": "u1", "ts": 0}
    await v2._blur_video_task(bid, path, "u1", mask_face=True)

    job = v2._blur_jobs[bid]
    assert job["status"] == "done"
    assert job["masked"] is False


async def test_mask_true_subprocess_failure_does_not_block(patched, monkeypatch):
    """打码子进程失败:不抛错、用原视频继续上传(用户体验不被打码故障拖垮)。"""
    async def _sub(in_path, out_path, detect_every=3):
        return {"ok": False, "error": "boom"}
    monkeypatch.setattr(v2, "_run_mask_subprocess", _sub)

    path = _mk_tmp_video()
    bid = "b4"
    v2._blur_jobs[bid] = {"status": "processing", "user_id": "u1", "ts": 0}
    await v2._blur_video_task(bid, path, "u1", mask_face=True)

    job = v2._blur_jobs[bid]
    assert job["status"] == "done"
    assert job["masked"] is False
    assert job["video_url"] == "https://cos-test/clone.mp4"


async def test_status_not_found(patched):
    with pytest.raises(HTTPException) as ei:
        await v2.upload_video_status("nope", current_user={"id": "u1"})
    assert ei.value.status_code == 404


async def test_status_ownership_forbidden(patched):
    v2._blur_jobs["b5"] = {"status": "done", "user_id": "owner", "ts": 0}
    with pytest.raises(HTTPException) as ei:
        await v2.upload_video_status("b5", current_user={"id": "attacker"})
    assert ei.value.status_code == 403


async def test_status_owner_ok_hides_user_id(patched):
    v2._blur_jobs["b6"] = {"status": "done", "user_id": "u1", "ts": 0, "video_url": "x"}
    res = await v2.upload_video_status("b6", current_user={"id": "u1"})
    assert res["status"] == "done"
    assert "user_id" not in res, "返回体不能泄漏 user_id"


def test_no_face_video_returns_any_face_false(tmp_path):
    """face_mask_pro 契约:无脸视频 → any_face=False(依赖未装则 skip)。"""
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("insightface")
    import numpy as np
    from app.services.face_mask_pro import mask_faces_in_video_pro

    src = str(tmp_path / "noface.mp4")
    w = cv2.VideoWriter(src, cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
    for _ in range(10):
        w.write(np.full((120, 160, 3), 127, dtype=np.uint8))  # 纯灰,无脸
    w.release()
    out = str(tmp_path / "out.mp4")
    r = mask_faces_in_video_pro(src, out, detect_every=3)
    assert r["ok"] is True
    assert r["any_face"] is False
