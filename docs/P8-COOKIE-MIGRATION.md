# P8 httpOnly Cookie 迁移路线图

> 后端阶段 1(commit `d59aab3`)+ 前端阶段 2(commit `3c09405`)已完成。
> 本文档跟踪阶段 3 的清理时间表 + 留意事项。

## 当前状态(2026-04-27)

| 路径 | cookie 路径 | header 路径 |
|---|---|---|
| 后端 `get_current_user` | ✅ 优先读 | ✅ fallback 兼容 |
| 后端 `/login` `/register` `/refresh` | ✅ Set-Cookie | ✅ body 仍返 token |
| 后端 `/logout` | ✅ 清 cookie | — |
| 前端 `AuthFetchInterceptor` | ✅ credentials:include | ✅ 19 处仍传 Authorization |
| 前端 localStorage | — | ✅ 仍读写 token / refresh_token |
| WebSocket(`/api/tasks/ws/{id}?token=`)| — | ✅ 浏览器规范,保留 |

**双轨期(transition)**: 当前。新登录用户主要走 cookie,老登录态(localStorage 残留 token)继续走 header。两路都通。

## 阶段 3 触发条件

满足任一即可启动:
1. **30 天等待期到**:从 commit `3c09405` 起算约 5/27,大部分老 token 已自然过期(refresh 30d)
2. **生产监控显示** Authorization header 调用量 < 5%(看 nginx access log 不带 Cookie 头但带 Authorization 的比例)
3. **业务方主动催**:有合规要求要拿到"localStorage 0 token"证书

## 阶段 3 清理清单(预估 1-2 工作日)

### 后端
- [ ] `get_current_user` 移除 header fallback(只接 cookie)
- [ ] `RefreshRequest` 把 `refresh_token` 字段移除(只走 cookie)
- [ ] `register` `login` `refresh` 响应 body 移除 `token` `access_token` `refresh_token` 字段
- [ ] 测试更新:`test_p8_cookies.py::test_me_reads_from_header_when_no_cookie` 改成预期 401

### 前端
- [ ] `AuthFetchInterceptor`:`tryRefresh` 不再读 localStorage(只走 cookie)
- [ ] `AuthFetchInterceptor`:重试不再 set Authorization
- [ ] `AuthFetchInterceptor`:`redirectToLogin` 不再清 localStorage(没东西可清)
- [ ] `AuthFetchInterceptor`:主动续期不再读 localStorage,改读 cookie expiry header(或 me 端点带 expire)
- [ ] auth/page.tsx::goAfterLogin:不再写 localStorage
- [ ] 19 处硬编码 `Authorization: Bearer ${token}` 全部清掉(grep `Bearer ` 找)
- [ ] 19 处 `localStorage.getItem("token")` 全部清掉
- [ ] auth?expired=1 的"会话已过期"文案保留

### 配置
- [ ] 生产 `.env.enc`:`COOKIE_DOMAIN=.ailixiao.com`(让两个子域共享)
- [ ] 生产 `.env.enc`:`COOKIE_SECURE=True`(默认就是,确认即可)

### 验证
- [ ] 老登录态(浏览器存了 localStorage token)是否优雅"被升级":
  - 阶段 3 部署后,localStorage 里的 token 不会自动转 cookie,**用户被踢一次重登**
  - **必须提前公告**:升级前 1 周邮件 / banner 通知"将清空登录态,需重新登录"
- [ ] 跨子域:登录 ailixiao.com 后访问 admin.ailixiao.com 不需重登(COOKIE_DOMAIN 配对)
- [ ] WebSocket 仍工作

## 留意:迁移失败回滚预案

如果阶段 3 部署后发现:
- 某些场景 cookie 不生效(比如自定义 client / mobile webview)
- 用户大量被踢

**立即回滚**:`bash /root/rollback.sh` 切回 standby。standby 仍是双轨版,header 路径还在。

## 决策日志

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-04-27 | 阶段 1 后端双轨 | 最小风险:server 同时认两路 |
| 2026-04-27 | 阶段 2 前端中心 patch | 改 1 个文件影响 71 处,ROI 极高 |
| 2026-04-27 | 阶段 3 留 30 天 | 让大部分老 refresh(30d TTL)自然过期 |
| TBD | 阶段 3 真切换 | 等用户授权 + 提前公告 |
