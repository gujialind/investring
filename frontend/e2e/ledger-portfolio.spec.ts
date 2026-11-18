/**
 * 前端 E2E：平台市值台账型组合视图（issue #905 PR3）
 *
 * 覆盖验收断言与口径：
 * - 断言1：台账型 draft 不出现「请执行首次申购……初始净值固定为 1.0000」文案；给手动激活引导。
 * - S3：draft 不可预录——未激活不渲染登记入口。
 * - 断言2：台账型无净值曲线/投资人份额；绩效口径A 只渲染区间收益与年化，不渲染回撤/波动率/TWR/净值。
 * - S8：viewer 无写入口（登记/激活按钮不可见，非仅提交 403）。
 * - CRUD：active 台账型经 UI 登记市值 → 汇总与列表更新。
 *
 * 数据说明：一律自建隔离组合（isolatedPortfolioCode 区分 project/worker/retry），kind=ledger、
 * owner=ADMIN（种子投资人）；不复用种子 E2E_PORT/E2E_ACTIVE（净值型契约组合）。
 * 激活走台账专用端点 POST /activate（绕开申购）；登记可经 UI Dialog 或 API。
 */
import { test, expect, type Page } from '@playwright/test';
import {
  openAppAndAuth,
  isolatedPortfolioCode,
  pickCalendarDay,
  openPopover,
  dialogByTitle,
  toISODate,
} from './helpers';

type Headers = { Authorization: string };

async function createLedgerViaApi(page: Page, headers: Headers, code: string, activate = false) {
  const resp = await page.request.post('/api/portfolios', {
    headers,
    data: { code, name: `台账夹具 ${code}`, kind: 'ledger', owner_investor_code: 'ADMIN' },
  });
  expect(resp, `创建台账组合 ${resp.status()} ${await resp.text()}`).toBeOK();
  if (activate) {
    const act = await page.request.post(`/api/portfolios/${code}/activate`, { headers });
    expect(act, `激活台账组合 ${act.status()} ${await act.text()}`).toBeOK();
  }
}

async function gotoDetailAndWait(page: Page, code: string) {
  await page.goto(`/portfolio/${code}`);
  await page.getByRole('heading', { level: 1 }).first().waitFor({ state: 'visible', timeout: 15_000 });
}

async function loginAsViewer(page: Page): Promise<void> {
  await page.context().clearCookies();
  await page.evaluate(() => window.localStorage.clear());
  await page.goto('/login');
  await page.getByLabel('用户名').fill('VIEWER');
  await page.getByLabel('密码').fill('viewer123');
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForURL(/\/(m\/)?dashboard/, { waitUntil: 'domcontentloaded' });
}

