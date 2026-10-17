/**
 * 前端 E2E：现金流水页（issue #725，PR-2 双端）
 *
 * 只验证**前端可观测行为**（后端账本口径由 PR-1 的 backend/tests/integration/test_cash_flows_*
 * 承担）：页面从 `GET /portfolios/{code}/cash-flows` 渲染、概览四卡、(平台,现金日期) rowSpan
 * 合并段（桌面）/ 每卡重复平台（移动）、「当日另有 N 笔」+ 当日弹窗、流水详情（对账块 +
 * 预计到账投影 + 前端推算文案）、按平台构成弹窗（看明细钻穿 / 更新预选平台 / 合计行无更新）、
 * 在途行「不计入余额」注记、ManageLinksCard 第 6 项入口；#727 评审补充：四卡/余额列/
 * 投影金额断言、写入口（现金转移）提交成功后读侧免刷新自动更新、对账块不被状态筛选污染。
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
  pickCalendarDay,
  pickPlatformOption,
  platformPopover,
  platformTrigger,
  settlePopovers,
  toISODate,
  toastByTitle,
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

/** d 之前最近一个**交易日**（跨天转移的转入腿现金日 = transfer_date 的下一交易日） */
async function prevTradingDay(
  page: Page,
  headers: { Authorization: string },
  d: string,
): Promise<string> {
  for (const year of [Number(d.slice(0, 4)), Number(d.slice(0, 4)) - 1]) {
    const rows = (await (
      await page.request.get(`/api/trading-calendar?year=${year}`, { headers })
    ).json()) as { calendar_date: string; is_open: boolean }[];
    const open = rows
      .filter((r) => r.is_open && r.calendar_date < d)
      .map((r) => r.calendar_date)
      .sort();
    if (open.length > 0) return open[open.length - 1];
  }
  throw new Error(`交易日历中找不到 ${d} 之前最近一个交易日`);
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

    // 概览四卡（#725 断言组：存量/流量四指标；金额断言 = #727 S5）
    // 首购 +40,000 已生效、调仓买入 -10,000 已生效：可用 30,000 / 在途 0 / 流入 +40,000 / 流出 -10,000
    const statAmounts: Record<string, string> = {
      available: '¥30,000.00',
      in_transit: '¥0.00',
      inflow: '+¥40,000.00',
      outflow: '-¥10,000.00',
    };
    for (const [k, v] of Object.entries(statAmounts)) {
      await expect(page.getByTestId(`stat-${k}`)).toBeVisible();
      await expect(page.getByTestId(`stat-${k}`)).toContainText(v);
    }

    const isMobile = testInfo.project.name === 'mobile';
    if (isMobile) {
      // #2 移动：不做 rowSpan，每卡重复显示平台；同日两笔各成一张卡
      const cards = page.getByTestId('cash-flow-row');
      await expect(cards).toHaveCount(2);
      await expect(cards.filter({ hasText: '申购 · 注资入账' }).first()).toBeVisible();
      await expect(cards.filter({ hasText: '调仓 · 买入扣款' }).first()).toBeVisible();
      // 余额列（卡片形态：日终余额行）= calculate_available_cash(as_of=d, HBZQ)（#727 S5）
      await expect(cards.filter({ hasText: '申购 · 注资入账' }).first()).toContainText('¥30,000.00');
    } else {
      // #2 桌面：同 (平台,日) 两笔合并 → 2 行、1 个组格（rowspan=2）、1 个余额格
      await expect(page.getByTestId('cash-flow-row')).toHaveCount(2);
      await expect(page.getByTestId('group-cell')).toHaveCount(1);
      await expect(page.getByTestId('balance-cell')).toHaveCount(1);
      await expect(page.getByTestId('group-cell')).toHaveAttribute('rowspan', '2');
      // 合并余额格的值（#727 S5）
      await expect(page.getByTestId('balance-cell')).toContainText('¥30,000.00');
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
    // 「共 N 笔」与对账块同源 = 不按状态过滤的同日全组（#727 B2）
    await expect(
      sameDay.getByText('当日该平台共 2 笔（含被当前筛选隐藏的行）')
    ).toBeVisible({ timeout: 10_000 });
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

    // 四卡金额（#727 S5）：转出腿已生效（HBZQ 40,000-5,000），转入腿在途只进 in_transit；
    // 组合口径流量卡不含平台间转移（内部搬运）→ 流入仅首购 +40,000、流出 0
    await expect(page.getByTestId('stat-available')).toContainText('¥35,000.00');
    await expect(page.getByTestId('stat-in_transit')).toContainText('¥5,000.00');
    await expect(page.getByTestId('stat-inflow')).toContainText('+¥40,000.00');
    await expect(page.getByTestId('stat-outflow')).toContainText('¥0.00');
    // 余额列（申购行所在 (HBZQ, d) 组）：日终 = 40,000 - 5,000（转出腿已生效）
    await expect(
      page.getByTestId('cash-flow-row').filter({ hasText: '申购 · 注资入账' }).first()
    ).toContainText('¥35,000.00');

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
    // 投影合计（#727 S5）：TTJJ 当前日终 0 + 本笔到账 5,000 = 合计 ¥5,000.00
    await expect(projection).toContainText('+5,000.00');
    await expect(projection).toContainText('¥5,000.00');

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // #727 B1/S5：页头「现金转移」提交成功后，读侧经 cashFlows.root 失效**免手动刷新**
  // 自动更新（列表出现两腿新行 + 存量卡变化）
  test('现金转移提交成功后读侧刷新（#727 B1）', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const d = await nearestTradingDay(page, headers);
    const code = isolatedCode(testInfo, 'F');

    await post(page, headers, '/api/portfolios', { code, name: `现金流水刷新 ${code}` });
    await seedSubscription(page, headers, code, d);

    await gotoPortfolioSubpage(page, code as PortfolioCode, 'cash-flows');
    await expect(page.getByTestId('stat-available')).toContainText('¥40,000.00');
    await expect(page.getByTestId('stat-in_transit')).toContainText('¥0.00');
    await expect(
      page.getByTestId('cash-flow-row').filter({ hasText: '平台间转移' })
    ).toHaveCount(0);

    // 走页头弹窗造跨天转移 HBZQ → TTJJ。转移日期必须是交易日：Dialog 默认客户端今天
    // （可能为非交易日），显式改到 d；勾选跨天 → 转入腿在途，四卡存量变化可断言
    await page.getByTestId('btn-transfer').click();
    const dlg = dialogByTitle(page, '平台间现金转移');
    await dlg.waitFor({ timeout: 10_000 });
    await openPopover(page, platformTrigger(dlg, '选择转出平台'), platformPopover(page));
    await pickPlatformOption(platformPopover(page), PLATFORM);
    await openPopover(page, platformTrigger(dlg, '选择转入平台'), platformPopover(page));
    await pickPlatformOption(platformPopover(page), ARRIVAL);
    await dlg.locator('input#transfer_amount').fill(String(TRANSFER_AMOUNT));
    await pickCalendarDay(page, dlg.locator('button#transfer_date'), d);
    await dlg.locator('input#cross_day').check();
    await dlg.getByRole('button', { name: '确认转移' }).click();

    await expect(toastByTitle(page, '转移成功')).toBeVisible({ timeout: 10_000 });
    await expect(dlg).toBeHidden({ timeout: 15_000 });

    // 不 reload 页面：mutation onSuccess 失效 cashFlows.root → 列表与四卡自动刷新。
    // 跨天转移两腿两行：转出腿已生效 (HBZQ, d)、转入腿在途 (TTJJ, 下一交易日)
    await expect(
      page.getByTestId('cash-flow-row').filter({ hasText: '平台间转移' })
    ).toHaveCount(2, { timeout: 15_000 });
    await expect(page.getByTestId('stat-available')).toContainText('¥35,000.00');
    await expect(page.getByTestId('stat-in_transit')).toContainText('¥5,000.00');

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // #727 B2：详情弹窗对账块用**不按 status 过滤**的同日全组数据——筛「在途」打开
  // pending 行详情时，同日已生效变动仍全部列出并闭合日终余额；列表自身筛选行为不变。
  // 造数不用二次申购：第二笔申购确认要求申请日存在组合净值快照（NAV_NOT_AVAILABLE），
  // 隔离组合内补快照成本高。改用「首购落 p → 同日转 20,000 去 TTJJ → TTJJ 跨天转回
  // 5,000（转入腿 pending、现金日 = p 的下一交易日 = d）→ d 日调仓买入 10,000」，
  // (HBZQ, d) 组即 1 笔已生效 + 1 笔在途，构成污染场景（全程无快照）。
  test('对账块不被状态筛选污染（#727 B2）', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const d = await nearestTradingDay(page, headers);
    const p = await prevTradingDay(page, headers, d);
    const code = isolatedCode(testInfo, 'S');

    await post(page, headers, '/api/portfolios', { code, name: `现金流水对账 ${code}` });
    await seedSubscription(page, headers, code, p); // (HBZQ, p) 首购 +40,000 已生效
    // 同日转移 HBZQ → TTJJ：给 TTJJ 备好可用现金（转移有 INSUFFICIENT_CASH 闸门）
    await post(page, headers, `/api/portfolios/${code}/cash-transfer`, {
      from_platform: PLATFORM,
      to_platform: ARRIVAL,
      amount: 20000,
      transfer_date: p,
      cross_day: false,
    });
    // 跨天转回：转出腿 (TTJJ, p) 已生效，转入腿 pending 落 (HBZQ, d)
    await post(page, headers, `/api/portfolios/${code}/cash-transfer`, {
      from_platform: ARRIVAL,
      to_platform: PLATFORM,
      amount: TRANSFER_AMOUNT,
      transfer_date: p,
      cross_day: true,
    });
    // (HBZQ, d) 组内的已生效行：调仓买入 CASH 腿（创建即 confirmed）
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

    await gotoPortfolioSubpage(page, code as PortfolioCode, 'cash-flows');
    await pickStatus(page, testInfo, '在途');
    // 列表自身的状态筛选行为保留：在屏只剩 pending 转入腿一行（同组已生效行被筛掉）
    await expect(page.getByTestId('cash-flow-row')).toHaveCount(1, { timeout: 15_000 });
    const row = page.getByTestId('cash-flow-row').filter({ hasText: '平台间转移' }).first();
    await expect(row).toBeVisible();

    await row.getByTestId('row-detail').first().click();
    const detail = dialogByTitle(page, '流水详情');
    await detail.waitFor({ timeout: 10_000 });
    const recon = detail.getByTestId('recon-block');
    await expect(recon).toBeVisible();
    // 对账块不被污染：同日已生效变动仍列出（修复前筛「在途」会把它滤掉、
    // 落「当日无已生效变动」且日终余额失去来源）
    await expect(recon).toContainText('当日已生效 · 调仓 · 买入扣款');
    await expect(recon).toContainText('-10,000.00');
    await expect(recon).not.toContainText('当日无已生效变动');
    // 日终 = 40,000 − 20,000 − 10,000（本笔 pending 转入腿不计入）
    await expect(recon).toContainText('¥10,000.00');
    // 投影块合计 = 日终 10,000 + 本笔到账 5,000（前端推算，#727 S5）
    await expect(detail.getByTestId('projection-block')).toContainText('¥15,000.00');
    await detail.getByRole('button', { name: '关闭' }).click();
    await expect(detail).toBeHidden({ timeout: 15_000 });

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
