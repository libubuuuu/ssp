---
name: reference-aiview-timing-fields
description: aiview 图片慢的真因分解：中转面板耗时只统计到响应头，aiview读响应体仅14-18KB/s(2MB要2分钟)；query接口v1.4.0新增timing字段已接入日志
metadata: 
  node_type: memory
  type: reference
  originSessionId: 734a2e74-6025-4262-ae76-40c807d9e22a
---

2026-06-12 凌晨实测钉死图片 3-7 分钟的完整账目（测试单 req_2c86f0be，T1=423s）：

- `timing.queueWaitMs` aiview 本地排队：**1s**（不是排队问题）
- `timing.responseHeaderMs` 等中转响应头：**311s** ← 中转面板显示的"耗时"只统计这段
- `timing.responseBodyReadMs` aiview 读中转响应体：**108s** ← 隐藏大头，1517KB/108s≈14KB/s；另两单 17-18KB/s
- `timing.postUpstreamMs` 解析落库：18ms；我们端（提交1s+轮询≤3s）共 ~4s

**结论**：拥堵全在 aiview↔中转链路（上游慢 + 响应体吞吐拨号级），我们端到端开销 ≤5s。
中转面板/aiview面板显示的耗时都不含响应体传输，永远比真实墙钟少 1-2 分钟——对质时要用 `timing.totalMs`。

aiview 文档 v1.4.0（`/root/API_DOCUMENTATION(4).md`）给 query 接口加了 timing 分解字段。
commit bb3908f 已把 queue/header/body/post/total 写进 `[AIVIEW-IMG] query completed` 日志，慢单 grep 即归因，无需再手工 probe。

probe 方法（只读零成本）：用 supervisor 同款 openssl 解密 .env.enc 导出 env，调 `svc._send("GET", f"/open/v1/image/query/{rid}", "")`。

关联：[[project-ssp-2026-06-11-harvester-regression]]（同事件前半段：我们侧三个放大器已修）
