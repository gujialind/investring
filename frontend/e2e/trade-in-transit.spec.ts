/**
 * 前端 E2E 测试：调仓在途资金生命周期（#493）
 *
 * 守护「单腿确认」的完整账务链路，两条链路各自走真实 UI：
 *   A. 买入「创建即扣款 → T 快照在途 → 确认后份额入账」
 *      - 创建弹窗明示「提交即记扣款、基金份额仍待确认」，且扣款平台只在买入侧出现；
 *      - 创建后基金腿 pending、读侧派生的 cash_platform_code/cash_confirm_date 即为扣款信息；
 *      - D 日快照（基金腿尚未生效）现金已扣、出现等额买入在途，total_value 无缺口；
 *      - 确认弹窗回显扣款平台/扣款日；确认后份额入账、D+1 快照在途归零。
 *   B. 卖出「创建无现金腿 → 确认录入到账日/到账平台 → C..A 在途 → 到账」
 *      - 创建弹窗**不出现**到账平台输入（到账信息一律在确认弹窗录入，创建期传入后端直接拒绝）；
 *      - 创建后组内只有基金腿（无 CASH 腿），列表只显示一行主行、无现金子行；
 *      - 确认弹窗出现「到账日期/到账平台」并通过 preview 回显有效值（缺省 A = C、平台 = 基金平台）；
 *      - C..A 之间快照记等额卖出在途、现金未增；到账日快照在途归零转为 CASH；
 *      - 已确认卖出的窄表单只提交到账日（+备注），其他财务字段仍走「先取消确认」提示。
 *
 * 数据说明（与 helpers.ts 的种子契约互补）：
 * - 本 spec **自建隔离组合**（`E2E493<当日><workerIndex>`，经 POST /api/portfolios）而不用
 *   种子契约组合：完整买卖链需要「零快照 → 逐日生成 → 连续快照」且不得与其它 spec 共享
 *   在途/现金状态；`E2E_PORT` 是零交易契约组合（被其它 spec 依赖）、`E2E_ACTIVE` 禁止
 *   recalculate/catch-up/generate-next（#354 红线）。每 worker 独立组合亦使本 spec 天然
 *   并行安全、重跑幂等（首购申购金额固定、账户为新建空账本，无需清理残留）。
 * - 现金注入经 API「申购 + 确认」（首窗净值 1.0000 无需行情）；确认日为申请日下一交易日，
 *   故申请日取 D 的前一工作日，使 CASH 腿 confirm_date 恰落 D 当天。
 * - 日期锚定经 `/api/trading-calendar` 取「today 起最近一个交易日」D 及其后 3 个交易日
 *   （#468：不写死年份、不假设周末、不用 `new Date()` 当交易日）。
 * - **价格夹具**：快照按产品 nav_lag_days 严格取价（#96/#178），场外基金确认后持仓需要
 *   D..D+3 的净值才能生成快照（D+3 是卖出到账日快照，份额已减但仍持仓）。夹具由后端
 *   E2E 种子 `seed_e2e_active` 以 ORM 写入 `000300.OF`/`CN_OTC`（净值与本文件的 NAV
 *   常量一一对应），**不是** 由本 spec 直连数据库——CI 的 E2E 栈跑 MySQL
 *   （`.github/workflows/e2e-stack.yml`），直连 SQLite 文件在 CI 上必然失败。种子侧由
 *   `backend/tests/integration/test_seed_contract.py::TestE2EActiveContract` 锁定，
 *   改净值口径须两侧同步。
 */
import { test, expect, type Locator, type Page, type TestInfo } from '@playwright/test';
import {
  authHeaders,
  collectPageErrors,
  dialogByTitle,
  gotoPortfolioSubpage,
  openFilterPanelIfMobile,
  openSubmitTradeDialog,
  platformPopover,
  productOption,
  productPopover,
  toastByTitle,
  type PortfolioCode,
} from './helpers';

/** 在途链路用的场外基金（种子产品，confirm_days=1 → 确认日 = 下单日下一交易日） */
const OTC_PRODUCT = { code: '000300.OF', market: 'CN_OTC', name: '沪深300联接A' };
/** 交易平台（= 缺省扣款/到账平台）与卖出改选的到账平台（种子 4 平台之一） */
const FUND_PLATFORM = 'HBZQ';
const ARRIVAL_PLATFORM = 'TTJJ';
/** 首购申购金额与买入金额：买入后现金 30000，卖出净额 3200（2000 份 × 1.6000） */
const CASH_INJECT = 40000;
const BUY_AMOUNT = 10000;
const SELL_SHARES = 2000;
/**
 * 净值口径（4 位小数）：与 `seed_e2e_active` 的 `000300.OF` 夹具**一一对应**
 * （D=1.5000、D+1=1.5500、D+2=1.6000）——D=1.5000 使买入得 6666.67 份，
 * 卖出确认取 D+2 净值 1.6000（2000 份 → 3200 元）。改这里必须同步改种子。
 */
