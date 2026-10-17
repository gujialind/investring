/**
 * 前端 E2E：现金流水页（issue #725，PR-2 双端）
 *
 * 只验证**前端可观测行为**（后端账本口径由 PR-1 的 backend/tests/integration/test_cash_flows_*
 * 承担）：页面从 `GET /portfolios/{code}/cash-flows` 渲染、概览四卡、(平台,现金日期) rowSpan
 * 合并段（桌面）/ 每卡重复平台（移动）、「当日另有 N 笔」+ 当日弹窗、流水详情（对账块 +
 * 预计到账投影 + 前端推算文案）、按平台构成弹窗（看明细钻穿 / 更新预选平台 / 合计行无更新）、
 * 在途行「不计入余额」注记、ManageLinksCard 第 6 项入口。
 *
 * 数据红线（M11 / helpers 种子契约）：**自建隔离组合**造数，绝不对 E2E_ACTIVE 做
 * recalculate/generate-next；导航用例（无写操作）才复用 E2E_ACTIVE 详情页只读点击。
 * 隔离组合 code 区分 project/worker/retry（照 trade-in-transit.spec.ts 的槽位口径），
 * 「跑 E2E 前重启后端重灌种子」是幂等前提。日期经 /api/trading-calendar 锚定（#468），
 * 净值夹具由后端 seed_e2e_active 提供（000300.OF/CN_OTC）。
 */
import { test, expect, type Page, type TestInfo } from '@playwright/test';
import {
  authHeaders,
  collectPageErrors,
  dialogByTitle,
  gotoPortfolioDetail,
  gotoPortfolioSubpage,
  openFilterPanelIfMobile,
  openPopover,
  settlePopovers,
  toISODate,
  E2E_ACTIVE,
  type PortfolioCode,
} from './helpers';

const PLATFORM = 'HBZQ'; // 华宝证券（= 缺省申赎/交易平台）
const ARRIVAL = 'TTJJ'; // 天天基金（转移转入方，制造跨平台在途腿）
const PRODUCT = { code: '000300.OF', market: 'CN_OTC' };
const SUB_AMOUNT = 40000;
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

/** 取认证头前先落到应用同源页（about:blank 读 localStorage 会抛 SecurityError，见 trade-in-transit） */
async function openAppAndAuth(page: Page): Promise<{ Authorization: string }> {
  await page.goto('/');
  await expect(page.locator('body')).toBeVisible();
  return authHeaders(page);
}

/** today 起最近一个交易日（含 today）；当年无开市日则回看上一年末（#468） */
async function nearestTradingDay(page: Page, headers: { Authorization: string }): Promise<string> {
  const today = toISODate(new Date());
  for (const year of [Number(today.slice(0, 4)), Number(today.slice(0, 4)) - 1]) {
    const rows = (await (
      await page.request.get(`/api/trading-calendar?year=${year}`, { headers })
    ).json()) as { calendar_date: string; is_open: boolean }[];
    const open = rows
      .filter((r) => r.is_open && r.calendar_date <= today)
      .map((r) => r.calendar_date)
      .sort();
    if (open.length > 0) return open[open.length - 1];
  }
  throw new Error(`交易日历中找不到 ${today} 起最近一个交易日`);
}

/** d 的前一工作日（使首购确认日恰落 d：申请日下一交易日 = d） */
function prevWorkday(d: string): string {
  const apply = new Date(`${d}T00:00:00`);
  do {
    apply.setDate(apply.getDate() - 1);
  } while (apply.getDay() === 0 || apply.getDay() === 6);
  return toISODate(apply);
}

/** 本 worker 独占隔离组合 code（≤20 字符）：E2E725(6)+日期(8)+proj(1)+worker(1)+retry(1)+后缀(1)=18 */
function isolatedCode(testInfo: TestInfo, suffix: string): string {
  const day = toISODate(new Date()).replace(/-/g, '');
  const proj = testInfo.project.name === 'mobile' ? 'M' : 'D';
  return `E2E725${day}${proj}${testInfo.workerIndex}${testInfo.retry}${suffix}`;
}

/** 首购注资：申购（申请日=d 前一工作日 → 确认日=d）+ 确认 → sub_buy 已生效现金流入 @ (PLATFORM, d) */
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
    amount: SUB_AMOUNT,
    apply_date: prevWorkday(d),
  });
  expect(sub.confirm_date, '首购确认日应为 d 当天').toBe(d);
  await post(page, headers, `/api/subscriptions/${sub.id}/confirm`);
}

