/**
 * 前端 E2E：份额变动事件「查看详情」只读弹窗（issue #754，双端）
 *
 * 覆盖验收断言：
 * - 断言 1/2/9：列表每行（pending/confirmed、父记录）有「查看详情」入口，双端弹窗可开，
 *   关闭后列表状态不变
 * - 断言 3：confirmed reinvest_dividend 全字段回查——解析净值 1.6000、红利金额
 *   ¥500.00（= quantize(1000 份 × 0.5)，与后端计算单点同源）、确认时间非 --
 * - 断言 4：confirmed cash_dividend 红利金额 == 现金变化；到账日 null 值 --、
 *   注「默认除息日 {除息日}」带生效日（与列表列/确认弹窗口径对齐，#804 复审）
 * - 断言 5：pending 自动计算型全 --（不出现误导性 0.00）；reinvest_nav null 注
 *   「确认时取除息日净值」（存量 0 值形态后端拒绝经 API 造出，照实显示由
 *   formatNav 的 0 值不 fallback 保证，utils 单测覆盖）
 * - 断言 6：pending forced_adjustment 直填值照实显示、未填项 --
 * - 断言 7：confirm → unconfirm 后详情回落 --（三态读侧）
 * - 不适用字段整行隐藏（spec §5.1 决议②）：类型专属行按矩阵显隐
 *
 * 数据红线（照 share-change-cash-pay-date.spec.ts / trade-detail-dialog.spec.ts）：
 * 自建隔离组合造数，不碰 E2E_ACTIVE；日期经 /api/trading-calendar 锚定；
 * 净值夹具复用种子 000300.OF 的 D..D+4 窗口（除息日 = D+2 → 1.6000）。
 */
import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { format } from 'date-fns';
import {
  authHeaders,
  collectPageErrors,
  dialogByTitle,
  gotoPortfolioSubpage,
  isolatedPortfolioCode,
  openAppAndAuth,
  type PortfolioCode,
} from './helpers';

const PRODUCT = { code: '000300.OF', market: 'CN_OTC' };
const PLATFORM = 'HBZQ';
type Headers = { Authorization: string };

/** mobile webkit 点击 stable 等待按帧率计费，长链路按 project 放宽预算（#763） */
function budget(testInfo: TestInfo): number {
  return testInfo.project.name === 'mobile' ? 120_000 : 90_000;
}

