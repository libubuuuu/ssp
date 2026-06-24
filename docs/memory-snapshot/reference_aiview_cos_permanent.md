---
name: reference_aiview_cos_permanent
description: aiview 上游用我们自己的 COS，openapi/ 前缀是永久公有读直链，结果无需再下载归档
metadata: 
  node_type: memory
  type: reference
  originSessionId: 1443a638-1a9c-4142-935f-3f454e849558
---

aiview.club（视频复刻 V2 / AI 图片 seedream+gpt-image-2 / seedance 的上游中转）与我们是**合作关系，直接用我们自己的腾讯云 COS**（桶 `ailixiao-uploads-1421174544`，region `ap-guangzhou`）。

自 aiview Open API 文档 **v1.4.0（2026-06-15）** 起，图片/视频生成结果由 aiview 直接持久化到我们桶的 **`openapi/` 前缀，公有读、永久不过期**，返回现成的 COS 直链（`image_urls` / `video_url` / `tail_image_url`）。

**关键含义**：
- 这些结果**本来就落在我们自己的存储里**，再用 `archive_url` 下载到 `/opt/ssp/uploads` 是把同一份东西重存一遍，纯浪费 → 2026-06-16 起 `media_archiver.is_permanent_cos_result()` 命中 openapi/ 链即放行不下载（见 [[project_ssp_2026_06_16_daily]]）。
- **区分**：我们自己 `upload_to_cos` 走 **`uploads/` 前缀 + presigned 会过期签名 URL**（靠 `regenerate_cos_url` 续签）——**绝不能当永久链放行**。只认 `openapi/` 前缀。
- fal.media 仍 30 天过期，照常归档。
- 给用户的链接因此变成 COS 域名（非 ailixiao.com），但因是我们自己的桶，用户接受。公有读=URL 即可公网访问（与旧 /uploads 同等可达）；COS 存储+出流量走我们账单。

aiview 文档另一关键事实（影响 face-mask）：**aiview 拒真人脸，除非是本账号近 30 天内由 Seedance2.0/2.0Fast/Seedream5.0lite 生成的含人脸原始产物**——这是 face-mask 打码 + "专业版生成人脸不打码"入口成立的依据。

延伸优化（未做，待验证）：专业版出图本身已是 openapi/ COS 永久 URL，理论上可直接喂进视频复刻 image_urls，aiview 认出自家近期产物 → 既不打码又不被拒，比下载重传更干净。

文档路径：`/root/API_DOCUMENTATION(6).md`（v1.5.0）。关联 [[feedback_ssp_endpoint_capability_mismatch]]（端点能力必 link 官方文档）。
