---
name: ssp-pricing-locked-2026-05-13
description: 2026-05-13 老板锁定定价 + 实施完毕:50 积分 = 1 元、视频 50/秒、图片 20/张、文案 5/次
metadata: 
  node_type: memory
  type: project
  originSessionId: 7c096b06-9598-4023-b283-d95e15842516
---

2026-05-13 老板最终定价已全部落地(commit f7847fb 推 origin/feat/auth-email-code-ui)。
取代 [[ssp-todo-2026-05-13-5-14]] TODO,该 memory 已过期可删。

# 锁定参数

## 汇率
- **50 积分 = 1 元**(注意:不是 100/¥,2026-05-13 晚改的)

## 任务单价

| 功能 | 后端入口 | 单价 |
|---|---|---|
| 图片(GPT-image-2 / 分镜图) | image/* + ad_video/preview + video/general/storyboard + video/frame-extract/replace | **20 积分/张(¥0.4)** |
| 视频(图生/分镜/图片/视频复刻) | video/* generate 端点 | **50 积分/秒(¥1/s)** |
| AI 文案(analyze / scene_regen) | */analyze + ad_video/scene_regen | **5 积分/次** |
| 图生视频 i2v | /api/video/image-to-video 手动 deduct | duration(3/5/10/15)× 50 |
| 视频复刻 V2 | calc_total_credits(seg, plan) | Σ ai 段 duration × 50,下限 50 |

## 充值套餐(payment.py)

| SKU | 价格 | 积分 | 等价折扣 |
|---|---|---|---|
| credit small  | ¥10   | 500    | 无 |
| credit medium | ¥50   | 2500   | 无 |
| credit large  | ¥100  | 5250   | 9.5 折 |
| pkg monthly   | ¥199  | 12500  | 8 折 |
| pkg quarterly | ¥499  | 35000  | 7 折 |
| pkg yearly    | ¥1699 | 140000 | 6 折 |

# 改动文件总览(commit f7847fb)

后端:
- `app/services/billing.py` PRICING map
- `app/services/video_clone_v2_pricing.py`(CREDITS_PER_SEC=50 + CREDITS_PER_YUAN=50;calc_total_credits 改签名 `(segments, plan=None)`,自带 duration 也可不传 plan)
- `app/services/video_clone_v2_processor.py`(_refund_partial 按段 plan_by_idx[idx].duration × 50)
- `app/api/video_clone_v2.py` estimate(加 video_duration_sec optional + plan_segments_v2)、create 传 plan_back
- `app/api/replicate.py` L268 + L344
- `app/api/video_general.py` L250 + L367 + L440(sum duration × 50)
- `app/api/video_frame_extract.py` L135 + L260 + L340
- `app/api/video.py` /image-to-video 删 @require_credits 改手动动态扣
- `app/api/jobs.py` /submit video_i2v 动态 duration × 50
- `app/api/payment.py` PACKAGES + CREDIT_PACKS

前端:
- `frontend/src/app/video-clone-v2/page.tsx` estimate 加 video_duration_sec
- `frontend/src/lib/i18n/{zh,en}.ts` /pricing 文案

测试(配套):
- `tests/conftest.py` register fixture 默认充 1000(原默认 INITIAL_CREDITS=10 不够任何任务)
- 8 个测试文件改新定价:billing/jobs/decorators/refund_tracker/video_studio/ad_video/admin/v2_pricing/v2_ultimate
- 519 测试全过

# 部署状态(2026-05-13 收工)

- /root/ssp git: f7847fb 已推 origin/feat/auth-email-code-ui ✓
- /opt/ssp: rsync + chown 完毕(测试用)
- **未 deploy 蓝绿**:用户尚未跑 bash /root/deploy.sh
- 下次起 deploy 即可上线(参考 [[feedback_ssp_deploy_via_script]])

# 公开前需补

- 新用户 INITIAL_CREDITS=10 不够任何任务(image=20)。要么 register 后赠 100(对应 zh.ts:95 文案"赠送 100 积分"),要么把 i18n 文案改成"赠送 10 积分"。当前文案对不上,小坑。

# 相关 memory

- [[project_ssp_v2_working_pipeline]] — V2 链路不要回退
- [[project_ssp_v2_always_on]] — V2 永久开
- [[feedback_ssp_deploy_via_script]] — 走 deploy.sh 不要手动 rsync
- [[feedback_ssp_deploy_rsync_first]] — rsync + chown 后再跑 deploy
