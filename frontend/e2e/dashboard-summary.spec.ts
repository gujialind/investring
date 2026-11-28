import { test, expect, type Page } from '@playwright/test';
import type { DashboardSummary } from '../src/lib/api/dashboard';
import type { Subscription } from '../src/types/subscription';
import type { Trade } from '../src/types/trade';
import { collectPageErrors } from './helpers';

const subscriptions: Subscription[] = [
  {
    id: 9101,
    portfolio_code: 'ESP_SHORT',
    investor_code: 'ADMIN',
    platform_code: 'HBZQ',
    sub_type: 'subscribe',
    amount: 2000,
    apply_date: '2026-11-24',
    status: 'pending',
  },
  {
    id: 9102,
    portfolio_code: 'ESP_LONG',
    investor_code: 'ADMIN',
    platform_code: 'HBZQ',
    sub_type: 'subscribe',
    amount: 100000,
    apply_date: '2025-01-02',
    confirm_date: '2025-01-03',
    status: 'confirmed',
  },
];

const trades: Trade[] = [
  {
    id: 9201,
    portfolio_code: 'ESP_LONG',
    product_code: '510300.SH',
    display_code: '510300.SH',
    market: 'CN_EXCHANGE',
    platform_code: 'HBZQ',
    trade_type: 'buy',
    amount: 8200,
    fee: 0,
    trade_date: '2026-11-25',
    status: 'pending',
  },
  {
    id: 9202,
    portfolio_code: 'ESP_SHORT',
    product_code: '000300.OF',
    display_code: '000300.OF',
    market: 'CN_OTC',
    platform_code: 'HBZQ',
    trade_type: 'sell',
    shares: 300,
    fee: 0,
    trade_date: '2026-11-20',
    status: 'confirmed',
  },
  {
    id: 9203,
    portfolio_code: 'ESP_SHORT',
    product_code: 'CASH',
    market: '',
    platform_code: 'HBZQ',
    trade_type: 'sell',
    amount: 2000,
    fee: 0,
    trade_date: '2026-11-24',
    status: 'confirmed',
    transfer_group: 'sub_9101',
  },
];

const reliableSummary: DashboardSummary = {
  total_value: 603000,
  total_profit: 3000,
  aggregate_xirr: {
    value: 12.34,
    reliable: true,
    reason: null,
    cash_flow_count: 3,
    holding_days: 420,
  },
  pending_subscription_count: 1,
  pending_trade_count: 1,
  portfolios: [
    {
      code: 'ESP_LONG',
      name: '长期组合',
      kind: 'nav',
      total_value: 400000,
      total_profit: 5000,
      cumulative_return: 1.25,
      mwr: 9.87,
      annualization_reliable: true,
      holding_days: 420,
    },
    {
      code: 'ESP_SHORT',
      name: '短期组合',
      kind: 'nav',
      total_value: 203000,
      total_profit: -2000,
      cumulative_return: -0.98,
      mwr: null,
      annualization_reliable: false,
      holding_days: 1,
    },
  ],
};

async function mockDashboardLists(page: Page): Promise<void> {
  await page.route(
    (url) => url.pathname === '/api/subscriptions' && url.searchParams.get('page_size') === '100',
    (route) => route.fulfill({
      json: { items: subscriptions, total: subscriptions.length, page: 1, page_size: 100, total_pages: 1 },
    }),
  );
  await page.route(
    (url) => url.pathname === '/api/trades' && url.searchParams.get('page_size') === '100',
    (route) => route.fulfill({
      json: {
        items: trades,
        total: trades.length,
        total_groups: trades.length,
        total_pages: 1,
        page: 1,
        page_size: 100,
      },
    }),
  );
}

async function mockDashboard(page: Page, summary: DashboardSummary): Promise<void> {
  await mockDashboardLists(page);
  await page.route(
    (url) => url.pathname === '/api/dashboard/summary',
    (route) => route.fulfill({ json: summary }),
  );
}

