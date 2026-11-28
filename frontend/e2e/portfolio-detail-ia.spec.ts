import { test, expect, type Page } from '@playwright/test';
import {
  createPortfolioViaApi,
  E2E_ACTIVE,
  gotoPortfolioDetail,
  isolatedPortfolioCode,
  openAppAndAuth,
} from './helpers';

async function expectSectionOrder(page: Page, expected: string[]) {
  const selector = expected.map((id) => `[data-testid="${id}"]`).join(',');
  const actual = await page.locator(selector).evaluateAll((elements) =>
    elements.map((element) => element.getAttribute('data-testid')),
  );
  expect(actual).toEqual(expected);
  for (const id of expected) {
    await expect(page.getByTestId(id)).toBeVisible();
  }
}

test('活跃组合详情按资产与净值、持仓、绩效、入口、口径卡顺序展开', async ({
  page,
}, testInfo) => {
  await gotoPortfolioDetail(page, E2E_ACTIVE);
  const expected = [
    'portfolio-detail-stats',
    'portfolio-detail-assets',
    'portfolio-detail-nav',
    'portfolio-detail-holdings',
    'portfolio-detail-performance',
    ...(testInfo.project.name === 'chromium' ? ['portfolio-detail-links'] : []),
    'portfolio-info-card',
  ];
  await expectSectionOrder(page, expected);
});

test('组合口径卡只读展示成立日期、类型、状态与代码，桌面入口沿用既有路由', async ({
  page,
}, testInfo) => {
  await gotoPortfolioDetail(page, E2E_ACTIVE);
  const card = page.getByTestId('portfolio-info-card');
  await expect(card).toBeVisible();
  await expect(card.getByText('成立日期', { exact: true }).locator('..')).toContainText(
    /\d{4}-\d{2}-\d{2}|--/,
  );
  await expect(card.getByText('组合类型', { exact: true }).locator('..')).toContainText('净值型');
  await expect(card.getByText('组合状态', { exact: true }).locator('..')).toContainText('活跃');
  await expect(card.getByText('组合代码', { exact: true }).locator('..')).toContainText(E2E_ACTIVE);
  await expect(card.getByRole('link')).toHaveCount(0);

  if (testInfo.project.name === 'chromium') {
    const links = page.getByTestId('portfolio-detail-links');
    const investors = links.getByRole('link', { name: '投资人', exact: true });
    const trades = links.getByRole('link', { name: '交易记录', exact: true });
    await expect(investors).toHaveAttribute('href', `/portfolio/${E2E_ACTIVE}?tab=investors`);
    await expect(trades).toHaveAttribute('href', `/portfolio/${E2E_ACTIVE}/trades`);
    await investors.click();
    await expect(page).toHaveURL(/[?&]tab=investors/);
    await expect(
      page.getByTestId('portfolio-investors-table').or(page.getByTestId('portfolio-investors-cards')),
    ).toBeVisible();
  } else {
    await expect(page.getByTestId('portfolio-detail-links')).toHaveCount(0);
  }
});

test('草稿组合不伪造成立日期或空持仓段，只保留口径卡', async ({ page }, testInfo) => {
  const headers = await openAppAndAuth(page);
  const code = isolatedPortfolioCode('E944IA', testInfo);
  await createPortfolioViaApi(page, headers, code);
  await page.goto(`/portfolio/${code}`);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(`E2E 夹具组合 ${code}`);

  const card = page.getByTestId('portfolio-info-card');
  await expect(card).toBeVisible();
  await expect(card.getByText('成立日期', { exact: true }).locator('..')).toContainText('--');
  await expect(card.getByText('组合状态', { exact: true }).locator('..')).toContainText('草稿');
  await expect(card.getByText('组合代码', { exact: true }).locator('..')).toContainText(code);
  await expect(page.getByTestId('portfolio-detail-holdings')).toHaveCount(0);
  await expect(page.getByTestId('portfolio-detail-assets')).toHaveCount(0);
  await expect(page.getByTestId('portfolio-detail-nav')).toHaveCount(0);
  await expect(page.getByTestId('portfolio-detail-performance')).toHaveCount(0);
  if (testInfo.project.name === 'chromium') {
    await expect(page.getByTestId('portfolio-detail-links').getByRole('link', { name: '投资人' })).toBeVisible();
    await expect(page.getByTestId('portfolio-detail-links').getByRole('link', { name: '交易记录' })).toHaveCount(0);
  } else {
    await expect(page.getByTestId('portfolio-detail-stats')).toHaveCount(0);
    await expect(page.getByTestId('portfolio-detail-links')).toHaveCount(0);
  }
});
