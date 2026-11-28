import { test, expect, type Page } from '@playwright/test';
import {
  collectPageErrors,
  E2E_ACTIVE,
  gotoPortfolioDetail,
  portfolioPath,
} from './helpers';

const PRODUCT_PATH = `/portfolio/${E2E_ACTIVE}/product/510300.SH?market=CN_EXCHANGE`;
const CASH_PRODUCT_PATH = `/portfolio/${E2E_ACTIVE}/product/CASH?market=`;
const RETURN_FOCUS_KEY = 'investring:mobile-product-return:E2E_ACTIVE';
const PRODUCT_RETURN_VALUE = '510300.SH-CN_EXCHANGE';

function productRow(page: Page) {
  return page
    .getByTestId('portfolio-holdings')
    .locator('[data-product-code="510300.SH"]');
}

function readReturnFocus(page: Page) {
  return page.evaluate((key) => sessionStorage.getItem(key), RETURN_FOCUS_KEY);
}

test('移动产品聚合行保留字段但移除卡壳，桌面卡片不变（#944 Phase 6.2）', async ({
  page,
}, testInfo) => {
  await gotoPortfolioDetail(page, E2E_ACTIVE);
  const row = productRow(page);
  const card = page.getByTestId('holding-product-card').filter({ hasText: '沪深300ETF' });
  const cash = page.getByTestId('holding-product-card').filter({ hasText: '可用现金' });

  await expect(row).toBeVisible();
  await expect(row).toHaveAttribute('data-product-code', '510300.SH');
  await expect(row).toHaveAttribute('data-product-key', PRODUCT_RETURN_VALUE);
  await expect(card).toContainText('510300 · A股场内');
  await expect(card).toContainText(/占比\s+\d+\.\d%/);
  await expect(card).toContainText(/最新收益\(\d{2}\/\d{2}\)/);
  await expect(card).not.toContainText('华宝证券');
  await expect(card.getByTestId('total-profit-value')).toBeVisible();
  await expect(card.getByTestId('daily-profit-value')).toBeVisible();
  await expect(cash.getByText('可用现金', { exact: true })).toBeVisible();
  await expect(cash).not.toContainText('华宝证券');

  const groupCards = page.getByTestId('asset-group-cards').first();
  if (testInfo.project.name === 'mobile') {
    await expect(card).toHaveCSS('border-top-width', '0px');
    await expect(card).toHaveCSS('border-radius', '0px');
    await expect(row).toHaveCSS('border-top-left-radius', '0px');
    await expect(groupCards).toHaveCSS('display', 'block');
    for (const width of [320, 390, 430]) {
      await page.setViewportSize({ width, height: 844 });
      await expect(card).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      const offenders = await page.evaluate(() =>
        [...document.querySelectorAll('body *')]
          .map((element) => {
            const rect = element.getBoundingClientRect();
            return {
              tag: element.tagName,
              className: element.className,
              text: element.textContent?.trim().slice(0, 40),
              left: rect.left,
              right: rect.right,
            };
          })
          .filter((item) => item.right > window.innerWidth + 1 || item.left < -1)
          .slice(0, 8),
      );
      expect(overflow, `移动产品行在 ${width}px 不得撑宽页面：${JSON.stringify(offenders)}`).toBeLessThanOrEqual(1);
    }
  } else {
    await expect(card).toHaveCSS('border-top-width', '1px');
    await expect(groupCards).toHaveCSS('display', 'grid');
  }
});

test('产品行 Enter 往返恢复焦点，移动详情保留底栏且不污染桌面会话（#944 Phase 6.2）', async ({
  page,
}, testInfo) => {
  await gotoPortfolioDetail(page, E2E_ACTIVE);
  await productRow(page).focus();
  if (testInfo.project.name === 'mobile') {
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  }
  await page.keyboard.press('Enter');
  await page.waitForURL(/\/product\/510300\.SH\?market=CN_EXCHANGE/);

  const bottomNav = page.getByTestId('bottom-nav');
  if (testInfo.project.name === 'mobile') {
    const back = page.getByRole('link', { name: '返回组合详情', exact: true });
    await expect(back).toBeFocused();
    await expect(bottomNav).toBeVisible();
    expect(await readReturnFocus(page)).toBe(PRODUCT_RETURN_VALUE);

    await back.click();
    await page.waitForURL(/\/portfolio\/E2E_ACTIVE$/);
    await expect(productRow(page)).toBeFocused();
    const headerBox = await page.getByTestId('page-header').boundingBox();
    const rowBox = await productRow(page).boundingBox();
    expect(headerBox).not.toBeNull();
    expect(rowBox).not.toBeNull();
    expect(rowBox!.y).toBeGreaterThanOrEqual(headerBox!.y + headerBox!.height);
    expect(await readReturnFocus(page)).toBeNull();
  } else {
    await expect(bottomNav).not.toBeVisible();
    expect(await readReturnFocus(page)).toBeNull();
  }
});

