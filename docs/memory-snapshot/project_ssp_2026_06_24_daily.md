---
name: project_ssp_2026_06_24_daily
description: 2026-06-24 事件与修复：COS欠费停服全站上传瘫痪/aiview Invalid image瞬时误报重试
metadata: 
  node_type: memory
  type: project
  originSessionId: 7a43c3f4-65db-4859-b684-069499d56911
---

2026-06-24 处理两件生产问题，commit 4b09fc9（在分支 fix/img-no-retry-and-face-mask-contour 上部署，该分支即线上码，未合 main）。

**1. 图片上传全站瘫痪 = 腾讯云 COS 欠费停服（非代码）**
- 现象：`/api/image/upload/cos` 与 `/api/video/clone-v2/upload/image` 全 500。
- 真因：COS `put_object` 返 `CosServiceError code=UnavailableForLegalReasons "your account is arrears"`。账户级冻结，所有走 COS 的上传同挂。
- 处置：用户充值即恢复（充值后 PUT+预签名 GET 实测均正常，不需重新部署）。
- 加固：`cos_upload.upload_to_cos` 捕获 `CosServiceError` → 透传上游 code + 落日志 → 返干净 503，不再裸 500 泄露 traceback 路径。参见 [[feedback_ssp_surface_raw_upstream_error]]。

**2. 「无效图片：格式不支持或分辨率超出范围」= 我们自己把发给 aiview 的负载做大了（不是 aiview 抽风，最初误判已纠正）**
- 文案是 aiview 原始 `Invalid image: format not supported or resolution out of range` 透传，但**真因在我们这边**。
- 排查铁证：把失败/成功的实际入图从 COS 扒下来逐字节比 → 全是 PNG RGB 2048² 干净图，两张失败的字节数(1735731)跟一张成功的**完全相同**。同一张图既成功又失败 → 排除图内容/格式/分辨率。
- 真因：变量在 **aiview 异步拉取我们传的预签名 COS URL** 这一步。`_shrink_ref_for_aiview` 把上传时已存好的 JPEG **又转回 PNG**（照片转 PNG 涨 5 倍=1.66MB）且只缩到 2048。从我们同区服务器拉这张 1.66MB 图都要 0.6~4.8s 抖动，aiview 跨网拉更易慢/超时/截断 → 它把拉取失败误报成图格式问题。网络抖动=间歇性。
- 治本(commit 1371a04)：`_shrink_ref_for_aiview` 改 **JPEG q90 + MAX_SIDE=1536**，1.66MB→~280KB(6x)，大降 aiview 拉取失败率。
- 兜底(commit 4b09fc9)：曾加"命中签名重投一次"，**已于 commit 7822da5 移除**(老板决定：失败就失败、aiview原样透传、不重试)。

**⚠️ 真因更正(当晚复盘，前两次诊断都错)：**
- "图太大→改JPEG"是**误判**。实测失败 job 发给 aiview 的是 37KB/65KB/73KB 小干净 JPEG(800~1131px)，照样报 "Invalid image"，绝无可能"分辨率超范围"。
- 真因：**aiview 是聚合器，把 gpt-image-2 请求轮流分发给多家上游**(成功出图 URL 来自 url-img.xmu.la / ai.soruxgpt.com / img.xmu.la/imageN / image.qlhazycoder.top / 我们COS 等多域)。**部分上游坏了一拿到就回 Invalid image**；分到好的→成功、坏的→失败，故同图时成时败、失败率 30~50%。
- **这是 aiview 侧问题，非我方代码/非用户图/非负载。** 我方无法治本，需账号方找 aiview 摘坏上游，或默认切 seedream(专业版上游可能更稳)。
- JPEG缩图(1371a04)+比例透传(82fa6eb)是好改进保留，但解决不了 aiview 上游。
- 教训：连续误判两次(先甩锅aiview抽风、再怪自己PNG大)，都是证据不足就下结论+宣布"修好"。参见 [[feedback_ssp_no_pattern_match]] [[feedback_ssp_surface_raw_upstream_error]]。

部署：deploy.sh 零停机蓝绿，预部署测试通过 + 冒烟 6/6，切到 green。