/** 生成 target 单日快照（余额列 = calculate_available_cash(as_of=cash_date) 需快照基线） */
async function generateSnapshot(
  page: Page,
  headers: { Authorization: string },
  code: string,
  target: string,
): Promise<void> {
  const r = await post<{ success: boolean }>(page, headers, '/api/snapshots/generate', {
    portfolio_code: code,
    target_date: target,
  });
  expect(r.success, `${target} 快照生成失败`).toBe(true);
}

/** 流水类型 Select（shadcn combobox，placeholder 即当前回显）；移动端先展开折叠面板 */
async function pickFlowType(page: Page, testInfo: TestInfo, label: string): Promise<void> {
  await openFilterPanelIfMobile(page, testInfo);
  const trigger = page.getByRole('combobox').filter({ hasText: '全部流水类型' });
  const option = page.getByRole('option', { name: label });
  await openPopover(page, trigger, option);
  await option.click({ timeout: 10_000 });
  await settlePopovers(page);
}

/** 状态 Select（在途 / 已生效） */
async function pickStatus(page: Page, testInfo: TestInfo, label: string): Promise<void> {
  await openFilterPanelIfMobile(page, testInfo);
  const trigger = page.getByRole('combobox').filter({ hasText: '全部状态' });
  const option = page.getByRole('option', { name: label });
  await openPopover(page, trigger, option);
  await option.click({ timeout: 10_000 });
  await settlePopovers(page);
}

