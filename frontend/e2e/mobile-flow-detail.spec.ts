import { test, expect } from '@playwright/test';
import { E2E_ACTIVE, gotoPortfolioSubpage } from './helpers';

test('移动流水核对明细支持键盘展开、保持阅读位置且刷新不折叠（#944 D1）', async ({ page }, testInfo) => {
  await gotoPortfolioSubpage(page, E2E_ACTIVE, 'trades');

  if (testInfo.project.name !== 'mobile') {
    await expect(page.locator('table')).toBeVisible();
    await expect(page.getByTestId('trade-card-list')).toHaveCount(0);
    await expect(page.getByTestId('trade-card-details')).toHaveCount(0);
    return;
  }

  const card = page.getByTestId('trade-card').filter({ hasText: '现金扣款' }).first();
  await expect(card).toBeVisible();
  await expect(card.getByText('金额', { exact: true })).toBeVisible();
  await expect(card.getByTestId('trade-cash-subrow')).toBeVisible();
  await expect(card.getByText('交易日期', { exact: true })).toBeVisible();

  const trigger = card.getByTestId('trade-card-details');
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(trigger).not.toHaveAttribute('aria-controls');
  await expect(card.getByTestId('trade-card-shares')).toHaveCount(0);
  const box = await trigger.boundingBox();
  expect(box?.height, '展开触发器触控高度不得小于 44px').toBeGreaterThanOrEqual(44);

  await trigger.focus();
  const before = await trigger.evaluate((element) => ({
    top: element.getBoundingClientRect().top,
    scrollY: window.scrollY,
  }));
  await page.keyboard.press('Enter');
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  await expect(card.getByTestId('trade-card-shares')).toBeVisible();
  await expect(card.getByTestId('trade-card-price')).toBeVisible();
  await expect(card.getByText('手续费', { exact: true })).toBeVisible();

  const controls = await trigger.getAttribute('aria-controls');
  expect(controls).toBeTruthy();
  await expect(card.locator(`[id="${controls}"]`)).toBeVisible();
  const after = await trigger.evaluate((element) => ({
    top: element.getBoundingClientRect().top,
    scrollY: window.scrollY,
  }));
  expect(Math.abs(after.top - before.top), '展开内容不得推动触发器离开原阅读位置').toBeLessThanOrEqual(1);
  expect(after.scrollY).toBe(before.scrollY);

  await page.clock.install();
  await page.clock.fastForward(31_000);
  await page.context().setOffline(true);
  const refetch = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/trades' && request.method() === 'GET',
  );
  await page.context().setOffline(false);
  await refetch;
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  await expect(card.getByTestId('trade-card-shares')).toBeVisible();

  await page.keyboard.press('Enter');
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(trigger).not.toHaveAttribute('aria-controls');
  await expect(card.getByTestId('trade-card-shares')).toHaveCount(0);
});
