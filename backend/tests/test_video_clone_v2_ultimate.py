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
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.database import get_db
from app.services import video_clone_v2_processor as proc_mod
from app.services.video_clone_v2_pricing import CREDITS_PER_SEC, calc_segment_credits


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


# ─── 退款撞 SQLite 锁安全(2026-06-17 加固回归)──────────────────────────


@contextmanager
def _locked_db():
    """模拟 pending_refunds 登记 INSERT 撞 'database is locked' 写锁。"""
    class _LockingConn:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")
        def commit(self):
            pass
        def close(self):
            pass
    yield _LockingConn()


class TestRefundLockSafety:
    """退款登记撞 'database is locked' 时,绝不能被静默吞掉当成"已退过"而跳过
    add_credits(旧实现 `except: pass` + `if not row` 会凭空吃用户的钱)。
    正确行为:照常上抛 OperationalError —— 响亮失败、可人工补救,顶层 status 守卫保证不重复退。"""

    def _seed_user_and_job(self, credits_initial=100, credits_charged=650, status="refunded"):
        from app.services.auth import create_user
        user = create_user(email=f"u{uuid.uuid4().hex[:8]}@test.com", password="x" * 8)
        with get_db() as conn:
            conn.execute("UPDATE users SET credits = ? WHERE id = ?", (credits_initial, user["id"]))
            job_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO video_clone_v2_jobs (id, user_id, type, replacement_mode, "
                "input_video_url, input_video_duration_sec, image_urls, prompt, "
                "segments_plan, segments_count, total_credits_charged, status) "
                "VALUES (?, ?, 'single', 'full', 'x', 8, '[]', 'p', '[]', 1, ?, ?)",
                (job_id, user["id"], credits_charged, status),
            )
            conn.commit()
        return user["id"], job_id

    def test_refund_full_lock_raises_not_silent_skip(self):
        """核心回归:撞锁必须上抛,且不留"假装已退"的脏状态(余额不变 + refunded 不被错误置位)。"""
        user_id, job_id = self._seed_user_and_job(credits_initial=100, credits_charged=650)
        job = {"id": job_id, "user_id": user_id, "total_credits_charged": 650}
        with patch.object(proc_mod, "get_db", _locked_db):
            with pytest.raises(sqlite3.OperationalError):
                asyncio.run(proc_mod._refund_full(job))
        with get_db() as conn:
            credits = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()[0]
            refunded = conn.execute(
                "SELECT total_credits_refunded FROM video_clone_v2_jobs WHERE id = ?", (job_id,)
            ).fetchone()[0]
        assert credits == 100   # 没多没少,没被静默吞
        assert refunded == 0    # 没被错误标记成已退

    def test_refund_partial_lock_raises_not_silent_skip(self):
        user_id, job_id = self._seed_user_and_job(credits_initial=100, credits_charged=650)
        job = {"id": job_id, "user_id": user_id, "total_credits_charged": 650}
        with patch.object(proc_mod, "get_db", _locked_db):
            with pytest.raises(sqlite3.OperationalError):
                asyncio.run(proc_mod._refund_partial(job, 0, 80))
        with get_db() as conn:
            credits = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()[0]
        assert credits == 100

    def test_refund_full_normal_then_idempotent(self):
        """正常退一次 + 重复调用不再退(幂等仍成立,没被加固改坏)。"""
        user_id, job_id = self._seed_user_and_job(credits_initial=100, credits_charged=650)
        job = {"id": job_id, "user_id": user_id, "total_credits_charged": 650}
        asyncio.run(proc_mod._refund_full(job))
        asyncio.run(proc_mod._refund_full(job))  # 重复 — 不应再退
        with get_db() as conn:
            credits = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()[0]
            refunded = conn.execute(
                "SELECT total_credits_refunded FROM video_clone_v2_jobs WHERE id = ?", (job_id,)
            ).fetchone()[0]
        assert credits == 750   # 100 + 650,只退一次
        assert refunded == 650


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
            # 2026-05-13:按段 duration × 50 算
            total_credits = sum(
                calc_segment_credits(float(p.get("duration") or 0))
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

    def _mock_download(self, src_video=P220_VIDEO):
        """mock httpx 下载 — 把 P220 视频复制成 aiview 输出 / 用户输入。"""
        import shutil
        async def _stream_to(url, out_path):
            shutil.copy2(src_video, out_path)
        return _stream_to

    @patch("app.services.video_clone_v2_processor.upload_to_cos")
    @patch("app.services.video_clone_v2_processor.call_aiview_seedance")
    @patch("app.services.video_clone_v2_processor._download_to_local")
    def test_all_segs_succeed_concats_dual_versions(
        self, mock_download, mock_aiview, mock_cos, tmp_path, monkeypatch,
    ):
        """3 段全 AI 全成功 → concat → 双版本归档。"""
        mock_cos.return_value = "https://fake.cos/in.mp4"
        mock_aiview.return_value = {
            "video_url": "https://fake.cos/out.mp4",
            "actual_cost_usd": None,
            "raw_response": {"request_id": "req-1"},
        }
        # mock aiview output download
        async def _dl(url, out):
            import shutil; shutil.copy2(P220_VIDEO, out)
        mock_download.side_effect = _dl

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
            {"idx": 0, "start": 0.0, "duration": 2.0, "source_type": "ai"},
            {"idx": 1, "start": 2.0, "duration": 2.0, "source_type": "ai"},
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

    @patch("app.services.video_clone_v2_processor.upload_to_cos")
    @patch("app.services.video_clone_v2_processor.call_aiview_seedance")
    @patch("app.services.video_clone_v2_processor._download_to_local")
    def test_one_seg_fails_others_complete_full_refund(
        self, mock_download, mock_aiview, mock_cos, tmp_path, monkeypatch,
    ):
        """任意 ai 段失败(包括 retry)→ 整单 failed + 全额退款。"""
        mock_cos.return_value = "https://fake.cos/in.mp4"
        async def _always_fail(*a, **kw):
            raise RuntimeError("fake aiview failure")
        mock_aiview.side_effect = _always_fail

        async def _dl(url, out):
            import shutil; shutil.copy2(P220_VIDEO, out)
        mock_download.side_effect = _dl

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
            {"idx": 0, "start": 0.0, "duration": 2.0, "source_type": "ai"},
            {"idx": 1, "start": 2.0, "duration": 2.0, "source_type": "ai"},
            {"idx": 2, "start": 4.0, "duration": 2.0, "source_type": "ai"},
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
        assert row[0] == "failed", f"期望 failed(整单失败),实际:{row[0]}"
        # 2026-05-13:全额退款 = Σ(每段 duration × 50)= charged
        assert row[1] == charged, f"期望全额退 {charged},实际退 {row[1]}"
        assert user_credits == balance_before + charged

    @patch("app.services.video_clone_v2_processor.upload_to_cos")
    @patch("app.services.video_clone_v2_processor.call_aiview_seedance")
    def test_all_segs_fail_marks_failed_and_partial_refunds(
        self, mock_aiview, mock_cos, tmp_path, monkeypatch,
    ):
        """所有 ai 段都失败 → status=failed,所有段 credits 各自退。"""
        mock_cos.return_value = "https://fake.cos/in.mp4"
        async def _all_fail(*a, **kw):
            raise RuntimeError("fake aiview failure")
        mock_aiview.side_effect = _all_fail

        plan = [
            {"idx": 0, "start": 0.0, "duration": 2.0, "source_type": "ai"},
            {"idx": 1, "start": 2.0, "duration": 2.0, "source_type": "ai"},
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
        # 2026-05-13:2 段 ai 各 2s × 50 = 各 100 → 全退 200
        assert row[1] == 2 * calc_segment_credits(2.0)


# ─── Pydantic extra="forbid" 安全网测试 ────────────────────────────────

class TestPydanticForbidSafetyNet:
    """🚩 单档:Pydantic Request 类加了 extra="forbid",防老 client 传废弃 tier 字段被静默接受"""

    def _make_client_and_token(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import video_clone_v2 as v2_module
        from app.api.auth import create_jwt_token
        from app.services.auth import create_user

        monkeypatch.setenv("ENABLE_VIDEO_CLONE_V2", "true")
        from app.config import get_settings
        get_settings.cache_clear()

        app = FastAPI()
        app.include_router(v2_module.router, prefix="/api/video/clone-v2")
        c = TestClient(app)

        em = f"u{uuid.uuid4().hex[:8]}@test.com"
        u = create_user(email=em, password="x" * 8)
        token = create_jwt_token(u["id"], em, "user")
        return c, token

    def test_segment_with_tier_field_rejected(self, monkeypatch):
        """单档:API body 传废弃的 tier 字段必须 422,不能静默接受"""
        c, token = self._make_client_and_token(monkeypatch)
        body = {
            "type": "single",
            "replacement_mode": "partial",
            "segments": [
                {"idx": 0, "source_type": "ai", "tier": "economy"},  # ⚠️ 旧前端遗留废弃字段
            ],
            "video_url": "https://fake.fal.media/v.mp4",
            "video_duration_sec": 6.0,
            "video_sha256": "a" * 64,
            "image_urls": [],
            "prompt": "测试",
            "disclaimer_acknowledged": True,
        }
        r = c.post("/api/video/clone-v2/create", json=body,
                   headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 422, f"期望 422 拒 tier 字段,实际:{r.status_code} body={r.text[:200]}"
        assert "tier" in r.text.lower(), f"422 错误信息应提到 tier,实际:{r.text[:200]}"

    def test_request_without_unknown_fields_passes_pydantic(self, monkeypatch):
        """对照测试:body 不含废弃字段 → 不被 Pydantic 拒(可能业务 4xx 但不是 422 schema 错)"""
        c, token = self._make_client_and_token(monkeypatch)
        body = {
            "type": "single",
            "replacement_mode": "partial",
            "segments": [{"idx": 0, "source_type": "ai"}],  # ✅ 没有 tier
            "video_url": "https://fake.fal.media/v.mp4",
            "video_duration_sec": 6.0,
            "video_sha256": "a" * 64,
            "image_urls": [],
            "prompt": "测试",
            "disclaimer_acknowledged": True,
        }
        # mock process_v2_job 不真跑
        from app.api import video_clone_v2 as v2_module
        async def _noop(jid): pass
        monkeypatch.setattr(v2_module, "process_v2_job", _noop)

        r = c.post("/api/video/clone-v2/create", json=body,
                   headers={"Authorization": f"Bearer {token}"})
        # 不是 422(Pydantic 不报)。可能 200 / 业务 4xx,不能是 schema 错
        assert r.status_code != 422, f"合法 body 不该被 Pydantic 拒,实际:{r.status_code} {r.text[:200]}"


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
            # 2026-05-13 新定价:2 段 × 8s × 50 = 800 积分/单,需够 4 次扣(尾/中/无效/multi)
            conn.execute("UPDATE users SET credits = ? WHERE id = ?", (5000, u["id"]))
            conn.commit()
        token = create_jwt_token(u["id"], em, "user")

        # 13s 视频 → 丢弃尾部 [11,13] 2s → effective 11s → 1 段(≤15s 单段路径)
        # B 阶段语义:trim_start/end 是丢弃区间
        body = {
            "type": "ultimate",
            "replacement_mode": "partial",
            "segments": [
                {"idx": 0, "source_type": "ai"},
            ],
            "video_url": "https://fake.fal.media/v.mp4",
            "video_duration_sec": 13.0,
            "video_sha256": "a" * 64,
            "image_urls": [],
            "prompt": "婴儿练习抬头",
            "disclaimer_acknowledged": True,
            "trim_start": 11,    # drop 区间起点
            "trim_end": 13,      # drop 区间终点
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
        assert row[2] == 11.0
        assert row[3] == 13.0
        assert row[4] == 2.0
        # 1 段 ai × 11s × CREDITS_PER_SEC (effective_duration=11)
        assert row[5] == calc_segment_credits(11.0, CREDITS_PER_SEC)
        assert row[6] == 1

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
        assert row2[3] == 1  # effective 11s = 1 段(≤15s 单段路径)

        # ── 校验:drop_end <= drop_start 拒绝 ──
        body_bad = dict(body)
        body_bad["trim_start"] = 10
        body_bad["trim_end"] = 5  # 终点 < 起点
        r3 = c.post("/api/video/clone-v2/create", json=body_bad,
                    headers={"Authorization": f"Bearer {token}"})
        assert r3.status_code == 400
        assert "trim 丢弃区间非法" in r3.json()["detail"]

        # ── 多段 drop:13s 视频丢 [5,6] + [8,9] = 2s → effective 11s → 1 段 ──
        body_multi = dict(body)
        body_multi.pop("trim_start", None)
        body_multi.pop("trim_end", None)
        body_multi["trim_drop_ranges"] = [[5, 6], [8, 9]]
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
        assert ranges == [[5.0, 6.0], [8.0, 9.0]]
        assert row4[1] == 2.0
        assert row4[2] == 1  # effective 11s = 1 段

        # ── 多段 drop 重叠现在允许,按并集算 + 入库 merged ──
        # 13s 视频,丢 [5,8] + [7,10] → union=[5,10]=5s → effective 8s → 1 段
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
        assert row5[2] == 1  # effective 8s = 1 段(≤15s 单段路径)

        # ── legacy trim_start/end 仍兼容(不传 trim_drop_ranges)──
        body_legacy = dict(body)
        body_legacy["trim_start"] = 11
        body_legacy["trim_end"] = 13
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
        assert _j.loads(row6[0]) == [[11.0, 13.0]]

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
            # 2026-05-13 新定价:1 段 6s × 50 = 300 积分,余额 2000 够多次试
            conn.execute("UPDATE users SET credits = ? WHERE id = ?", (2000, u["id"]))
            conn.commit()
        token = create_jwt_token(u["id"], em, "user")

        base_body = {
            "type": "single",
            "replacement_mode": "partial",
            "segments": [{"idx": 0, "source_type": "ai"}],
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

        # SSRF 守卫在 duration check 之前运行,任何时长 + 非法 URL 都应 400
        r = c.post("/api/video/clone-v2/check-duration", json={
            "video_duration_sec": 10.0,
            "video_url": "http://169.254.169.254/latest/meta-data/",
        }, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 400
        assert "https" in r.json()["detail"]

        # video_url 不传 + 合法时长 → 200
        r2 = c.post("/api/video/clone-v2/check-duration", json={
            "video_duration_sec": 10.0,
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


# ─── /upload/video + /upload/image 端点测试(COS 路径)────────────────────

class TestV2UploadCos:
    """Gate 3:upload_video / upload_image 端点切 COS 后的 mock 测试。

    只验证 HTTP 层 + cos_upload.upload_to_cos 被调用,不真打 COS。
    """

    def _make_v2_client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import video_clone_v2 as v2_module
        from app.api.auth import create_jwt_token
        from app.services.auth import create_user

        monkeypatch.setenv("ENABLE_VIDEO_CLONE_V2", "true")
        from app.config import get_settings
        get_settings.cache_clear()

        app = FastAPI()
        app.include_router(v2_module.router, prefix="/api/video/clone-v2")
        c = TestClient(app)
        em = f"u{uuid.uuid4().hex[:8]}@cos.test"
        u = create_user(email=em, password="x" * 8)
        token = create_jwt_token(u["id"], em, "user")
        return c, token

    def test_upload_video_calls_cos(self, monkeypatch):
        """upload_video 端点:mock upload_to_cos 返 COS URL → 200 + url 字段正确。"""
        import subprocess
        from app.api import video_clone_v2 as v2_module

        c, token = self._make_v2_client(monkeypatch)
        fake_url = "https://ailixiao-uploads-1421174544.cos.ap-guangzhou.myqcloud.com/uploads/abc.mp4"

        def _fake_subprocess_run(cmd, **kw):
            m = MagicMock()
            m.stdout = '{"format":{"duration":"5.0"}}'
            m.returncode = 0
            return m

        monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)
        monkeypatch.setattr(v2_module, "upload_to_cos", lambda p: fake_url)
        monkeypatch.setattr(v2_module, "cache_store", lambda sha, p: False)
        monkeypatch.setattr(v2_module, "cache_clean_old", lambda: None)
        # 模块 load 时 STORAGE_BUCKET 未设 → 测试里手动补白名单
        v2_module._ALLOWED_VIDEO_HOSTS.add("ailixiao-uploads-1421174544.cos.ap-guangzhou.myqcloud.com")

        data = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 100  # fake mp4 magic
        files = {"file": ("test.mp4", data, "video/mp4")}
        r = c.post("/api/video/clone-v2/upload/video", files=files,
                   headers={"Authorization": f"Bearer {token}"})
        # 2026-06-15:upload_video 改异步,端点秒回 blur_job_id;COS 上传在后台 _blur_video_task 里做
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("blur_job_id"), "应返回 blur_job_id"
        assert body["status"] == "processing"

    async def test_upload_video_ssrf_guard_rejects_bad_url(self, monkeypatch, tmp_path):
        """SSRF 守卫:后台 task 里 upload_to_cos 返非白名单域名 → validate_video_url 拦截 → 任务 failed。"""
        import subprocess
        from app.api import video_clone_v2 as v2_module

        def _fake_subprocess_run(cmd, **kw):
            m = MagicMock()
            m.stdout = '{"format":{"duration":"3.0"}}'
            m.returncode = 0
            return m

        monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)
        monkeypatch.setattr(v2_module, "sha256_file", lambda p: "S")
        monkeypatch.setattr(v2_module, "upload_to_cos", lambda p: "https://evil.com/steal.mp4")
        monkeypatch.setattr(v2_module, "cache_store", lambda sha, p: False)
        monkeypatch.setattr(v2_module, "cache_clean_old", lambda: None)

        path = str(tmp_path / "x.mp4")
        with open(path, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypisom")
        v2_module._blur_jobs["ssrf"] = {"status": "processing", "user_id": "u1", "ts": 0}
        await v2_module._blur_video_task("ssrf", path, "u1", mask_face=False)
        job = v2_module._blur_jobs["ssrf"]
        assert job["status"] == "failed", f"非白名单 URL 应被 SSRF 守卫拦截,实际:{job}"
        v2_module._blur_jobs.clear()

    def test_upload_image_calls_cos(self, monkeypatch):
        """upload_image 端点:mock upload_to_cos 返 COS URL → 200。"""
        from app.api import video_clone_v2 as v2_module

        c, token = self._make_v2_client(monkeypatch)
        fake_url = "https://ailixiao-uploads-1421174544.cos.ap-guangzhou.myqcloud.com/uploads/img.jpg"
        monkeypatch.setattr(v2_module, "upload_to_cos", lambda p: fake_url)

        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.new("RGB", (100, 100), color=(128, 128, 128)).save(buf, format="JPEG")
        img_bytes = buf.getvalue()

        files = {"file": ("ok.jpg", img_bytes, "image/jpeg")}
        data = {"role": "product"}
        r = c.post("/api/video/clone-v2/upload/image", files=files, data=data,
                   headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        assert r.json()["image_url"] == fake_url
