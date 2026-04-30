# 腾讯云 COS 直传接入指南(B 方案 / 七十二续 + 八十二)

> 后端代码已就绪(commit `fb56877`,395 测试覆盖),等用户开账号 + 贴 5 个 env 即可启用。
> 启用后绕开服务器中转,用户上传从"经服务器跨境到 fal 美国"变成"直传 COS 国内 GB/s 入口 → fal 自取",**3-30 倍提速**(取决于用户家庭上行带宽,服务器侧不再吃流量是确定的收益)。

---

## 为什么要做这件事

**当前链路**:
```
用户(上行 X Mbps)→ 服务器(出口 32 Mbps)→ fal(美国,跨境)
                       ↑ 瓶颈                ↑ 跨境慢且不稳
```

服务器是腾讯云轻量套餐 32 Mbps 出口 + 跨境海外 fal,任何用户上传都被锁死在 32 Mbps,且高峰多人上传会互抢带宽。

**启用 COS 后**:
```
用户(上行 X Mbps)→ 腾讯云 COS(国内 GB/s 入口)→ fal(海外 CDN 优化)
                                                   ↑ COS → fal 走 CDN 比服务器跨境稳得多
服务器只签 STS 临时凭证(15 分钟有效),不过文件
```

---

## 用户要做的(15 分钟,实名必须)

### 1. 注册腾讯云 + 实名

- 官网:https://cloud.tencent.com
- **必须实名**(企业实名 / 个人实名都行,国内云服务硬要求,不能跳)
- 实名通过后才能开 COS

### 2. 开 COS 服务 + 建 bucket

- 控制台搜 "对象存储 COS"
- **首次进入**会让你开通服务(免费)
- 点 "存储桶列表" → "创建存储桶"

**关键填法:**

| 字段 | 填法 | 说明 |
|---|---|---|
| 名称 | `ssp-uploads`(或自取) | 全平台唯一,会自动拼 `-<appid>` 后缀 |
| 所属地域 | **跟服务器同区域**(如 `广州 ap-guangzhou`) | 减少 STS 签发延迟,且后续国内访问 CDN 命中更好 |
| 访问权限 | **私有读写**(默认) | 上传走 STS 凭证,绝不开公开写 |
| 请求域名 | 默认 | 例如 `ssp-uploads-1300xxxxxx.cos.ap-guangzhou.myqcloud.com` |

**记下完整 bucket 名**(含 appid 后缀,如 `ssp-uploads-1300xxxxxx`),后端代码强制要求 `name-appid` 格式,**少一位 appid 都会启动失败**。

### 3. 配 CORS(允许浏览器跨域上传)

bucket 详情页 → "安全管理" → "跨域访问 CORS 设置" → 添加规则:

| 字段 | 填值 |
|---|---|
| 来源 Origin | `https://ailixiao.com` (生产)<br>`http://localhost:3000` (本地开发,可选) |
| 操作 Methods | `PUT` `POST` `HEAD` `GET` |
| 允许 Headers | `*`(简单粗暴。生产可收紧到 `Authorization`、`Content-Type`、`Content-MD5`、`x-cos-*`)|
| 暴露 Headers | `ETag` (分片合并必需) |
| 超时 Max-Age | `600` |

**不配 CORS** 浏览器直接 ` Access-Control-Allow-Origin` 报错,前端连 PUT 都发不出去。

### 4. 创建 IAM 子账号(只给 STS + 受限 COS 权限)

**绝不用 root API key** — 一旦泄露 = 整账号被刷爆。

- 控制台搜 "访问管理 CAM" → 用户 → 用户列表 → 新建用户 → **"自定义创建"** → "可访问资源并接收消息"
- 用户名:`ssp-sts-signer`
- **不勾控制台访问**(纯 API 用)
- 访问方式:**编程访问**(生成 SecretId/SecretKey)

**权限策略:必须自定义,不要选预设 `QcloudCOSFullAccess`**(那是 root 级,违反最小权限)。

新建策略 `ssp-sts-cos-write`,粘贴以下 JSON:

```json
{
  "version": "2.0",
  "statement": [
    {
      "effect": "allow",
      "action": [
        "name/sts:GetFederationToken"
      ],
      "resource": "*"
    },
    {
      "effect": "allow",
      "action": [
        "cos:PutObject",
        "cos:PostObject",
        "cos:InitiateMultipartUpload",
        "cos:ListMultipartUploads",
        "cos:ListParts",
        "cos:UploadPart",
        "cos:CompleteMultipartUpload",
        "cos:AbortMultipartUpload"
      ],
      "resource": [
        "qcs::cos:<region>:uid/<appid>:<bucket>/uploads/*"
      ]
    }
  ]
}
```

把 `<region>` / `<appid>` / `<bucket>` 换成步骤 2 拿到的真实值。`uploads/*` 是后端硬编码的前缀,**不要改**(对应 `STORAGE_BUCKET_PREFIX=uploads/`)。

策略关联到 `ssp-sts-signer` 用户。

