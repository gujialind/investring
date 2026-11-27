import { defineConfig, devices, type PlaywrightTestConfig } from '@playwright/test';

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
// 直接运行时从被测地址派生服务端口，避免启动服务与请求地址不一致。
const baseURL = process.env.BASE_URL || 'http://localhost:3000';
const webServerPort = Number(new URL(baseURL).port) || 3000;
const authFile = process.env.E2E_AUTH_FILE || 'e2e/.auth/admin.json';

// 报告器：html + list 恒定；PLAYWRIGHT_JSON_OUTPUT_FILE 存在时追加 json（#466 主 E2E job
// 用它汇总 flaky 用例）。刻意在配置里追加而非用 CLI --reporter：CLI 会整体替换本列表，
// html 的 open:'never' 等配置随之丢失。形态对比采集仍走 CLI --reporter（见 e2e-stack.yml）。
const reporters: NonNullable<PlaywrightTestConfig['reporter']> = [
  ['html', { open: 'never' }],
  ['list'],
];
if (process.env.PLAYWRIGHT_JSON_OUTPUT_FILE) {
  reporters.push(['json']);
}

export default defineConfig({
  testDir: './e2e',
  outputDir: process.env.E2E_OUTPUT_DIR || 'test-results',
  timeout: 30_000,
  // 断言轮询上界：全局 5s；mobile project 单独放宽到 15s（#958，理由见 projects.mobile 注释）
  expect: { timeout: 5_000 },

  // CI 中失败自动重试 2 次
  retries: process.env.CI ? 2 : 0,

  // 并行执行：CI 下 2 workers（ubuntu-latest runner 2 核，不贪多），
  // 本地默认按 CPU 核数自适应
  fullyParallel: true,
  workers: process.env.CI ? 2 : undefined,

  reporter: reporters,

  use: {
    baseURL,
    // 交互动作与 locator.waitFor()/page.waitForFunction() 的全局上界（#551 §2）：
    // 此前未设，裸写法默认**无上界**，一次被吞的点击（#524）会静默吃掉整条用例剩余预算。
    // 取 10_000 与全部显式站点同值，且在 timeout: 30_000 下仍容得下 3 次动作失败才撞全局
    // 超时——放宽到 15_000 只留 2 次，退化成排查不出东西的裸用例超时。
    // 覆盖面到此为止，两条都在外面：① page.request.* 用独立 TimeoutSettings，仍是 30s，
    // 数据准备不受影响；② expect 断言归 expect.timeout。导航（goto/waitForURL）自 #665
    // 起由下面的 navigationTimeout 独立兜住——它此前的 fixture 缺省是 0（等于无上界），
    // 会在回落到 actionTimeout 之前先短路，所以放宽动作上界也救不了卡住的导航。
    // 另外 helpers.ts 的 3_000 分支探针必须保持显式且小于本值，否则 openPopover 的
    // 「有没有开成」判定会被上界改写。
    // ③ 三层上界按 project 分级（#958，沿用 #763 的帧率计费口径；实测判据见下方
    // projects.mobile 注释）：mobile 把动作上界放宽到 20_000、断言上界放宽到
    // 15_000、用例总预算放宽到 90_000，chromium 全部保持本文件的全局值。三者的
    // 从属关系不变：各自的 actionTimeout 仍须严格小于所属 project 的用例总预算
    // （mobile 20s < 90s）；navigationTimeout 不随 project 变，仍 15_000，对 mobile
    // 90s 的用例预算同样成立。spec 内显式 `{ timeout: N }` 是语义而非兜底，恒优先。
    actionTimeout: 10_000,
    // 导航上界（#665）：platform-detail 的深链用例偶发抖动此前只能撞 timeout: 30_000
    // 收场，报错是「Test timeout exceeded」，指不到 waitForURL 这一步。取 15_000：
    // 大于生产 standalone 下的正常导航（含 2 workers 抢后端的抖动），又严格小于全局
    // 用例超时，卡住的导航会以「navigating to …」点名失败而不是烧掉整条用例预算。
    navigationTimeout: 15_000,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'on-first-retry',
  },

  // 自动启动生产构建服务（issue #171）：E2E 直接跑 standalone server.js，
  // 与 Docker 容器完全同形态（next start 与 output:'standalone' 不兼容）。
  // CI/本地运行前需先 npm run build 并组装 standalone 静态资源
  // （cp .next/static 与 public 入 .next/standalone，见 e2e-stack.yml / Dockerfile）。
  // 历史上曾跑在 next dev 上，按需编译/Fast Refresh full reload 竞态
  // 是 PR #169 类 flaky 的根因，切生产构建后此类竞态结构性消失。
  webServer: process.env.E2E_SERVER_MANAGED === 'true' ? undefined : {
    command: `HOSTNAME=127.0.0.1 PORT=${webServerPort} node .next/standalone/server.js`,
    port: webServerPort,
    reuseExistingServer: false,
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
        storageState: authFile,
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
      // mobile 的三层预算单点放宽（#958，沿用 #763 的帧率计费口径）：
      //   actionTimeout 10_000 → 20_000（动作/`locator.waitFor`/`page.waitForRequest`）
      //   expect.timeout 5_000 → 15_000（断言轮询）
      //   timeout 30_000 → 90_000（用例总预算）
      // 为什么必须放宽内层而不只是总预算：PR #951 第 6 轮采集（run 45869732853）的
      // 失败点是 `locator.click / page.waitForRequest: Timeout 10000ms exceeded` 与
      // `toHaveAttribute ... Timeout: 5000ms`——被动作/断言上界卡住，#763 那种只放宽
      // 用例总预算的写法（mobileTimeout 助手）在这类失败上根本没机会生效。帧间隔实测
      // 1.7–1.9s，10s 只够约 5 帧（stable 判定本身就要连续两帧），20s 给到约 10 帧。
      // 顺带把 #763 遗留的「逐 spec 复制 mobileTimeout、其余 spec 内层从未放宽」收成
      // 单点配置；spec 内显式 test.setTimeout(90_000/120_000) 与显式 `{ timeout: N }`
      // 仍覆盖本处取值，保持不动。chromium 帧调度不依赖页面视觉变化，三层都保持全局
      // 值，桌面慢回归照样红。
      // 「少产生抖动」的另一条（capture 侧 mobile 降并发/串行，#958 方向 3）**尚未落地**：
      // 须先读到 trace 结论再定，见 frontend/e2e/AGENTS.md §1；trace 取证已同批落地。
      timeout: 90_000,
      expect: { timeout: 15_000 },
      use: {
        ...devices['iPhone 13'],
        storageState: authFile,
        actionTimeout: 20_000,
      },
      dependencies: ['setup'],
    },
  ],
});
