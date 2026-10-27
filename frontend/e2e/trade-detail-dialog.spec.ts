/**
 * 前端 E2E：只读交易详情弹窗 + `?trade=` 深链（issue #789，双端）
 *
 * 覆盖验收断言：
 * - 现金流水「关联业务」按 pair_ref.kind 分流可点：fund_leg → 弹窗内打开只读交易详情；
 *   transfer_leg → 关流水详情、定位转移记录组弹窗
 * - 详情弹窗只读：无编辑/确认/删除按钮；CASH 腿不出现「份额/价格」行（#732 同口径，
 *   不把记账单位 1.0000 渲成净值）
 * - 调仓页每行（含 CASH 腿）行内「详情」入口；开关事实源 = URL `?trade=<id>`：
 *   关闭清 trade 参、保留其他筛选参数；后退/前进行为可预期
 * - 深链直达：交易不在当前筛选内（trade_type=sell 排除买入）也自动打开详情；
 *   mobile project 经 middleware 重定向到 /m/...，query 保留，同用例覆盖移动端深链
 *
 * 数据红线（照 cash-flows.spec.ts）：自建隔离组合造数，不碰 E2E_ACTIVE；日期经
 * /api/trading-calendar 锚定；调仓买入的 CASH 扣款腿创建即 confirmed（#493）。
 */
import { test, expect, type Page } from '@playwright/test';
import {
  collectPageErrors,
  dialogByTitle,
  gotoPortfolioSubpage,
  isolatedPortfolioCode,
  nearestTradingDay,
  openAppAndAuth,
  portfolioPath,
  toISODate,
  type PortfolioCode,
} from './helpers';

const PLATFORM = 'HBZQ'; // 华宝证券（= 缺省交易平台）
const ARRIVAL = 'TTJJ'; // 天天基金（转移对方平台）
const PRODUCT = { code: '000300.OF', market: 'CN_OTC' };
const BUY_AMOUNT = 10000;
const TRANSFER_AMOUNT = 5000;

async function post<T>(
  page: Page,
  headers: { Authorization: string },
  path: string,
  data?: unknown,
): Promise<T> {
  const resp = await page.request.post(path, { headers, data });
  expect(resp.ok(), `POST ${path} → ${resp.status()} ${await resp.text()}`).toBeTruthy();
  return (await resp.json()) as T;
}

/** d 的前一工作日（使首购确认日恰落 d：申请日下一交易日 = d） */
function prevWorkday(d: string): string {
  const apply = new Date(`${d}T00:00:00`);
  do {
    apply.setDate(apply.getDate() - 1);
  } while (apply.getDay() === 0 || apply.getDay() === 6);
  return toISODate(apply);
}

/** 首购注资：申购（申请日=d 前一工作日 → 确认日=d）+ 确认 → 现金入账 @ (PLATFORM, d) */
async function seedSubscription(
  page: Page,
  headers: { Authorization: string },
  code: string,
  d: string,
): Promise<void> {
  const sub = await post<{ id: number; confirm_date: string }>(page, headers, '/api/subscriptions', {
    portfolio_code: code,
    investor_code: 'ADMIN',
    platform_code: PLATFORM,
    sub_type: 'subscribe',
    amount: 40000,
    apply_date: prevWorkday(d),
  });
  expect(sub.confirm_date, '首购确认日应为 d 当天').toBe(d);
  await post(page, headers, `/api/subscriptions/${sub.id}/confirm`);
}

/** 调仓买入：基金腿 pending + CASH 扣款腿创建即 confirmed；返回基金腿 id（深链用） */
async function seedBuyTrade(
  page: Page,
  headers: { Authorization: string },
  code: string,
  d: string,
): Promise<number> {
  const t = await post<{ id: number }>(page, headers, '/api/trades', {
    portfolio_code: code,
    product_code: PRODUCT.code,
    market: PRODUCT.market,
    platform_code: PLATFORM,
    trade_type: 'buy',
    trade_date: d,
    amount: BUY_AMOUNT,
    fee: 0,
  });
  return t.id;
}