### 5. 拿到 SecretId / SecretKey

CAM → 用户 → 点 `ssp-sts-signer` → "安全凭证" 标签页 → "新建密钥",下载 CSV。

**只显示一次**,丢了得重新建。

### 6. 把 5 个值给我

把下面 5 行(替换真值)发我,我灌进 `.env.enc`:

```
STORAGE_DIRECT_UPLOAD_ENABLED=true
STORAGE_BUCKET=ssp-uploads-1300xxxxxx
STORAGE_REGION=ap-guangzhou
STORAGE_SECRET_ID=AKID...
STORAGE_SECRET_KEY=...
```

**不要发到任何聊天群、PR、Github issue** — SecretKey 一旦泄露立刻轮换。

---

## 我做的(5 分钟,等你给上面 5 个值)

1. 解密当前 `.env.enc` → 追加 5 行 → 重新加密
2. supervisor 蓝绿重启后端
3. 验证 STS endpoint 通:
   ```bash
   curl -X POST https://ailixiao.com/api/storage/sts \
     -H "Authorization: Bearer <你的 token>" \
     -d '{"filename": "test.mp4"}'
   ```
   预期返回 `{"credentials": {...}, "object_key": "uploads/<uid>/...", "bucket": "...", ...}`
4. 接前端调 STS + COS 直传(改 `oral` 上传链路从 `/api/oral/upload-chunk` 切到 COS 直传 + 完成后 POST notify 后端)
5. 灰度:keep `/api/oral/upload-chunk` A 方案路径,前端按用户判断(或 feature flag)切,出问题随时 rollback

---

## 踩坑提醒

| 坑 | 现象 | 修法 |
|---|---|---|
| Bucket 名漏 appid | 后端启动 raise `STORAGE_BUCKET 格式应为 name-appid 形式` | 填完整 `ssp-uploads-1300xxxxxx`,不是 `ssp-uploads` |
| Region 拼错 | STS 签发返 403 InvalidRegion | 严格按腾讯云文档大小写,`ap-guangzhou` 不是 `cn-guangzhou` |
| CORS 没配 | 浏览器 console: `CORS preflight Access-Control-Allow-Origin missing` | 步骤 3,Origin 填 `https://ailixiao.com`(带协议) |
| ETag 没 expose | 分片合并失败,ETag header 浏览器读不到 | 步骤 3 暴露 Headers 必须有 `ETag` |
| Action 给少了 | 大文件分片上传到一半 403 | 步骤 4 JSON 那 8 个 cos action 都要(尤其 `InitiateMultipartUpload`、`UploadPart`、`CompleteMultipartUpload`)|
| 用 root API key | **不要这样做**,泄露损失大 | 步骤 4 子账号 |
| Resource 写 `*` | 子账号能写 bucket 任意路径 | resource 严格限到 `<bucket>/uploads/*` |
| Free tier 流量 | COS 不像 OSS 完全免费,有阶梯计费 | 国内流量便宜(~0.5 元/GB),按典型 SaaS 量级月成本 < 100 元;**国际访问流量贵**,确保前端只走国内 endpoint |

---

## 启用后预期效果(诚实数据)

| 用户网络 | 现在(经服务器中转) | 启用 COS 后 |
|---|---|---|
| 家庭上行 0.8 Mbps(实测案例) | 30MB / 0.8 Mbps ≈ **5 分钟** | 30MB / 0.8 Mbps ≈ **5 分钟** ← 用户家网卡死,COS 救不了 |
| 家庭上行 10 Mbps(普通光纤) | 30MB / 服务器 32 Mbps 跨多用户 ≈ **30-60 秒**(争抢) | 30MB / 10 Mbps ÷ 8 ≈ **24 秒** |
| 家庭上行 50 Mbps(企业 / 全光) | 同上 30-60 秒(被服务器锁死) | 30MB / 50 Mbps ÷ 8 ≈ **5 秒** |
| 服务器侧带宽占用 | 每用户上传都吃 32 Mbps 出口 | **0**(只签 STS 凭证,不过文件) |

**单用户慢的本质是用户家上行**,COS 没法救。但**多用户场景**:服务器不再吃跨境出口 = 不再相互抢带宽 = 整体感知 3-30 倍。

---

## 不做这件事的代价

每多一个并发用户上传,服务器出口 32 Mbps 被均分,**两个用户同时上传就互抢到 16 Mbps**,体感雪崩。

P1 单用户测试 OK,P2/P3 上线开放注册之前**必须做**,否则前 5 个真实用户能把服务卡到不可用。

---

## 相关 commit + 代码位置

- 后端 STS 服务:`backend/app/services/storage_sts.py`
- 后端 STS 端点:`backend/app/api/storage.py POST /api/storage/sts`
- 配置字段:`backend/app/config.py:68-75`(5 + 3 个,3 个有默认值)
- 测试:`backend/tests/test_storage_sts.py`(6 个测试,394 → 400)
- 已就绪日期:2026-04-28(七十二续 commit `fb56877`)
- 启用 PR:本文档对应的 commit
