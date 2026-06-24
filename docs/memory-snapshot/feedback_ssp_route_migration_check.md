---
name: feedback-ssp-route-migration-check
description: 改路由/迁移端点前必须 grep 全部调用方并同步更新，不能留旧路径
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c40c09b4-8a0b-4e34-ade9-40ab9ca6991a
---

改路由或迁移端点时，必须 grep 全部调用方（前端页面、后端服务、脚本、测试）后再 deploy，确保没有任何代码仍指向旧路径。

**Why:** 2026-06-06 把 /api/video/upload/image* 迁到 /api/image/upload/* 时，若前端没同步更新会导致上传功能 404 失效，用户完全无法使用。用户要求这种情况不能再发生。

**How to apply:** 迁移端点前先 `grep -rn "旧路径" frontend/src backend/app scripts/`，列出所有调用方，全部改完确认 0 剩余后再 commit + deploy。
