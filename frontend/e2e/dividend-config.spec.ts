/**
 * #887 PR1：分红方式配置管理入口（CN_OTC 限定，A14/R10）+ 确认弹窗预选（A12）+ 一次确认即沉淀（A13）。
 * 双端共用；只用现有 API 在独立组合造数，不访问 DB/Tushare，不推进共享 E2E_ACTIVE。
 * 复用种子 000300.OF/CN_OTC 的 D..D+4 净值窗口（同 share-change-dividend-method.spec）：
 * D 买入 1500/1.5=1000 份，D+1 登记，D+2 除息。每个 project/retry/场景独立组合，每轮全新种子库。
 *
 * 覆盖边界（如实）：default 行仅由迁移 0027 回填产生、API 不可创建（POST 恒 explicit），
 * 独立组合在栈启动后造数故无 default 行；确认弹窗预选机制与来源标注经 explicit 命中路径
 * （A12 边界③ degraded 回退）双端实测，default→「系统默认，未确认过」的 source→文案映射
 * 由后端 resolve 单测（source 分级）+ 组件逻辑覆盖。
 */
import { test, expect, type Page, type TestInfo } from '@playwright/test';
import type { ShareChangeEvent } from '../src/types/share-change-event';
import {
  collectPageErrors,
  dialogByTitle,
  gotoPortfolioSubpage,
  nextTradingDays,
  openAppAndAuth,
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
  const [entitlement, ex, confirmDay] = days.slice(index + 1, index + 4);
  const code = `E887_${info.project.name}_${info.retry}_${scenario}`;
  expect(code.length).toBeLessThanOrEqual(20);
  await post(page, headers, '/api/portfolios', { code, name: `分红配置 ${code}` });
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
  return { code, headers, entitlement, ex, confirmDay };
}

type Scenario = Awaited<ReturnType<typeof setup>>;

async function createEvent(page: Page, s: Scenario) {
  return post<ShareChangeEvent>(page, s.headers, '/api/share-change-events', {
    portfolio_code: s.code, product_code: PRODUCT.code, market: PRODUCT.market,
    platform_code: PLATFORM, event_type: 'dividend_pending',
    entitlement_date: s.entitlement, ex_date: s.ex, div_cash: 0.1,
  });
}

async function setConfig(page: Page, s: Scenario, method: Method, effectiveFrom: string) {
  return post(page, s.headers, '/api/dividend-configs', {
    portfolio_code: s.code, product_code: PRODUCT.code, market: PRODUCT.market,
    platform_code: PLATFORM, dividend_method: method, effective_from: effectiveFrom,
  });
}

async function listConfigs(page: Page, s: Scenario) {
  const response = await page.request.get('/api/dividend-configs', {
    headers: s.headers,
    params: { portfolio_code: s.code, product_code: PRODUCT.code, platform_code: PLATFORM, market: PRODUCT.market },
  });
  await expect(response).toBeOK();
  return (await response.json()).items as { id: number; dividend_method: string; source: string; effective_from: string }[];
}

const configDialog = (page: Page) => dialogByTitle(page, '分红方式配置');
const confirmDialog = (page: Page) => dialogByTitle(page, '确认份额变动事件');
const toast = (page: Page, title: string) => page.getByTestId('toast-card').filter({ hasText: title });

