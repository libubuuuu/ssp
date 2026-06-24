---
name: ssp-2026-06-17
description: "视频复刻V2 'database is locked'两处同源修复(进度写+退款静默吞钱),最新 commit 27ee23e"
metadata: 
  node_type: memory
  type: project
  originSessionId: 57e1f962-3c90-4c66-ac94-1f2425bd8d39
---

2026-06-17,围绕 V2 `database is locked` 同源问题两连修:

1. **628aa6f**(凌晨,上一轮会话)— `_db_update_segment_stage`(纯进度UI记账)撞锁抛异常,被段级 except 当成生成失败→重试3次→整单退款+原始错误甩用户(见用户截图)。修:进度写改尽力而为,DB异常只 log 不上抛。
2. **27ee23e**(本轮)— 同一个锁落在**退款路径**更致命:`_refund_full`/`_refund_partial` 用裸 `except: pass` 吞 INSERT 撞锁,紧接 `if not row` 把"行不存在"误判成"已退过"→直接 return→add_credits 永不执行→用户看到"已退还"但钱没回账户(静默吞钱)。修:INSERT 改 `INSERT OR IGNORE`(SQL层吞重复主键但不吞 OperationalError),early-return 条件改为只 `refunded==1`。撞锁时响亮上抛可人工补救;顶层 `process_v2_job` 的 `status=='processing'` 守卫保证不重复退。补 TestRefundLockSafety 3 例。

**治本仍是 Phase 2 迁 Postgres**(SQLite 单写锁是根)。锁本身当前罕见(日志仅偶发)。
关联 [[feedback_ssp_dict_get_default_zero_anti_pattern]](钱必须爆炸式失败,别静默当0/已退)、[[project_ssp_v2_quality_upscaler]]。
线上 active=blue(27ee23e),green 待命可 rollback。
