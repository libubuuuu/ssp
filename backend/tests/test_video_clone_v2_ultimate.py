"""P221 V2 — B 阶段 ultimate 多段路径测试。

覆盖:
- /create 接受 type=ultimate + 多段 segments + trim 字段
- _refund_partial 单段退款幂等
- _build_segment_clip / _concat_with_demuxer 真 ffmpeg(用 P220 测试视频)
- _process_ultimate 主流程:全成功 / 部分失败 / 全失败 / 拼接失败
- 全程 mock fal_client(不动真 fal)
"""
import asyncio
import json
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.database import get_db
from app.services import video_clone_v2_processor as proc_mod
from app.services.video_clone_v2_pricing import TIER_CREDITS


P220_VIDEO = "/opt/ssp/uploads/probe/p220_balance_test/result_480p_2s_input.mp4"
skip_if_no_p220 = pytest.mark.skipif(
    not os.path.exists(P220_VIDEO),
    reason=f"P220 测试视频不存在:{P220_VIDEO}",
)


# ─── _refund_partial 幂等测试 ─────────────────────────────────────────

class TestRefundPartial:
    def _seed_user_and_job(self, credits_initial=100, credits_charged=45):
        """直接 INSERT 一条 ultimate job,准备退款上下文。"""
        from app.services.auth import create_user
        user = create_user(email=f"u{uuid.uuid4().hex[:8]}@test.com", password="x" * 8)
        with get_db() as conn:
            conn.execute("UPDATE users SET credits = ? WHERE id = ?", (credits_initial, user["id"]))
            job_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO video_clone_v2_jobs (id, user_id, type, replacement_mode, "
                "input_video_url, input_video_duration_sec, image_urls, prompt, "
                "segments_plan, segments_count, total_credits_charged, status) "
                "VALUES (?, ?, 'ultimate', 'full', 'x', 16, '[]', 'p', '[]', 2, ?, 'processing')",
                (job_id, user["id"], credits_charged),
            )
            conn.commit()
        return user["id"], job_id

    def test_refund_partial_first_call_credits_user(self):
        user_id, job_id = self._seed_user_and_job(credits_initial=50, credits_charged=40)
        job = {"id": job_id, "user_id": user_id, "total_credits_charged": 40}
        # 退段 0 = 20 积分
        asyncio.run(proc_mod._refund_partial(job, 0, 20))
        with get_db() as conn:
            row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
            jrow = conn.execute(
                "SELECT total_credits_refunded FROM video_clone_v2_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        assert row[0] == 70   # 50 + 20
        assert jrow[0] == 20

    def test_refund_partial_idempotent(self):
        user_id, job_id = self._seed_user_and_job(credits_initial=50, credits_charged=40)
        job = {"id": job_id, "user_id": user_id, "total_credits_charged": 40}
        asyncio.run(proc_mod._refund_partial(job, 0, 20))
        asyncio.run(proc_mod._refund_partial(job, 0, 20))  # 重复 — 不应再退
        with get_db() as conn:
            row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
        assert row[0] == 70  # 仍然是 50 + 20,不重复退

    def test_refund_partial_different_segs_independent(self):
        user_id, job_id = self._seed_user_and_job(credits_initial=50, credits_charged=40)
        job = {"id": job_id, "user_id": user_id, "total_credits_charged": 40}
        asyncio.run(proc_mod._refund_partial(job, 0, 15))
        asyncio.run(proc_mod._refund_partial(job, 1, 20))  # 不同 idx
        with get_db() as conn:
            row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
            jrow = conn.execute(
                "SELECT total_credits_refunded FROM video_clone_v2_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        assert row[0] == 85  # 50 + 15 + 20
        assert jrow[0] == 35


# ─── _build_segment_clip + _concat_with_demuxer 真 ffmpeg ──────────────

@skip_if_no_p220
class TestConcatRealFfmpeg:
    def test_original_seg_keeps_audio(self, tmp_path):
        """source_type=original:有音轨直接用,无音轨补静音。"""
        # 切 P220 一段 2s 出来当 original seg
        seg_file = str(tmp_path / "seg.mp4")
        asyncio.run(proc_mod._ffmpeg([
            "-i", P220_VIDEO, "-ss", "0", "-t", "2",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            seg_file,
        ]))
        result = {"idx": 0, "source_type": "original", "status": "ready", "output_url": None}
        plan_item = {"idx": 0, "start": 0.0, "duration": 2.0}
        out = asyncio.run(proc_mod._build_segment_clip(
            result, plan_item, seg_file, P220_VIDEO, str(tmp_path)
        ))
        assert os.path.exists(out)
        # 应有音频流(原视频含音频或补的静音)
        assert asyncio.run(proc_mod._has_audio_stream(out))

    def test_concat_two_segs(self, tmp_path):
        """切 P220 出两段 → concat → 视频时长 ≈ 4s。"""
        seg0 = str(tmp_path / "seg0.mp4")
        seg1 = str(tmp_path / "seg1.mp4")
        asyncio.run(proc_mod._ffmpeg([
            "-i", P220_VIDEO, "-ss", "0", "-t", "2",
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", seg0,
        ]))
        asyncio.run(proc_mod._ffmpeg([
            "-i", P220_VIDEO, "-ss", "2", "-t", "2",
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", seg1,
        ]))
        out = str(tmp_path / "concat.mp4")
        asyncio.run(proc_mod._concat_with_demuxer([seg0, seg1], out, str(tmp_path)))
        dur = asyncio.run(proc_mod._ffprobe_duration(out))
        assert 3.5 < dur < 4.5, f"concat 时长异常:{dur}"

    def test_compute_keep_ranges_helper(self):
        """_compute_keep_ranges 各种丢弃配置正确算补集。"""
        from app.services.video_clone_v2_processor import _compute_keep_ranges
        assert _compute_keep_ranges([], 18) == [(0.0, 18)]
        assert _compute_keep_ranges([(0, 2)], 18) == [(2.0, 18)]
        assert _compute_keep_ranges([(16, 18)], 18) == [(0.0, 16.0)]
        # 中段
        assert _compute_keep_ranges([(8, 10)], 18) == [(0.0, 8.0), (10.0, 18)]
        # 多段非连续
        assert _compute_keep_ranges([(5, 7), (12, 13)], 18) == [(0.0, 5.0), (7.0, 12.0), (13.0, 18)]
        # 3 段
        assert _compute_keep_ranges([(0, 1), (5, 6), (15, 16)], 18) == [
            (1.0, 5.0), (6.0, 15.0), (16.0, 18)
        ]
        # 重叠 → 合并
        assert _compute_keep_ranges([(2, 5), (4, 7)], 18) == [(0.0, 2.0), (7.0, 18)]
        # 全丢
        assert _compute_keep_ranges([(0, 18)], 18) == []

    def test_multi_drop_three_keep_concat(self, tmp_path):
        """3 段 keep concat:模拟用户丢非连续 2 段 → 拼出 3 段保留区。"""
        full_dur = asyncio.run(proc_mod._ffprobe_duration(P220_VIDEO))
        if full_dur < 6:
            pytest.skip(f"P220 视频 {full_dur}s 太短")
        # 丢 2 段 → 3 段 keep
        d1 = (full_dur * 0.2, full_dur * 0.3)
        d2 = (full_dur * 0.6, full_dur * 0.7)
        from app.services.video_clone_v2_processor import _compute_keep_ranges
        keeps = _compute_keep_ranges([d1, d2], full_dur)
        assert len(keeps) == 3, f"期望 3 段 keep,得 {len(keeps)}"

        clips = []
        for i, (ks, ke) in enumerate(keeps):
            out = str(tmp_path / f"keep_{i}.mp4")
            asyncio.run(proc_mod._ffmpeg([
                "-i", P220_VIDEO, "-ss", str(ks), "-t", str(ke - ks),
                "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", out,
            ]))
            clips.append(out)

        final = str(tmp_path / "concat.mp4")
        asyncio.run(proc_mod._concat_with_demuxer(clips, final, str(tmp_path)))
        out_dur = asyncio.run(proc_mod._ffprobe_duration(final))
        expected = full_dur - (d1[1] - d1[0]) - (d2[1] - d2[0])
        assert abs(out_dur - expected) < 0.5, \
            f"3 段 keep concat 后时长 {out_dur} ≠ {expected}"

    def test_middle_drop_pre_post_concat(self, tmp_path):
        """中段 drop:模拟 8s 视频丢 [3, 5] 2s → pre 3s + post 3s concat = 6s。"""
        # P220 视频实际 ~8s,我们模拟它就是输入
        full_dur = asyncio.run(proc_mod._ffprobe_duration(P220_VIDEO))
        if full_dur < 6:
            pytest.skip(f"P220 视频 {full_dur}s 太短跑不了 middle drop 测试")
        # drop [full*0.4, full*0.6]
        ds, de = full_dur * 0.4, full_dur * 0.6
        pre  = str(tmp_path / "pre.mp4")
        post = str(tmp_path / "post.mp4")
        out  = str(tmp_path / "input.mp4")
        asyncio.run(proc_mod._ffmpeg([
            "-i", P220_VIDEO, "-ss", "0", "-t", str(ds),
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", pre,
        ]))
        asyncio.run(proc_mod._ffmpeg([
            "-i", P220_VIDEO, "-ss", str(de), "-t", str(full_dur - de),
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", post,
        ]))
        asyncio.run(proc_mod._concat_with_demuxer([pre, post], out, str(tmp_path)))
        out_dur = asyncio.run(proc_mod._ffprobe_duration(out))
        expected = full_dur - (de - ds)
        assert abs(out_dur - expected) < 0.5, \
            f"middle drop 后时长 {out_dur} ≠ {expected}(full={full_dur}, drop=[{ds:.2f},{de:.2f}])"


# ─── _process_ultimate 全流程 mock fal ────────────────────────────────

@skip_if_no_p220
class TestProcessUltimate:
    """用 P220 视频 + mock fal 测主流程。"""

    def _setup_job(self, plan_segments, replacement_mode="full"):
        """造一个 ultimate job 行 + plan,返 (user_id, job_id)。"""
        from app.services.auth import create_user
        user = create_user(email=f"u{uuid.uuid4().hex[:8]}@test.com", password="x" * 8)
        with get_db() as conn:
            conn.execute("UPDATE users SET credits = ? WHERE id = ?", (100, user["id"]))
            total_credits = sum(
                TIER_CREDITS.get(p.get("tier"), 0)
                for p in plan_segments if p.get("source_type") == "ai"
            )
            job_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO video_clone_v2_jobs (id, user_id, type, replacement_mode, "
                "input_video_url, input_video_duration_sec, image_urls, prompt, prompt_compiled, "
                "segments_plan, segments_count, total_credits_charged, status) "
                "VALUES (?, ?, 'ultimate', ?, 'file://" + P220_VIDEO + "', 8, '[]', 'test', 'test', ?, ?, ?, 'processing')",
                (job_id, user["id"], replacement_mode,
                 json.dumps(plan_segments), len(plan_segments), total_credits),
            )
            conn.commit()
        return user["id"], job_id, total_credits

    def _mock_fal_success(self, fal_url="https://fake.fal.media/out.mp4"):
        """mock fal_client.upload_file_async + subscribe_async,返成功。"""
        async def _upload(*a, **kw): return "https://fake.fal.media/in.mp4"
        async def _subscribe(*a, **kw):
            return {"video": {"url": fal_url}, "request_id": "req-mock"}
        return _upload, _subscribe

    def _mock_download(self, src_video=P220_VIDEO):
        """mock httpx 下载 — 把 P220 视频复制成 fal 输出 / 用户输入。"""
        import shutil
        async def _stream_to(url, out_path):
            # 不管啥 URL 都返 P220 内容
            shutil.copy2(src_video, out_path)
        return _stream_to

    @patch("app.services.video_clone_v2_processor.fal_client")
    @patch("app.services.video_clone_v2_processor._download_fal_to_local")
    def test_all_segs_succeed_concats_dual_versions(
        self, mock_download_fal, mock_fal, tmp_path, monkeypatch,
    ):
        """3 段全 AI 全成功 → concat → 双版本归档。"""
        # mock fal
        mock_fal.upload_file_async = AsyncMock(return_value="fake_in")
        mock_fal.subscribe_async = AsyncMock(return_value={
            "video": {"url": "https://fake.fal.media/out.mp4"},
            "request_id": "req-1",
        })
        # mock fal output download
        async def _dl(url, out):
            import shutil; shutil.copy2(P220_VIDEO, out)
        mock_download_fal.side_effect = _dl

        # mock 用户 input 下载(httpx 流式) — 直接拷 P220
        # 注意:client.stream(...) 返 async CM(不是 coroutine),所以 _fake_httpx_get 是 sync
        def _fake_httpx_get(url, *a, **kw):
            class _R:
                status_code = 200
                async def aiter_bytes(self, chunk_size=64*1024):
                    with open(P220_VIDEO, "rb") as f:
                        while True:
                            ch = f.read(chunk_size)
                            if not ch: break
                            yield ch
                async def __aenter__(self): return self
                async def __aexit__(self, *a): pass
            return _R()

        # mock _archive_local_dual 输出位置(prod 需要 ssp-app 写 /opt/ssp/uploads,测试改 tmp)
        v2_uploads = tmp_path / "v2_uploads"
        v2_uploads.mkdir()
        async def _fake_archive(local_video, job_id):
            import shutil
            jd = v2_uploads / job_id
            jd.mkdir(parents=True, exist_ok=True)
            wm = str(jd / f"{job_id}_watermarked.mp4")
            raw = str(jd / f"{job_id}_raw.mp4")
            shutil.copy2(local_video, raw)
            shutil.copy2(local_video, wm)
            return {
                "raw_local_path": raw, "watermarked_local_path": wm,
                "raw_url": f"https://test/{job_id}_raw.mp4",
                "watermarked_url": f"https://test/{job_id}_wm.mp4",
            }
        monkeypatch.setattr(proc_mod, "_archive_local_dual", _fake_archive)

        plan = [
            {"idx": 0, "start": 0.0, "duration": 2.0, "source_type": "ai", "tier": "economy"},
            {"idx": 1, "start": 2.0, "duration": 2.0, "source_type": "ai", "tier": "economy"},
        ]
        user_id, job_id, charged = self._setup_job(plan)

        # 用 patch httpx 的 stream
        with patch("httpx.AsyncClient") as mock_client_cls:
            client_inst = MagicMock()
            client_inst.__aenter__ = AsyncMock(return_value=client_inst)
            client_inst.__aexit__ = AsyncMock(return_value=False)
            client_inst.stream = lambda method, url: _fake_httpx_get(url)
            mock_client_cls.return_value = client_inst

            asyncio.run(proc_mod.process_v2_job(job_id))

        # 验证 DB
        with get_db() as conn:
            row = conn.execute(
                "SELECT status, final_video_url_watermarked, final_video_url_raw, "
                "total_credits_refunded FROM video_clone_v2_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        assert row[0] == "completed", f"期望 completed,实际:{row[0]}"
        assert row[1] and "_wm" in row[1]
        assert row[2] and "_raw" in row[2]
        assert row[3] == 0  # 没失败段不退款

    @patch("app.services.video_clone_v2_processor.fal_client")
    @patch("app.services.video_clone_v2_processor._download_fal_to_local")
    def test_one_seg_fails_others_complete_partial_refund(
        self, mock_download_fal, mock_fal, tmp_path, monkeypatch,
    ):
        """段 1 fal 失败 → 段 0/2 成功 → 拼 2 段 + 退段 1 credits。"""
        mock_fal.upload_file_async = AsyncMock(return_value="fake_in")
        # subscribe 第 2 次(idx=1)抛错
        call_count = {"n": 0}
        async def _flaky_subscribe(*a, **kw):
            call_count["n"] += 1
            # 段 1 第一次 + retry 都失败 = 2 次
            if call_count["n"] in (3, 4):  # 段 1 first call + retry
                raise RuntimeError("fake fal NSFW")
            return {"video": {"url": "https://fake.fal.media/out.mp4"}, "request_id": "ok"}
        mock_fal.subscribe_async = _flaky_subscribe

        async def _dl(url, out):
            import shutil; shutil.copy2(P220_VIDEO, out)
        mock_download_fal.side_effect = _dl

        async def _fake_archive(local_video, job_id):
            import shutil
            jd = tmp_path / "out" / job_id
            jd.mkdir(parents=True, exist_ok=True)
            wm = str(jd / f"{job_id}_watermarked.mp4")
            raw = str(jd / f"{job_id}_raw.mp4")
            shutil.copy2(local_video, raw)
            shutil.copy2(local_video, wm)
            return {
                "raw_local_path": raw, "watermarked_local_path": wm,
                "raw_url": "https://test/raw", "watermarked_url": "https://test/wm",
            }
        monkeypatch.setattr(proc_mod, "_archive_local_dual", _fake_archive)

        plan = [
            {"idx": 0, "start": 0.0, "duration": 2.0, "source_type": "ai", "tier": "economy"},
            {"idx": 1, "start": 2.0, "duration": 2.0, "source_type": "ai", "tier": "economy"},
            {"idx": 2, "start": 4.0, "duration": 2.0, "source_type": "ai", "tier": "economy"},
        ]
        user_id, job_id, charged = self._setup_job(plan)
        # _setup_job UPDATE credits = 100 直接(没走真实扣款),所以 balance_before = 100
        balance_before = 100

        def _fake_httpx_get(url):
            class _R:
                status_code = 200
                async def aiter_bytes(self, chunk_size=64*1024):
                    with open(P220_VIDEO, "rb") as f:
                        while True:
                            ch = f.read(chunk_size)
                            if not ch: break
                            yield ch
                async def __aenter__(self): return self
                async def __aexit__(self, *a): pass
            return _R()

        with patch("httpx.AsyncClient") as mock_client_cls:
            client_inst = MagicMock()
            client_inst.__aenter__ = AsyncMock(return_value=client_inst)
            client_inst.__aexit__ = AsyncMock(return_value=False)
            client_inst.stream = lambda method, url: _fake_httpx_get(url)
            mock_client_cls.return_value = client_inst

            asyncio.run(proc_mod.process_v2_job(job_id))

        with get_db() as conn:
            row = conn.execute(
                "SELECT status, total_credits_refunded FROM video_clone_v2_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            user_credits = conn.execute(
                "SELECT credits FROM users WHERE id = ?", (user_id,)
            ).fetchone()[0]
        assert row[0] == "completed", f"期望 completed(部分成功也算),实际:{row[0]}"
        assert row[1] == TIER_CREDITS["economy"]   # 退段 1 = 15 积分
        assert user_credits == balance_before + TIER_CREDITS["economy"]

    @patch("app.services.video_clone_v2_processor.fal_client")
    def test_all_segs_fail_marks_failed_and_partial_refunds(
        self, mock_fal, tmp_path, monkeypatch,
    ):
        """所有 ai 段都失败 → status=failed,所有段 credits 各自退。"""
        mock_fal.upload_file_async = AsyncMock(return_value="fake_in")
        async def _all_fail(*a, **kw):
            raise RuntimeError("fake fal failure")
        mock_fal.subscribe_async = _all_fail

        plan = [
            {"idx": 0, "start": 0.0, "duration": 2.0, "source_type": "ai", "tier": "economy"},
            {"idx": 1, "start": 2.0, "duration": 2.0, "source_type": "ai", "tier": "standard"},
        ]
        user_id, job_id, charged = self._setup_job(plan)

        def _fake_httpx_get(url):
            class _R:
                status_code = 200
                async def aiter_bytes(self, chunk_size=64*1024):
                    with open(P220_VIDEO, "rb") as f:
                        while True:
                            ch = f.read(chunk_size)
                            if not ch: break
                            yield ch
                async def __aenter__(self): return self
                async def __aexit__(self, *a): pass
            return _R()

        with patch("httpx.AsyncClient") as mock_client_cls:
            client_inst = MagicMock()
            client_inst.__aenter__ = AsyncMock(return_value=client_inst)
            client_inst.__aexit__ = AsyncMock(return_value=False)
            client_inst.stream = lambda method, url: _fake_httpx_get(url)
            mock_client_cls.return_value = client_inst

            asyncio.run(proc_mod.process_v2_job(job_id))

        with get_db() as conn:
            row = conn.execute(
                "SELECT status, total_credits_refunded FROM video_clone_v2_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        assert row[0] == "failed"
        assert row[1] == TIER_CREDITS["economy"] + TIER_CREDITS["standard"]   # 全段都退


# ─── /create 端点测试(API 层) ─────────────────────────────────────────

class TestCreateUltimateApi:
    """ultimate /create endpoint 接受新 schema(replacement_mode + segments + trim)."""

    def test_create_ultimate_persists_trim(self, monkeypatch):
        """/create body 含 trim 字段 → DB 写入 + plan 用 effective_duration."""
        # 这里只测 schema + DB 写入,不真触发 process_v2_job
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import auth as auth_module
        from app.api import video_clone_v2 as v2_module

        # mock guard 让 V2 端点放行
        monkeypatch.setenv("ENABLE_VIDEO_CLONE_V2", "true")
        from app.config import get_settings
        get_settings.cache_clear()

        # mock process_v2_job 不真跑
        async def _noop(jid): pass
        monkeypatch.setattr(v2_module, "process_v2_job", _noop)

        app = FastAPI()
        app.include_router(auth_module.router, prefix="/api/auth")
        app.include_router(v2_module.router, prefix="/api/video/clone-v2")
        c = TestClient(app)

        # 注册 + 拿 token + 加积分
        em = f"u{uuid.uuid4().hex[:8]}@test.com"
        from app.services.auth import create_user, create_jwt_token
        u = create_user(email=em, password="x" * 8)
        with get_db() as conn:
            conn.execute("UPDATE users SET credits = ? WHERE id = ?", (200, u["id"]))
            conn.commit()
        token = create_jwt_token(u["id"], em, "user")

        # 18s 视频 → 丢弃尾部 [16,18] 2s → effective 16s → 2 段
        # B 阶段语义:trim_start/end 是丢弃区间
        body = {
            "type": "ultimate",
            "replacement_mode": "partial",
            "segments": [
                {"idx": 0, "source_type": "ai", "tier": "economy"},
                {"idx": 1, "source_type": "ai", "tier": "standard"},
            ],
            "video_url": "https://fake.fal.media/v.mp4",
            "video_duration_sec": 18.0,
            "video_sha256": "a" * 64,
            "image_urls": [],
            "prompt": "婴儿练习抬头",
            "disclaimer_acknowledged": True,
            "trim_start": 16,    # drop 区间起点
            "trim_end": 18,      # drop 区间终点
            "trimmed_seconds": 2,
        }
        r = c.post("/api/video/clone-v2/create", json=body, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        d = r.json()
        job_id = d["job_id"]

        with get_db() as conn:
            row = conn.execute(
                "SELECT type, replacement_mode, trim_start, trim_end, trimmed_seconds, "
                "total_credits_charged, segments_count "
                "FROM video_clone_v2_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        assert row[0] == "ultimate"
        assert row[1] == "partial"
        assert row[2] == 16.0
        assert row[3] == 18.0
        assert row[4] == 2.0
        assert row[5] == TIER_CREDITS["economy"] + TIER_CREDITS["standard"]
        assert row[6] == 2  # 2 段(plan 用 effective=16s 算)

        # ── 同一 client 测中段 drop:18s 视频丢中间 [8, 10] 2s → effective 16s → 2 段 ──
        body_mid = dict(body)
        body_mid["trim_start"] = 8.0
        body_mid["trim_end"] = 10.0
        body_mid["video_url"] = "https://fake.fal.media/v2.mp4"
        body_mid["video_sha256"] = "b" * 64
        r2 = c.post("/api/video/clone-v2/create", json=body_mid,
                    headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 200, r2.text
        with get_db() as conn:
            row2 = conn.execute(
                "SELECT trim_start, trim_end, trimmed_seconds, segments_count "
                "FROM video_clone_v2_jobs WHERE id = ?", (r2.json()["job_id"],)
            ).fetchone()
        assert row2[0] == 8.0
        assert row2[1] == 10.0
        assert row2[2] == 2.0
        assert row2[3] == 2  # effective 16s = 2 段

        # ── 校验:drop_end <= drop_start 拒绝 ──
        body_bad = dict(body)
        body_bad["trim_start"] = 10
        body_bad["trim_end"] = 5  # 终点 < 起点
        r3 = c.post("/api/video/clone-v2/create", json=body_bad,
                    headers={"Authorization": f"Bearer {token}"})
        assert r3.status_code == 400
        assert "trim 丢弃区间非法" in r3.json()["detail"]

        # ── 多段 drop:24s 视频丢 [5,6] + [12,13] = 2s → effective 22s(8 倍数 16)──
        # 实际我们用 18s 视频 + 2 段共 2s drop → effective 16s = 2 段
        body_multi = dict(body)
        body_multi.pop("trim_start", None)
        body_multi.pop("trim_end", None)
        body_multi["trim_drop_ranges"] = [[5, 6], [12, 13]]
        body_multi["trimmed_seconds"] = 2
        body_multi["video_url"] = "https://fake.fal.media/v3.mp4"
        body_multi["video_sha256"] = "c" * 64
        r4 = c.post("/api/video/clone-v2/create", json=body_multi,
                    headers={"Authorization": f"Bearer {token}"})
        assert r4.status_code == 200, r4.text
        with get_db() as conn:
            row4 = conn.execute(
                "SELECT trim_drop_ranges_json, trimmed_seconds, segments_count "
                "FROM video_clone_v2_jobs WHERE id = ?", (r4.json()["job_id"],)
            ).fetchone()
        import json as _j
        ranges = _j.loads(row4[0])
        assert ranges == [[5.0, 6.0], [12.0, 13.0]]
        assert row4[1] == 2.0
        assert row4[2] == 2  # effective 16s = 2 段

        # ── 多段 drop 重叠现在允许,按并集算 + 入库 merged ──
        # 18s 视频,丢 [5,8] + [7,10] → union=[5,10]=5s → effective 13s → 2 段(8+5)
        body_overlap = dict(body_multi)
        body_overlap["trim_drop_ranges"] = [[5, 8], [7, 10]]   # 7-8 重叠
        body_overlap["trimmed_seconds"] = 5
        body_overlap["video_url"] = "https://fake.fal.media/v4.mp4"
        body_overlap["video_sha256"] = "d" * 64
        r5 = c.post("/api/video/clone-v2/create", json=body_overlap,
                    headers={"Authorization": f"Bearer {token}"})
        assert r5.status_code == 200, r5.text
        with get_db() as conn:
            row5 = conn.execute(
                "SELECT trim_drop_ranges_json, trimmed_seconds, segments_count "
                "FROM video_clone_v2_jobs WHERE id = ?", (r5.json()["job_id"],)
            ).fetchone()
        assert _j.loads(row5[0]) == [[5.0, 10.0]], "重叠区段应 merge 成 [[5,10]]"
        assert row5[1] == 5.0, "trimmed_seconds 用 union 不是 sum"
        assert row5[2] == 2  # effective 13s = [(0,8),(8,13)] 两段

        # ── legacy trim_start/end 仍兼容(不传 trim_drop_ranges)──
        body_legacy = dict(body)
        body_legacy["trim_start"] = 16
        body_legacy["trim_end"] = 18
        body_legacy["video_url"] = "https://fake.fal.media/v5.mp4"
        body_legacy["video_sha256"] = "e" * 64
        r6 = c.post("/api/video/clone-v2/create", json=body_legacy,
                    headers={"Authorization": f"Bearer {token}"})
        assert r6.status_code == 200
        with get_db() as conn:
            row6 = conn.execute(
                "SELECT trim_drop_ranges_json FROM video_clone_v2_jobs WHERE id = ?",
                (r6.json()["job_id"],)
            ).fetchone()
        # legacy 单段也会自动转成 1-elem JSON 持久化
        assert _j.loads(row6[0]) == [[16.0, 18.0]]

        # ── 黑客视角:NaN / Infinity 必拒 ──
        body_nan = dict(body_multi)
        body_nan["trim_drop_ranges"] = [[float("nan"), 5]]
        body_nan["video_url"] = "https://fake.fal.media/nan.mp4"
        body_nan["video_sha256"] = "f" * 64
        # JSON 不能直接序列化 NaN — 用字符串模拟攻击者绕过
        import json as _json
        bad_payload = _json.dumps(body_nan, allow_nan=True)
        r_nan = c.post(
            "/api/video/clone-v2/create",
            data=bad_payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        # NaN 被 pydantic 接受时,我们的 isfinite 守卫拒绝;否则 422 也 OK
        assert r_nan.status_code in (400, 422), r_nan.text

        body_inf = dict(body_multi)
        body_inf["trim_drop_ranges"] = [[0, float("inf")]]
        body_inf["video_url"] = "https://fake.fal.media/inf.mp4"
        body_inf["video_sha256"] = "g" * 64
        bad_inf = _json.dumps(body_inf, allow_nan=True)
        r_inf = c.post(
            "/api/video/clone-v2/create", data=bad_inf,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        assert r_inf.status_code in (400, 422), r_inf.text

        # ── 9 段 (>8) 拒绝 — 防止恶意拖死 ffmpeg ──
        body_many = dict(body_multi)
        body_many["video_duration_sec"] = 64.0
        body_many["trim_drop_ranges"] = [[i * 5.0, i * 5.0 + 0.5] for i in range(9)]
        body_many["video_url"] = "https://fake.fal.media/many.mp4"
        body_many["video_sha256"] = "h" * 64
        r_many = c.post("/api/video/clone-v2/create", json=body_many,
                        headers={"Authorization": f"Bearer {token}"})
        assert r_many.status_code == 400
        assert "最多 8 段" in r_many.json()["detail"]

        # ── 单段太短 (< 0.05s) 拒绝 ──
        body_tiny = dict(body_multi)
        body_tiny["trim_drop_ranges"] = [[0, 0.01]]
        body_tiny["video_url"] = "https://fake.fal.media/tiny.mp4"
        body_tiny["video_sha256"] = "i" * 64
        r_tiny = c.post("/api/video/clone-v2/create", json=body_tiny,
                        headers={"Authorization": f"Bearer {token}"})
        assert r_tiny.status_code == 400
        assert "太短" in r_tiny.json()["detail"]

        # ── effective < 4s 拒绝 ──
        body_short = dict(body_multi)
        body_short["video_duration_sec"] = 8.0
        body_short["trim_drop_ranges"] = [[0, 5]]
        body_short["video_url"] = "https://fake.fal.media/short.mp4"
        body_short["video_sha256"] = "j" * 64
        r_short = c.post("/api/video/clone-v2/create", json=body_short,
                         headers={"Authorization": f"Bearer {token}"})
        assert r_short.status_code == 400
        assert "不足 4 秒" in r_short.json()["detail"]

        # cleanup
        get_settings.cache_clear()


# ─── SSRF 守卫:video_url 协议 + host allowlist ────────────────────────

class TestSSRFGuard:
    """validate_video_url 单元测试 + endpoint 接入测试。

    防御场景:
    - 内网探测   http://localhost / http://127.0.0.1 / http://169.254.169.254
    - 协议绕过   file:// / ftp:// / gopher://
    - DNS 绕过   IP 直连
    - allowlist 外部 host
    """

    def test_validate_video_url_unit(self):
        """直接调 validate_video_url 8 个用例。"""
        from app.api.video_clone_v2 import validate_video_url
        from fastapi import HTTPException

        # ✅ 1. 通过:fal.media 正主
        assert validate_video_url("https://fal.media/files/x.mp4") == \
            "https://fal.media/files/x.mp4"
        # ✅ 2. 通过:v3.fal.media 子域(suffix 匹配)
        assert validate_video_url("https://v3.fal.media/x.mp4") == \
            "https://v3.fal.media/x.mp4"
        # ✅ 通过:cdn.ailixiao.com 自家 CDN
        assert validate_video_url("https://cdn.ailixiao.com/v.mp4") == \
            "https://cdn.ailixiao.com/v.mp4"

        # ❌ 3. http:// 拒
        with pytest.raises(HTTPException) as exc:
            validate_video_url("http://fal.media/x.mp4")
        assert exc.value.status_code == 400
        assert "https" in exc.value.detail

        # ❌ 4. localhost 不在 allowlist 拒
        with pytest.raises(HTTPException) as exc:
            validate_video_url("https://localhost/x.mp4")
        assert "白名单" in exc.value.detail or "host" in exc.value.detail

        # ❌ 5. 127.0.0.1 IP 直连拒
        with pytest.raises(HTTPException) as exc:
            validate_video_url("https://127.0.0.1/x.mp4")
        assert "IP 直连" in exc.value.detail

        # ❌ 6. 169.254.169.254 云元数据拒(IP 直连分支)
        with pytest.raises(HTTPException) as exc:
            validate_video_url("https://169.254.169.254/latest/meta-data/")
        assert "IP 直连" in exc.value.detail

        # ❌ 7. evil.com 不在 allowlist 拒
        with pytest.raises(HTTPException) as exc:
            validate_video_url("https://evil.com/payload.mp4")
        assert exc.value.status_code == 400

        # ❌ 8. file:// 拒
        with pytest.raises(HTTPException) as exc:
            validate_video_url("file:///etc/passwd")
        assert exc.value.status_code == 400

        # 边界:空字符串 / None / IPv6 地址
        with pytest.raises(HTTPException):
            validate_video_url("")
        with pytest.raises(HTTPException):
            validate_video_url("https://[::1]/x.mp4")  # IPv6 localhost
        # 子域名后缀但 host 本身不是白名单(.fal.media.evil.com 风格)
        with pytest.raises(HTTPException):
            validate_video_url("https://fal.media.evil.com/x.mp4")

    def test_create_endpoint_rejects_ssrf(self, monkeypatch):
        """/create 端点上 SSRF host 必拒(401 之后,400 验证)。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import auth as auth_module
        from app.api import video_clone_v2 as v2_module

        monkeypatch.setenv("ENABLE_VIDEO_CLONE_V2", "true")
        from app.config import get_settings
        get_settings.cache_clear()

        async def _noop(jid): pass
        monkeypatch.setattr(v2_module, "process_v2_job", _noop)

        app = FastAPI()
        app.include_router(auth_module.router, prefix="/api/auth")
        app.include_router(v2_module.router, prefix="/api/video/clone-v2")
        c = TestClient(app)

        em = f"u{uuid.uuid4().hex[:8]}@test.com"
        from app.services.auth import create_user, create_jwt_token
        u = create_user(email=em, password="x" * 8)
        with get_db() as conn:
            conn.execute("UPDATE users SET credits = ? WHERE id = ?", (200, u["id"]))
            conn.commit()
        token = create_jwt_token(u["id"], em, "user")

        base_body = {
            "type": "single",
            "replacement_mode": "partial",
            "segments": [{"idx": 0, "source_type": "ai", "tier": "economy"}],
            "video_url": "PLACEHOLDER",
            "video_duration_sec": 6.0,
            "video_sha256": "a" * 64,
            "image_urls": [],
            "prompt": "测试",
            "disclaimer_acknowledged": True,
        }

        attacks = [
            "http://fal.media/x.mp4",            # 非 https
            "https://localhost/x.mp4",           # 内网
            "https://127.0.0.1/x.mp4",           # IP 直连
            "https://169.254.169.254/x",         # 云元数据
            "https://evil.com/x.mp4",            # 外网 allowlist 外
            "file:///etc/passwd",                # 协议拒
            "ftp://fal.media/x.mp4",             # 协议拒
            "https://fal.media.evil.com/x.mp4",  # 后缀混淆
        ]
        for atk in attacks:
            body = dict(base_body)
            body["video_url"] = atk
            r = c.post("/api/video/clone-v2/create", json=body,
                       headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 400, f"攻击 URL 没被拒:{atk} → {r.status_code} {r.text[:200]}"

        # 同 endpoint 的合法 URL 通过 SSRF 检查(后续可能因别的原因失败,但不是 SSRF)
        body_ok = dict(base_body)
        body_ok["video_url"] = "https://v3.fal.media/legit.mp4"
        r_ok = c.post("/api/video/clone-v2/create", json=body_ok,
                      headers={"Authorization": f"Bearer {token}"})
        # 合法 URL 应该过 SSRF 守卫(到达后续逻辑)→ 200 或别的 400(eg. 段不够),
        # 但不能因 SSRF 而 400
        if r_ok.status_code == 400:
            assert "白名单" not in r_ok.json()["detail"]
            assert "https" not in r_ok.json()["detail"]
            assert "IP 直连" not in r_ok.json()["detail"]

        get_settings.cache_clear()

    def test_check_duration_endpoint_rejects_ssrf(self, monkeypatch):
        """/check-duration 端点 video_url 也必须过 SSRF 守卫(motion_score 会去 GET)。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import auth as auth_module
        from app.api import video_clone_v2 as v2_module

        monkeypatch.setenv("ENABLE_VIDEO_CLONE_V2", "true")
        from app.config import get_settings
        get_settings.cache_clear()

        app = FastAPI()
        app.include_router(auth_module.router, prefix="/api/auth")
        app.include_router(v2_module.router, prefix="/api/video/clone-v2")
        c = TestClient(app)

        em = f"u{uuid.uuid4().hex[:8]}@test.com"
        from app.services.auth import create_user, create_jwt_token
        u = create_user(email=em, password="x" * 8)
        token = create_jwt_token(u["id"], em, "user")

        # 24s 视频需要 trim → 走 motion_score 路径(读 video_url)
        r = c.post("/api/video/clone-v2/check-duration", json={
            "video_duration_sec": 24.5,
            "video_url": "http://169.254.169.254/latest/meta-data/",
        }, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 400
        assert "https" in r.json()["detail"]

        # video_url 不传也允许(走静态 fallback)
        r2 = c.post("/api/video/clone-v2/check-duration", json={
            "video_duration_sec": 24.5,
        }, headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 200

        get_settings.cache_clear()


# ─── union 算法独立验证(对应前端 totalDroppedAsUnion)────────────────────

class TestUnionDuration:
    """直接覆盖 _compute_keep_ranges 内联的 merge 算法 + /create 内联实现。

    跟前端 totalDroppedAsUnion 必须等价 — 两端口径一致才不会出现"前端显示 5s
    后端按 6s 扣"的对不上账。
    """

    def _union_total(self, ranges, full=100.0):
        """复用 processor._compute_keep_ranges 的合并块,返回并集总宽度。"""
        from app.services.video_clone_v2_processor import _compute_keep_ranges
        keeps = _compute_keep_ranges(ranges, full)
        kept_total = sum(e - s for s, e in keeps)
        return full - kept_total

    def test_union_4_cases(self):
        # 1. 部分重叠 [(2,5),(4,7)] → 并集 [2,7] = 5
        assert abs(self._union_total([(2, 5), (4, 7)]) - 5.0) < 0.01
        # 2. 不重叠 [(2,5),(10,12)] → 3 + 2 = 5
        assert abs(self._union_total([(2, 5), (10, 12)]) - 5.0) < 0.01
        # 3. 完全包含 [(2,5),(3,4)] → 3
        assert abs(self._union_total([(2, 5), (3, 4)]) - 3.0) < 0.01
        # 4. 紧贴 [(2,5),(5,8)] → 合并 = 6
        assert abs(self._union_total([(2, 5), (5, 8)]) - 6.0) < 0.01
        # 5. 三段链式重叠 [(0,3),(2,5),(4,7)] → 并集 [0,7] = 7
        assert abs(self._union_total([(0, 3), (2, 5), (4, 7)]) - 7.0) < 0.01
        # 6. 单段 = 自己宽度
        assert abs(self._union_total([(0, 5)]) - 5.0) < 0.01
        # 7. 空 = 0
        assert abs(self._union_total([]) - 0.0) < 0.01
