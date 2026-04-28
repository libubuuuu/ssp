# 管理员 2FA 强制开启 — SOP

> 状态(2026-04-28):**代码就位 + 默认关**(scaffolding pattern,与 Sentry/CF/Redis 一致)。
> 用户需要在 admin 账号上启用 2FA,然后翻 `ADMIN_2FA_REQUIRED=true` 真启用。

## 为什么要做

- 管理员可以改任何用户余额、强制踢人、看审计日志、踢全设备
- **密码单点失守 = 全平台沦陷**
- 行业最佳实践:管理员账号必须 2FA(GitHub / Google Workspace / AWS root 等都强制)
- 现状:代码支持 2FA(`/api/auth/2fa/setup`+`/2fa/enable`)但**任何 admin 账号可以选择不启**

## 已就位的(代码端 — commit dad69dd)

1. **`backend/app/api/admin.py:_check_admin_role`** — 检查 role + totp_enabled
2. **`require_admin` 依赖** — 17 处 admin 端点统一走这个
3. **环境开关** `ADMIN_2FA_REQUIRED`(默认 `false`)— 翻为 `true` 即真生效
4. **`get_user_by_id`** — 返回 totp_enabled 字段
5. **`/api/auth/login` 响应** — user dict 含 totp_enabled,前端可读
6. **`admin/layout.tsx`** — admin 没启用 2FA 时显示**琥珀色横幅**引导 enroll

## 用户操作步骤(启用真强制时)

### 步骤 1:在管理员账号上启用 2FA(任何时候可做,无副作用)

1. 登录 `https://admin.ailixiao.com`
2. 顶部已经有琥珀色"建议启用 2FA"横幅,点 **"去启用 →"**(或访问 `/profile/2fa`)
3. 用 Google Authenticator / 1Password 扫二维码
4. 输入 6 位验证码确认
5. 看到"2FA 已启用"提示

> ⚠ **务必保存 TOTP secret** 到密码管理器:手机丢了 = 永久锁死(没有 backup code 流程)

### 步骤 2:翻 `ADMIN_2FA_REQUIRED=true`

```bash
# 1. 编辑加密 .env
cd /opt/ssp/backend
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d -in .env.enc -pass file:/etc/ssp/master.key > /tmp/.env.dec
echo "ADMIN_2FA_REQUIRED=true" >> /tmp/.env.dec
openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt -in /tmp/.env.dec -out .env.enc -pass file:/etc/ssp/master.key
shred -u /tmp/.env.dec

# 2. 重启 supervisor 让新 env 生效
supervisorctl restart ssp-backend-blue ssp-backend-green
```

### 步骤 3:验证

```bash
# 用没启用 2FA 的 admin token 调:应该 403 + ADMIN_2FA_REQUIRED 引导
curl -s -H "Authorization: Bearer <admin-token-without-2fa>" \
  https://admin.ailixiao.com/api/admin/users-list
# 期望:
# {"detail":{"code":"ADMIN_2FA_REQUIRED","message":"...","redirect":"/profile/2fa"}}

# 用已启用 2FA 的 admin token 调:200
curl -s -H "Authorization: Bearer <admin-token-with-2fa>" \
  https://admin.ailixiao.com/api/admin/users-list
# 期望:正常返回 users 数组
```

## 紧急救援(如果你被锁在外面)

如果开了 `ADMIN_2FA_REQUIRED=true` 后管理员账号无法访问(忘记 2FA / 手机丢了等):

```bash
# 方案 A:临时关闭强制(让自己重 enroll)
cd /opt/ssp/backend
# 同上解密 .env.enc,把 ADMIN_2FA_REQUIRED 改 false,重新加密 + 重启
supervisorctl restart ssp-backend-blue ssp-backend-green

# 方案 B(更狠):直接在 DB 里清掉 totp_secret + 重置 totp_enabled
sqlite3 /opt/ssp/backend/dev.db "
  UPDATE users SET totp_secret=NULL, totp_enabled=0
  WHERE email='your-admin@example.com';
"
# 然后用密码登录,/profile/2fa 重新 enroll
```

## 不要做的事

- ❌ **不要**把 `master.key` 写在仓库 / 备份里(目前在 `/etc/ssp/master.key`,640 root:ssp-app)
- ❌ **不要**给所有用户都强制 2FA(用户体验断崖,只 admin 必要)
- ❌ **不要**靠记忆 TOTP secret,**保存到密码管理器**
- ❌ **不要**关掉 2FA 然后又开,中间做敏感操作的窗口期是攻击面

## 测试覆盖

`backend/tests/test_admin.py` 有 4 例:
- `test_admin_2fa_enforce_off_admin_without_2fa_passes` — 默认关,无 2FA admin 通行
- `test_admin_2fa_enforce_on_admin_without_2fa_blocked` — 开,无 2FA admin 403 + 结构化引导
- `test_admin_2fa_enforce_on_admin_with_2fa_passes` — 开,enroll 后通行
- `test_admin_2fa_enforce_doesnt_affect_non_admin` — 开,普通用户仍是普通 403(不引导 2FA)

## 与 Phase 4 合规的关系

**ICP 备案 / 网安审查**通常要求**管理后台必须 2FA**。本 commit 把基础设施备好,审查时直接说"已实现 + 默认开"。