async function post<T = unknown>(page: Page, headers: Headers, path: string, data?: unknown): Promise<T> {
  const response = await page.request.post(path, { headers, data });
  expect(response.ok(), `POST ${path} → ${response.status()} ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

interface Scenario {
  code: string;
  headers: Headers;
  day: string;         // 最近一个 <= today 的交易日（首购/快照锚点）
  apply: string;       // day 前一交易日（申购申请日 → 确认日 = day）
  entitlement: string; // D+1 交易日（权益登记日，有持仓快照）
  ex: string;          // D+2 交易日（除息日，种子净值 1.6000）
}

/** 自建隔离组合 + 交易日历锚定（形态照 share-change-cash-pay-date.spec.ts::setupPortfolio） */
async function setupScenario(page: Page, testInfo: TestInfo, suffix: string): Promise<Scenario> {
  const headers = await authHeaders(page);
  const today = format(new Date(), 'yyyy-MM-dd');
  const year = Number(today.slice(0, 4));
  const calendars = await Promise.all([year - 1, year, year + 1].map(async (y) => {
    const response = await page.request.get(`/api/trading-calendar?year=${y}`, { headers });
    await expect(response).toBeOK();
    return (await response.json()) as { calendar_date: string; is_open: boolean }[];
  }));
  const days = calendars.flat().filter((d) => d.is_open).map((d) => d.calendar_date).sort();
  const index = days.filter((d) => d <= today).length - 1;
  expect(index).toBeGreaterThan(0);
  expect(days.length).toBeGreaterThan(index + 3);
  const code = isolatedPortfolioCode('E754', testInfo, suffix);
  expect(code.length).toBeLessThanOrEqual(20);
  await post(page, headers, '/api/portfolios', { code, name: `事件详情 ${code}` });
  return { code, headers, day: days[index], apply: days[index - 1], entitlement: days[index + 1], ex: days[index + 2] };
}

/** 首购注资 + 持仓 1000 份（1500 / 1.5）+ day 与 entitlement 两日快照（confirm 的基数来源） */
async function seedHolding(page: Page, s: Scenario): Promise<void> {
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

async function createEvent(page: Page, s: Scenario, body: Record<string, unknown>): Promise<{ id: number }> {
  return post<{ id: number }>(page, s.headers, '/api/share-change-events', {
    portfolio_code: s.code,
    product_code: PRODUCT.code,
    market: PRODUCT.market,
    platform_code: PLATFORM,
    entitlement_date: s.entitlement,
    ex_date: s.ex,
    ...body,
  });
}

/** 进入事件列表并返回行定位器；reload 用于 REST 造数/状态变更后刷新行集 */
async function gotoEvents(page: Page, s: Scenario): Promise<void> {
  await gotoPortfolioSubpage(page, s.code as PortfolioCode, 'share-change-events');
}

async function openDetail(page: Page, row: ReturnType<Page['locator']>): Promise<ReturnType<Page['locator']>> {
  await row.getByTitle('查看详情', { exact: true }).click();
  const dlg = dialogByTitle(page, '事件详情');
  await expect(dlg).toBeVisible({ timeout: 10_000 });
  return dlg;
}

test.describe('份额变动事件详情弹窗（#754，双端）', () => {
  test('pending 再投资：计算字段全 --、输入可见、类型专属行显隐、关闭后列表不变', async ({ page }, testInfo) => {
    test.setTimeout(budget(testInfo));
    const errors = collectPageErrors(page);
    await openAppAndAuth(page);
    const s = await setupScenario(page, testInfo, 'A');
    await seedHolding(page, s);
    const event = await createEvent(page, s, { event_type: 'reinvest_dividend', div_cash: 0.5 });

    await gotoEvents(page, s);
    // count() 不自动重试：先等目标行渲染，避免列表首帧读到 0（竞态 flake）
    await expect(page.getByTestId(`share-event-${event.id}`)).toBeVisible({ timeout: 10_000 });
    const rowCount = await page.locator('table tbody tr').count();
    expect(rowCount).toBeGreaterThan(0);
    const row = page.getByTestId(`share-event-${event.id}`);
    const dlg = await openDetail(page, row);

    // 输入可见
    await expect(dlg.getByTestId('detail-div-cash')).toHaveText('¥0.5000');
    await expect(dlg.getByTestId('detail-entitlement-date')).toHaveText(s.entitlement);
    await expect(dlg.getByTestId('detail-ex-date')).toHaveText(s.ex);
    // 计算字段全 --（pending 未确认，不出现误导性 0.00）
    for (const testid of [
      'detail-entitlement-shares', 'detail-dividend-amount', 'detail-shares-change',
      'detail-shares-before', 'detail-shares-after', 'detail-cash-change', 'detail-confirmed-at',
    ]) {
      await expect(dlg.getByTestId(testid), testid).toHaveText('--');
    }
    // 再投资净值 null：-- + 「确认时取除息日净值」注记（#744 口径）
    await expect(dlg.getByTestId('detail-reinvest-nav')).toContainText('--');
    await expect(dlg.getByText('（确认时取除息日净值）')).toBeVisible();
    // 不适用字段整行隐藏（§5.1 决议②）：现金到账日/比例/每份分红之外的类型专属行
    await expect(dlg.getByTestId('detail-cash-pay-date')).toHaveCount(0);
    await expect(dlg.getByTestId('detail-ratio')).toHaveCount(0);
    // 只读契约：弹窗内无写操作按钮，footer 只有「关闭」
    await expect(dlg.getByRole('button', { name: '编辑' })).toHaveCount(0);
    await expect(dlg.getByRole('button', { name: '确认' })).toHaveCount(0);
    await expect(dlg.getByRole('button', { name: '取消' })).toHaveCount(0);

    // 关闭后列表状态不变（行数不重置）
    await dlg.getByRole('button', { name: '关闭', exact: true }).click();
    await expect(dlg).toBeHidden({ timeout: 15_000 });
    await expect(page.locator('table tbody tr')).toHaveCount(rowCount);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('confirmed 再投资：解析净值/红利金额/确认时间回查，unconfirm 后回落 --（断言 3/7）', async ({ page }, testInfo) => {
    test.setTimeout(budget(testInfo));
    const errors = collectPageErrors(page);
    await openAppAndAuth(page);
    const s = await setupScenario(page, testInfo, 'B');
    await seedHolding(page, s);
    const event = await createEvent(page, s, { event_type: 'reinvest_dividend', div_cash: 0.5 });
    // 除息日 = D+2，种子净值 1.6000；红利金额 = quantize(1000 × 0.5) = 500.00；
    // 份额变化 = 500 / 1.6 = 312.50 份（两步量化，与确认预览同值）
    await post(page, s.headers, `/api/share-change-events/${event.id}/confirm`);

    await gotoEvents(page, s);
    const row = page.getByTestId(`share-event-${event.id}`);
    await expect(row).toContainText('已确认');
    const dlg = await openDetail(page, row);
    await expect(dlg.getByTestId('detail-status')).toContainText('已确认');
    await expect(dlg.getByTestId('detail-reinvest-nav')).toContainText('1.6000');
    await expect(dlg.getByTestId('detail-dividend-amount')).toHaveText('¥500.00');
    await expect(dlg.getByTestId('detail-entitlement-shares')).toHaveText('1,000.00 份');
    await expect(dlg.getByTestId('detail-shares-change')).toHaveText('312.50 份');
    await expect(dlg.getByTestId('detail-confirmed-at')).toHaveText(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/);
    await expect(dlg.getByTestId('detail-div-cash')).toHaveText('¥0.5000');
    await dlg.getByRole('button', { name: '关闭', exact: true }).click();
    await expect(dlg).toBeHidden({ timeout: 15_000 });

    // unconfirm 后重开：确认时间与红利金额回落 --（读侧派生跟随状态）
    await post(page, s.headers, `/api/share-change-events/${event.id}/unconfirm`);
    await page.reload();
    const reopened = await openDetail(page, page.getByTestId(`share-event-${event.id}`));
    await expect(reopened.getByTestId('detail-status')).toContainText('待确认');
    await expect(reopened.getByTestId('detail-confirmed-at')).toHaveText('--');
    await expect(reopened.getByTestId('detail-dividend-amount')).toHaveText('--');
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('confirmed 现金分红：红利金额 == 现金变化，到账日 null 注「默认除息日」（断言 4）', async ({ page }, testInfo) => {
    test.setTimeout(budget(testInfo));
    const errors = collectPageErrors(page);
    await openAppAndAuth(page);
    const s = await setupScenario(page, testInfo, 'C');
    await seedHolding(page, s);
    const event = await createEvent(page, s, { event_type: 'cash_dividend', div_cash: 0.5 });
    await post(page, s.headers, `/api/share-change-events/${event.id}/confirm`);

    await gotoEvents(page, s);
    const dlg = await openDetail(page, page.getByTestId(`share-event-${event.id}`));
    await expect(dlg.getByTestId('detail-dividend-amount')).toHaveText('¥500.00');
    await expect(dlg.getByTestId('detail-cash-change')).toHaveText('¥500.00');
    await expect(dlg.getByTestId('detail-cash-pay-date')).toContainText('--');
    await expect(dlg.getByText(`（默认除息日 ${s.ex}）`)).toBeVisible();
    // 类型显隐：现金分红无再投资净值/份额确认日行
    await expect(dlg.getByTestId('detail-reinvest-nav')).toHaveCount(0);
    await expect(dlg.getByTestId('detail-share-confirm-date')).toHaveCount(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('pending 强制调整：直填值照实显示、未填项 --（断言 6）', async ({ page }, testInfo) => {
    test.setTimeout(budget(testInfo));
    const errors = collectPageErrors(page);
    await openAppAndAuth(page);
    const s = await setupScenario(page, testInfo, 'D');
    const event = await createEvent(page, s, {
      event_type: 'forced_adjustment', shares_change: 10, cash_change: -25,
    });

    await gotoEvents(page, s);
    const dlg = await openDetail(page, page.getByTestId(`share-event-${event.id}`));
    await expect(dlg.getByTestId('detail-shares-change')).toHaveText('10.00 份');
    await expect(dlg.getByTestId('detail-cash-change')).toHaveText('¥-25.00');
    await expect(dlg.getByTestId('detail-shares-before')).toHaveText('--');
    await expect(dlg.getByTestId('detail-shares-after')).toHaveText('--');
    await expect(dlg.getByTestId('detail-confirmed-at')).toHaveText('--');
    // 类型显隐：强制调整无每份分红/参与分红份额/红利金额行
    await expect(dlg.getByTestId('detail-div-cash')).toHaveCount(0);
    await expect(dlg.getByTestId('detail-entitlement-shares')).toHaveCount(0);
    await expect(dlg.getByTestId('detail-dividend-amount')).toHaveCount(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