test.describe('现金流水页（#725，双端）', () => {
  // #16：ManageLinksCard 第 6 项进入本页（只读点击 E2E_ACTIVE 详情页，无写操作）
  test('组合详情「现金流水」入口进入本页', async ({ page }) => {
    const errors = collectPageErrors(page);
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await page.getByRole('link', { name: '现金流水' }).click();
    await expect(page.getByRole('heading', { level: 1, name: '现金流水' })).toBeVisible({
      timeout: 15_000,
    });
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // #2/#6/#7/#8/#13/#14：同日两笔（首购 + 调仓买入）→ 合并段 / 当日另有 / 详情 / 构成弹窗
  test('概览四卡 · 同日合并 · 当日另有 N 笔 · 详情 · 按平台构成', async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const d = await nearestTradingDay(page, headers);
    const code = isolatedCode(testInfo, 'R');

    await post(page, headers, '/api/portfolios', { code, name: `现金流水渲染 ${code}` });
    await seedSubscription(page, headers, code, d);
    // 调仓买入：CASH 扣款腿创建即 confirmed（rebal_buy 已生效 @ (PLATFORM, d)），基金腿 pending
    await post(page, headers, '/api/trades', {
      portfolio_code: code,
      product_code: PRODUCT.code,
      market: PRODUCT.market,
      platform_code: PLATFORM,
      trade_type: 'buy',
      trade_date: d,
      amount: BUY_AMOUNT,
      fee: 0,
    });
    await generateSnapshot(page, headers, code, d);

    await gotoPortfolioSubpage(page, code as PortfolioCode, 'cash-flows');

    // 概览四卡（#725 断言组：存量/流量四指标）
    for (const k of ['available', 'in_transit', 'inflow', 'outflow']) {
      await expect(page.getByTestId(`stat-${k}`)).toBeVisible();
    }

    const isMobile = testInfo.project.name === 'mobile';
    if (isMobile) {
      // #2 移动：不做 rowSpan，每卡重复显示平台；同日两笔各成一张卡
      const cards = page.getByTestId('cash-flow-row');
      await expect(cards).toHaveCount(2);
      await expect(cards.filter({ hasText: '申购 · 注资入账' }).first()).toBeVisible();
      await expect(cards.filter({ hasText: '调仓 · 买入扣款' }).first()).toBeVisible();
    } else {
      // #2 桌面：同 (平台,日) 两笔合并 → 2 行、1 个组格（rowspan=2）、1 个余额格
      await expect(page.getByTestId('cash-flow-row')).toHaveCount(2);
      await expect(page.getByTestId('group-cell')).toHaveCount(1);
      await expect(page.getByTestId('balance-cell')).toHaveCount(1);
      await expect(page.getByTestId('group-cell')).toHaveAttribute('rowspan', '2');
    }

    // #7：筛「调仓 · 买入扣款」后在屏只剩 1 行，同组总笔数 2 → 「当日另有 1 笔」
    await pickFlowType(page, testInfo, '调仓 · 买入扣款');
    const sameDayLink = page.getByTestId('same-day-link');
    await expect(sameDayLink).toBeVisible({ timeout: 15_000 });
    await expect(sameDayLink).toContainText('当日');
    await expect(sameDayLink).toContainText('1 笔');

    // #8：点开当日弹窗 → 列出该平台当日全部（含被类型筛选隐藏的申购行）
    await sameDayLink.click();
    const sameDay = dialogByTitle(page, '现金流水');
    await sameDay.waitFor({ timeout: 10_000 });
    await expect(sameDay.getByTestId('recon-block')).toBeVisible();
    await expect(sameDay.getByText('申购 · 注资入账').first()).toBeVisible();
    await sameDay.getByRole('button', { name: '关闭' }).click();
    await expect(sameDay).toBeHidden({ timeout: 15_000 });
    // 关窗回原筛选：类型筛选仍在（在屏仍只剩调仓行）
    await expect(page.getByTestId('same-day-link')).toBeVisible();

    // #6：详情弹窗 → 对账块 + 关联业务（调仓 CASH 腿 pair_ref 指基金腿）
    await page.getByTestId('row-action').first().click();
    const detail = dialogByTitle(page, '流水详情');
    await detail.waitFor({ timeout: 10_000 });
    await expect(detail.getByTestId('recon-block')).toBeVisible();
    await expect(detail.getByText('关联业务').first()).toBeVisible();
    await detail.getByRole('button', { name: '关闭' }).click();
    await expect(detail).toBeHidden({ timeout: 15_000 });

    // #13/#14：按平台构成弹窗
    await page.getByTestId('open-breakdown').click();
    const breakdown = dialogByTitle(page, '按平台现金构成');
    await breakdown.waitFor({ timeout: 10_000 });
    await expect(breakdown.getByTestId('breakdown-row').first()).toBeVisible();
    if (!isMobile) {
      // 合计行无「更新」：构成表最后一行（合计）不含 breakdown-update
      const table = breakdown.getByTestId('breakdown-table');
      await expect(table.locator('tbody tr').last()).toContainText('合计');
      await expect(table.locator('tbody tr').last().getByTestId('breakdown-update')).toHaveCount(0);
    }
    // 「看明细」钻穿：应用该平台筛选并关闭弹窗
    await breakdown.getByTestId('breakdown-drill').first().click();
    await expect(breakdown).toBeHidden({ timeout: 15_000 });
    // 重新打开 → 「更新」以该平台预选启动更新现金市值
    await page.getByTestId('open-breakdown').click();
    await breakdown.waitFor({ timeout: 10_000 });
    await breakdown.getByTestId('breakdown-update').first().click();
    const mmv = dialogByTitle(page, '更新现金市值');
    await mmv.waitFor({ timeout: 10_000 });
    await expect(mmv.getByTestId('platform-trigger').filter({ hasText: '华宝证券' })).toBeVisible();

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // #9/#10：跨天转移转入腿在途 → 「不计入余额」注记 + 详情投影块 + 前端推算文案
  test('在途转移腿：不计入余额注记 + 预计到账投影（前端推算）', async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const d = await nearestTradingDay(page, headers);
    const code = isolatedCode(testInfo, 'T');

    await post(page, headers, '/api/portfolios', { code, name: `现金流水在途 ${code}` });
    await seedSubscription(page, headers, code, d);
    // 跨天转移：转出腿当日 confirmed，转入腿 pending 至下一交易日（在途、未生效）。
    // 必须在**任何快照之前**造转移——create_cash_transfer 要求 transfer_date 严格晚于
    // 最新快照日（DATE_BEFORE_SNAPSHOT）。本页余额列与详情投影走 calculate_available_cash
    // 实时算（snapshot-independent），故本用例无需生成快照即可断言在途注记与投影块。
    await post(page, headers, `/api/portfolios/${code}/cash-transfer`, {
      from_platform: PLATFORM,
      to_platform: ARRIVAL,
      amount: TRANSFER_AMOUNT,
      transfer_date: d,
      cross_day: true,
    });

    await gotoPortfolioSubpage(page, code as PortfolioCode, 'cash-flows');
    // 状态筛选「在途」→ 只剩转入 pending 腿
    await pickStatus(page, testInfo, '在途');

    const row = page.getByTestId('cash-flow-row').filter({ hasText: '平台间转移' }).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    // #9：未到账行金额注「不计入余额」
    await expect(row.getByText('不计入余额').first()).toBeVisible();

    // #10：pending 转入腿详情弹窗投影块 + 明写「不是后端 calculate_available_cash 的结果」。
    // 投影只在 pending 转入腿（action=confirm，余额不含本笔）上数值正确，故走该行的「详情」
    // 入口（确认到账按钮旁）；confirmed-未来腿的余额列已含本笔，前端不投影（见 lib 注释）。
    await row.getByTestId('row-detail').first().click();
    const detail = dialogByTitle(page, '流水详情');
    await detail.waitFor({ timeout: 10_000 });
    const projection = detail.getByTestId('projection-block');
    await expect(projection).toBeVisible();
    await expect(projection).toContainText('不是');
    await expect(projection).toContainText('calculate_available_cash');

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
