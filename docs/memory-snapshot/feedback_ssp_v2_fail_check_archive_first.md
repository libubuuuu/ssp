---
name: ssp-v2-archive
description: "视频复刻V2\"生成失败+全额退款\"常见真因是生成成功后下载归档抖动,排查先看 archive failed 日志"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 371dbaba-f143-4d52-9d22-7f8e9451be60
---

视频复刻 V2 报"生成失败 + 全额退款",**第一怀疑不是模型/prompt/NSFW,而是生成成功之后的下载归档环节**。

**这是潜伏问题,非某天首发**:archive 下载失败从 2026-06-09 起几乎每天 1 笔(06-09×2/06-10/06-11/06-14),不是 06-14 突然出现。06-11 job `00c9f405` 错误是铁证:"peer closed connection... received 4259840 expected 5581627"(fal CDN 中途掐断下载)。06-14 job `9473d90b`:fal 模型已出片、已扣 770 积分,但 `_download_to_local` 下载 fal.media 成片时 `httpx.ReadTimeout`,零重试 → 整单判失败全额退款,钱退了却没拿到片。

**为何"感觉"最近才有**:(1) 06-10 commit 311472c 才加"archive 失败补退全额积分",之前 archive 失败可能不退款(06-09 第一笔退 0),加后才以"失败+退款"清晰暴露;(2) 小样本放大(06-14 只跑 2 单中 1 单=50%,而 06-11 跑 29 单仅败 2)。排查时务必查 DB 历史 error_step 分布,别只看当天已轮转日志就下"今天唯一/首发"结论(我已踩,守 [[feedback_ssp_self_audit_same_standard]])。

**两处下载点都曾零重试(已修)**:`video_clone_v2_archive.py:_download_to_local`(成片归档)+ `video_clone_v2_processor.py:_download_to_local`(AI 段下载,现已复用前者)。现网络/超时类错误指数退避重试 3 次,timeout 分类 connect30/read180/write60/pool30,fix commit `1acb0e5`。

**Why:** fal CDN 大视频下载本就慢且瞬时抖,搬运环节失败让上游成功+扣费的整单白跑,是慢性体验雷(关联 [[project_ssp_v2_walltime_ux]])。
**How to apply:** 排查 V2 失败先 `grep "archive failed" /var/log/ssp-backend-blue.out.log`(或当前活跃槽);出现下载/上传类异常说明是搬运而非生成问题,别甩锅模型(守 [[feedback_ssp_no_pattern_match]])。
