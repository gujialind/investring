import { expect, test, type Page } from '@playwright/test';
import {
  E2E_ACTIVE,
  collectPageErrors,
  createPortfolioViaApi,
  gotoPortfolioDetail,
  isolatedPortfolioCode,
  openAppAndAuth,
} from './helpers';

type SnapshotItem = {
  snapshot_date: string;
  unit_price: number;
  total_shares: number;
  total_value: number;
  in_transit_total: number;
};

const PREVIOUS = '2026-11-24';
const LATEST = '2026-11-26';

function snapshot(snapshotDate: string, unitPrice: number): SnapshotItem {
  return {
    snapshot_date: snapshotDate,
    unit_price: unitPrice,
    total_shares: 100_000,
    total_value: 100_000 * unitPrice,
    in_transit_total: 0,
  };
}

async function mockSnapshotReads(
  page: Page,
  items: SnapshotItem[],
  missingDates: string[] = [],
  listStatus: number | (() => number) = 200,
  statusLatestDate: string | undefined = items[0]?.snapshot_date,
) {
  await page.route(/\/api\/snapshots\/portfolios\/[^/]+\/list(?:\?|$)/, async (route) => {
    const url = new URL(route.request().url());
    const status = typeof listStatus === 'function' ? listStatus() : listStatus;
    expect(url.searchParams.get('limit')).toBe('2');
    await route.fulfill({
      status,
      json:
        status === 200
          ? {
              portfolio_code: E2E_ACTIVE,
              items,
              total: items.length,
              limit: 2,
            }
          : { detail: '快照读取失败' },
    });
  });
  await page.route(/\/api\/snapshots\/portfolios\/[^/]+\/status(?:\?|$)/, (route) =>
    route.fulfill({
      status: 200,
      json: {
        portfolio_code: E2E_ACTIVE,
        latest_snapshot_date: statusLatestDate,
        total_snapshots: items.length,
        first_snapshot_date: items.at(-1)?.snapshot_date,
        missing_dates: missingDates,
        catchup_pending_dates: [],
        auto_snapshot_enabled: true,
      },
    }),
  );
}

async function expectHeaderDate(page: Page, date: string) {
  await expect(page.getByTestId('page-header')).toContainText(`最新快照日期 ${date}`);
  await expect(page.getByText(/最新快照日期：/)).toHaveCount(0);
}

