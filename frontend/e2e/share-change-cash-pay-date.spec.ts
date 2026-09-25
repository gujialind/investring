/**
 * #522：双端现金分红到账日录入、编辑与回显。
 * 自建组合按 project/retry/场景区分；每轮使用新种子库，不推进共享 E2E_ACTIVE。
 * 预览夹具复用种子 000300.OF 的 D/D+1 净值，仅在自有组合生成两日快照。
 */
import { test, expect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { addDays, format, nextSaturday, parseISO } from 'date-fns';
import type { ShareChangeEvent } from '../src/types/share-change-event';
import {
  E2E_PORT,
  authHeaders,
  collectPageErrors,
  dialogByTitle,
  gotoPortfolioSubpage,
  openPopover,
  pickPlatformOption,
  platformPopover,
  productOption,
  productPopover,
  settlePopovers,
  type PortfolioCode,
} from './helpers';

const PRODUCT = { code: '000300.OF', market: 'CN_OTC' };
const PLATFORM = 'HBZQ';
type Headers = { Authorization: string };

async function post<T = unknown>(page: Page, headers: Headers, path: string, data?: unknown): Promise<T> {
  const response = await page.request.post(path, { headers, data });
  await expect(response).toBeOK();
  return response.json();
}

async function setupPortfolio(page: Page, testInfo: TestInfo, scenario: string) {
  await gotoPortfolioSubpage(page, E2E_PORT, 'share-change-events');
  const headers = await authHeaders(page);
  const today = format(new Date(), 'yyyy-MM-dd');
  const year = Number(today.slice(0, 4));
  const calendars = await Promise.all([year - 1, year, year + 1].map(async (y) => {
    const response = await page.request.get(`/api/trading-calendar?year=${y}`, { headers });
    await expect(response).toBeOK();
    return await response.json() as { calendar_date: string; is_open: boolean }[];
  }));
  const days = calendars.flat().filter((d) => d.is_open).map((d) => d.calendar_date).sort();
  const day = days.filter((d) => d <= today).at(-1);
  expect(day, '种子日历须覆盖今天之前的最近交易日').toBeTruthy();
  const index = days.indexOf(day!);
  expect(index).toBeGreaterThan(0);
  expect(days.length).toBeGreaterThan(index + 2);
  const [entitlement, ex] = [days[index + 1], days[index + 2]];
  const weekend = format(nextSaturday(parseISO(ex)), 'yyyy-MM-dd');
  const code = `E522_${testInfo.project.name}_${testInfo.retry}_${scenario}`;
  expect(code.length).toBeLessThanOrEqual(20);
  await post(page, headers, '/api/portfolios', { code, name: `分红到账日 ${code}` });
  return { code, headers, day: day!, apply: days[index - 1], entitlement, ex, weekend };
}

type Scenario = Awaited<ReturnType<typeof setupPortfolio>>;

async function seedHolding(page: Page, s: Scenario) {
  const subscription = await post<{ id: number; confirm_date: string }>(page, s.headers, '/api/subscriptions', {
    portfolio_code: s.code,
    investor_code: 'ADMIN',
    platform_code: PLATFORM,
    sub_type: 'subscribe',
    amount: 10000,
    apply_date: s.apply,
  });
  expect(subscription.confirm_date).toBe(s.day);
  await post(page, s.headers, `/api/subscriptions/${subscription.id}/confirm`);
  const trade = await post<{ id: number }>(page, s.headers, '/api/trades', {
    portfolio_code: s.code,
    product_code: PRODUCT.code,
    market: PRODUCT.market,
    platform_code: PLATFORM,
    trade_type: 'buy',
    trade_date: s.day,
    amount: 1500,
    fee: 0,
  });
  await post(page, s.headers, `/api/trades/${trade.id}/confirm`);
  for (const target of [s.day, s.entitlement]) {
    const result = await post<{ success: boolean }>(page, s.headers, '/api/snapshots/generate', {
      portfolio_code: s.code,
      target_date: target,
    });
    expect(result.success).toBe(true);
  }
}

async function pickDay(page: Page, trigger: Locator, target: string) {
  const current = (await trigger.textContent())?.trim();
  await openPopover(page, trigger, page.locator('button.rdp-day_button').first());
  const day = page.locator(`button.rdp-day_button[data-day="${target}"]`);
  if ((await day.count()) === 0) {
    const from = current && /^\d{4}-\d{2}-\d{2}$/.test(current) ? current : format(new Date(), 'yyyy-MM-dd');
    const direction = target > from ? 'next' : 'previous';
    await page.locator(`button.rdp-button_${direction}`).click();
  }
  await expect(day).toBeEnabled();
  await day.click();
  await settlePopovers(page);
  await expect(trigger).toHaveText(target);
}

async function openCreate(page: Page, s: Scenario) {
  await gotoPortfolioSubpage(page, s.code as PortfolioCode, 'share-change-events');
  await page.getByRole('button', { name: '新建事件' }).click();
  const dlg = dialogByTitle(page, '新建份额变动事件');
  await openPopover(page, dlg.getByLabel('产品代码', { exact: true }), productPopover(page));
  const products = productPopover(page);
  await products.getByPlaceholder('搜索产品代码/名称').fill(PRODUCT.code);
  await productOption(products, PRODUCT.code, PRODUCT.market).click();
  await settlePopovers(page);
  await openPopover(page, dlg.getByLabel('平台', { exact: true }), platformPopover(page));
  await pickPlatformOption(platformPopover(page), PLATFORM);
  await pickDay(page, dlg.getByLabel('权益登记日', { exact: true }), s.entitlement);
  await pickDay(page, dlg.getByLabel('除息日', { exact: true }), s.ex);
  await dlg.getByLabel('每份分红金额（元）', { exact: true }).fill('0.5');
  return dlg;
}

async function submitEvent(page: Page, dlg: Locator, id?: number) {
  const method = id === undefined ? 'POST' : 'PUT';
  const path = id === undefined ? '/api/share-change-events' : `/api/share-change-events/${id}`;
  const [response] = await Promise.all([
    page.waitForResponse((r) => r.request().method() === method && new URL(r.url()).pathname === path),
    dlg.getByRole('button', { name: id === undefined ? '创建' : '保存', exact: true }).click(),
  ]);
  expect(response.ok(), `${method} ${path}: ${await response.text()}`).toBeTruthy();
  await expect(dlg).toBeHidden();
  return {
    event: await response.json() as ShareChangeEvent,
    payload: response.request().postDataJSON() as Record<string, unknown>,
  };
}

async function expectStoredDate(page: Page, s: Scenario, id: number, date: string | null) {
  const response = await page.request.get(`/api/share-change-events/${id}`, { headers: s.headers });
  await expect(response).toBeOK();
  expect((await response.json()).cash_pay_date).toBe(date);
}

async function expectPreview(page: Page, row: Locator, id: number, date: string, isDefault = false) {
  const [response] = await Promise.all([
    page.waitForResponse((r) => new URL(r.url()).pathname === `/api/share-change-events/${id}/preview`),
    row.getByTitle('确认', { exact: true }).click(),
  ]);
  expect(response.ok(), await response.text()).toBeTruthy();
  const { preview } = await response.json();
  expect(Number(preview.cash_change)).toBe(500);
  const dlg = dialogByTitle(page, '确认份额变动事件');
  await expect(dlg.getByText('¥500.00', { exact: true })).toBeVisible();
  await expect(dlg.getByText('现金到账日', { exact: true })).toBeVisible();
  await expect(dlg.getByText(date, { exact: true })).toHaveCount(isDefault ? 2 : 1);
  await expect(dlg.getByText('（默认除息日）', { exact: true })).toHaveCount(isDefault ? 1 : 0);
  await expect(dlg.getByRole('button', { name: '确认', exact: true })).toBeEnabled();
  await dlg.getByRole('button', { name: '取消', exact: true }).click();
  await expect(dlg).toBeHidden();
}

test.describe('现金分红到账日（#522，双端）', () => {
  test('周末到账日创建、编辑、显式清空与预览回显', async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const errors = collectPageErrors(page);
    const s = await setupPortfolio(page, testInfo, 'A');
    await seedHolding(page, s);
    const create = await openCreate(page, s);
    await pickDay(page, create.getByLabel('现金到账日（可选）'), s.weekend);
    await expect(create.getByTestId('cash-pay-date-field')).toContainText(`有效到账日：${s.weekend}`);
    const { event, payload } = await submitEvent(page, create);
    expect(payload.cash_pay_date).toBe(s.weekend);

    try {
      await expectStoredDate(page, s, event.id, s.weekend);
      const row = page.locator('table tbody tr').filter({ hasText: '现金分红' });
      await expect(row.getByTestId('event-cash-pay-date')).toHaveText(s.weekend);
      // 创建表单显式提交 shares_change/cash_change=0（既有表单行为），故为 0 值而非
      // #424 的 NULL「--」形态；REST 直造 NULL 行由 mobile-share-change-events.spec.ts 断言。
      await expect(row.getByTestId('event-shares-change')).toHaveText('0.00 份');
      await expect(row.getByTestId('event-cash-change')).toHaveText('¥0.00');
      await expectPreview(page, row, event.id, s.weekend);

      await row.getByTitle('编辑', { exact: true }).click();
      const edit = dialogByTitle(page, '编辑份额变动事件');
      await expect(edit.getByLabel('现金到账日（可选）')).toHaveText(s.weekend);
      const later = format(addDays(parseISO(s.weekend), 1), 'yyyy-MM-dd');
      await pickDay(page, edit.getByLabel('现金到账日（可选）'), later);
      const updated = await submitEvent(page, edit, event.id);
      expect(updated.payload.cash_pay_date).toBe(later);
      await expectStoredDate(page, s, event.id, later);
      await expect(row.getByTestId('event-cash-pay-date')).toHaveText(later);

      await row.getByTitle('编辑', { exact: true }).click();
      await expect(edit.getByLabel('现金到账日（可选）')).toHaveText(later);
      await edit.getByTestId('cash-pay-date-field').getByRole('button', { name: '清除日期' }).click();
      await expect(edit.getByLabel('现金到账日（可选）')).toHaveText('默认除息日');
      await expect(edit.getByTestId('cash-pay-date-field')).toContainText(`有效到账日：${s.ex}（默认除息日）`);
      const cleared = await submitEvent(page, edit, event.id);
      expect(cleared.payload).toHaveProperty('cash_pay_date', null);
      await expectStoredDate(page, s, event.id, null);
      await expect(row.getByTestId('event-cash-pay-date')).toHaveText(`${s.ex}默认除息日`);
      await expectPreview(page, row, event.id, s.ex, true);

      await row.getByTitle('编辑', { exact: true }).click();
      await expect(edit.getByLabel('现金到账日（可选）')).toHaveText('默认除息日');
      await edit.getByRole('button', { name: '取消', exact: true }).click();
      expect(errors).toEqual([]);
    } finally {
      const response = await page.request.delete(`/api/share-change-events/${event.id}`, { headers: s.headers });
      await expect(response).toBeOK();
    }
  });

  test('默认到账日创建及非现金类型显隐、切换后不提交遗留日期', async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const s = await setupPortfolio(page, testInfo, 'B');
    const create = await openCreate(page, s);
    const createdIds: number[] = [];
    try {
      await expect(create.getByLabel('现金到账日（可选）')).toHaveText('默认除息日');
      const defaultEvent = await submitEvent(page, create);
      createdIds.push(defaultEvent.event.id);
      expect(defaultEvent.payload).not.toHaveProperty('cash_pay_date');
      await expectStoredDate(page, s, defaultEvent.event.id, null);
      const cashRow = page.locator('table tbody tr').filter({ hasText: '现金分红' });
      await expect(cashRow.getByTestId('event-cash-pay-date')).toHaveText(`${s.ex}默认除息日`);

      const nextCreate = await openCreate(page, s);
      for (const label of ['分红再投资', '份额拆分', '份额合并', '红股送股', '强制调整']) {
        await pickDay(page, nextCreate.getByLabel('现金到账日（可选）'), s.weekend);
        await openPopover(page, nextCreate.getByLabel('事件类型', { exact: true }), page.getByRole('listbox'));
        await page.getByRole('option', { name: label, exact: true }).click();
        await settlePopovers(page);
        await expect(nextCreate.getByTestId('cash-pay-date-field')).toHaveCount(0);
        await openPopover(page, nextCreate.getByLabel('事件类型', { exact: true }), page.getByRole('listbox'));
        await page.getByRole('option', { name: '现金分红', exact: true }).click();
        await settlePopovers(page);
        await expect(nextCreate.getByLabel('现金到账日（可选）')).toHaveText('默认除息日');
      }
      await pickDay(page, nextCreate.getByLabel('现金到账日（可选）'), s.weekend);
      await openPopover(page, nextCreate.getByLabel('事件类型', { exact: true }), page.getByRole('listbox'));
      await page.getByRole('option', { name: '分红再投资', exact: true }).click();
      await settlePopovers(page);
      await nextCreate.getByLabel('再投资净值', { exact: true }).fill('1.5');
      const nonCash = await submitEvent(page, nextCreate);
      createdIds.push(nonCash.event.id);
      expect(nonCash.payload).not.toHaveProperty('cash_pay_date');
      await expectStoredDate(page, s, nonCash.event.id, null);
      const row = page.locator('table tbody tr').filter({ hasText: '分红再投资' });
      await expect(row.getByTestId('event-cash-pay-date')).toHaveText('--');
      await row.getByTitle('编辑', { exact: true }).click();
      const edit = dialogByTitle(page, '编辑份额变动事件');
      await expect(edit.getByTestId('cash-pay-date-field')).toHaveCount(0);
      await edit.getByLabel('备注', { exact: true }).fill('非现金类型不提交到账日');
      const updated = await submitEvent(page, edit, nonCash.event.id);
      expect(updated.payload).not.toHaveProperty('cash_pay_date');
    } finally {
      for (const id of createdIds) {
        const response = await page.request.delete(`/api/share-change-events/${id}`, { headers: s.headers });
        await expect(response).toBeOK();
      }
    }
  });
});
