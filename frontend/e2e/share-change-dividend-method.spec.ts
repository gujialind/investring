/**
 * #775 方案 B：双端逐笔选择分红方式。只用现有 API 在独立组合造数，不访问 DB/Tushare，
 * 不推进共享 E2E_ACTIVE。持仓/净值复用种子 000300.OF 的 D..D+4 窗口：
 * D 买入 1500 / 1.5 = 1000 份，D+1 登记，D+2 除息净值 1.6，每份红利 0.1。
 * 每个 project/retry/场景拥有独立组合；每轮须用全新种子库，不用 nonce 绕过重复执行。
 */
import { test, expect, type Locator, type Page, type TestInfo } from '@playwright/test';
import type { ShareChangeEvent, ShareChangeEventPreview } from '../src/types/share-change-event';
import {
  collectPageErrors,
  dialogByTitle,
  gotoPortfolioSubpage,
  openAppAndAuth,
  openFilterPanelIfMobile,
  openPopover,
  pickCalendarDay,
  settlePopovers,
  toISODate,
  type PortfolioCode,
} from './helpers';

const PRODUCT = { code: '000300.OF', market: 'CN_OTC' };
const PLATFORM = 'HBZQ';
type Headers = { Authorization: string };
type Method = 'cash_dividend' | 'reinvest_dividend';

async function post<T = unknown>(page: Page, headers: Headers, path: string, data?: unknown): Promise<T> {
  const response = await page.request.post(path, { headers, data });
  await expect(response).toBeOK();
  return response.json();
}

async function setup(page: Page, info: TestInfo, scenario: string) {
  // WebKit 多次日历/Select 点击的帧等待预算，桌面保留更紧的上界（#763）。
  test.setTimeout(info.project.name === 'mobile' ? 120_000 : 60_000);
  const headers = await openAppAndAuth(page);
  const today = toISODate(new Date());
  const year = Number(today.slice(0, 4));
  const calendars = await Promise.all([year - 1, year, year + 1].map(async (y) => {
    const response = await page.request.get(`/api/trading-calendar?year=${y}`, { headers });
    await expect(response).toBeOK();
    return await response.json() as { calendar_date: string; is_open: boolean }[];
  }));
  const days = calendars.flat().filter((d) => d.is_open).map((d) => d.calendar_date).sort();
  const day = days.filter((d) => d <= today).at(-1);
  expect(day, '种子日历必须覆盖今天之前的最近交易日').toBeTruthy();
  const index = days.indexOf(day!);
  expect(index).toBeGreaterThan(0);
  expect(days.length).toBeGreaterThan(index + 4);
  const [entitlement, ex, confirmDay, laterDay] = days.slice(index + 1, index + 5);
  const code = `E775_${info.project.name}_${info.retry}_${scenario}`;
  expect(code.length).toBeLessThanOrEqual(20);
  await post(page, headers, '/api/portfolios', { code, name: `分红方式 ${code}` });
  const subscription = await post<{ id: number; confirm_date: string }>(page, headers, '/api/subscriptions', {
    portfolio_code: code, investor_code: 'ADMIN', platform_code: PLATFORM,
    sub_type: 'subscribe', amount: 10000, apply_date: days[index - 1],
  });
  expect(subscription.confirm_date).toBe(day);
  await post(page, headers, `/api/subscriptions/${subscription.id}/confirm`);
  const trade = await post<{ id: number }>(page, headers, '/api/trades', {
    portfolio_code: code, product_code: PRODUCT.code, market: PRODUCT.market,
    platform_code: PLATFORM, trade_type: 'buy', trade_date: day, amount: 1500, fee: 0,
  });
  await post(page, headers, `/api/trades/${trade.id}/confirm`);
  for (const target of [day, entitlement]) {
    const result = await post<{ success: boolean }>(page, headers, '/api/snapshots/generate', {
      portfolio_code: code, target_date: target,
    });
    expect(result.success).toBe(true);
  }
  return { code, headers, entitlement, ex, confirmDay, laterDay };
}

type Scenario = Awaited<ReturnType<typeof setup>>;

