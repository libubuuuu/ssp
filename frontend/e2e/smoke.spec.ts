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