test.describe('首页汇总（#944 Phase 3.2）', () => {
  test('三指标、可靠 XIRR、逐行 XIRR 与琥珀待确认议程', async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await mockDashboard(page, reliableSummary);
    await page.goto('/dashboard');

    const summary = page.getByTestId('dashboard-summary');
    await expect(summary).toBeVisible();
    await expect(page.getByTestId('dashboard-summary-total_value')).toHaveText('¥603,000.00');
    await expect(page.getByTestId('dashboard-summary-total_profit')).toHaveText('+¥3,000.00');
    await expect(page.getByTestId('dashboard-summary-total_profit')).toHaveClass(/text-gain/);
    await expect(page.getByTestId('dashboard-summary-aggregate_xirr')).toHaveText('+12.34%');
    await expect(page.getByTestId('dashboard-summary-xirr-reason')).toHaveCount(0);
    await expect(page.getByText('平均累计收益')).toHaveCount(0);
    await expect(page.getByText('资产构成')).toHaveCount(0);

    const heroFontSize = await page.getByTestId('dashboard-summary-total_value')
      .evaluate((element) => getComputedStyle(element).fontSize);
    expect(heroFontSize).toBe(testInfo.project.name === 'mobile' ? '36px' : '48px');

    const longRow = page.locator('[data-testid="dashboard-portfolio-row"][data-portfolio-code="ESP_LONG"]');
    const shortRow = page.locator('[data-testid="dashboard-portfolio-row"][data-portfolio-code="ESP_SHORT"]');
    await expect(longRow.getByTestId('dashboard-portfolio-value')).toHaveText('¥400,000.00');
    await expect(longRow.getByTestId('dashboard-portfolio-profit')).toHaveText('+¥5,000.00');
    await expect(longRow.getByTestId('dashboard-portfolio-xirr')).toHaveText('+9.87%');
    await expect(shortRow.getByTestId('dashboard-portfolio-profit')).toHaveText('-¥2,000.00');
    await expect(shortRow.getByTestId('dashboard-portfolio-xirr')).toHaveText('--');

    const agenda = page.getByTestId('dashboard-pending-agenda');
    await expect(agenda).toHaveClass(/bg-warning-soft/);
    await expect(agenda).toContainText('2 笔待确认');
    await expect(agenda.getByTestId('dashboard-pending-item')).toHaveCount(2);
    await expect(page.getByTestId('dashboard-recent-activity')).toContainText('510300.SH');
    await expect(page.getByTestId('dashboard-recent-activity')).toContainText('ESP_SHORT');
    await expect(page.getByTestId('dashboard-recent-item')).toHaveCount(4);
    await expect(page.getByTestId('dashboard-recent-activity')).not.toContainText('CASH');
    if (testInfo.project.name === 'mobile') {
      await expect(page.getByRole('button', { name: '用户菜单', exact: true })).toBeVisible();
      await expect(page.getByRole('heading', { name: '账户', exact: true })).toHaveCount(0);
    }
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('最近活动配对调仓与平台转移现金腿，并过滤申赎现金腿', async ({ page }) => {
    const pairedTrades: Trade[] = [
      {
        id: 9401,
        portfolio_code: 'ESP_LONG',
        product_code: '510300.SH',
        display_code: '510300.SH',
        market: 'CN_EXCHANGE',
        platform_code: 'HBZQ',
        trade_type: 'buy',
        amount: 8200,
        fee: 0,
        trade_date: '2026-11-26',
        status: 'confirmed',
        transfer_group: 'rebal_dashboard1',
      },
      {
        id: 9402,
        portfolio_code: 'ESP_LONG',
        product_code: 'CASH',
        market: '',
        platform_code: 'HBZQ',
        trade_type: 'sell',
        amount: 8200,
        fee: 0,
        trade_date: '2026-11-26',
        confirm_date: '2026-11-26',
        status: 'confirmed',
        transfer_group: 'rebal_dashboard1',
      },
      {
        id: 9403,
        portfolio_code: 'ESP_SHORT',
        product_code: 'CASH',
        market: '',
        platform_code: 'HBZQ',
        trade_type: 'sell',
        amount: 500,
        fee: 0,
        trade_date: '2026-11-25',
        confirm_date: '2026-11-25',
        status: 'confirmed',
        transfer_group: 'a1b2c3d4e5f6',
      },
      {
        id: 9404,
        portfolio_code: 'ESP_SHORT',
        product_code: 'CASH',
        market: '',
        platform_code: 'MYCF',
        trade_type: 'buy',
        amount: 500,
        fee: 0,
        trade_date: '2026-11-25',
        confirm_date: '2026-11-25',
        status: 'confirmed',
        transfer_group: 'a1b2c3d4e5f6',
      },
      {
        id: 9405,
        portfolio_code: 'ESP_LONG',
        product_code: 'CASH',
        market: '',
        platform_code: 'HBZQ',
        trade_type: 'buy',
        amount: 100000,
        fee: 0,
        trade_date: '2026-11-24',
        confirm_date: '2026-11-24',
        status: 'confirmed',
        transfer_group: 'sub_9102',
      },
    ];
    await mockDashboard(page, {
      ...reliableSummary,
      pending_subscription_count: 0,
      pending_trade_count: 0,
    });
    await page.route(
      (url) => url.pathname === '/api/subscriptions' && url.searchParams.get('page_size') === '100',
      (route) => route.fulfill({
        json: {
          items: [subscriptions[1]],
          total: 1,
          page: 1,
          page_size: 100,
          total_pages: 1,
        },
      }),
    );
    await page.route(
      (url) => url.pathname === '/api/trades' && url.searchParams.get('page_size') === '100',
      (route) => route.fulfill({
        json: {
          items: pairedTrades,
          total: pairedTrades.length,
          total_groups: pairedTrades.length,
          total_pages: 1,
          page: 1,
          page_size: 100,
        },
      }),
    );
    await page.goto('/dashboard');

    const rows = page.getByTestId('dashboard-recent-item');
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText('买入');
    await expect(rows.nth(0)).toContainText('510300.SH · ESP_LONG');
    await expect(rows.nth(1)).toContainText('现金转出');
    await expect(rows.nth(1)).toContainText('ESP_SHORT · 平台 HBZQ');
    await expect(rows.nth(1)).toContainText('¥500.00');
    await expect(rows.nth(2)).toContainText('申购');
    await expect(page.getByTestId('dashboard-recent-activity')).not.toContainText('CASH');
  });

  test('待确认现金到账腿显示现金待到账，最近活动仍只显示调仓主腿', async ({ page }) => {
    const arrivalTrades: Trade[] = [
      {
        id: 9501,
        portfolio_code: 'ESP_SHORT',
        product_code: '000300.OF',
        display_code: '000300.OF',
        market: 'CN_OTC',
        platform_code: 'HBZQ',
        trade_type: 'sell',
        shares: 300,
        fee: 0,
        trade_date: '2026-11-26',
        status: 'confirmed',
        transfer_group: 'rebal_arrival01',
      },
      {
        id: 9502,
        portfolio_code: 'ESP_SHORT',
        product_code: 'CASH',
        market: '',
        platform_code: 'HBZQ',
        trade_type: 'buy',
        amount: 2000,
        fee: 0,
        trade_date: '2026-11-26',
        confirm_date: '2026-11-30',
        status: 'pending',
        transfer_group: 'rebal_arrival01',
      },
    ];
    await mockDashboard(page, {
      ...reliableSummary,
      pending_subscription_count: 0,
      pending_trade_count: 1,
    });
    await page.route(
      (url) => url.pathname === '/api/subscriptions' && url.searchParams.get('page_size') === '100',
      (route) => route.fulfill({
        json: { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 },
      }),
    );
    await page.route(
      (url) => url.pathname === '/api/trades' && url.searchParams.get('page_size') === '100',
      (route) => route.fulfill({
        json: {
          items: arrivalTrades,
          total: arrivalTrades.length,
          total_groups: arrivalTrades.length,
          total_pages: 1,
          page: 1,
          page_size: 100,
        },
      }),
    );
    await page.goto('/dashboard');

    const pending = page.getByTestId('dashboard-pending-item');
    await expect(pending).toHaveCount(1);
    await expect(pending).toContainText('现金待到账');
    await expect(pending).toContainText('组合 ESP_SHORT · 平台 HBZQ');
    await expect(pending).toContainText('¥2,000.00');
    await expect(pending).toContainText('2026-11-30');
    await expect(pending).not.toContainText('2026-11-26');
    await expect(pending).not.toContainText('买入 · CASH');
    await expect(page.getByTestId('dashboard-recent-item')).toHaveCount(1);
    await expect(page.getByTestId('dashboard-recent-item')).toContainText('000300.OF · ESP_SHORT');
    await expect(page.getByTestId('dashboard-recent-activity')).not.toContainText('CASH');
  });

  test('短历史 XIRR 显示 -- 与后端给出的持有期不足原因', async ({ page }, testInfo) => {
    await mockDashboard(page, {
      ...reliableSummary,
      aggregate_xirr: {
        value: null,
        reliable: false,
        reason: 'short_history',
        cash_flow_count: 2,
        holding_days: 1,
      },
      portfolios: reliableSummary.portfolios.map((portfolio) => ({
        ...portfolio,
        mwr: 88,
        annualization_reliable: false,
        holding_days: 1,
      })),
    });
    await page.goto('/dashboard');

    await expect(page.getByTestId('dashboard-summary-aggregate_xirr')).toHaveText('--');
    await expect(page.getByTestId('dashboard-summary-xirr-reason')).toHaveText('持有期不足 90 天');
    const rows = page.getByTestId('dashboard-portfolio-row');
    await expect(rows).toHaveCount(2);
    for (const row of await rows.all()) {
      await expect(row.getByTestId('dashboard-portfolio-xirr')).toHaveText('--');
      if (testInfo.project.name === 'mobile') {
        await expect(row.getByText('持有期不足 90 天', { exact: true })).toBeVisible();
      }
    }
  });

  test('无有效解的 XIRR 显示 -- 与后端无解原因，不被短历史启发式覆盖', async ({ page }) => {
    await mockDashboard(page, {
      ...reliableSummary,
      aggregate_xirr: {
        value: null,
        reliable: false,
        reason: 'no_solution',
        cash_flow_count: 2,
        holding_days: 1,
      },
    });
    await page.goto('/dashboard');

    await expect(page.getByTestId('dashboard-summary-aggregate_xirr')).toHaveText('--');
    await expect(page.getByTestId('dashboard-summary-xirr-reason')).toHaveText('当前现金流无法求得有效解');
  });

  test('首载等待 summary，不提前显示伪 0 或活跃组合空态', async ({ page }) => {
    let releaseSummary!: () => void;
    const summaryGate = new Promise<void>((resolve) => {
      releaseSummary = resolve;
    });
    await mockDashboardLists(page);
    await page.route(
      (url) => url.pathname === '/api/dashboard/summary',
      async (route) => {
        await summaryGate;
        await route.fulfill({ json: reliableSummary });
      },
    );
    const subscriptionsLoaded = page.waitForResponse(/\/api\/subscriptions\?/);
    const tradesLoaded = page.waitForResponse(/\/api\/trades\?/);
    await page.goto('/dashboard');
    await Promise.all([subscriptionsLoaded, tradesLoaded]);

    try {
      await expect(page.getByTestId('dashboard-summary')).toHaveCount(0);
      await expect(page.getByText('¥0.00', { exact: true })).toHaveCount(0);
      await expect(page.getByText('暂无活跃组合，请先创建组合')).toHaveCount(0);
    } finally {
      releaseSummary();
    }
    await expect(page.getByTestId('dashboard-summary-total_value')).toHaveText('¥603,000.00');
  });

  test('summary HTTP 500 渲染 QueryErrorState，重试后恢复且不落空态', async ({ page }, testInfo) => {
    let attempts = 0;
    await mockDashboardLists(page);
    await page.route(
      (url) => url.pathname === '/api/dashboard/summary',
      (route) => {
        attempts += 1;
        return attempts <= 2
          ? route.fulfill({
              status: 500,
              json: { detail: { error: 'DASHBOARD_UNAVAILABLE', message: '仪表盘汇总暂不可用' } },
            })
          : route.fulfill({ json: reliableSummary });
      },
    );
    await page.goto('/dashboard');

    await expect(page.getByText('加载失败：仪表盘汇总暂不可用')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('暂无活跃组合，请先创建组合')).toHaveCount(0);
    if (testInfo.project.name === 'mobile') {
      await expect(page.getByRole('button', { name: '用户菜单', exact: true })).toBeVisible();
      await expect(page.getByRole('heading', { name: '账户', exact: true })).toHaveCount(0);
    }
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await expect(page.getByTestId('dashboard-summary-total_value')).toHaveText('¥603,000.00');
    expect(attempts).toBeGreaterThanOrEqual(3);
  });

  test('活动列表失败保留 summary，显示失败态而非空态，重试后恢复', async ({ page }) => {
    let attempts = 0;
    let fail = true;
    await mockDashboard(page, reliableSummary);
    await page.route(
      (url) => url.pathname === '/api/subscriptions' && url.searchParams.get('page_size') === '100',
      (route) => {
        attempts += 1;
        return fail
          ? route.fulfill({
              status: 500,
              json: { detail: { error: 'SUBSCRIPTIONS_UNAVAILABLE', message: '申赎记录暂不可用' } },
            })
          : route.fulfill({
              json: { items: subscriptions, total: subscriptions.length, page: 1, page_size: 100, total_pages: 1 },
            });
      },
    );
    await page.goto('/dashboard');

    await expect(page.getByTestId('dashboard-summary-total_value')).toHaveText('¥603,000.00');
    await expect(page.getByText('加载失败：申赎记录暂不可用')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('暂无交易记录')).toHaveCount(0);
    fail = false;
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await expect(page.getByTestId('dashboard-recent-activity')).toContainText('ESP_SHORT');
    expect(attempts).toBeGreaterThanOrEqual(2);

    fail = true;
    await page.clock.setFixedTime(Date.now() + 31_000);
    await page.locator('a[href$="/portfolio"]:visible').first().click();
    await expect(page).toHaveURL(/\/portfolio$/);
    await page.locator('a[href$="/dashboard"]:visible').first().click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByText('加载失败：申赎记录暂不可用')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('dashboard-recent-activity')).toContainText('ESP_SHORT');
    await expect(page.getByTestId('dashboard-summary-total_value')).toHaveText('¥603,000.00');
    await expect(page.getByText('暂无交易记录')).toHaveCount(0);
    fail = false;
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await expect(page.getByText('加载失败：申赎记录暂不可用')).toHaveCount(0);
    await expect(page.getByTestId('dashboard-recent-activity')).toContainText('ESP_SHORT');
    expect(attempts).toBeGreaterThanOrEqual(4);
  });

  test('汇总后台刷新失败保留上次数据并独立重试，不污染活动列表', async ({ page }) => {
    let summaryAttempts = 0;
    await mockDashboardLists(page);
    await page.route(
      (url) => url.pathname === '/api/dashboard/summary',
      (route) => {
        summaryAttempts += 1;
        if (summaryAttempts === 1) return route.fulfill({ json: reliableSummary });
        if (summaryAttempts <= 3) {
          return route.fulfill({
            status: 500,
            json: { detail: { error: 'DASHBOARD_UNAVAILABLE', message: '仪表盘汇总暂不可用' } },
          });
        }
        return route.fulfill({ json: reliableSummary });
      },
    );
    await page.goto('/dashboard');

    await expect(page.getByTestId('dashboard-summary-total_value')).toHaveText('¥603,000.00');
    await expect(page.getByTestId('dashboard-recent-activity')).toContainText('ESP_SHORT');
    await page.clock.setFixedTime(Date.now() + 31_000);
    await page.locator('a[href$="/portfolio"]:visible').first().click();
    await expect(page).toHaveURL(/\/portfolio$/);
    await page.locator('a[href$="/dashboard"]:visible').first().click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect.poll(() => summaryAttempts).toBeGreaterThanOrEqual(3);

    const summaryError = page.getByTestId('dashboard-summary-error');
    await expect(summaryError).toContainText('汇总更新失败，当前显示上次数据');
    await expect(summaryError).toContainText('加载失败：仪表盘汇总暂不可用');
    await expect(page.getByTestId('dashboard-summary-total_value')).toHaveText('¥603,000.00');
    await expect(page.getByTestId('dashboard-recent-activity')).toContainText('ESP_SHORT');
    await expect(page.getByText('暂无活跃组合，请先创建组合')).toHaveCount(0);
    await summaryError.getByRole('button', { name: '重试', exact: true }).click();
    await expect(summaryError).toHaveCount(0);
    await expect(page.getByTestId('dashboard-summary-total_value')).toHaveText('¥603,000.00');
    expect(summaryAttempts).toBeGreaterThanOrEqual(4);
  });

  test('待确认计数超过六笔时展示截断提示', async ({ page }) => {
    const manySubscriptions: Subscription[] = Array.from({ length: 7 }, (_, index) => ({
      id: 9300 + index,
      portfolio_code: 'ESP_SHORT',
      investor_code: 'ADMIN',
      platform_code: 'HBZQ',
      sub_type: 'subscribe',
      amount: 2000,
      apply_date: '2026-11-24',
      status: 'pending',
    }));
    await mockDashboard(page, {
      ...reliableSummary,
      pending_subscription_count: 7,
      pending_trade_count: 1,
    });
    await page.route(
      (url) => url.pathname === '/api/subscriptions' && url.searchParams.get('page_size') === '100',
      (route) => route.fulfill({
        json: {
          items: manySubscriptions,
          total: manySubscriptions.length,
          page: 1,
          page_size: 100,
          total_pages: 1,
        },
      }),
    );
    await page.goto('/dashboard');

    const agenda = page.getByTestId('dashboard-pending-agenda');
    await expect(agenda).toContainText('8 笔待确认');
    await expect(agenda.getByTestId('dashboard-pending-item')).toHaveCount(6);
    await expect(agenda).toContainText('还有 2 笔，请前往组合页查看');
  });

  test('待确认计数大于零但前百条无明细时显示明确兜底', async ({ page }) => {
    await mockDashboard(page, reliableSummary);
    await page.route(
      (url) => url.pathname === '/api/subscriptions' && url.searchParams.get('page_size') === '100',
      (route) => route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 } }),
    );
    await page.route(
      (url) => url.pathname === '/api/trades' && url.searchParams.get('page_size') === '100',
      (route) => route.fulfill({
        json: { items: [], total: 0, total_groups: 0, total_pages: 0, page: 1, page_size: 100 },
      }),
    );
    await page.goto('/dashboard');

    const agenda = page.getByTestId('dashboard-pending-agenda');
    await expect(agenda).toContainText('2 笔待确认');
    await expect(agenda).toContainText('当前列表未显示待确认明细，请前往组合页查看');
    await expect(agenda.getByTestId('dashboard-pending-item')).toHaveCount(0);
  });
});
