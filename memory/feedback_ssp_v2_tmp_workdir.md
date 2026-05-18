---
name: SSP V2 /tmp 工作目录 owner 必须 ssp-app
description: video_clone_v2 processor mkdir /tmp/video_clone_v2_work/{job_id} 失败 → 协程死 → DB 卡 processing + 积分锁住,根因是父目录 owner=root
type: feedback
originSessionId: 8fc0fc07-2ff7-45b2-9506-d87424466ec8
---
video_clone_v2 / 任何 ssp-app 进程在 /tmp 下建子目录的服务,父目录 **必须** chown ssp-app:ssp-app(或 mode 1777)。

**Why:**
2026-05-10 凌晨实测踩两次:
1. job ac0388d2:误诊为 fake URL 下载失败 — 实际根本没到下载,mkdir 第一步就 PermissionError
2. job f1e6cb40:同根因复现,backend log 写明 `PermissionError: [Errno 13] Permission denied: /tmp/video_clone_v2_work/{job_id}`

`/tmp` 本身是 1777 sticky,但 root 模式时(降权前)创建的 `/tmp/video_clone_v2_work/` 子目录 owner=root 755 → ssp-app(UID 998)无权 mkdir 第二层。
processor `Path(work_dir).mkdir(parents=True)` 的异常被 BackgroundTask 默默吞 → DB `_db_update_job` 永远不被调用 → 用户付 30 积分卡死,前端轮询永远 200 processing。

**How to apply:**
- 用户报"V2 任务卡 processing 不出片":第一动作 `ls -ld /tmp/video_clone_v2_work` 看 owner,不是 ssp-app 就 `chown -R ssp-app:ssp-app /tmp/video_clone_v2_work/`
- 修完 owner,卡死的 job 不会自动恢复(协程已死),必须手动走 `_refund_full(job) + _db_update_job(status='failed', error_step='orphan_recovery_perm')` 等价回收链路
- 长期修:在 `/root/ssp/deploy/p221-a2-deploy.sh`(或 backend 启动 hook)加 `mkdir -p /tmp/video_clone_v2_work && chown ssp-app:ssp-app /tmp/video_clone_v2_work` 幂等步,确保每次 deploy / supervisor 重启都正确
- 同模式适用于 V2 cache `/tmp/v2_cache/`、其他 ssp-app 写 /tmp 的服务

**诊断口诀(用户报"卡 processing"):**
1. `grep {job_id} /var/log/ssp-backend-green.err.log` 看有没有 PermissionError / Traceback
2. `ls -ld /tmp/video_clone_v2_work` 看 owner
3. `ls /tmp/video_clone_v2_work/{job_id}` 工作目录有没有创建(没有 → 死在 mkdir 之前)
4. `SELECT updated_at, created_at FROM video_clone_v2_jobs WHERE id=?` — updated_at == created_at 说明 _db_update_job 一次都没调过 → 协程根本没活到 download/split