async function createEvent(page: Page, s: Scenario, fields: Partial<ShareChangeEvent> = {}) {
  return post<ShareChangeEvent>(page, s.headers, '/api/share-change-events', {
    portfolio_code: s.code, product_code: PRODUCT.code, market: PRODUCT.market,
    platform_code: PLATFORM, event_type: 'dividend_pending',
    entitlement_date: s.entitlement, ex_date: s.ex, div_cash: 0.1, ...fields,
  });
}

async function stored(page: Page, s: Scenario, id: number): Promise<ShareChangeEvent> {
  const response = await page.request.get(`/api/share-change-events/${id}`, { headers: s.headers });
  await expect(response).toBeOK();
  return response.json();
}

const rowFor = (page: Page, id: number) => page.getByTestId(`share-event-${id}`);
const confirmDialog = (page: Page) => dialogByTitle(page, '确认份额变动事件');
const confirmButton = (dlg: Locator) => dlg.getByRole('button', { name: '确认', exact: true });
const previewValues = (page: Page) => confirmDialog(page).getByTestId('event-confirm-preview');

async function chooseMethod(page: Page, method: Method) {
  await openPopover(page, confirmDialog(page).getByLabel('分红方式', { exact: true }), page.getByRole('listbox'));
  await page.getByRole('option', { name: method === 'cash_dividend' ? '现金分红' : '分红再投资', exact: true }).click();
  await settlePopovers(page);
}

async function previewAfter(page: Page, id: number, action: () => Promise<unknown>, status = 200) {
  const [response] = await Promise.all([
    page.waitForResponse((r) => r.request().method() === 'GET'
      && new URL(r.url()).pathname === `/api/share-change-events/${id}/preview`),
    action(),
  ]);
  expect(response.status(), await response.text()).toBe(status);
  return response;
}

function gate() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

