import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright 配置
 * 跑前请先把后端启动 + frontend dev server 起好(或指向生产 https://ailixiao.com)
 *
 * 本地跑:
 *   PLAYWRIGHT_BASE_URL=http://localhost:3000 npx playwright test
 * 跑 staging / 生产:
 *   PLAYWRIGHT_BASE_URL=https://ailixiao.com npx playwright test
 */
export default defineConfig({
  testDir: "./e2e",
  // 单测试超时 30s(数字人 / 视频生成不在 e2e 范围内,只测金路径)
  timeout: 30 * 1000,
  expect: { timeout: 5000 },
  // CI 上一次失败重跑 1 次(防 flaky)
  retries: process.env.CI ? 1 : 0,
  // 本地跑允许并行,CI 串行(防数据库竞态)
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    // P8: 让 cookie 真起作用(httpOnly + Secure 在 https 下)
    ignoreHTTPSErrors: true,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