test.describe('组合详情页头快照日期与百分比标签（#943 S3）', () => {
  for (const scenario of [
    { name: '正收益', previous: 1, latest: 1.0123, nav: '1.0123', change: '+1.23%' },
    { name: '负收益', previous: 1, latest: 0.9875, nav: '0.9875', change: '-1.25%' },
    { name: '真实零收益', previous: 1, latest: 1, nav: '1.0000', change: '0.00%' },
  ]) {
    test(`${scenario.name}：页头日期与净值日涨跌同源`, async ({ page }) => {
      const errors = collectPageErrors(page);
      await mockSnapshotReads(page, [
        snapshot(LATEST, scenario.latest),
        snapshot(PREVIOUS, scenario.previous),
      ]);
      await gotoPortfolioDetail(page, E2E_ACTIVE);

      await expectHeaderDate(page, LATEST);
      await expect(page.getByTestId('portfolio-stat-nav')).toHaveText(scenario.nav);
      const daily = page.getByTestId('portfolio-stat-daily-return-chip');
      await expect(daily).toHaveText(scenario.change);
      await expect(daily).toHaveAttribute('aria-label', `快照日涨跌率 ${scenario.change}`);
      await expect(daily).toHaveAttribute('role', 'img');
      expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
    });
  }

  test('无快照：日期占位且不伪造 0.00%', async ({ page }) => {
    await mockSnapshotReads(page, []);
    await gotoPortfolioDetail(page, E2E_ACTIVE);

    await expectHeaderDate(page, '--');
    await expect(page.getByTestId('portfolio-stat-nav')).toHaveText('--');
    await expect(page.getByTestId('portfolio-stat-daily-return-chip')).toHaveCount(0);
  });

  test('单快照：显示最新日期与净值但不显示日涨跌', async ({ page }) => {
    await mockSnapshotReads(page, [snapshot(LATEST, 1.0123)]);
    await gotoPortfolioDetail(page, E2E_ACTIVE);

    await expectHeaderDate(page, LATEST);
    await expect(page.getByTestId('portfolio-stat-nav')).toHaveText('1.0123');
    await expect(page.getByTestId('portfolio-stat-daily-return-chip')).toHaveCount(0);
  });

  test('比较区间有缺口：不把多日变动当成日涨跌', async ({ page }) => {
    await mockSnapshotReads(
      page,
      [snapshot(LATEST, 1.0123), snapshot(PREVIOUS, 1)],
      ['2026-11-25'],
    );
    await gotoPortfolioDetail(page, E2E_ACTIVE);

    await expectHeaderDate(page, LATEST);
    await expect(page.getByTestId('portfolio-stat-nav')).toHaveText('1.0123');
    await expect(page.getByTestId('portfolio-stat-daily-return-chip')).toHaveCount(0);
  });

  test('list 与 status 最新日期不一致：保守不显示日涨跌', async ({ page }) => {
    await mockSnapshotReads(
      page,
      [snapshot(LATEST, 1.0123), snapshot(PREVIOUS, 1)],
      [],
      200,
      PREVIOUS,
    );
    await gotoPortfolioDetail(page, E2E_ACTIVE);

    await expectHeaderDate(page, LATEST);
    await expect(page.getByTestId('portfolio-stat-nav')).toHaveText('1.0123');
    await expect(page.getByTestId('portfolio-stat-daily-return-chip')).toHaveCount(0);
  });

  test('draft：不读取快照，查询失败也不会阻挡首次申购激活', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E943D', testInfo);
    await createPortfolioViaApi(page, headers, code);
    const snapshotReads: string[] = [];
    await page.route(/\/api\/snapshots\/portfolios\/[^/]+\/(?:list|status)(?:\?|$)/, (route) => {
      snapshotReads.push(route.request().url());
      return route.abort();
    });
    await page.goto(`/portfolio/${code}`);
    await page.getByRole('heading', { level: 1 }).first().waitFor({ state: 'visible' });

    await expect(page.getByRole('button', { name: '首次申购激活' })).toBeVisible();
    await expectHeaderDate(page, '--');
    expect(snapshotReads).toEqual([]);
  });

  test('台账 draft：不读取快照，不显示快照日期或日涨跌', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E943L', testInfo);
    const response = await page.request.post('/api/portfolios', {
      headers,
      data: { code, name: `台账夹具 ${code}`, kind: 'ledger', owner_investor_code: 'ADMIN' },
    });
    expect(response.ok(), `创建台账组合 ${response.status()} ${await response.text()}`).toBeTruthy();
    let snapshotReads = 0;
    await page.route(/\/api\/snapshots\/portfolios\/[^/]+\/(?:list|status)(?:\?|$)/, (route) => {
      snapshotReads += 1;
      return route.abort();
    });
    await page.goto(`/portfolio/${code}`);
    await page.getByRole('heading', { level: 1 }).first().waitFor({ state: 'visible' });

    await expect(page.getByTestId('ledger-activate')).toBeVisible();
    await expect(page.getByTestId('page-header')).not.toContainText('最新快照日期');
    await expect(page.getByTestId('portfolio-stat-daily-return-chip')).toHaveCount(0);
    expect(snapshotReads).toBe(0);
  });

  test('快照列表失败：显示查询失败态而非空快照', async ({ page }, testInfo) => {
    await mockSnapshotReads(page, [], [], 500);
    await page.goto('/portfolio/E2E_ACTIVE');

    await expect(page.getByText('加载失败：快照读取失败')).toBeVisible();
    await expect(page.getByRole('button', { name: '重试' })).toBeVisible();
    await expect(page.getByTestId('portfolio-stat-daily-return-chip')).toHaveCount(0);
    await expect(page.getByTestId('page-header')).toHaveCount(0);
    if (testInfo.project.name === 'mobile') {
      await expect(page.getByRole('link', { name: '返回列表' })).toHaveAttribute(
        'href',
        '/m/portfolio',
      );
    }
  });

  for (const scenario of [
    {
      name: '有快照',
      items: [snapshot(LATEST, 1.0123), snapshot(PREVIOUS, 1)],
      date: LATEST,
      nav: '1.0123',
      change: '+1.23%',
    },
    { name: '空快照', items: [], date: '--', nav: '--', change: null },
  ]) {
    test(`后台刷新失败（${scenario.name}）：保留详情并显示可重试失败态`, async ({ page }) => {
      await page.clock.install();
      let failList = false;
      await mockSnapshotReads(page, scenario.items, [], () => (failList ? 500 : 200));
      await gotoPortfolioDetail(page, E2E_ACTIVE);
      await expectHeaderDate(page, scenario.date);
      await expect(page.getByTestId('portfolio-stat-nav')).toHaveText(scenario.nav);
      if (scenario.change) {
        await expect(page.getByTestId('portfolio-stat-daily-return-chip')).toHaveText(scenario.change);
      } else {
        await expect(page.getByTestId('portfolio-stat-daily-return-chip')).toHaveCount(0);
      }

      failList = true;
      await page.clock.fastForward(31_000);
      await page.getByRole('link', { name: '返回组合列表', exact: true }).click();
      await page.waitForURL(/\/(m\/)?portfolio$/);
      const basePath = new URL(page.url()).pathname.startsWith('/m/') ? '/m' : '';
      await page.locator(`a[href="${basePath}/portfolio/${E2E_ACTIVE}"]`).first().click();
      await page.waitForURL(/\/(m\/)?portfolio\/E2E_ACTIVE$/);

      await expect(page.getByText('加载失败：快照读取失败')).toBeVisible();
      await expectHeaderDate(page, scenario.date);
      await expect(page.getByTestId('portfolio-stat-nav')).toHaveText(scenario.nav);
      await expect(page.getByTestId('portfolio-stat-daily-return-chip')).toHaveCount(0);

      failList = false;
      await page.getByRole('button', { name: '重试' }).click();
      await expect(page.getByText('加载失败：快照读取失败')).toHaveCount(0);
      if (scenario.change) {
        await expect(page.getByTestId('portfolio-stat-daily-return-chip')).toHaveText(scenario.change);
      } else {
        await expect(page.getByTestId('portfolio-stat-daily-return-chip')).toHaveCount(0);
      }
    });
  }
});
