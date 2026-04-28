# 微信支付 V3 启用 SOP(用户主导)

> 六十八续:代码已就位(scaffold),用户拿到商户号 + 配 cert 后改 env 即用。

## 现状

- `app/services/wechat_pay.py` — V3 客户端封装(签名 / Native 下单 / AESGCM 解密)
- `app/api/wechat_pay.py` — 3 个端点 `/api/wechat-pay/{create,query,notify}`
- 默认 **WECHAT_PAY_ENABLED=false**,启用前 503,**不影响现有手动入账流程**
- 测试 +10(373 → 383)覆盖鉴权 / 状态 / 解密

## 用户操作清单

### Step 1:申请商户号(微信支付商户后台)

- 注册 https://pay.weixin.qq.com,提交营业执照、对公账户等(1-2 周审核)
- 拿到 **商户号 mch_id**(10 位数字)
- 关联 **AppID**(公众号 / 小程序 / 开放平台,看用户场景。本 stub 用 Native 扫码,关联开放平台 AppID 即可)

### Step 2:商户后台配置

1. **API 安全 → APIv3 密钥**:设置 32 字节随机字符串
2. **API 证书**:申请 + 下载,得到:
   - 商户私钥 PEM(`apiclient_key.pem`)
   - 商户证书 PEM(用于序列号 cert_serial)
3. **支付目录**:加 `https://ailixiao.com/pricing`(或前端发起支付的页面)
4. **回调 URL**:配 `https://ailixiao.com/api/wechat-pay/notify`(必须 HTTPS,公网可达)

### Step 3:服务器部署

```bash
# 在生产服务器上传商户私钥(权限 600,只读 ssp-app)
sudo -u ssp-app mkdir -p /etc/ssp/wechat-pay
sudo cp apiclient_key.pem /etc/ssp/wechat-pay/
sudo chmod 600 /etc/ssp/wechat-pay/apiclient_key.pem
sudo chown ssp-app:ssp-app /etc/ssp/wechat-pay/apiclient_key.pem
```

### Step 4:env 配置

编辑 `/opt/ssp/backend/.env`(用 manage_env 工具加密保护):

```env
WECHAT_PAY_ENABLED=true
WECHAT_PAY_MCH_ID=1900000000
WECHAT_PAY_APP_ID=wxXXXXXXXXXXXXX
WECHAT_PAY_API_V3_KEY=YOUR_32_BYTE_API_V3_KEY
WECHAT_PAY_CERT_SERIAL=YOUR_CERT_SERIAL_NUMBER
WECHAT_PAY_PRIVATE_KEY_PATH=/etc/ssp/wechat-pay/apiclient_key.pem
WECHAT_PAY_NOTIFY_URL=https://ailixiao.com/api/wechat-pay/notify
```

### Step 5:补完平台公钥验签(⚠ 必须)

**当前 stub 的回调端点 `/api/wechat-pay/notify` 没真做验签**(代码里 TODO 注释)。
启用前必须实现:

1. 拉平台证书:`GET /v3/certificates`(本身需要商户证书签名)
2. 缓存到内存或 disk,定期刷新(每 12 小时拉一次)
3. 回调时按 `Wechatpay-Serial` 头选择对应平台公钥验签

参考实现见 [wechatpay-python](https://github.com/wechatpay-apiv3/wechatpay-python) 库 — 可直接装这个库替代当前 stub。

**未做平台公钥验签 = 任何人构造合法 AESGCM 密文都能伪造回调入账(虽然 APIv3 密钥保护下密文构造很难,但仍是单层防御)**。

### Step 6:重启 backend + 蓝绿验证

```bash
bash /root/deploy.sh
# 测一笔小额(¥0.01)真支付,看 audit_log 是否写入
```

### Step 7:前端集成

`/pricing` 页面起 Native 支付:

```tsx
const r = await fetch(`/api/payment/orders/create`, { /* ... */ });
const { order_id } = await r.json();
const wp = await fetch(`/api/wechat-pay/create/${order_id}`, { method: "POST" });
const { code_url } = await wp.json();
// 用 qrcode 库渲染 code_url 成二维码图,用户扫码支付
// 同时启 setInterval 每 3s 调 /api/wechat-pay/query/{order_id} 看 trade_state
```

## 启用前 Checklist

- [ ] 商户号审核通过
- [ ] APIv3 密钥设置(32 字节)
- [ ] 商户私钥 PEM 上传到 `/etc/ssp/wechat-pay/apiclient_key.pem`(权限 600)
- [ ] 平台公钥验签实现(stub 未做,启用前必须补)
- [ ] env 6 个 WECHAT_PAY_* 字段全填
- [ ] 回调 URL 在商户后台配置 + 公网可达 + HTTPS
- [ ] 前端 /pricing 接入 Native 二维码渲染
- [ ] 测一笔 ¥0.01 真支付端到端通过
- [ ] 24h 观察 audit_log,确认无误

## 启用后保留的兜底

手动入账流程(`/api/payment/orders/{id}/confirm`)**继续保留**:
- 微信回调延迟 / 失败时管理员手动确认
- 退款 / 异议订单管理员手动处理(本 stub 未实现退款)

## 留给下一轮做

本 stub 没做的(scope 控制):

- 退款 API(`/v3/refund/domestic/refunds`)
- 平台公钥拉取 + 缓存(必须做才能上线)
- 对账下载 + 自动核账
- JSAPI / 小程序支付(只做了 Native 扫码)
- 退款回调通知

## 联调环境

微信提供商户号沙箱(test mode),在商户后台开启"生产/沙箱"切换。建议:
1. 先在沙箱跑通完整流程
2. 切生产前再做 ¥0.01 真支付测一遍
