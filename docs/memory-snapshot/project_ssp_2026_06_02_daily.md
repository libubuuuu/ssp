---
name: ssp-2026-06-02
description: 视频复刻V2上传从fal切腾讯COS，commit 70ef796
metadata: 
  node_type: memory
  type: project
  originSessionId: 1abeb302-bc07-4c8c-9848-dca4916c92b5
---

视频复刻 V2 上传从 fal 中转改走腾讯 COS 直传，commit 70ef796，已蓝绿部署（green 槽激活）。

**改动范围（仅此一项，其余未碰）：**
- `backend/app/services/cos_upload.py`：新建，`upload_to_cos(file_path)` → put_object + presigned GET 24h
- `backend/app/api/video_clone_v2.py`：
  - import 换成 `upload_to_cos`（删掉 `fal_upload_with_retry`）
  - `_ALLOWED_VIDEO_HOSTS` frozenset → set，模块 load 时动态 add `STORAGE_BUCKET.cos.REGION.myqcloud.com`
  - `upload_video` / `upload_image` 两处切 `asyncio.to_thread(upload_to_cos, ...)`
- `backend/requirements.txt`：补 `cos-python-sdk-v5>=1.9.30`
- `backend/tests/test_video_clone_v2_ultimate.py`：新增 `TestV2UploadCos`（3 例）

**Gate 状态：**
- Gate 1：`cos-python-sdk-v5` 已装进 `/opt/ssp/backend/venv`，requirements.txt 已写
- Gate 2：.env.enc 里 4 个 STORAGE_* 键早已存在（STS 配置时写入），无需改
- Gate 3：3 新测试全过，全套无新回归

**已验证：**
- `_ALLOWED_VIDEO_HOSTS` 含 `ailixiao-uploads-1421174544.cos.ap-guangzhou.myqcloud.com` ✓
- nginx → green(8001)，blue 停机可 rollback ✓

**用户待自测：**
发一条 ≤15s 视频复刻，看后端 `[V2-UPLOAD-VIDEO]` 日志 `url=` 是否 `*.cos.ap-guangzhou.myqcloud.com`。

**Why:** 服务器 32Mbps 出口不再是上传瓶颈，视频走用户带宽直上 COS CDN。
**How to apply:** 回退时把 upload_video / upload_image 两处改回 `fal_upload_with_retry`，或 `bash /root/rollback.sh`。