test.describe('逐笔选择分红方式（#775，桌面/移动共用）', () => {
  test('待定无默认、标签筛选、新建不露待定、编辑仅提交公共字段', async ({ page }, info) => {
    const errors = collectPageErrors(page);
    const s = await setup(page, info, 'A');
    const event = await createEvent(page, s);
    const known = await createEvent(page, s, { event_type: 'cash_dividend' });
    await gotoPortfolioSubpage(page, s.code as PortfolioCode, 'share-change-events');
    const row = rowFor(page, event.id);
    await expect(row).toContainText('分红方式待定');
    await expect(row).toContainText('待确认');
    await expect(row.getByTestId('event-shares-change')).toHaveText('--');
    await expect(row.getByTestId('event-cash-change')).toHaveText('--');

    const requests: string[] = [];
    page.on('request', (r) => {
      if (new URL(r.url()).pathname === `/api/share-change-events/${event.id}/preview`) requests.push(r.url());
    });
    await row.getByTitle('确认', { exact: true }).click();
    const dlg = confirmDialog(page);
    await expect(dlg.getByLabel('分红方式', { exact: true })).toHaveText('请选择分红方式');
    await expect(dlg).toContainText('请先选择分红方式');
    await expect(dlg).not.toContainText('预览失败');
    await expect(confirmButton(dlg)).toBeDisabled();
    await expect(previewValues(page)).toHaveCount(0);
    await dlg.getByRole('button', { name: '取消', exact: true }).click();
    expect(requests).toEqual([]);

    await row.getByTitle('编辑', { exact: true }).click();
    const edit = dialogByTitle(page, '编辑份额变动事件');
    await expect(edit).toContainText('分红方式在确认时选择');
    await expect(edit.locator('input')).toHaveCount(2); // 仅每份分红金额、备注
    await expect(edit.locator('button#edit_entitlement_date')).toBeVisible();
    await expect(edit.locator('button#edit_ex_date')).toBeVisible();
    await expect(edit.getByTestId('cash-pay-date-field')).toHaveCount(0);
    await expect(edit.getByTestId('share-confirm-date-field')).toHaveCount(0);
    await edit.getByLabel('每份分红金额（元）', { exact: true }).fill('0.2');
    await edit.getByLabel('备注', { exact: true }).fill('仅公告公共字段');
    const [updated] = await Promise.all([
      page.waitForResponse((r) => r.request().method() === 'PUT'
        && new URL(r.url()).pathname === `/api/share-change-events/${event.id}`),
      edit.getByRole('button', { name: '保存', exact: true }).click(),
    ]);
    expect(updated.ok(), await updated.text()).toBe(true);
    expect(updated.request().postDataJSON()).toEqual({
      ex_date: s.ex, entitlement_date: s.entitlement, div_cash: 0.2, notes: '仅公告公共字段',
    });
    await expect(edit).toBeHidden();
    expect(await stored(page, s, event.id)).toMatchObject({ event_type: 'dividend_pending', status: 'pending', div_cash: 0.2 });

    await openFilterPanelIfMobile(page, info);
    await openPopover(page, page.getByRole('combobox').filter({ hasText: '全部类型' }), page.getByRole('listbox'));
    const [filtered] = await Promise.all([
      page.waitForResponse((r) => new URL(r.url()).pathname === '/api/share-change-events'
        && new URL(r.url()).searchParams.get('event_type') === 'dividend_pending'),
      page.getByRole('option', { name: '分红方式待定', exact: true }).click(),
    ]);
    expect(filtered.ok()).toBe(true);
    await settlePopovers(page);
    await expect(row).toBeVisible();
    await expect(rowFor(page, known.id)).toHaveCount(0);

    await page.getByRole('button', { name: '新建事件' }).click();
    const create = dialogByTitle(page, '新建份额变动事件');
    await openPopover(page, create.getByLabel('事件类型', { exact: true }), page.getByRole('listbox'));
    await expect(page.getByRole('option')).toHaveText(['现金分红', '分红再投资', '份额拆分', '份额合并', '红股送股', '强制调整']);
    await expect(page.getByRole('option', { name: '分红方式待定', exact: true })).toHaveCount(0);
    await page.getByRole('option', { name: '现金分红', exact: true }).click();
    await settlePopovers(page);
    await create.getByRole('button', { name: '取消', exact: true }).click();
    expect(errors).toEqual([]);
  });

  for (const method of ['cash_dividend', 'reinvest_dividend'] as const) {
    test(`${method}：真实预览与确认方向一致，提交中禁用输入`, async ({ page }, info) => {
      const errors = collectPageErrors(page);
      const s = await setup(page, info, method === 'cash_dividend' ? 'C' : 'R');
      const event = await createEvent(page, s);
      await gotoPortfolioSubpage(page, s.code as PortfolioCode, 'share-change-events');
      await rowFor(page, event.id).getByTitle('确认', { exact: true }).click();
      const response = await previewAfter(page, event.id, () => chooseMethod(page, method));
      const { preview } = await response.json() as { preview: ShareChangeEventPreview };
      expect(preview).toMatchObject({
        event_type: method, cash_effective_date: s.ex, share_effective_date: s.ex,
        entitlement_shares: 1000, div_cash: 0.1, dividend_amount: 100,
        shares_change: method === 'cash_dividend' ? 0 : 62.5,
        cash_change: method === 'cash_dividend' ? 100 : 0,
      });
      const values = previewValues(page);
      await expect(values.getByText(method === 'cash_dividend' ? '现金分红' : '分红再投资', { exact: true })).toBeVisible();
      await expect(values.getByText(method === 'cash_dividend' ? '0.00 份' : '62.50 份', { exact: true })).toBeVisible();
      if (method === 'reinvest_dividend') {
        await expect(values.getByText('1.6000', { exact: true })).toBeVisible();
        await expect(values.getByText('¥0.00', { exact: true })).toBeVisible();
      } else {
        await expect(values.getByText('¥100.00', { exact: true })).toHaveCount(2);
        await expect(values.getByText('再投资净值', { exact: true })).toHaveCount(0);
      }
      expect(await stored(page, s, event.id)).toMatchObject({ event_type: 'dividend_pending', status: 'pending' });
      const dlg = confirmDialog(page);
      const dateField = method === 'cash_dividend' ? 'cash_pay_date' : 'share_confirm_date';
      const dated = await previewAfter(page, event.id, () =>
        pickCalendarDay(page, dlg.locator(`button#confirm_${dateField}`), s.confirmDay));
      expect((await dated.json()).preview).toMatchObject({
        cash_effective_date: method === 'cash_dividend' ? s.confirmDay : s.ex,
        share_effective_date: method === 'reinvest_dividend' ? s.confirmDay : s.ex,
      });
      await expect(confirmButton(dlg)).toBeEnabled();
      const release = gate();
      const confirmPath = `/api/share-change-events/${event.id}/confirm`;
      await page.route(`**${confirmPath}`, async (route) => {
        await release.promise;
        await route.continue();
      });
      const confirmed = page.waitForResponse((r) => new URL(r.url()).pathname === confirmPath);
      try {
        const [request] = await Promise.all([
          page.waitForRequest((r) => new URL(r.url()).pathname === confirmPath),
          confirmButton(dlg).click(),
        ]);
        expect(request.postDataJSON()).toEqual({ event_type: method, [dateField]: s.confirmDay });
        await expect(dlg.getByLabel('分红方式', { exact: true })).toBeDisabled();
        await expect(dlg.locator(method === 'cash_dividend' ? 'button#confirm_cash_pay_date' : 'button#confirm_share_confirm_date')).toBeDisabled();
        await expect(confirmButton(dlg)).toBeDisabled();
        await expect(dlg.getByRole('button', { name: '取消', exact: true })).toBeDisabled();
      } finally {
        release.resolve();
      }
      const result = await confirmed;
      expect(result.ok(), await result.text()).toBe(true);
      await expect(dlg).toBeHidden();
      const saved = await stored(page, s, event.id);
      expect(saved).toMatchObject({
        event_type: method, status: 'confirmed', shares_change: preview.shares_change,
        cash_change: preview.cash_change, entitlement_shares: preview.entitlement_shares,
        cash_pay_date: method === 'cash_dividend' ? s.confirmDay : null,
        share_confirm_date: method === 'reinvest_dividend' ? s.confirmDay : null,
      });
      await expect(rowFor(page, event.id)).toContainText('已确认');
      await expect(rowFor(page, event.id)).toContainText(method === 'cash_dividend' ? '现金分红' : '分红再投资');
      expect(errors).toEqual([]);
    });
  }

  test('方式/日期刷新预览、加载中输入不卸载、旧响应不串台、取消重开不落库', async ({ page }, info) => {
    const errors = collectPageErrors(page);
    const s = await setup(page, info, 'S');
    const event = await createEvent(page, s);
    const other = await createEvent(page, s, { event_type: 'cash_dividend', cash_pay_date: s.laterDay });
    const writes: string[] = [];
    page.on('request', (r) => {
      if (['PUT', 'POST'].includes(r.method()) && new URL(r.url()).pathname.startsWith(`/api/share-change-events/${event.id}`)) writes.push(r.url());
    });
    await gotoPortfolioSubpage(page, s.code as PortfolioCode, 'share-change-events');
    await rowFor(page, event.id).getByTitle('确认', { exact: true }).click();
    const dlg = confirmDialog(page);
    await previewAfter(page, event.id, () => chooseMethod(page, 'cash_dividend'));
    const cash = await previewAfter(page, event.id, () => pickCalendarDay(page, dlg.locator('button#confirm_cash_pay_date'), s.confirmDay));
    expect((await cash.json()).preview.cash_effective_date).toBe(s.confirmDay);
    await expect(previewValues(page).getByText(s.confirmDay, { exact: true })).toBeVisible();
    const reinvest = await previewAfter(page, event.id, () => chooseMethod(page, 'reinvest_dividend'));
    expect(new URL(reinvest.url()).searchParams.has('cash_pay_date')).toBe(false);
    await expect(dlg.locator('button#confirm_share_confirm_date')).toHaveText('默认除息日到账');
    const dated = await previewAfter(page, event.id, () => pickCalendarDay(page, dlg.locator('button#confirm_share_confirm_date'), s.confirmDay));
    expect((await dated.json()).preview).toMatchObject({ event_type: 'reinvest_dividend', share_effective_date: s.confirmDay });
    await expect(previewValues(page).getByText(s.confirmDay, { exact: true })).toBeVisible();

    // 人为延迟旧候选响应：不是等文案的同步拐棍，直接控制请求来验证输入实例和缓存隔离。
    const release = gate();
    const completed = gate();
    const trigger = await dlg.locator('button#confirm_share_confirm_date').elementHandle();
    const methodTrigger = await dlg.locator('button#confirm_dividend_method').elementHandle();
    await page.route(`**/api/share-change-events/${event.id}/preview?*`, async (route) => {
      if (new URL(route.request().url()).searchParams.get('share_confirm_date') === s.laterDay) {
        try {
          await release.promise;
          const response = await route.fetch();
          await route.fulfill({ response });
        } finally {
          completed.resolve();
        }
      } else {
        await route.continue();
      }
    });
    try {
      await Promise.all([
        page.waitForRequest((r) => new URL(r.url()).searchParams.get('share_confirm_date') === s.laterDay),
        pickCalendarDay(page, dlg.locator('button#confirm_share_confirm_date'), s.laterDay),
      ]);
      await expect(confirmButton(dlg)).toBeDisabled();
      await expect(previewValues(page)).toHaveCount(0);
      expect(await trigger!.evaluate((el) => el.isConnected)).toBe(true);
      expect(await methodTrigger!.evaluate((el) => el.isConnected)).toBe(true);
      await expect(dlg.locator('button#confirm_share_confirm_date')).toBeEnabled();
      const cashAgain = await previewAfter(page, event.id, () => chooseMethod(page, 'cash_dividend'));
      expect([...new URL(cashAgain.url()).searchParams.entries()]).toEqual([['event_type', 'cash_dividend']]);
      await expect(dlg.locator('button#confirm_cash_pay_date')).toHaveText('默认除息日');
    } finally {
      release.resolve();
    }
    await completed.promise;
    await expect(previewValues(page).getByText('现金分红', { exact: true })).toBeVisible();
    await expect(previewValues(page).getByText('再投资净值', { exact: true })).toHaveCount(0);
    await expect(confirmButton(dlg)).toBeEnabled();
    await dlg.getByRole('button', { name: '取消', exact: true }).click();
    expect(await stored(page, s, event.id)).toMatchObject({
      event_type: 'dividend_pending', status: 'pending', cash_pay_date: null, share_confirm_date: null,
    });
    await rowFor(page, event.id).getByTitle('确认', { exact: true }).click();
    await expect(dlg.getByLabel('分红方式', { exact: true })).toHaveText('请选择分红方式');
    await expect(confirmButton(dlg)).toBeDisabled();
    await expect(previewValues(page)).toHaveCount(0);
    await dlg.getByRole('button', { name: '取消', exact: true }).click();
    // 换另一笔事件不能携带前一笔候选，须按该笔原类型/日期重新预览。
    await previewAfter(page, other.id, () => rowFor(page, other.id).getByTitle('确认', { exact: true }).click());
    await expect(dlg.getByLabel('分红方式', { exact: true })).toHaveText('现金分红');
    await expect(dlg.locator('button#confirm_cash_pay_date')).toHaveText(s.laterDay);
    await expect(previewValues(page).getByText(s.laterDay, { exact: true })).toBeVisible();
    await dlg.getByRole('button', { name: '取消', exact: true }).click();
    expect(writes).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('已有现金日期预填；预览错误仍可改方式，确认失败保留候选且不保存', async ({ page }, info) => {
    const s = await setup(page, info, 'E');
    const event = await createEvent(page, s, { event_type: 'cash_dividend', cash_pay_date: s.confirmDay });
    await gotoPortfolioSubpage(page, s.code as PortfolioCode, 'share-change-events');
    const opened = await previewAfter(page, event.id, () => rowFor(page, event.id).getByTitle('确认', { exact: true }).click());
    expect(new URL(opened.url()).searchParams.get('cash_pay_date')).toBe(s.confirmDay);
    const dlg = confirmDialog(page);
    await expect(dlg.getByLabel('分红方式', { exact: true })).toHaveText('现金分红');
    await expect(dlg.locator('button#confirm_cash_pay_date')).toHaveText(s.confirmDay);
    // 真后端拒绝早于除息日的到账日，错误状态不能卸载输入。
    await previewAfter(page, event.id, () => pickCalendarDay(page, dlg.locator('button#confirm_cash_pay_date'), s.entitlement), 422);
    await expect(dlg).toContainText('预览失败');
    await expect(confirmButton(dlg)).toBeDisabled();
    await expect(dlg.locator('button#confirm_cash_pay_date')).toHaveText(s.entitlement);
    await previewAfter(page, event.id, () => chooseMethod(page, 'reinvest_dividend'));
    await expect(previewValues(page).getByText('62.50 份', { exact: true })).toBeVisible();
    await expect(confirmButton(dlg)).toBeEnabled();
    // 确认失败的 UI 恢复独立于预览失败；服务原子性另由后端测试证明。
    await page.route(`**/api/share-change-events/${event.id}/confirm`, (route) => route.fulfill({
      status: 422, contentType: 'application/json',
      body: JSON.stringify({ detail: { error: 'SNAPSHOT_DEPENDENCY', message: '测试：该事件已有快照依赖' } }),
    }));
    await confirmButton(dlg).click();
    await expect(page.getByTestId('toast-card').filter({ hasText: '测试：该事件已有快照依赖' })).toBeVisible();
    await expect(dlg.getByLabel('分红方式', { exact: true })).toHaveText('分红再投资');
    await expect(dlg.getByLabel('分红方式', { exact: true })).toBeEnabled();
    expect(await stored(page, s, event.id)).toMatchObject({ event_type: 'cash_dividend', cash_pay_date: s.confirmDay, status: 'pending' });
    await dlg.getByRole('button', { name: '取消', exact: true }).click();
    await previewAfter(page, event.id, () => rowFor(page, event.id).getByTitle('确认', { exact: true }).click());
    await expect(dlg.getByLabel('分红方式', { exact: true })).toHaveText('现金分红');
    await expect(dlg.locator('button#confirm_cash_pay_date')).toHaveText(s.confirmDay);
  });

  test('同日登记再投保留原确认日可预览，改现金仍拒绝，错误后可改回再投', async ({ page }, info) => {
    const s = await setup(page, info, 'D');
    // 事件须先于除息日快照录入；份额确认日晚于除息日，生成后不会被提前自动确认。
    const event = await createEvent(page, s, {
      event_type: 'reinvest_dividend', entitlement_date: s.ex, share_confirm_date: s.confirmDay,
    });
    const snapshot = await post<{ success: boolean }>(page, s.headers, '/api/snapshots/generate', {
      portfolio_code: s.code, target_date: s.ex,
    });
    expect(snapshot.success).toBe(true);
    await gotoPortfolioSubpage(page, s.code as PortfolioCode, 'share-change-events');
    const response = await previewAfter(page, event.id, () => rowFor(page, event.id).getByTitle('确认', { exact: true }).click());
    expect((await response.json()).preview.share_effective_date).toBe(s.confirmDay);
    const dlg = confirmDialog(page);
    await expect(dlg.getByLabel('分红方式', { exact: true })).toHaveText('分红再投资');
    await expect(dlg.locator('button#confirm_share_confirm_date')).toHaveText(s.confirmDay);
    await expect(confirmButton(dlg)).toBeEnabled();
    await previewAfter(page, event.id, () => chooseMethod(page, 'cash_dividend'), 422);
    await expect(confirmButton(dlg)).toBeDisabled();
    await expect(dlg.getByLabel('分红方式', { exact: true })).toBeEnabled();
    // 切回后原专属日期已丢弃；重新填写原日可恢复既有再投，不放宽同日现金规则。
    await previewAfter(page, event.id, () => chooseMethod(page, 'reinvest_dividend'), 422);
    await previewAfter(page, event.id, () => pickCalendarDay(page, dlg.locator('button#confirm_share_confirm_date'), s.confirmDay));
    await expect(confirmButton(dlg)).toBeEnabled();
    expect(await stored(page, s, event.id)).toMatchObject({ event_type: 'reinvest_dividend', share_confirm_date: s.confirmDay, status: 'pending' });
  });
});
