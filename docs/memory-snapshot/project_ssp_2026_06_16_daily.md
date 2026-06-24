---
name: project_ssp_2026_06_16_daily
description: SSP 2026-06-16 大改动汇总——face-mask 打码调厚+专业版不打码入口 / aiview 结果跳过本地归档
metadata: 
  node_type: memory
  type: project
  originSessionId: 1443a638-1a9c-4142-935f-3f454e849558
---

SSP 2026-06-16 上线（main == 线上 == commit `7db6ed6`，真蓝绿全量切换 + 合并 main 消除分叉）。

**① face-mask（视频复刻 V2 人脸打码）**
- 打码调厚：`face_mask_pro.py` 两个默认 `blocks=12 → 8`（马赛克格子更大更糊更实，用户本地实测 8 合适）。caller `face_mask_worker.py` 不传 blocks，继承默认。
- 前端新增"专业版生成人脸(不打码)"入口（`video-clone-v2/page.tsx`）：`handleImageUpload` 加 `noMask` 参数，`mask_face=String(maskFace && !noMask)`；person 区第二个 FileInput 传 `noMask=true`。后端 `upload_image` 已支持 `mask_face`，未改后端。
- 取消了字幕保护（不做 preserve_subtitle / 不装 EAST），用无字幕打码。
- 依据：aiview 文档 v1.5.0 行 414——aiview 拒真人脸，**除非是本账号近 30 天内由 Seedance2.0/2.0Fast/Seedream5.0lite 生成的含人脸原始产物**；故"专业版 AI 生成的脸"受信任、本就不该打码。详见 [[reference_aiview_cos_permanent]]。

**② COS：aiview openapi 结果跳过本地归档**（commit 9475033）
- `media_archiver.is_permanent_cos_result()`：精确识别我们桶 openapi/ 永久公有读直链（host=`{STORAGE_BUCKET}.cos.{STORAGE_REGION}.myqcloud.com` 且 path `/openapi/`）→ `archive_url` 原样放行不下载。
- `jobs._bg_archive` 命中永久链直接 continue（否则重试循环把"URL 没变化"误判失败，空转 120s + 假错误日志）。
- 只放行 openapi/；我们自己 `uploads/` 是 presigned 会过期签名链，不碰；fal.media 照常归档。V2 走独立 archive 未碰。详见 [[reference_aiview_cos_permanent]]。

**③ 视频打码+选择器隐藏，但人物图片涂鸦保留**（最终 commit 79e6e78，上线；中途 dade7f8/de4bdc9 被此版取代）
- 两个独立前端开关（`video-clone-v2/page.tsx` 顶部）：
  - `SHOW_FACE_MASK = false` → 隐藏三选一选择器 UI + **视频不打码**（视频慢，全片解码 ~3.5min/60s）。`maskFace = SHOW_FACE_MASK && privacyMode==="auto"` 恒 false，视频 `mask_face=String(maskFace)` 恒 false。
  - `MASK_PERSON_IMAGE = true` → **人物图片仍自动涂鸦盖脸**（单图 `blur_faces_in_image` 很快，保留肖像保护）+ 显示"专业版生成人脸(不打码)"opt-out 按钮。图片 `mask_face=String(MASK_PERSON_IMAGE && !noMask)`。
  - 升配后把 `SHOW_FACE_MASK` 改回 true 即恢复视频打码 + 选择器。
- 已验证 SSR：选择器"需要替换人脸" 0 处，专业版按钮 1 处。
- ⚠️ 副作用：**视频**不打码 → 上传真人脸视频会被 aiview 拒（`Real face detected`）；图片仍涂鸦故不受影响。

