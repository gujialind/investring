/**
 * 手动触发任务的异步形态（issue #714；#715 任务页下线后入口迁到设置页「日志管理」）
 *
 * 防止复发的缺陷：`POST /api/system/tasks/{code}/run` 原先在请求内同步跑完任务，
 * 请求寿命 = 任务时长，nginx `location /api/` 的 60s proxy_read_timeout 把长任务切成
 * 504——任务在服务端照常成功，入口却显示失败。改为「提交即返 + 轮询终态」后钉三条：
 *  1. 受理请求是**短请求**，且回执只带 execution_id/status=pending（不含任务结果）
 *  2. 进行中信号（行内「执行中」+ 禁用重复触发）来自轮询到的状态，不来自请求本身
 *  3. 结果 toast 与执行历史的成功行在终态才出现，受理瞬间不得出现失败措辞
 *
 * #715 迁移说明：任务页（/settings/tasks）已下线，`log_cleanup` 的手动触发入口迁到
 * 设置页「日志管理」Tab → 执行历史子 Tab 的「立即清理」按钮（LogsContent 的 ExecutionsPanel）。
 * 本 spec 随之改到该入口触发，异步契约（提交即返 / 轮询终态 / 终态 toast）原样保留——
 * 迁移只换 UI 载体，不换任务运行形态，故这三条守卫继续有效。
 *
 * 数据说明：只用 `log_cleanup`——它在本次独占的 E2E 库里清理过期日志，无业务副作用，
 * 也不触碰外部数据源（`nav_sync` / `trading_calendar_sync` 要打第三方接口，
 * `snapshot_generate` 会改种子快照，后者是 E2E 红线）。用例 2 用 `page.route` 把轮询
 * 钉在 `running`——本地 log_cleanup 毫秒级完成，「执行中」在真实时序下不可稳定观测。
 *
 * 并发说明：一次 E2E 只有一个后端实例，chromium 与 mobile 两个 project 会同码点「立即清理」，
 * 而 #714 的单 active 锁按设计对后到的请求回 409（实测确实出现过）。锁只在任务运行期
 * 持有，log_cleanup 毫秒级即释放，故 submit 等锁空出来再投；409 本身是否正确由
 * 后端集成测试（tests/integration/test_tasks.py）断言，这里不重复。
 */
import { test, expect } from '@playwright/test';
import type { Page, Request } from '@playwright/test';
import { collectPageErrors } from './helpers';

const TASK = 'log_cleanup';
const RUN_PATH = `/api/system/tasks/${TASK}/run`;
const POLL_URL = /\/api\/system\/tasks\/executions\/\d+$/;

/** 一次提交的结果：状态码、回执体、以及「点确认 → 拿到响应」的耗时 */
interface RunOutcome {
  status: number;
  body: { execution_id?: number; task_code?: string; status?: string };
  elapsed: number;
}

/**
 * 导航到手动触发入口（#715）：设置页 → 日志管理 Tab → 执行历史子 Tab。
 * 「立即清理」按钮只在执行历史子 Tab 激活时挂载（Radix Tabs 默认卸载非激活面板），
 * 故三条用例都先走这里，再操作 data-testid="log-cleanup-run"。
 */
async function gotoExecutionsTab(page: Page) {
  await page.goto('/settings');
  await page.getByRole('tab', { name: '日志管理' }).click();
  await page.getByRole('tab', { name: '执行历史' }).click();
  await expect(page.getByTestId('log-cleanup-run')).toBeVisible({ timeout: 10_000 });
}

function acceptedToast(page: Page) {
  return page.getByTestId('toast-card').filter({ hasText: '任务已启动' });
}

function doneToast(page: Page) {
  return page.getByTestId('toast-card').filter({ hasText: '任务执行完成' });
}

function failureToast(page: Page) {
  return page.getByTestId('toast-card').filter({ hasText: /启动失败|任务执行失败/ });
}

/**
 * 触发按钮：用不可变的 data-testid 定位（迁移前是 `执行 log_cleanup` 的可变 aria-label）。
 * running 态下按钮 disabled、行内出「执行中」，但 testid 不变，「禁用」断言才等得到元素。
 */
function runButton(page: Page) {
  return page.getByTestId('log-cleanup-run');
}

