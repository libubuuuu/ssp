# Cloudflare CDN 接入指南

> 后端代码 + nginx snippet 已写 — 等用户做 DNS 切换。

## 为什么要接

- **国内访问改善**:CF 边缘节点比腾讯云轻量服务器(43.134.71.189,新加坡区)更近用户
- **DDoS 防护**:免费版自带 L3/L4 防护 + 简单 L7 规则
- **隐藏源 IP**:用户看到的是 CF 的 IP,真服务器 IP 不暴露
- **HTTPS 卸载**(可选):CF 帮你跑 SSL,源服务器可以是 HTTP(我们仍保留 HTTPS,double encryption)

## 用户要做的(15 分钟)

### 1. Cloudflare 账号 + 加站点

- 注册 https://cloudflare.com
- 仪表盘 → **Add a Site** → 输入 `ailixiao.com`
- 选 **Free 计划**
- CF 会扫描现有 DNS 记录,几乎全自动

### 2. 改 DNS 服务器

CF 会给你 2 个 nameservers,例如:
```
isla.ns.cloudflare.com
neel.ns.cloudflare.com
```

去你的域名注册商(腾讯云 / 阿里云 / GoDaddy)把 nameservers 改成 CF 给的两个。**最久 24 小时全球生效**(国内一般 30 分钟以内)。

### 3. CF DNS 记录确认 ☁️ 橙色云

仪表盘 → DNS 检查这几个:

| Name | Type | Value | Proxy |
|---|---|---|---|
| ailixiao.com | A | 43.134.71.189 | **☁️ Proxied** (橙色) |
| www | CNAME 或 A | ... | **☁️ Proxied** |
| admin | A | 43.134.71.189 | **☁️ Proxied** |
| monitor | A | 43.134.71.189 | **☁️ Proxied** |

**橙色云 = CF 接管**;灰色云 = DNS only(不走 CDN)。所有流量入口必须橙色。

### 4. SSL/TLS 设置

仪表盘 → SSL/TLS → Overview → 选 **Full (strict)** ⚠️ 必须 strict
- Off:都是 HTTP,危险
- Flexible:CF 用 HTTPS 但回源 HTTP — 可能引入 mixed content 漏洞
- Full:CF HTTPS + 回源 HTTPS,但不验证证书 — 还行
- **Full (strict):CF HTTPS + 回源 HTTPS + 验证证书 — 安全**

我们的源已有 Let's Encrypt 证书,Full strict 直接能用。

### 5. 强制 HTTPS

仪表盘 → SSL/TLS → Edge Certificates:
- **Always Use HTTPS**:开
- **Automatic HTTPS Rewrites**:开
- **Minimum TLS Version**:1.2

### 6. 服务器侧 nginx 配置

代码已经写好,执行命令:
```bash
# 把 snippet 安装到 nginx
sudo cp /opt/ssp/deploy/cloudflare-real-ip.conf /etc/nginx/snippets/

# 编辑 /etc/nginx/sites-enabled/default,在每个 server {} 内第一行加:
#     include /etc/nginx/snippets/cloudflare-real-ip.conf;
# 三个 server 都要加(ailixiao.com / admin / monitor)

# 测试 + 重载
sudo nginx -t
sudo nginx -s reload
```

### 7. 验证生效

```bash
# 服务器侧查日志,$remote_addr 应该是用户真实 IP 而非 CF IP
tail -f /var/log/nginx/access.log

# 用户那边 curl 看 IP 头
curl -I https://ailixiao.com -H "User-Agent: test"
# 响应头应有 cf-ray / server: cloudflare
```

后端 `get_client_ip()` 已经把 `CF-Connecting-IP` 列为最高优先级,接入 CF 后 IP 限流 / 审计日志 自动用真实 IP。

## 常见坑

### 坑 1:Real IP 没生效,日志全是 CF IP
- 检查 `/etc/nginx/snippets/cloudflare-real-ip.conf` 是否真存在
- 检查 server {} 里 `include` 是否在 location 之前
- `nginx -T` 看完整配置确认 set_real_ip_from 真生效

### 坑 2:CF IP 段过期(每年至少一次)
官方列表:https://www.cloudflare.com/ips/
新增的段不在我们 snippet 里 → real_ip_header 无效 → 用户 IP 错。
**每年 1 月对一次**,不变就 OK。

### 坑 3:回源 HTTPS 证书失效 → CF 报 525
源 nginx Let's Encrypt 自动续期。如果失效,certbot renew 即可。

### 坑 4:WS(WebSocket)断
确认 CF DNS 记录还是橙色,且 CF 仪表盘 → Network → WebSockets 是 **On**(默认开)。

## 不要做的事

- ❌ DNS 设灰色云 + 期望 CF 防护(灰色 = DNS only,无防护)
- ❌ SSL 模式选 Flexible(回源 HTTP,后端会以为是 HTTP 跳 HTTPS 死循环)
- ❌ 在 CF Page Rules 里设 "Always Online" 缓存所有页面(动态站会缓存登录态,UAT 灾难)
- ❌ 暴露源 IP 到 DNS(留 grey-cloud A 记录会泄漏)
- ❌ 用 CF Pro 的 WAF(免费够用,Pro 19$/月不一定值)

## 接入后好处验证清单

- [ ] curl 响应头有 `cf-ray` 和 `server: cloudflare`
- [ ] 国内访问延迟从 ~200ms 降到 ~50ms
- [ ] 服务器 IP 在 dnschecker.org 上查不到 ailixiao.com
- [ ] nginx access.log 的 $remote_addr 是真用户 IP
- [ ] 后端 audit_log.ip 字段是真用户 IP(刷一次任何审计动作再查)