（以下为更早的全隐藏说明，已被上面取代，保留备查）
**曾经的全隐藏版** de4bdc9：单一 `SHOW_FACE_MASK` 总开关，视频+图片都不打码、选择器+专业版按钮全隐藏。被 79e6e78（图片涂鸦保留）取代。
- 服务器配置过低（2vCPU 纯CPU，打码 ~3.5min/60s，串行+nice 拖慢出片），用户决定先**整个隐藏打码功能、回到"不做处理"版本**、等升配再开。
- 前端 `video-clone-v2/page.tsx` 总开关 `const SHOW_FACE_MASK = false`（文件顶部第 16 行）：
  - 隐藏整块"人脸隐私处理"三选一选择器 UI（`{SHOW_FACE_MASK && (...)}` 包裹）
  - `privacyMode` 默认 `none`、`maskFace = SHOW_FACE_MASK && ...` 恒 false → **视频+图片都不打码**，原文件直传
  - 一并隐藏"专业版生成人脸(不打码)"按钮（无打码场景下冗余）
  - 隐私选择器 JSX + 后端打码代码 + upload_image 的 mask_face 支持**全保留**，`SHOW_FACE_MASK` 改回 `true` 即一键恢复全部（选择器+视频+图片打码+专业版按钮）
- 已验证：SSR HTML 0 处出现"人脸隐私处理/需要替换人脸"，确认真不渲染。
- ⚠️ 副作用：打码全关后，上传**真人脸**（视频或图片）会被 aiview 拒（`Real face detected`），现阶段视频复刻只能用 AI 生成人物/已自行处理素材。
- 提速方案（未做，待用户定）：①单遍解码 ②单次编码（码内估省~40%）；真正数量级靠硬件——独开 CVM 专跑打码 / GPU 实例。用户已索取腾讯云升配/购买链接。

**deploy.sh smoke-test bug 已修**（commit 8a91345）：`dirname "$0"` 经 symlink(`/root/deploy.sh`)返回 `/root`，找不到 `/root/ssp/deploy/smoke-test.sh`/`push-alert.sh` → 冒烟+自动回滚安全网两次部署被静默跳过。改用 `SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"`。已验证：dade7f8 部署时冒烟测试真跑（6 通过/0 失败）。

**④ 历史页时间显示转本地时区**（commit ba26840）：`generation_history`/`video_clone_v2_jobs` 的 created_at 都是 UTC 串,前端原 `.slice()` 直接显示=UTC,北京用户看着晚 8h。加 `fmtLocal()`（补 `T...Z`→`toLocaleString` 浏览器本地时区,失败回退）。纯前端,两源格式一致(都 TEXT CURRENT_TIMESTAMP)。⚠️ 另发现 `completed_at` 列被 `time.strftime` 写成了 CST(本地)而非 UTC（`video_clone_v2_processor.py` 三处），但该列只用于 admin 统计 + 已删的口播 modal,**不展示给用户**,故未修。

**⑤ 图片生成并行尝试→已回退**（4a1ee21 上线，065e7d0 revert）：曾把 `image/page.tsx` 改非阻塞并行（`pending[]`+`MAX_PARALLEL=6`+`watchJob`）。**用户实测感觉更慢→要求改回串行,已 revert**。原因:图片生成瓶颈在 aiview 上游（文档"全站共享最多 10 并发"，是所有客户共享的），并发提交把 aiview 队列填满+后端 Semaphore(5) 争抢→每张比单独跑更慢（吞吐 vs 延迟取舍）。站点是 HTTP/2,排除了 SSE 连接耗尽。结论:**单张串行=独占资源最快出图,当前小机器+aiview 共享队列下并行不划算**。现已回到改前串行行为。
- ⚠️ **后端站点级瓶颈未动**：`jobs.py:2040` 全局 `Semaphore(5)` 且 `async with` **包住整个 `event.wait()`**（含 aiview 5-6 分钟纯等待），全站最多 5 个生成；注释(490)写"不再占用 semaphore 等待"但实现没做到。后端生成实测平均 ~6 分钟/张(见耗时查询)。后续优化 B(semaphore 只卡提交不卡等待)/C(5→10) 待用户定。

**git 状态**：main == deploy/face-mask-prod == origin/main == origin/deploy/face-mask-prod == 线上 == `4a1ee21`，零分叉。所有改动均经真蓝绿部署 + 冒烟 6/6。

**关联**：[[project_ssp_2026_06_11_harvester_regression]]（归档链路）、[[feedback_ssp_frontend_verify]]、[[project_ssp_v2_working_pipeline_locked]]。