test.describe('台账型组合视图（#905 PR3）', () => {
  test('draft：无首购文案、有激活引导、不渲染登记入口（断言1 + S3）', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E905D', testInfo, 'L');
    await createLedgerViaApi(page, headers, code, false);
    await gotoDetailAndWait(page, code);

    // 断言1：绝不出现净值型首购激活文案
    await expect(page.getByText('请执行首次申购')).toHaveCount(0);
    await expect(page.getByText('初始净值固定为 1.0000')).toHaveCount(0);
    // 台账激活引导 + 激活按钮（admin）
    await expect(page.getByText('组合尚未激活')).toBeVisible();
    await expect(page.getByTestId('ledger-activate')).toBeVisible();
    // S3：draft 不渲染登记入口
    await expect(page.getByTestId('ledger-entry-add')).toHaveCount(0);
    await expect(page.getByTestId('ledger-flow-add')).toHaveCount(0);
    // 断言2：无净值走势/投资人份额视图
    await expect(page.getByText('资产分布')).toHaveCount(0);
    await expect(page.getByText('净值走势')).toHaveCount(0);
  });

  test('active：UI 登记市值 → 汇总更新；口径A 绩效无回撤/波动率；无净值/份额（断言2）', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E905A', testInfo, 'L');
    await createLedgerViaApi(page, headers, code, true);
    const today = toISODate(new Date());
    // 经 API 登记一笔本金进出（注资 100），市值经 UI Dialog 登记
    const flow = await page.request.post(`/api/portfolios/${code}/ledger/flows`, {
      headers,
      data: { platform_code: 'HBZQ', flow_date: today, direction: 'inflow', amount: 100 },
    });
    expect(flow, `登记进出 ${flow.status()} ${await flow.text()}`).toBeOK();

    await gotoDetailAndWait(page, code);
    await expect(page.getByTestId('ledger-entry-add')).toBeVisible();
    await expect(page.getByTestId('ledger-flow-add')).toBeVisible();

    // UI 登记市值 100
    await page.getByTestId('ledger-entry-add').click();
    const dlg = dialogByTitle(page, '登记平台市值');
    await openPopover(page, dlg.getByTestId('platform-trigger'), page.getByTestId('platform-option').first());
    await page.getByTestId('platform-option').first().click();
    await pickCalendarDay(page, dlg.locator('button#ledger-entry-date'), today);
    await dlg.locator('#ledger-entry-value').fill('100');
    await dlg.getByRole('button', { name: '登记', exact: true }).click();
    await expect(page.getByTestId('toast-card').filter({ hasText: '登记成功' })).toBeVisible();

    // 汇总：当前市值 100、净入金 100、累计收益 0（收益=市值−净入金）
    await expect(page.getByText('¥100.0000').first()).toBeVisible();
    await expect(page.getByText('累计收益')).toBeVisible();

    // 口径A 绩效：区间收益与年化在；回撤/波动率/TWR/净值类不渲染
    await expect(page.getByText('区间收益与年化')).toBeVisible();
    await expect(page.getByText('最大回撤')).toHaveCount(0);
    await expect(page.getByText('年化波动率')).toHaveCount(0);
    await expect(page.getByText('期初净值')).toHaveCount(0);
    // 断言2：无净值走势/资产分布/份额
    await expect(page.getByText('净值走势')).toHaveCount(0);
    await expect(page.getByText('资产分布')).toHaveCount(0);

    // 双端目检取证（整页截图，供人工 inspect 布局/数字格式）
    await page.screenshot({ path: testInfo.outputPath('ledger-active.png'), fullPage: true });
  });

  test('viewer：台账写入口不可见（S8，非仅提交 403）', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E905V', testInfo, 'L');
    await createLedgerViaApi(page, headers, code, true);
    await loginAsViewer(page);
    await gotoDetailAndWait(page, code);

    // 读侧可见（汇总），写入口不可见
    await expect(page.getByText('台账汇总')).toBeVisible();
    await expect(page.getByTestId('ledger-entry-add')).toHaveCount(0);
    await expect(page.getByTestId('ledger-flow-add')).toHaveCount(0);
    await expect(page.getByTestId('ledger-activate')).toHaveCount(0);
  });

  test('仪表盘 R8 合并：台账计入总资产/总收益 chip，四卡栅格（双端目检取证）', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E905R', testInfo, 'L');
    await createLedgerViaApi(page, headers, code, true);
    const today = toISODate(new Date());
    await page.request.post(`/api/portfolios/${code}/ledger/entries`, {
      headers, data: { platform_code: 'HBZQ', value_date: today, market_value: 100 },
    });
    await page.request.post(`/api/portfolios/${code}/ledger/flows`, {
      headers, data: { platform_code: 'HBZQ', flow_date: today, direction: 'inflow', amount: 100 },
    });
    await page.goto('/dashboard');
    await expect(page.getByTestId('dashboard-stat-total_value')).toBeVisible();
    // R8：总收益 chip 存在（含台账型）；平均收益率标「不含台账型」
    await expect(page.getByTestId('dashboard-stat-total_profit')).toBeVisible();
    await expect(page.getByText('不含台账型').first()).toBeVisible();
    // B3（#905 PR3 L2）：台账活跃卡显「累计收益(元)」，不把 None 收益率渲染成 0.00%
    await expect(page.getByText('累计收益 ¥0.00').first()).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('dashboard-r8.png'), fullPage: true });
  });

  test('S2：真实点击激活后汇总从 -- 变 0 且登记入口出现（totals 失效回归）', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E905S', testInfo, 'L');
    await createLedgerViaApi(page, headers, code, false);
    await gotoDetailAndWait(page, code);
    // draft 汇总为 --（None），不出现 0
    await expect(page.getByText('¥0.0000')).toHaveCount(0);
    // 真实点击激活（非仅断言按钮存在）
    await page.getByTestId('ledger-activate').click();
    await expect(page.getByTestId('toast-card').filter({ hasText: '激活成功' })).toBeVisible();
    // 激活后 ledger totals 失效重取：active 无记录 → 0（不再 --），登记入口出现
    await expect(page.getByText('¥0.0000').first()).toBeVisible();
    await expect(page.getByTestId('ledger-entry-add')).toBeVisible();
  });

  test('B1：绩效请求失败渲染错误态+重试，而非七个--空态（失败≠空态）', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E905B', testInfo, 'L');
    await createLedgerViaApi(page, headers, code, true);
    await page.route(`**/api/portfolios/${code}/performance`, (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: { error: 'INTERNAL', message: '绩效服务故障' } }),
      }),
    );
    await gotoDetailAndWait(page, code);
    await expect(page.getByText('绩效服务故障')).toBeVisible();
    await expect(page.getByRole('button', { name: /重试/ })).toBeVisible();
  });
});