/** 点一次「立即清理」并等响应 */
async function submitOnce(page: Page): Promise<RunOutcome> {
  // ConfirmDialog 是 Radix AlertDialog（role="alertdialog"），不是 dialog：
  // helpers.dialogByTitle 按 [role="dialog"] 定位，这里恒 0 命中。#715 入口标题为「清理过期日志」。
  const dlg = page.getByRole('alertdialog').filter({ hasText: '清理过期日志' });
  const dlgVisible = dlg.waitFor({ state: 'visible' });
  await runButton(page).click();
  await dlgVisible;

  // 计时起点取「浏览器把请求发出去」那一刻，不是「点确认」那一刻：webkit 上点击到
  // 请求上路实测能吃掉几秒，那属于渲染引擎，既不受 nginx 60s 约束，也不随任务同步/
  // 异步形态变化，算进来只会让 mobile 端在慢机器上无意义翻红（实测 7.3s vs 请求本身 0.1s）
  let sentAt = 0;
  const onRequest = (req: Request) => {
    if (req.url().includes(RUN_PATH)) sentAt = Date.now();
  };
  const clickAt = Date.now();
  page.on('request', onRequest);
  try {
    const responsePromise = page.waitForResponse((r) => r.url().includes(RUN_PATH));
    await dlg.getByRole('button', { name: '清理', exact: true }).click();
    const response = await responsePromise;
    const receivedAt = Date.now();
    return {
      status: response.status(),
      body: await response.json(),
      elapsed: sentAt > 0 ? receivedAt - sentAt : receivedAt - clickAt,
    };
  } finally {
    page.off('request', onRequest);
  }
}

/**
 * 提交直到被受理（200）。被单 active 锁挡回 409 时等旧「启动失败」toast 自己过期
 * （3s）再重投，避免它的回执和下一轮的成功回执同时挂在屏幕上。
 */
async function submitUntilAccepted(page: Page): Promise<RunOutcome> {
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    const outcome = await submitOnce(page);
    if (outcome.status === 200) {
      await expect(acceptedToast(page)).toBeVisible({ timeout: 10_000 });
      return outcome;
    }
    expect(outcome.status).toBe(409);
    await expect(page.getByTestId('toast-card').filter({ hasText: '启动失败' })).toHaveCount(0, {
      timeout: 10_000,
    });
  }
  throw new Error(`任务 ${TASK} 连续 5 次提交都被单 active 锁挡住`);
}

test.describe('任务手动触发（提交即返 + 轮询终态）', () => {
  test('受理请求短返回，回执只带 execution_id 与 pending', async ({ page }) => {
    const errors = collectPageErrors(page);
    await gotoExecutionsTab(page);

    const outcome = await submitUntilAccepted(page);

    expect(outcome.status).toBe(200);
    expect(outcome.body.task_code).toBe(TASK);
    expect(outcome.body.status).toBe('pending');
    expect(outcome.body.execution_id).toBeGreaterThan(0);
    // 请求寿命（发出→收到响应）上界 5s，远小于 nginx 的 60s。诚实交代它的保护范围：
    // log_cleanup 毫秒级就跑完，退化回「在请求内同步跑完任务」也照样过得了这条，
    // 真正钉住「提交即返」的是上面 status=pending 且回执不含结果字段；nav_sync（实测
    // 162s）那种「任务时长决定请求寿命」的形态在 E2E 里不可复现——它要打第三方接口。
    // 这条只兜住另一半：受理请求本身不该变成需要网关超时的长请求。
    expect(outcome.elapsed).toBeLessThan(5_000);

    await expect(failureToast(page)).toHaveCount(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('轮询到 running 时行内显示执行中并禁用重复触发', async ({ page }) => {
    const errors = collectPageErrors(page);

    // 轮询钉在 running；released 后放行真实请求——任务早已跑完，下一次轮询即终态。
    // 路由在导航前注册，确保 track() 起轮后首个 poll 就被拦。
    let released = false;
    await page.route(POLL_URL, async (route) => {
      if (released) {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 1,
          task_code: TASK,
          trigger_type: 'manual',
          status: 'running',
          started_at: new Date().toISOString(),
          finished_at: null,
          duration_ms: null,
          records_total: null,
          records_success: null,
          records_failed: null,
          error_message: null,
          error_stack: null,
          created_at: null,
        }),
      });
    });

    await gotoExecutionsTab(page);
    await submitUntilAccepted(page);

    // 进行中的显式信号：旧形态点完就阻塞在请求里，行内没有任何进度可言
    await expect(page.getByText('执行中').first()).toBeVisible({ timeout: 10_000 });
    await expect(runButton(page)).toBeDisabled();
    await expect(failureToast(page)).toHaveCount(0);

    released = true;
    await expect(doneToast(page)).toBeVisible({ timeout: 20_000 });
    await expect(runButton(page)).toBeEnabled();
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('终态后执行历史落出成功行', async ({ page }) => {
    const errors = collectPageErrors(page);
    await gotoExecutionsTab(page);

    await submitUntilAccepted(page);
    await expect(doneToast(page)).toBeVisible({ timeout: 20_000 });

    const row = page
      .getByRole('row')
      .filter({ hasText: TASK })
      .filter({ hasText: '成功' })
      .first();
    await expect(row).toBeVisible({ timeout: 10_000 });
    await expect(failureToast(page)).toHaveCount(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
