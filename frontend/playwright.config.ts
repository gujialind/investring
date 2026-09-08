import { defineConfig, devices } from '@playwright/test';

/**
 * InvestRing 前端 E2E 测试配置
 *
 * 运行方式：
 *   npx playwright test              # 运行所有测试
 *   npx playwright test --project=chromium  # 仅桌面端
 *   npx playwright test --project=mobile    # 仅移动端
 *   npx playwright test --ui          # 交互式 UI 模式
 *   npx playwright test --debug       # 调试模式
 */
// webServer.port 从 use.baseURL 派生而非另设环境变量：端口与被测地址同源，
// 不可能各说各话。需要隔离栈的调用方传 BASE_URL 指向自己的端口后，
// Playwright 就不会再在 :3000 上另起一份本调用方控制不了的服务。
// 不设 BASE_URL 时（CI 只设 CI=true）与改动前逐字等价：:3000。
const baseURL = process.env.BASE_URL || 'http://localhost:3000';
const webServerPort = Number(new URL(baseURL).port) || 3000;

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },

  // CI 中失败自动重试 2 次
  retries: process.env.CI ? 2 : 0,

  // 并行执行：CI 下 2 workers（ubuntu-latest runner 2 核，不贪多），
  // 本地默认按 CPU 核数自适应
  fullyParallel: true,
  workers: process.env.CI ? 2 : undefined,

  // 报告器
  reporter: [
    ['html', { open: 'never' }],
    ['list'],
  ],

  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'on-first-retry',
  },

  // 自动启动生产构建服务（issue #171）：E2E 直接跑 standalone server.js，
  // 与 Docker 容器完全同形态（next start 与 output:'standalone' 不兼容）。
  // CI/本地运行前需先 npm run build 并组装 standalone 静态资源
  // （cp .next/static 与 public 入 .next/standalone，见 ci.yml / Dockerfile）。
  // 历史上曾跑在 next dev 上，按需编译/Fast Refresh full reload 竞态
  // 是 PR #169 类 flaky 的根因，切生产构建后此类竞态结构性消失。
  webServer: {
    command: `PORT=${webServerPort} node .next/standalone/server.js`,
    port: webServerPort,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },

  projects: [
    // 认证状态设置（登录一次，所有测试共享）。
    // 生产构建下路由已预编译，原「路由预热 warmup project」（专为
    // next dev 按需编译设计，见 issue #171 评审）已移除。
    {
      name: 'setup',
      testMatch: /.*\.setup\.ts/,
    },
    // 桌面端 Chrome
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        storageState: 'e2e/.auth/admin.json',
      },
      dependencies: ['setup'],
    },
    // 移动端 webkit（iPhone 13）：PR #169 曾困「登录后 /dashboard→/m/dashboard
    // 307 跳转被取消（Load request cancelled）」改用 Pixel 5（chromium）绕过，
    // 后续实测定位到两个与 webkit 无关的 dev 竞态（根因，均已修复）：
    //   1. next dev 重编译触发的 Fast Refresh full reload 会取消进行中的导航；
    //   2. hydration 前 fill 的值被 controlled input 重渲染清空，登录静默失败
    //      （JS 较慢的引擎先触发）——auth.setup.ts/auth.spec.ts 已加水合等待
    {
      name: 'mobile',
      use: {
        ...devices['iPhone 13'],
        storageState: 'e2e/.auth/admin.json',
      },
      dependencies: ['setup'],
    },
  ],
});