test.describe('分红方式配置（#887，桌面/移动共用）', () => {
  test('CN_OTC 持仓行入口：保存/历史/删除（A14/R10 展示侧）', async ({ page }, info) => {
    const errors = collectPageErrors(page);
    const s = await setup(page, info, 'M');
    // 平台详情是深链（platforms/[platformCode]），不在 PortfolioSub 内；直导航 + 持仓区就绪信号，
    // mobile project 靠 proxy 按 UA 重定向到 /m（同 portfolioPath 口径）。
    await page.goto(`/portfolio/${s.code}/platforms/${PLATFORM}`);
    await page.getByTestId('platform-holdings-section').waitFor({ state: 'visible', timeout: 15_000 });
    const entry = page.getByTestId(`dividend-config-entry-${PRODUCT.code}`);
    await expect(entry).toBeVisible();
    await entry.click();
    const dlg = configDialog(page);
    await expect(dlg).toBeVisible();
    await expect(dlg.getByTestId('dividend-config-empty')).toBeVisible();

    await openPopover(page, dlg.getByLabel('新增/变更方式'), page.getByRole('listbox'));
    await page.getByRole('option', { name: '现金分红', exact: true }).click();
    await settlePopovers(page);
    // 生效日不依赖组件默认「客户端今天」：CI 浏览器 TZ=UTC 而业务日按 Asia/Shanghai，
    // 客户端 today 可能早于业务日一天，默认值会落「历史行」致删除被业务规则拒绝
    // （dividend_config_service.delete_config：effective_from < business_today 即拒）。
    // 改取后端 /trading-calendar/next 的首个开市日——严格晚于客户端 today，故恒 ≥ 业务日，
    // 双端/双时区均可删（#896 L2 评审任务外发现的授权扩围修）。
    const [effectiveFrom] = await nextTradingDays(page, s.headers, toISODate(new Date()), 1);
    await pickCalendarDay(page, dlg.locator('button#dividend-config-effective-from'), effectiveFrom);
    await dlg.getByTestId('dividend-config-save').click();
    await expect(toast(page, '配置已保存')).toBeVisible();
    const row = dlg.getByTestId('dividend-config-row');
    await expect(row).toContainText('现金分红');
    await expect(row).toContainText('显式配置');

    await row.getByRole('button', { name: '删除' }).click();
    await expect(toast(page, '删除成功')).toBeVisible();
    await expect(dlg.getByTestId('dividend-config-empty')).toBeVisible();
    expect(await listConfigs(page, s)).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('确认弹窗 explicit 命中预选并标注来源，手动改选不被覆盖（A12/A6）', async ({ page }, info) => {
    const errors = collectPageErrors(page);
    const s = await setup(page, info, 'P');
    const event = await createEvent(page, s);
    await setConfig(page, s, 'reinvest_dividend', s.entitlement);
    await gotoPortfolioSubpage(page, s.code as PortfolioCode, 'share-change-events');
    await page.getByTestId(`share-event-${event.id}`).getByTitle('确认', { exact: true }).click();
    const dlg = confirmDialog(page);
    // resolve 命中 → 预选再投资 + 来源标注（explicit degraded 回退）
    await expect(dlg.getByTestId('confirm-config-source')).toHaveText('配置值（自动落地回退）');
    await expect(dlg.getByLabel('分红方式', { exact: true })).toContainText('分红再投资');
    // 手动改选现金 → 不被异步预选覆盖
    await openPopover(page, dlg.getByLabel('分红方式', { exact: true }), page.getByRole('listbox'));
    await page.getByRole('option', { name: '现金分红', exact: true }).click();
    await settlePopovers(page);
    await expect(dlg.getByLabel('分红方式', { exact: true })).toContainText('现金分红');
    expect(errors).toEqual([]);
  });

  test('勾选「记住」确认成功后沉淀 explicit；不勾选则无配置（A13）', async ({ page }, info) => {
    const errors = collectPageErrors(page);
    const s = await setup(page, info, 'R');
    const event = await createEvent(page, s);
    await gotoPortfolioSubpage(page, s.code as PortfolioCode, 'share-change-events');
    await page.getByTestId(`share-event-${event.id}`).getByTitle('确认', { exact: true }).click();
    const dlg = confirmDialog(page);
    await openPopover(page, dlg.getByLabel('分红方式', { exact: true }), page.getByRole('listbox'));
    await page.getByRole('option', { name: '现金分红', exact: true }).click();
    await settlePopovers(page);
    // 默认不勾选 → 先确认一次不沉淀
    await expect(dlg.getByLabel('记住本次方式，以后自动入账')).not.toBeChecked();
    await dlg.getByLabel('记住本次方式，以后自动入账').check();
    const [confirmResp] = await Promise.all([
      page.waitForResponse((r) => r.request().method() === 'POST'
        && new URL(r.url()).pathname === `/api/share-change-events/${event.id}/confirm`),
      dlg.getByRole('button', { name: '确认', exact: true }).click(),
    ]);
    expect(confirmResp.status(), await confirmResp.text()).toBe(200);
    await expect(toast(page, '确认成功')).toBeVisible();
    // 沉淀走同一 POST（from_confirmation=true），effective_from=max(权益登记日, 业务当日)（S4 B）。
    // 本场景权益登记日=除息日前一交易日（未来），max=权益登记日；历史权益登记日 clamp 由后端单测证伪。
    await expect.poll(async () => (await listConfigs(page, s)).length).toBe(1);
    const configs = await listConfigs(page, s);
    expect(configs[0].dividend_method).toBe('cash_dividend');
    expect(configs[0].source).toBe('explicit');
    expect(configs[0].effective_from).toBe(s.entitlement);
    expect(errors).toEqual([]);
  });

  test('配置列表读取失败呈现错误态而非空态，可重试（B2）', async ({ page }, info) => {
    const s = await setup(page, info, 'E');
    await page.goto(`/portfolio/${s.code}/platforms/${PLATFORM}`);
    await page.getByTestId('platform-holdings-section').waitFor({ state: 'visible', timeout: 15_000 });
    // 仅拦截列表端点（pathname 精确匹配，不误伤 /resolve）；标志位切换故障/恢复，
    // 避免 page.unroute 谓词引用不一致导致拦截未解除。
    let failList = true;
    await page.route((url) => url.pathname === '/api/dividend-configs', (route) => {
      if (failList) {
        return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: { error: 'INTERNAL', message: '模拟列表故障' } }) });
      }
      return route.continue();
    });
    await page.getByTestId(`dividend-config-entry-${PRODUCT.code}`).click();
    const dlg = configDialog(page);
    await expect(dlg.getByTestId('dividend-config-error')).toBeVisible();
    await expect(dlg.getByTestId('dividend-config-empty')).toHaveCount(0);
    await expect(dlg.getByTestId('dividend-config-retry')).toBeVisible();
    // 恢复后端后重试 → 空态（失败≠空态，重试可恢复）
    failList = false;
    await dlg.getByTestId('dividend-config-retry').click();
    await expect(dlg.getByTestId('dividend-config-empty')).toBeVisible();
  });

  test('非 CN_OTC 分红事件不显示「记住」（S1/A14）', async ({ page }, info) => {
    const s = await setup(page, info, 'H');
    // CN_EXCHANGE 现金分红人工事件（允许登记），force_cover 绕过平台覆盖校验
    const event = await post<ShareChangeEvent>(page, s.headers, '/api/share-change-events?force_cover=true', {
      portfolio_code: s.code, product_code: '510300.SH', market: 'CN_EXCHANGE',
      platform_code: PLATFORM, event_type: 'cash_dividend',
      entitlement_date: s.entitlement, ex_date: s.ex, div_cash: 0.1,
    });
    await gotoPortfolioSubpage(page, s.code as PortfolioCode, 'share-change-events');
    await page.getByTestId(`share-event-${event.id}`).getByTitle('确认', { exact: true }).click();
    const dlg = confirmDialog(page);
    await expect(dlg.getByLabel('分红方式', { exact: true })).toBeVisible();
    await expect(dlg.getByLabel('记住本次方式，以后自动入账')).toHaveCount(0);
  });
});