test.describe('只读交易详情弹窗与深链（#789，双端）', () => {
  // fund_leg 关联业务 → 弹窗内打开只读交易详情；弹窗无写操作按钮
  test('现金流水：fund_leg 关联业务点开只读交易详情', async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const d = await nearestTradingDay(page, headers);
    const code = isolatedPortfolioCode('E2E789', testInfo, 'F');

    await post(page, headers, '/api/portfolios', { code, name: `详情弹窗 ${code}` });
    await seedSubscription(page, headers, code, d);
    await seedBuyTrade(page, headers, code, d);

    await gotoPortfolioSubpage(page, code as PortfolioCode, 'cash-flows');

    // 调仓买入扣款行的 pair_ref 指向基金腿（fund_leg）
    const rebalRow = page.getByTestId('cash-flow-row').filter({ hasText: '调仓 · 买入扣款' });
    await rebalRow.getByTestId('row-action').first().click();
    const detail = dialogByTitle(page, '流水详情');
    await detail.waitFor({ timeout: 10_000 });
    await expect(detail.getByTestId('pair-ref-fund-leg')).toBeVisible();
    await detail.getByTestId('pair-ref-fund-leg').click();

    // 共享只读详情弹窗：与调仓交易页行内入口同组件（嵌套打开，流水详情保持原状）
    const trade = page.getByTestId('trade-detail-dialog');
    await expect(trade.getByTestId('trade-detail-body')).toBeVisible({ timeout: 10_000 });
    await expect(trade.getByTestId('trade-detail-body')).toContainText('000300');
    await expect(trade.getByTestId('trade-detail-body')).toContainText('¥10,000.00');
    // 基金腿 pending → 状态「待确认」+ 配对 CASH 腿块可见（paired_leg 填充，#789 后端同形）
    await expect(trade.getByTestId('trade-detail-body')).toContainText('待确认');
    await expect(trade.getByTestId('paired-leg-block')).toBeVisible();
    // 只读契约：弹窗内无任何写操作按钮（写操作一律回到调仓页生命周期矩阵）
    await expect(trade.getByRole('button', { name: '编辑' })).toHaveCount(0);
    await expect(trade.getByRole('button', { name: '确认' })).toHaveCount(0);
    await expect(trade.getByRole('button', { name: '删除' })).toHaveCount(0);

    // 关闭详情 → 回到流水详情弹窗（现金流水页的转移「确认到账」/重估「撤销」入口不受影响）
    await trade.getByRole('button', { name: 'Close' }).click();
    await expect(trade).toBeHidden({ timeout: 15_000 });
    await expect(detail.getByText('关联业务').first()).toBeVisible();

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // transfer_leg 关联业务 → 定位转移记录组；顺带覆盖 CASH 腿详情的现金语义（无份额/价格行）
  test('现金流水：transfer_leg 关联业务定位转移记录组；CASH 腿详情无份额净值语义', async ({
    page,
  }, testInfo) => {
    test.setTimeout(90_000);
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const d = await nearestTradingDay(page, headers);
    const code = isolatedPortfolioCode('E2E789', testInfo, 'T');

    await post(page, headers, '/api/portfolios', { code, name: `转移关联 ${code}` });
    await seedSubscription(page, headers, code, d);
    // 当日完成转移：两腿同组（transfer_group 12 位十六进制），两腿 CASH
    await post(page, headers, `/api/portfolios/${code}/cash-transfer`, {
      from_platform: PLATFORM,
      to_platform: ARRIVAL,
      amount: TRANSFER_AMOUNT,
      transfer_date: d,
    });

    await gotoPortfolioSubpage(page, code as PortfolioCode, 'cash-flows');

    const transferRow = page.getByTestId('cash-flow-row').filter({ hasText: '平台间转移' });
    await transferRow.getByTestId('row-action').first().click();
    const detail = dialogByTitle(page, '流水详情');
    await detail.waitFor({ timeout: 10_000 });
    await expect(detail.getByTestId('pair-ref-transfer-leg')).toBeVisible();
    // 定位转移记录组：关流水详情、开转移记录弹窗
    await detail.getByTestId('pair-ref-transfer-leg').click();
    await expect(detail).toBeHidden({ timeout: 15_000 });
    const list = dialogByTitle(page, '现金转移记录');
    await list.waitFor({ timeout: 10_000 });
    await list.getByRole('button', { name: 'Close' }).click();
    await expect(list).toBeHidden({ timeout: 15_000 });

    // CASH 腿详情（调仓页行内入口）：转移卖出主行 = 现金类资产产品行
    await gotoPortfolioSubpage(page, code as PortfolioCode, 'trades');
    const cashMain = page.getByRole('row').filter({ hasText: '现金类资产' }).first();
    await cashMain.getByRole('button', { name: '详情' }).click();
    const trade = page.getByTestId('trade-detail-dialog');
    await expect(trade.getByTestId('trade-detail-body')).toBeVisible({ timeout: 10_000 });
    await expect(trade.getByTestId('trade-detail-body')).toContainText('¥5,000.00');
    // CASH 腿无份额/净值语义（#732 同口径）：这两行整行不渲染
    await expect(trade.getByTestId('detail-row').filter({ hasText: '份额' })).toHaveCount(0);
    await expect(trade.getByTestId('detail-row').filter({ hasText: '价格' })).toHaveCount(0);

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // 行内开关 + URL 事实源 + 后退前进 + 深链直达（不在当前筛选内）
  test('调仓页：行内详情开关 · 关闭清参保留筛选 · 后退前进 · 深链直达', async ({
    page,
  }, testInfo) => {
    test.setTimeout(90_000);
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const d = await nearestTradingDay(page, headers);
    const code = isolatedPortfolioCode('E2E789', testInfo, 'S');

    await post(page, headers, '/api/portfolios', { code, name: `详情深链 ${code}` });
    await seedSubscription(page, headers, code, d);
    const tradeId = await seedBuyTrade(page, headers, code, d);
    const tradesUrl = portfolioPath(code as PortfolioCode, 'trades');

    // 带其他筛选参数进入（platform 预填 = 「关闭清参保留其他筛选」的判据）
    await page.goto(`${tradesUrl}?platform=${PLATFORM}`);
    await page.getByRole('button', { name: '提交交易' }).first().waitFor({ state: 'visible', timeout: 15_000 });

    // 行内「详情」→ URL 带 trade 参、弹窗打开
    const trade = page.getByTestId('trade-detail-dialog');
    await page.getByRole('button', { name: '详情' }).first().click();
    await expect(trade.getByTestId('trade-detail-body')).toBeVisible({ timeout: 10_000 });
    await page.waitForURL(/[?&]trade=\d+/);
    expect(new URL(page.url()).searchParams.get('platform')).toBe(PLATFORM);

    // 关闭（X）→ trade 参清除、platform 保留
    await trade.getByRole('button', { name: 'Close' }).click();
    await expect(trade).toBeHidden({ timeout: 15_000 });
    await page.waitForURL((url) => !url.searchParams.has('trade'));
    expect(new URL(page.url()).searchParams.get('platform')).toBe(PLATFORM);

    // 后退/前进行为可预期：open 走 push（后退关弹窗），再前进弹窗重开
    await page.getByRole('button', { name: '详情' }).first().click();
    await expect(trade.getByTestId('trade-detail-body')).toBeVisible({ timeout: 10_000 });
    await page.goBack();
    await expect(trade).toBeHidden({ timeout: 15_000 });
    await page.goForward();
    await expect(trade.getByTestId('trade-detail-body')).toBeVisible({ timeout: 10_000 });
    await trade.getByRole('button', { name: 'Close' }).click();
    await expect(trade).toBeHidden({ timeout: 15_000 });

    // 深链直达：平台筛选（TTJJ 在本组合无任何交易）把列表筛空，深链仍自动打开详情。
    // 注意 trade_type=sell 筛不空——基金买入的 CASH 扣款腿 trade_type=sell（扣款=卖出现金）
    await page.goto(`${tradesUrl}?platform=${ARRIVAL}&trade=${tradeId}`);
    // 探针：先确认 URL 事实源正确（区分「导航丢参」与「筛选未生效」）
    await page.waitForURL(new RegExp(`platform=${ARRIVAL}`));
    expect(page.url()).toContain(`trade=${tradeId}`);
    await expect(trade.getByTestId('trade-detail-body')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('无符合筛选条件的记录')).toBeVisible();

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