test('移动产品详情经底栏离开时清除未消费的返回焦点键（#944 Phase 6.2）', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', '仅移动产品详情保留底栏');
  await gotoPortfolioDetail(page, E2E_ACTIVE);
  await productRow(page).click();
  await page.waitForURL(/\/product\/510300\.SH\?market=CN_EXCHANGE/);
  expect(await readReturnFocus(page)).toBe(PRODUCT_RETURN_VALUE);

  await page
    .getByTestId('bottom-nav')
    .getByRole('link', { name: '首页', exact: true })
    .click();
  await page.waitForURL(/\/m\/dashboard$/);
  expect(await readReturnFocus(page)).toBeNull();
});

test('移动产品详情深链聚焦返回 crumb，末操作不被底栏遮挡（#944 Phase 6.2）', async ({
  page,
}, testInfo) => {
  await page.goto(PRODUCT_PATH);
  const actionRow = page.getByTestId('product-action-row');
  await expect(actionRow).toBeVisible();

  if (testInfo.project.name === 'mobile') {
    await expect(page.getByRole('link', { name: '返回组合详情', exact: true })).toBeFocused();
    const navBox = await page.getByTestId('bottom-nav').boundingBox();
    const actionBox = await actionRow.boundingBox();
    expect(navBox).not.toBeNull();
    expect(actionBox).not.toBeNull();
    expect(actionBox!.y + actionBox!.height).toBeLessThan(navBox!.y);
  } else {
    await expect(page).toHaveURL(portfolioPath(E2E_ACTIVE) + '/product/510300.SH?market=CN_EXCHANGE');
  }
});

test('现金产品详情保留底栏且不渲染净值卡（#944 Phase 6.2）', async ({
  page,
}, testInfo) => {
  await page.goto(CASH_PRODUCT_PATH);
  const actionRow = page.getByTestId('product-action-row');
  await expect(actionRow.getByRole('button', { name: '转入', exact: true })).toBeVisible();
  await expect(actionRow.getByRole('button', { name: '转出', exact: true })).toBeVisible();
  await expect(page.getByText('累计净值走势', { exact: true })).toHaveCount(0);

  if (testInfo.project.name === 'mobile') {
    await expect(page.getByRole('link', { name: '返回组合详情', exact: true })).toBeFocused();
    await expect(page.getByTestId('bottom-nav')).toBeVisible();
  }
});

test('存储不可用不阻断产品行跳转或持仓页（#944 Phase 6.2）', async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.addInitScript(() => {
    const storage = window.sessionStorage;
    for (const method of ['setItem', 'getItem', 'removeItem'] as const) {
      Object.defineProperty(storage, method, {
        configurable: true,
        value: () => {
          throw new DOMException('blocked', 'SecurityError');
        },
      });
    }
  });

  await gotoPortfolioDetail(page, E2E_ACTIVE);
  await expect(productRow(page)).toBeVisible();
  await productRow(page).click();
  await page.waitForURL(/\/product\/510300\.SH\?market=CN_EXCHANGE/);
  await expect(page.getByTestId('product-action-row')).toBeVisible();
  expect(errors).toHaveLength(0);
});

test('同码多市场返回焦点落在原市场行（#944 Phase 6.2）', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', '返回焦点仅移动端启用');
  await page.route(
    `**/api/positions/portfolio/${E2E_ACTIVE}/holdings/by-product`,
    async (route) => {
      const response = await route.fetch();
      const data = (await response.json()) as { products: Record<string, unknown>[] };
      const original = data.products.find(
        (item) => item.product_code === '510300.SH' && item.market === 'CN_EXCHANGE',
      );
      expect(original).toBeDefined();
      data.products.push({
        ...original,
        market: 'CN_OTC',
        product_name: '沪深300ETF（OTC）',
      });
      await route.fulfill({ response, json: data });
    },
  );

  await gotoPortfolioDetail(page, E2E_ACTIVE);
  const rows = page.getByTestId('portfolio-holdings').locator('[data-product-code="510300.SH"]');
  await expect(rows).toHaveCount(2);
  const secondRow = rows.nth(1);
  await expect(secondRow).toHaveAttribute('data-product-key', '510300.SH-CN_OTC');
  await secondRow.focus();
  await page.keyboard.press('Enter');
  await page.waitForURL(/\/product\/510300\.SH\?market=CN_OTC/);
  await page.goBack();
  await page.waitForURL(/\/portfolio\/E2E_ACTIVE$/);
  await expect(rows.nth(1)).toBeFocused();
  expect(await readReturnFocus(page)).toBeNull();
});