const NAV: Record<'D' | 'D1' | 'D2', string> = { D: '1.5000', D1: '1.5500', D2: '1.6000' };

/** 本地日期 → yyyy-MM-dd（避免 toISOString 的 UTC 时移） */
function toISODate(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

/** 取「today 起最近一个交易日」（含 today）；当年尚无开市日则回看上一年末（#468 口径） */
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

/** D 之后的第 n 个交易日（后端 /trading-calendar/next 为唯一事实来源） */
async function nextTradingDays(
  page: Page,
  headers: { Authorization: string },
  from: string,
  n: number,
): Promise<string[]> {
  const out: string[] = [];
  let cursor = from;
  for (let i = 0; i < n; i++) {
    const resp = await page.request.get(
      `/api/trading-calendar/next?from_date=${cursor}&days=1`,
      { headers },
    );
    expect(resp.ok(), `取 ${cursor} 的下一交易日失败 ${resp.status()}`).toBeTruthy();
    cursor = ((await resp.json()) as { trading_day: string }).trading_day;
    out.push(cursor);
  }
  return out;
}

/**
 * 本 worker 独占的隔离组合 code（≤20 字符，`portfolio.code` 是 String(20)）。
 *
 * 必须同时区分 **project / worker / retry**，否则必然撞车：
 * - `chromium` 与 `mobile` 两个 project 都会跑本文件，而 `workerIndex`（与
 *   `parallelIndex`）都是**每个 project 内**从 0 重新计数 → 只用 worker 槽位会让
 *   mobile 复用 chromium 已建的同名组合，`POST /api/portfolios` 撞主键直接失败。
 * - CI 主 e2e job 带 `retries: 2`：失败重试若沿用同一 code，也会撞上上次残留的组合。
 *
 * 长度：`E2E493`(6) + 日期(8) + project(1) + worker(1) + retry(1) = 17，加用例后缀 1 位 = 18。
 */
function isolatedPortfolioCode(testInfo: {
  project: { name: string };
  workerIndex: number;
  retry: number;
}): string {
  const day = toISODate(new Date()).replace(/-/g, '');
  const proj = testInfo.project.name === 'mobile' ? 'M' : 'D';
  return `E2E493${day}${proj}${testInfo.workerIndex}${testInfo.retry}`;
}

/**
 * 建隔离组合 + 注入现金：POST /api/portfolios（draft）→ 申购（申请日 = D 前一工作日，
 * 确认日 = D）+ 确认。返回组合 code 与三个交易日。
 */
async function setupIsolatedPortfolio(
  page: Page,
  headers: { Authorization: string },
  code: string,
  days: [string, string, string],
): Promise<void> {
  const [d] = days;
  const createResp = await page.request.post('/api/portfolios', {
    data: { code, name: `E2E 在途资金链路 ${code}` },
    headers,
  });
  expect(
    createResp.ok(),
    `创建隔离组合失败 ${createResp.status()} ${await createResp.text()}`,
  ).toBeTruthy();

  const apply = new Date(`${d}T00:00:00`);
  do {
    apply.setDate(apply.getDate() - 1);
  } while (apply.getDay() === 0 || apply.getDay() === 6);

  const subResp = await page.request.post('/api/subscriptions', {
    data: {
      portfolio_code: code,
      investor_code: 'ADMIN',
      platform_code: FUND_PLATFORM,
      sub_type: 'subscribe',
      amount: CASH_INJECT,
      apply_date: toISODate(apply),
    },
    headers,
  });
  expect(
    subResp.ok(),
    `注入现金：创建申购失败 ${subResp.status()} ${await subResp.text()}`,
  ).toBeTruthy();
  const { id, confirm_date } = (await subResp.json()) as { id: number; confirm_date: string };
  expect(confirm_date, '首购确认日应为 D 当天（申请日的下一交易日）').toBe(d);

  const confirmResp = await page.request.post(`/api/subscriptions/${id}/confirm`, { headers });
  expect(
    confirmResp.ok(),
    `注入现金：确认申购失败 ${confirmResp.status()} ${await confirmResp.text()}`,
  ).toBeTruthy();
}

interface TradeRow {
  id: number;
  product_code: string;
  trade_type: string;
  status: string;
  trade_date: string;
  confirm_date: string | null;
  cash_platform_code: string | null;
  cash_confirm_date: string | null;
  amount: number | null;
  actual_amount: number | null;
  shares: number | null;
  transfer_group: string;
}

/** 组合全部基金腿（后端按 transfer_group 批量派生现金字段，不受分页/筛选截断） */
async function listTrades(
  page: Page,
  headers: { Authorization: string },
  code: string,
): Promise<TradeRow[]> {
  const resp = await page.request.get(
    `/api/trades?portfolio_code=${code}&page_size=100`,
    { headers },
  );
  expect(resp.ok(), `查询交易列表失败 ${resp.status()}`).toBeTruthy();
  return ((await resp.json()) as { items: TradeRow[] }).items;
}

/** 组合快照（按日期升序），供在途金额断言 */
async function listSnapshots(
  page: Page,
  headers: { Authorization: string },
  code: string,
): Promise<{ snapshot_date: string; total_value: number; in_transit_total: number | null }[]> {
  const resp = await page.request.get(`/api/snapshots/portfolios/${code}/list`, { headers });
  expect(resp.ok(), `查询快照列表失败 ${resp.status()}`).toBeTruthy();
  const body = (await resp.json()) as {
    items: { snapshot_date: string; total_value: number; in_transit_total: number | null }[];
  };
  return [...body.items].sort((a, b) => a.snapshot_date.localeCompare(b.snapshot_date));
}

/** 可用现金（后端无 as_of_date 参数，缺省即当天时点） */
async function availableCash(
  page: Page,
  headers: { Authorization: string },
  code: string,
): Promise<number> {
  const resp = await page.request.get(
    `/api/positions/portfolio/${code}/available-cash`,
    { headers },
  );
  expect(resp.ok(), `查询可用现金失败 ${resp.status()}`).toBeTruthy();
  return ((await resp.json()) as { available_cash: number }).available_cash;
}

/** 指定快照日的 CASH 持仓合计（跨平台求和；现金行判定用 cash_amount 非空） */
async function snapshotCash(
  page: Page,
  headers: { Authorization: string },
  code: string,
  snapshotDate: string,
): Promise<number> {
  const resp = await page.request.get(
    `/api/positions?portfolio_code=${code}&snapshot_date=${snapshotDate}&page_size=100`,
    { headers },
  );
  expect(resp.ok(), `查询 ${snapshotDate} 持仓失败 ${resp.status()}`).toBeTruthy();
  const body = (await resp.json()) as {
    items: { product_code: string; cash_amount: number | null }[];
  };
  return body.items
    .filter((p) => p.product_code === 'CASH' && p.cash_amount !== null)
    .reduce((sum, p) => sum + (p.cash_amount ?? 0), 0);
}

/**
 * 在 DatePicker 弹层内把触发按钮的值设为 targetISO（三个日期 helper 共用）。
 *
 * 「目标日 == 控件当前值」时**直接跳过、不点开弹层**：react-day-picker@10 单选
 * 模式（`calendar.tsx` 未传 `required`）会把「点已选日」当成取消选择
 * （`useSingle`: `!required && isSameDay → onSelect(undefined)`），而
 * `date-picker.tsx` 只在 newDate 非空时才关弹层——结果是值被清空、弹层还开着。
 * CI 2026-09-16 实踩：当天是交易日，目标 D == 表单默认值 today 两点重合才炸；
 * `trade-buy-amount-linkage.spec.ts` 早用「≠ today 才选」绕开过同一坑。
 * 目标日与 today 最多相差 4 个交易日，日历默认展示当前月（或已选日所在月），
 * 故 `data-day` 不在当前月时按月翻一次。
 */
async function pickDay(page: Page, trigger: Locator, targetISO: string): Promise<void> {
  if ((await trigger.textContent())?.trim() === targetISO) return;
  await trigger.click();
  await page.locator('button.rdp-day_button').first().waitFor();
  const day = page.locator(`button.rdp-day_button[data-day="${targetISO}"]`);
  if ((await day.count()) === 0) {
    const dir = targetISO > toISODate(new Date()) ? 'next' : 'previous';
    await page.locator(`button.rdp-button_${dir}`).click();
  }
  await day.click();
  await expect(trigger).toHaveText(targetISO);
}

/**
 * 经「单日生成」弹窗生成 targetISO 的快照（预检验证 → 确认生成）。
 * 与 datepicker-in-dialog.spec.ts 同口径：目标日与 today 相差至多 3 天，
 * 日历默认展示当前月，必要时按月翻一次。
 */
async function generateSnapshot(page: Page, targetISO: string): Promise<void> {
  await page.getByRole('button', { name: '单日生成' }).click();
  const dlg = dialogByTitle(page, '生成单日快照');
  await dlg.waitFor();

  const trigger = dlg.locator('button').filter({ hasText: /选择日期|\d{4}-\d{2}-\d{2}/ }).first();
  await pickDay(page, trigger, targetISO);

  await dlg.getByRole('button', { name: '预检验证' }).click();
  await dlg.getByRole('button', { name: '确认生成' }).click();
  await expect(toastByTitle(page, '快照生成成功')).toBeVisible({ timeout: 15_000 });
  await expect(dlg).toBeHidden({ timeout: 15_000 });
}

/** 在「提交交易」弹窗内选产品（按 code 搜索后点选目标 code|market 行） */
async function pickProduct(page: Page, dlg: Locator): Promise<void> {
  await dlg.locator('button#product_code').click();
  const popover = productPopover(page);
  await popover.getByPlaceholder('搜索产品代码/名称').fill(OTC_PRODUCT.code);
  const row = productOption(popover, OTC_PRODUCT.code, OTC_PRODUCT.market);
  await row.waitFor({ timeout: 10_000 });
  await row.click();
}

/** 在「提交交易」弹窗内选交易平台（缺省扣款平台，取种子平台 HBZQ） */
async function pickFundPlatform(page: Page, dlg: Locator): Promise<void> {
  await dlg.locator('button#platform_code').click();
  const popover = platformPopover(page);
  const option = popover.locator(`[data-testid="platform-option"][data-code="${FUND_PLATFORM}"]`);
  await option.waitFor({ timeout: 10_000 });
  await option.click();
}

/** 基金腿主行（按产品名收窄：现金子行首列是「现金…」标签，不含产品名） */
function fundRow(page: Page, tradeType: '买入' | '卖出'): Locator {
  return page
    .getByRole('row')
    .filter({ hasText: OTC_PRODUCT.name })
    .filter({ hasText: tradeType })
    .first();
}

/**
 * 清除交易列表的默认「近1年」日期区间（trade_date_end = today）。
 *
 * 本 spec 的卖出链路在**未来**交易日下单（D 是 ≤ today 的最近交易日 ⇒ D+1 起
 * 均晚于 today），默认区间会把这些行滤出列表；筛选状态只存于组件 state，
 * 每次 `gotoPortfolioSubpage` 重新挂载即复位，故每个 trades 页生命周期都要
 * 重新清一次（移动端先展开折叠筛选面板）。
 */
async function clearTradeDateRange(page: Page, testInfo: TestInfo): Promise<void> {
  await openFilterPanelIfMobile(page, testInfo);
  await page.getByRole('button', { name: '清除日期区间' }).click();
}

/**
 * 在「提交交易」弹窗内**显式**选择交易日期。
 *
 * 必须显式选：表单的日期默认值是 `toDateOnly(new Date())`（今天，见
 * `TradesContent.tsx` 的 `tradeDate` 初值），非交易日提交会 422 `NON_TRADING_DAY`、
 * 弹窗不关，`toBeHidden` 只能等到超时。`trade-buy-amount-linkage.spec.ts` 的
 * `selectTradeDate` 早就是同一写法（其注释即「表单默认 today，非交易日须改选」），
 * 本 spec 原先漏了这一步。目标日与 today 最多相差 4 个交易日，日历默认展示当前月，
 * 故 `data-day` 不在当前月时按月翻一次（与 `generateSnapshot` / `pickArrivalDate` 同口径）。
 */
async function selectTradeDate(page: Page, dlg: Locator, targetISO: string): Promise<void> {
  await pickDay(page, dlg.locator('button#trade_date'), targetISO);
}

/** 确认弹窗内改选到账日期（DatePicker id=cash_confirm_date，与后端 query 同名） */
async function pickArrivalDate(page: Page, dlg: Locator, targetISO: string): Promise<void> {
  await pickDay(page, dlg.locator('button#cash_confirm_date'), targetISO);
}

/** 确认弹窗内改选到账平台（SearchablePlatformSelect id=cash_platform_code，与后端 query 同名） */
async function pickArrivalPlatform(
  page: Page,
  dlg: Locator,
  platformCode: string,
): Promise<void> {
  await dlg.locator('button#cash_platform_code').click();
  const popover = platformPopover(page);
  const option = popover.locator(`[data-testid="platform-option"][data-code="${platformCode}"]`);
  await option.waitFor({ timeout: 10_000 });
  await option.click();
}

/**
 * 取认证头前**必须先落到应用同源页面**。
 *
 * `authHeaders` 经 `page.evaluate` 读 `window.localStorage`，而 Playwright 新开的
 * page 停在 `about:blank`（不透明源），直接读会抛
 * `SecurityError: Failed to read the 'localStorage' property from 'Window':
 * Access is denied for this document`。本 spec 的三个用例都要先经 REST 造隔离组合、
 * 建好之前没有组合页可导航，故统一先 `goto('/')`（mobile 端由 `src/proxy.ts` 按 UA
 * 重定向到 `/m/dashboard`，两端同为应用源）。仓库其余 spec 同样遵循
 * 「先导航 → 取头」的顺序（如 `gotoPortfolioDetail` + 可见性等待之后再 `authHeaders`）。
 */
async function openAppAndAuth(page: Page): Promise<{ Authorization: string }> {
  await page.goto('/');
  await expect(page.locator('body')).toBeVisible();
  return authHeaders(page);
}

test.describe('调仓在途资金生命周期（#493）', () => {
  // 两条链路分别自建隔离组合（workerIndex 区分），故两条可并行、也可单独重跑
  test('买入：创建即扣款 → T 快照在途 → 确认后份额入账', async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const d = await nearestTradingDay(page, headers);
    const [d1] = await nextTradingDays(page, headers, d, 1);
    const code = isolatedPortfolioCode(testInfo) + 'B';
    await setupIsolatedPortfolio(page, headers, code, [d, d1, '']);

    // ---- 1. 创建买入（真实表单）：买入侧有扣款平台、无到账信息，并明示「提交即记扣款」----
    const { dlg } = await openSubmitTradeDialog(page, code as PortfolioCode);
    await expect(dlg.getByTestId('buy-deduct-hint')).toContainText('提交即记扣款');
    await expect(dlg.getByTestId('sell-arrival-hint')).toHaveCount(0);
    await expect(dlg.locator('button#cash_platform_code')).toBeVisible();
    await pickProduct(page, dlg);
    await pickFundPlatform(page, dlg);
    // 显式选交易日：表单默认「今天」，非交易日提交会 NON_TRADING_DAY（见 selectTradeDate）
    await selectTradeDate(page, dlg, d);
    await dlg.getByLabel('实际支付金额（含费，元）').fill(String(BUY_AMOUNT));
    await dlg.getByRole('button', { name: '提交交易' }).click();
    await expect(dlg).toBeHidden({ timeout: 15_000 });

    // ---- 2. 创建即扣款：CASH 腿 confirmed（现金日 D）、基金腿 pending ----
    const afterCreate = await listTrades(page, headers, code);
    const buyFund = afterCreate.find((t) => t.product_code === OTC_PRODUCT.code);
    expect(buyFund, '买入基金腿缺失').toBeTruthy();
    expect(buyFund!.trade_type).toBe('buy');
    expect(buyFund!.status).toBe('pending');
    expect(buyFund!.cash_platform_code).toBe(FUND_PLATFORM);
    expect(buyFund!.cash_confirm_date).toBe(d);
    const buyCash = afterCreate.find(
      (t) => t.transfer_group === buyFund!.transfer_group && t.product_code === 'CASH',
    );
    expect(buyCash, '买入配对 CASH 扣款腿缺失').toBeTruthy();
    expect(buyCash!.status, '买入扣款腿创建即 confirmed').toBe('confirmed');
    expect(buyCash!.trade_type).toBe('sell');
    expect(buyCash!.confirm_date).toBe(d);
    expect(await availableCash(page, headers, code)).toBe(CASH_INJECT - BUY_AMOUNT);

    // 列表：基金腿主行 + 现金子行（现金扣款 · 平台 · 扣款日）
    const buyRow = fundRow(page, '买入');
    await expect(buyRow).toBeVisible({ timeout: 15_000 });
    await expect(buyRow.getByText('待确认')).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: '现金扣款' }).first()).toBeVisible();

    // ---- 3. D 日快照：现金已扣、出现等额买入在途、total_value 无缺口 ----
    await gotoPortfolioSubpage(page, code as PortfolioCode, 'snapshots');
    await generateSnapshot(page, d);
    const snapsAfterD = await listSnapshots(page, headers, code);
    const snapD = snapsAfterD.find((s) => s.snapshot_date === d);
    expect(snapD, `D 日快照缺失（${d}）`).toBeTruthy();
    expect(snapD!.in_transit_total, 'D 日应记等额买入在途').toBe(BUY_AMOUNT);
    expect(snapD!.total_value, 'D 日总资产不应因在途出现缺口').toBe(CASH_INJECT);
    await expect(page.getByText(`¥${BUY_AMOUNT.toLocaleString('en-US')}.00`).first()).toBeVisible();

    // ---- 4. 确认弹窗：回显扣款平台与扣款日（= T），且不提供到账信息录入 ----
    await gotoPortfolioSubpage(page, code as PortfolioCode, 'trades');
    await fundRow(page, '买入').locator('button[title="确认"]').click();
    const confirmDlg = dialogByTitle(page, '确认买入');
    await confirmDlg.waitFor();
    // 扣款平台回显（基金平台与扣款平台两处均渲染同名平台，取其一即可证明回显）
    await expect(confirmDlg.getByText('华宝证券').first()).toBeVisible({ timeout: 15_000 });
    await expect(confirmDlg.locator('button#cash_confirm_date')).toHaveCount(0);
    await confirmDlg.getByRole('button', { name: '确认' }).click();
    await expect(confirmDlg).toBeHidden({ timeout: 15_000 });

    // ---- 5. 确认后：份额入账、在途归零、D+1 快照承接 ----
    const afterConfirm = (await listTrades(page, headers, code)).find(
      (t) => t.id === buyFund!.id,
    )!;
    expect(afterConfirm.status).toBe('confirmed');
    expect(afterConfirm.shares).toBe(6666.67);
    await gotoPortfolioSubpage(page, code as PortfolioCode, 'snapshots');
    await generateSnapshot(page, d1);
    const snapsAfterD1 = await listSnapshots(page, headers, code);
    const snapD1 = snapsAfterD1.find((s) => s.snapshot_date === d1);
    expect(snapD1, `D+1 日快照缺失（${d1}）`).toBeTruthy();
    expect(snapD1!.in_transit_total, 'D+1 基金份额入账后在途归零').toBe(0);

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('卖出：创建无现金腿 → 确认录入到账日 → C..A 在途 → 到账', async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const d = await nearestTradingDay(page, headers);
    const [d1, d2, d3, d4] = await nextTradingDays(page, headers, d, 4);
    const code = isolatedPortfolioCode(testInfo) + 'S';
    await setupIsolatedPortfolio(page, headers, code, [d, d1, d2]);

    // ---- 1. 先在 UI 建一笔买入并在 D+1 确认，取得可卖份额 ----
    const buyForm = await openSubmitTradeDialog(page, code as PortfolioCode);
    await pickProduct(page, buyForm.dlg);
    await pickFundPlatform(page, buyForm.dlg);
    await selectTradeDate(page, buyForm.dlg, d);
    await buyForm.dlg.getByLabel('实际支付金额（含费，元）').fill(String(BUY_AMOUNT));
    await buyForm.dlg.getByRole('button', { name: '提交交易' }).click();
    await expect(buyForm.dlg).toBeHidden({ timeout: 15_000 });
    await gotoPortfolioSubpage(page, code as PortfolioCode, 'snapshots');
    await generateSnapshot(page, d);
    await gotoPortfolioSubpage(page, code as PortfolioCode, 'trades');
    await fundRow(page, '买入').locator('button[title="确认"]').click();
    const buyConfirm = dialogByTitle(page, '确认买入');
    await buyConfirm.waitFor();
    await buyConfirm.getByRole('button', { name: '确认' }).click();
    await expect(buyConfirm).toBeHidden({ timeout: 15_000 });
    await gotoPortfolioSubpage(page, code as PortfolioCode, 'snapshots');
    await generateSnapshot(page, d1);

    // ---- 2. 创建卖出（真实表单）：无到账平台输入，且明示到账信息在确认时录入 ----
    await gotoPortfolioSubpage(page, code as PortfolioCode, 'trades');
    await page.getByRole('button', { name: '提交交易' }).first().click();
    const sellForm = dialogByTitle(page, '提交交易');
    await sellForm.waitFor();
    await sellForm.getByRole('button', { name: '卖出' }).click();
    await expect(sellForm.getByTestId('sell-arrival-hint')).toContainText('确认时录入');
    await expect(sellForm.locator('button#cash_platform_code')).toHaveCount(0);
    await pickProduct(page, sellForm);
    await pickFundPlatform(page, sellForm);
    await sellForm.getByLabel('份额').fill(String(SELL_SHARES));
    // 交易日 = D+2 ⇒ 该产品 confirm_days=1 ⇒ 基金确认日 C = D+3（见下方 C 断言）
    await selectTradeDate(page, sellForm, d2);
    await sellForm.getByRole('button', { name: '提交交易' }).click();
    await expect(sellForm).toBeHidden({ timeout: 15_000 });

    // ---- 3. 创建只建基金腿：组内无 CASH 腿、读侧派生现金字段为 null ----
    const afterSellCreate = await listTrades(page, headers, code);
    const sellFund = afterSellCreate.find(
      (t) => t.product_code === OTC_PRODUCT.code && t.trade_type === 'sell',
    );
    expect(sellFund, '卖出基金腿缺失').toBeTruthy();
    expect(sellFund!.status).toBe('pending');
    expect(sellFund!.transfer_group).toMatch(/^rebal_/);
    expect(sellFund!.cash_platform_code).toBeNull();
    expect(sellFund!.cash_confirm_date).toBeNull();
    expect(
      afterSellCreate.filter((t) => t.transfer_group === sellFund!.transfer_group),
      '卖出创建期不得生成配对 CASH 腿',
    ).toHaveLength(1);
    // 卖出下单日 D+2 晚于真实 today，默认「近1年」区间（trade_date_end = today）会把它
    // 滤出列表——先清除日期区间再断言行可见性
    await clearTradeDateRange(page, testInfo);
    await expect(fundRow(page, '卖出')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('row').filter({ hasText: '现金到账' })).toHaveCount(0);

    // ---- 4. 确认弹窗：到账日期/平台可录入，缺省 A = C、平台 = 基金平台 ----
    await fundRow(page, '卖出').locator('button[title="确认"]').click();
    const sellConfirm = dialogByTitle(page, '确认卖出');
    await sellConfirm.waitFor();
    const arrivalTrigger = sellConfirm.locator('button#cash_confirm_date');
    await expect(arrivalTrigger).toBeVisible({ timeout: 15_000 });
    await expect(arrivalTrigger).toHaveText(d3); // 缺省 A = C = D+3（trade_date=D+2 + confirm_days=1）
    await expect(
      sellConfirm.getByTestId('platform-trigger').filter({ hasText: '华宝证券' }),
    ).toBeVisible();
    // 改选到账日 A = D+4（> C = D+3，形成 C..A 在途窗口）与到账平台 TTJJ（≠ 基金平台）
    // → 两者都进 preview query key 重新预览（预览值即确认值），期间确认按钮禁用
    const arrival = [d4];
    await pickArrivalDate(page, sellConfirm, arrival[0]);
    await pickArrivalPlatform(page, sellConfirm, ARRIVAL_PLATFORM);
    await expect(sellConfirm.getByRole('button', { name: '确认' })).toBeEnabled({
      timeout: 15_000,
    });
    await sellConfirm.getByRole('button', { name: '确认' }).click();
    await expect(sellConfirm).toBeHidden({ timeout: 15_000 });

    // ---- 5. 确认后建到账腿：trade_date = C、confirm_date = A、平台 = 所选平台 ----
    const afterSellConfirm = await listTrades(page, headers, code);
    const confirmedFund = afterSellConfirm.find((t) => t.id === sellFund!.id)!;
    expect(confirmedFund.status).toBe('confirmed');
    expect(confirmedFund.confirm_date).toBe(d3);
    expect(confirmedFund.cash_platform_code).toBe(ARRIVAL_PLATFORM);
    expect(confirmedFund.cash_confirm_date).toBe(arrival[0]);
    expect(confirmedFund.actual_amount).toBe(SELL_SHARES * Number(NAV.D2));
    const arrivalLeg = afterSellConfirm.find(
      (t) => t.transfer_group === sellFund!.transfer_group && t.product_code === 'CASH',
    );
    expect(arrivalLeg, '卖出确认应新建 CASH 到账腿').toBeTruthy();
    expect(arrivalLeg!.status).toBe('confirmed');
    expect(arrivalLeg!.trade_type).toBe('buy');
    expect(arrivalLeg!.trade_date).toBe(d3);
    expect(arrivalLeg!.confirm_date).toBe(arrival[0]);

    // 列表：主行已确认、现金子行标注「现金待到账」（未来到账不得标成已到账）
    // （重新挂载后默认「近1年」区间复位，须再清一次才能看到未来下单的卖出行）
    await gotoPortfolioSubpage(page, code as PortfolioCode, 'trades');
    await clearTradeDateRange(page, testInfo);
    const sellRow = fundRow(page, '卖出');
    await expect(sellRow).toBeVisible({ timeout: 15_000 });
    await expect(sellRow.getByText('已确认')).toBeVisible();
    await expect(page.getByText('现金待到账').first()).toBeVisible();
    await expect(page.getByText('天天基金').first()).toBeVisible();

    // ---- 6. 窄表单：只改到账日（+备注），回显当前到账日 ----
    await sellRow.locator('button[title="修改到账日期"]').click();
    const arrivalDlg = dialogByTitle(page, '修改到账日期');
    await arrivalDlg.waitFor();
    const arrivalEdit = arrivalDlg.getByLabel('到账日期', { exact: true });
    await expect(arrivalEdit).toHaveText(arrival[0]);
    await arrivalDlg.getByRole('button', { name: '保存修改' }).click();
    await expect(arrivalDlg).toBeHidden({ timeout: 15_000 });

    // ---- 7. C 之前无在途；C..A 之间记等额卖出在途、现金未增；A 日起转 CASH ----
    // 卖出 trade_date = D+2、该产品 confirm_days=1 ⇒ C = D+3、A = D+4。
    // D+2 快照是连续性必需的一格（快照必须逐交易日连续）：此时基金腿已 confirmed
    // 但 C = D+3 > D+2，卖出尚未生效 ⇒ **无在途**、份额与现金都不动。
    await gotoPortfolioSubpage(page, code as PortfolioCode, 'snapshots');
    // 快照列表默认「近1年」区间（end_date = today）会滤掉 D+1..D+4 的未来快照行，
    // 先清除区间让下方的在途金额文本断言可见（快照页日期选择器双端直渲染，
    // 不在移动端折叠筛选面板内，无需 openFilterPanelIfMobile）
    await page.getByRole('button', { name: '清除日期区间' }).click();
    await generateSnapshot(page, d2);
    const snaps = await listSnapshots(page, headers, code);
    const snapD2 = snaps.find((s) => s.snapshot_date === d2);
    expect(snapD2, `D+2 日快照缺失（${d2}）`).toBeTruthy();
    expect(snapD2!.in_transit_total, 'C=D+3 之前卖出尚未生效，不得记在途').toBe(0);

    // D+3 = C ≤ D < A：等额卖出在途、现金未增
    await generateSnapshot(page, d3);
    const snapsD3 = await listSnapshots(page, headers, code);
    const snapD3 = snapsD3.find((s) => s.snapshot_date === d3);
    expect(snapD3, `D+3 日快照缺失（${d3}）`).toBeTruthy();
    expect(snapD3!.in_transit_total, 'C..A 之间应记等额卖出在途').toBe(
      SELL_SHARES * Number(NAV.D2),
    );
    const cashBeforeArrival = await availableCash(page, headers, code);
    expect(cashBeforeArrival, '到账日之前现金不增加').toBe(CASH_INJECT - BUY_AMOUNT);
    await expect(page.getByText(`¥${(SELL_SHARES * Number(NAV.D2)).toLocaleString('en-US')}.00`).first()).toBeVisible();

    // D+4 = A：在途归零、现金增加
    await generateSnapshot(page, arrival[0]);
    const snapsAfterArrival = await listSnapshots(page, headers, code);
    const snapArrival = snapsAfterArrival.find((s) => s.snapshot_date === arrival[0]);
    expect(snapArrival, `到账日快照缺失（${arrival[0]}）`).toBeTruthy();
    expect(snapArrival!.in_transit_total, '到账日在途归零').toBe(0);
    // 「到账」的账本事实 = A 日快照的 CASH 持仓含到账资金（30000 + 3200，跨平台求和）。
    // 注意不能用 available-cash 端点观察「A 日起可用」：该端点恒锚定真实 today
    // （positions.py as_of_date=date.today()），而 A = D+4 恒在未来（D 是 ≤ today
    // 的最近交易日 ⇒ D+1 起均晚于 today），基线只会落到 ≤ today 的 D 日快照；
    // 「A 日起正常可用」半段由后端集成测试经 as_of_date 覆盖（计划 §4.1.7）。
    expect(await snapshotCash(page, headers, code, arrival[0]), 'A 日快照现金应含到账资金').toBe(
      CASH_INJECT - BUY_AMOUNT + SELL_SHARES * Number(NAV.D2),
    );
    // 反向断言时点口径（#70/#78）：未来到账绝不提前泄漏进今日可用现金
    expect(await availableCash(page, headers, code), 'A 日前可用现金不得因未来到账增加').toBe(
      CASH_INJECT - BUY_AMOUNT,
    );

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // 现金孤儿行（配对腿被筛掉/被删）不得露出调仓 CASH 生命周期按钮：后端对它们一律
  // CASH_TRADE_FORBIDDEN，按钮存在即诱导用户点出错误
  test('调仓 CASH 腿行不露出生命周期操作按钮', async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const d = await nearestTradingDay(page, headers);
    const [d1] = await nextTradingDays(page, headers, d, 1);
    const code = isolatedPortfolioCode(testInfo) + 'C';
    await setupIsolatedPortfolio(page, headers, code, [d, d1, '']);

    // 经 API 造一笔买入（创建即生成 confirmed 扣款腿），再过滤出「现金」产品行
    const createResp = await page.request.post('/api/trades', {
      data: {
        portfolio_code: code,
        product_code: OTC_PRODUCT.code,
        market: OTC_PRODUCT.market,
        platform_code: FUND_PLATFORM,
        trade_type: 'buy',
        trade_date: d,
        amount: BUY_AMOUNT,
        fee: 0,
      },
      headers,
    });
    expect(createResp.ok(), `造数失败 ${createResp.status()}`).toBeTruthy();

    await gotoPortfolioSubpage(page, code as PortfolioCode, 'trades');
    // 移动端筛选栏默认折叠，先展开（桌面端 no-op，#383 口径）
    await openFilterPanelIfMobile(page, testInfo);
    // 孤儿行造法 = 状态筛选收窄到「已确认」：买入组里基金腿 pending 被滤掉、
    // confirmed 扣款腿单独成行 → 现金孤儿行（现金 · 调仓）。**不能按产品 = CASH
    // 筛选**：/api/products 默认排除虚拟产品（#327，CASH/IN_TRANSIT 不可交易），
    // 产品筛选弹层永远列不出 CASH——这是组件刻意行为，不是数据缺失。
    await page.getByRole('combobox').filter({ hasText: '全部状态' }).click();
    await page.getByRole('option', { name: '已确认' }).click();

    const orphan = page.getByRole('row').filter({ hasText: '现金 · 调仓' }).first();
    await expect(orphan).toBeVisible({ timeout: 15_000 });
    await expect(orphan.locator('button[title="确认"]')).toHaveCount(0);
    await expect(orphan.locator('button[title="取消"]')).toHaveCount(0);
    await expect(orphan.locator('button[title="删除"]')).toHaveCount(0);
    await expect(orphan.locator('button[title="取消确认"]')).toHaveCount(0);

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
