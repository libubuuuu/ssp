# OSS 直传(B 方案)启用 SOP — 解服务器出口带宽瓶颈

> 七十二续:代码就位,用户开 COS 账号 + 配 env 即用,服务器无需改动。

## 为什么做这个

当前 A 方案(用户→服务器→fal CDN)受限:
- 服务器(腾讯云轻量)出口带宽 **32 Mbps 套餐硬限**
- 100MB 视频跨境到 fal CDN(美国)需 1-3 分钟
- 出口被打满后,其他用户体验也受影响

B 方案后:
- 用户上传走自己上行带宽(50-200 Mbps)+ COS 国内 CDN(GB/s 入口)
- 100MB 视频上传 10-30 秒(预期 **3-10 倍**提升)
- 服务器只签 STS 凭证(无文件流量),纯 API 调用

## 用户操作清单

### Step 1:开通腾讯云 COS

1. 登录 https://console.cloud.tencent.com/cos
2. 开通 COS 服务(免费,按量计费)
3. 创建 bucket:
   - Bucket 名:如 `ssp-uploads-1300000000`(末尾 appid 数字必须有)
   - 区域:**选跟服务器同地域**(广州 ap-guangzhou),减少跨地域费用
   - 访问权限:**私有读写**(STS 临时凭证授权)
4. 配 CORS 规则(让前端浏览器能上传):
   - 来源 Origin:`https://ailixiao.com`
   - 允许方法:`PUT, POST, GET, HEAD, DELETE`
   - 允许 Headers:`*`
   - 暴露 Headers:`ETag, x-cos-request-id`

### Step 2:子账号 + STS 权限

**不要用主账号 SecretId/SecretKey**,新建子账号专门签发 STS:

1. 控制台 → CAM → 用户列表 → 新建用户
2. 权限:`QcloudCamSubaccountFederationTokenPolicy`(只 STS 签发权限)
3. 拿到子账号 `SecretId` / `SecretKey`(只显示一次,保管好)

### Step 3:env 配置

`/opt/ssp/backend/.env`:

```env
STORAGE_DIRECT_UPLOAD_ENABLED=true
STORAGE_PROVIDER=tencent_cos
STORAGE_BUCKET=ssp-uploads-1300000000   # 替换为你的 bucket 名(含 appid)
STORAGE_REGION=ap-guangzhou             # 替换为你的区域
STORAGE_SECRET_ID=AKID-xxxxxxxxxxxxx    # 子账号 SecretId
STORAGE_SECRET_KEY=xxxxxxxxxxxxx        # 子账号 SecretKey
STORAGE_DURATION_SECONDS=900            # 临时凭证 15 分钟有效(默认值,可调)
STORAGE_BUCKET_PREFIX=uploads/          # 默认值,所有用户上传都在这个前缀下
```

### Step 4:重启 backend

```bash
bash /root/deploy.sh
```

测试 STS 端点:

```bash
# 拿到登录 token 后
curl -X POST https://ailixiao.com/api/storage/sts \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"filename":"test.mp4"}'

# 期望返回:
# {
#   "credentials": {"tmpSecretId":"...","tmpSecretKey":"...","sessionToken":"..."},
#   "expiredTime": 1730000000,
#   "bucket": "ssp-uploads-1300000000",
#   "region": "ap-guangzhou",
#   "object_key": "uploads/<user_id>/1730000000_test.mp4",
#   "public_url": "https://ssp-uploads-1300000000.cos.ap-guangzhou.myqcloud.com/uploads/..."
# }
```

### Step 5:前端集成(留下次,等账号到位)

我没在本续做 frontend 改造(等 COS 账号到位再做,scope 30 分钟)。
集成参考代码:

```tsx
// 1. 拿 STS 凭证
const r = await fetch("/api/storage/sts", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ filename: file.name }),
});
const { credentials, bucket, region, object_key, public_url } = await r.json();

// 2. 用 cos-js-sdk-v5 上传(npm install cos-js-sdk-v5)
import COS from "cos-js-sdk-v5";
const cos = new COS({
  getAuthorization(_options, callback) {
    callback({
      TmpSecretId: credentials.tmpSecretId,
      TmpSecretKey: credentials.tmpSecretKey,
      SecurityToken: credentials.sessionToken,
      ExpiredTime: 0,  // 0 = 用 SDK 缓存,签的时候带的有过期
    });
  },
});

await new Promise((resolve, reject) => {
  cos.uploadFile({
    Bucket: bucket,
    Region: region,
    Key: object_key,
    Body: file,
    onProgress(p) { setProgress(p.percent * 100); },
  }, (err, data) => err ? reject(err) : resolve(data));
});

// 3. 上传完后 public_url 可直接喂 fal API
await fetch("/api/video/image-to-video", {
  method: "POST",
  body: JSON.stringify({ image_url: public_url, prompt: "..." }),
});
```

### Step 6:逐步迁移现有上传端点

A 方案的端点(`/api/studio/upload` / `/api/ad-video/upload/image` 等)**保持运行**作为兜底,新前端走 B 方案。逐步切。

## 安全保障

- **STS 凭证 15 分钟过期**:即使被截获,影响窗口短
- **路径隔离**:每个用户只能写 `uploads/<user_id>/...`,凭证 policy 限定 resource
- **文件名清洗**:服务器端对 `..` `/` 等危险字符替换为 `_`
- **子账号最小权限**:只 GetFederationToken 权限,不能用主账号扔到日志
- **CORS 限制**:bucket CORS 只允许 ailixiao.com origin

## 已知限制

- `fal_client.upload_file_async` 不再用(B 方案后服务器无文件)
- fal API endpoint(如 `/api/video/image-to-video`)接受 OSS public_url 输入(fal 自己拉)
- COS 在国内,fal 服务器在美国 → fal 跨境拉文件可能慢。但这是 fal 拉而非服务器推,**不占用户的体验时间**(用户已在 OSS 上传完)
- 极速场景可考虑切 fal storage 入口(fal_client 仍可用,只是不再走服务器中转)

## 测试

```bash
cd /opt/ssp/backend
venv/bin/pytest tests/test_storage_sts.py -v
```

6 个测试覆盖:未启用 503 / 鉴权 / 成功签发 / 文件名清洗 / bucket 格式校验 / 字段必填。

## 启用前 Checklist

- [ ] COS bucket 创建好(含 appid 后缀)
- [ ] CORS 规则配好(ailixiao.com origin + 必要 methods)
- [ ] 子账号 SecretId/SecretKey 拿到(STS 权限)
- [ ] backend `.env` 配好 7 个 STORAGE_* 字段
- [ ] `bash /root/deploy.sh` 重启 backend
- [ ] curl 测 `/api/storage/sts` 返 200
- [ ] 前端集成(独立专项,30 分钟工作量)
- [ ] 关闭旧 A 方案端点(可选,先并存观察)
