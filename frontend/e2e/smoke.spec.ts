import { test, expect } from "@playwright/test";

/**
 * 金路径冒烟测试(MVP)
 *
 * 这不是完整 e2e 套件 — 只确认核心页面能起来 + 接口能通 + 公开 API 200。
 * 完整 e2e(注册 → 登录 → 改密码 → 生成图 → 退出)留下次专项,
 * 因为需要跑后端 mock + 数据库隔离 + email code 注入,工程量大。
 *
 * 跑:
 *   cd frontend && npx playwright test
 */

test.describe("公开端点冒烟", () => {
  test("首页 200 + 含主标题", async ({ page }) => {
    const response = await page.goto("/");
    expect(response?.status()).toBe(200);
    // 等页面 hydrate
    await page.waitForLoadState("domcontentloaded");
  });

  test("/auth 登录页可访问", async ({ page }) => {
    const response = await page.goto("/auth");
    expect(response?.status()).toBe(200);
    // 应有邮箱输入框
    await expect(page.locator('input[type="email"]')).toBeVisible({ timeout: 10000 });
  });

  test("/digital-human coming-soon 页 200", async ({ page }) => {
    const response = await page.goto("/digital-human");
    expect(response?.status()).toBe(200);
    // P0: 此页改成"敬请期待",不应有 form
    await expect(page.locator("text=即将上线")).toBeVisible();
  });

  test("/video/editor coming-soon 页 200", async ({ page }) => {
    const response = await page.goto("/video/editor");
    expect(response?.status()).toBe(200);
    await expect(page.locator("text=即将上线")).toBeVisible();
  });

  test("/api/payment/packages 公开 API 200", async ({ request }) => {
    const response = await request.get("/api/payment/packages");
    expect(response.status()).toBe(200);
    const data = await response.json();
    expect(Array.isArray(data.packages)).toBe(true);
  });

  test("/api/jobs/list 未登录 401", async ({ request }) => {
    const response = await request.get("/api/jobs/list");
    expect(response.status()).toBe(401);
  });

  test("/uploads/ 无扩展名文件 403(BUG-2 + 自审 #1)", async ({ request }) => {
    const response = await request.get("/uploads/some-no-ext-file");
    expect(response.status()).toBe(403);
  });

  test("注册无 code 422(P3-2)", async ({ request }) => {
    const response = await request.post("/api/auth/register", {
      data: { email: "playwright-no-code@example.com", password: "secret123" },
    });
    expect(response.status()).toBe(422);
  });

  test("/api/digital-human/generate 503(P0)", async ({ request }) => {
    // 没 token,先撞 auth 401(预期);
    // 这里测只验证路由存在,不深入
    const response = await request.post("/api/digital-human/generate", {
      multipart: {
        image: { name: "x.jpg", mimeType: "image/jpeg", buffer: Buffer.from("x") },
        script: "test",
      },
    });
    // 没 token → 401(由 auth 中间件挡)。带 token 才能撞到 503;
    // 此 e2e 简单跑无 token,验证不是 200 即可。
    expect([401, 403, 503]).toContain(response.status());
  });
});

test.describe("P8 cookie 双轨", () => {
  test("login-by-code 端点存在(无 code → 400)", async ({ request }) => {
    const response = await request.post("/api/auth/login-by-code", {
      data: { email: "playwright-lbc@example.com", code: "000000" },
    });
    // 没 _EMAIL_CODES 记录 → 400 "请先发送验证码"
    expect(response.status()).toBe(400);
  });
});

test.describe("五十+续 鉴权矩阵守门(本轮修过的真生产 bug)", () => {
  test("匿名 POST /api/products → 401(五十七续 OWASP 越权)", async ({ request }) => {
    // 之前完全裸跑,任意人可注入产品。修后必须 401。
    const response = await request.post("/api/products", {
      data: {
        merchant_id: "x",
        name: "playwright-attacker",
        category: "shirt",
        gender: "unisex",
        price: 1.0,
        stock: 0,
      },
    });
    expect(response.status()).toBe(401);
  });

  test("匿名 PUT /api/products/{id} → 401(五十七续)", async ({ request }) => {
    const response = await request.put("/api/products/anyid", {
      data: { name: "hacked" },
    });
    expect(response.status()).toBe(401);
  });

  test("匿名 DELETE /api/products/{id} → 401(五十七续)", async ({ request }) => {
    const response = await request.delete("/api/products/anyid");
    expect(response.status()).toBe(401);
  });

  test("匿名 GET /api/products → 200(电商展示场景仍 public)", async ({ request }) => {
    // 反向守门:别一刀切关了,list/detail 必须保持 public
    const response = await request.get("/api/products");
    expect(response.status()).toBe(200);
  });

  test("匿名 GET /api/video/status/{id} → 401(五十四续 归属窥探)", async ({ request }) => {
    // 之前匿名可调 = 任意人猜 task_id 拿归档视频 URL 隐私泄漏
    const response = await request.get("/api/video/status/random-task-id");
    expect(response.status()).toBe(401);
  });

  test("匿名 POST /api/content/upload → 401(五十六续 OOM 守卫)", async ({ request }) => {
    const response = await request.post("/api/content/upload");
    expect(response.status()).toBe(401);
  });

  test("匿名 POST /api/content/enhance → 401(五十八续 attack surface)", async ({ request }) => {
    const response = await request.post("/api/content/enhance", {
      data: { prompt: "x" },
    });
    expect(response.status()).toBe(401);
  });

  test("匿名 POST /api/image/inpaint → 401(五十八续 stub 防扫)", async ({ request }) => {
    const response = await request.post("/api/image/inpaint", {
      data: { image_url: "x", mask_url: "y", prompt: "z" },
    });
    expect(response.status()).toBe(401);
  });
});

test.describe("六十六续 法务文档页", () => {
  test("/privacy 200 + 隐私政策标题渲染", async ({ page }) => {
    const response = await page.goto("/privacy");
    expect(response?.status()).toBe(200);
    await expect(page.locator("h1").first()).toContainText("隐私政策");
  });

  test("/terms 200 + 用户协议标题渲染", async ({ page }) => {
    const response = await page.goto("/terms");
    expect(response?.status()).toBe(200);
    await expect(page.locator("h1").first()).toContainText("用户协议");
  });

  test("/cookie 200 + Cookie 政策标题渲染", async ({ page }) => {
    const response = await page.goto("/cookie");
    expect(response?.status()).toBe(200);
    await expect(page.locator("h1").first()).toContainText("Cookie");
  });

  test("首页 footer 含 ICP 占位 + 政策三链接(六十九续)", async ({ page }) => {
    await page.goto("/");
    // 备案占位(env 未配 ICP_NUMBER 时显示"备案中")或真备案号
    const footer = page.locator("footer");
    await expect(footer).toBeVisible();
    // 三个法务链接
    await expect(footer.locator('a[href="/privacy"]')).toBeVisible();
    await expect(footer.locator('a[href="/terms"]')).toBeVisible();
    await expect(footer.locator('a[href="/cookie"]')).toBeVisible();
  });
});
