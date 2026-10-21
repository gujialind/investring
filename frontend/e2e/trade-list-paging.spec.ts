/**
 * 前端 E2E：交易列表分页 —— 跨日同组两腿必须同页相邻（#734）
 *
 * 缺陷形状（修复前）：后端列表排序键按**行自身** `trade_date` 分档，而卖出组两腿的
 * `trade_date` 天然分叉（基金腿 = 下单日 O、配对 CASH 腿 = 基金确认日 C，见
 * `attach_paired_cash_leg`），同组两腿于是落进不同日期档；页边界一旦落在它们之间，
 * 一页只拿到现金腿、另一页只拿到基金腿。前端 `groupTradeRows` 只在**页内**按
 * `transfer_group` 结对，跨页拆开后两条腿各自被 #719 的 `paired_leg` 升级成「派生 pair」
 * ⇒ 同一笔卖出在两页各出现一次，且两处都不是真实结对形态。
 *
 * 修复后：排序锚点 = 同组合内同 `transfer_group` 的 `max(trade_date)`，同组两腿共用一个
 * 日期档、恒相邻，页边界不可能落在它们中间。
 *
 * ── 数据构造与页边界算术（**手工推导**，不得「跑一遍看输出再填断言」）──
 * `TradesContent` 的 `pageSize` 缺省 20（选择器只有 20/50/100，20 已是最小档），故要让
 * 页边界恰好落在两腿之间，必须精确铺满第 1 页。记 D = 「today 起最近一个交易日」、
 * D1/D2/D3 = 其后第 1/2/3 个交易日；`000300.OF` 的 confirm_days=1 ⇒ 确认日 = 下单日 + 1 交易日。
 * 组合内共 **25** 条 trade 行：
 *   ① 首购申赎的 CASH 腿 1 条：trade_date = 申请日 D0（< D），组号 `sub_{id}`；
 *   ② 买入组 2 条：基金腿 + CASH 扣款腿，trade_date 同为 D，组号 `rebal_*`；
 *   ③ **被测卖出组** 2 条：基金腿 trade_date = D2、CASH 到账腿 trade_date = C = D3（跨日）；
 *   ④ D3 当天现金转移 9 笔 = 18 条腿（组号是裸 12 位 hex）；
 *   ⑤ D2 当天现金转移 1 笔 = 2 条腿（同上）。
 *   合计 1 + 2 + 2 + 18 + 2 = 25 ⇒ 2 页（20 + 5），分页条因此必然渲染。
 *
 * 为什么日期要从 D 一路排到 D3（两条硬约束互相挤出来的，不是随手留白）：
 *   - 卖出创建时的可用份额校验以**最新快照**为唯一基线（`position_service.calculate_available_shares`
 *     只认快照份额，confirmed 买入不并入增量），无快照 ⇒ 基线恒 0 ⇒ 任何卖出都被
 *     `INSUFFICIENT_SHARES` 拒。故必须在卖出前生成快照（D 与 D1 各一次，满足连续性校验）。
 *   - 快照一旦存在，`trade_date`、现金转移的 `transfer_date`、以及卖出确认日都**必须严格晚于**
 *     最新快照日（`DATE_BEFORE_SNAPSHOT` / `SNAPSHOT_EXISTS` 一类闸门）。故快照只能停在 D1
 *     （= 买入确认日，份额已入账），被测卖出顺延到 D2、其现金腿落 D3，④⑤ 的转移日同样
 *     只能取 D3 / D2。快照不产生 trade 行，故 25 行的算术不受影响。
 *
 * 旧排序（trade_date DESC, transfer_group ASC, id DESC）下的位次：
 *   D3 档：④ 的 18 条腿占 1..18（hex 组号首字符 ∈ [0-9a-f] 恒 < 'r'，即恒排在 `rebal_`
 *          之前，与 uuid 取值无关），③ 的现金腿 = **19**；
 *   D2 档：⑤ 的 2 条腿（hex < rebal）占 20、21，③ 的**基金腿** = **22** ⇒ 落到第 2 页。
 *   ⇒ 第 1 页 = 8 个完整转移对 + 1 条被拆开的现金腿（19）+ 1 条被拆开的转移腿（20）：
 *      后两者各派生一行对手腿（`trade-counterpart-subrow` 计数 = 2），且**没有**卖出基金主行；
 *      第 2 页则出现那条卖出基金腿（同样派生）。
 * 新排序（组锚点 max(trade_date) DESC, transfer_group ASC, id DESC）下的位次：
 *   锚点 D3：④ 的 18 条腿（1..18）→ ③ 的两条腿（**19、20**，同锚点同组 ⇒ 相邻且同页）；
 *   锚点 D2：⑤ 的 2 条腿（21、22）；锚点 D：② 的 2 条腿（23、24）；锚点 D0：①（25）。
 *   ⇒ 第 1 页 = 10 个**完整**结对行、0 个派生 pair；第 2 页不含任何卖出组的腿。
 *
 * 于是下列断言在两侧都是确定性的（不赌 uuid 组号字典序）：第 1 页有且仅有一行
 * 「沪深300联接A · 卖出」主行、其后紧跟真实现金子行、派生子行计数为 0；第 2 页不得出现
 * 该卖出主行、派生子行计数同样为 0。修复前最先红在第 1 页的 API 断言（卖出基金腿不在
 * 第 1 页、`fundIdx = -1`，即两腿已被页边界拆开），UI 层断言是同一缺陷的第二道呈现。
 *
 * 数据说明（与 `trade-in-transit.spec.ts` 的种子契约互补）：
 * - **自建隔离组合**（`E2E734<yyyyMMdd><D|M><workerIndex><retry>`，经 POST /api/portfolios）：
 *   不碰共享 `E2E_ACTIVE`（禁止 recalculate/catch-up/generate-next，#354 红线），也不用
 *   `E2E_PORT`（零交易契约组合，被其它 spec 依赖）。本 spec 只在自己这个隔离组合上跑
 *   **单日 `POST /api/snapshots/generate`**（D 与 D1 各一次，卖出份额校验的必需前提，见上），
 *   既不触发 #354 红线里那三个批量端点，也不影响任何其它 spec 的数据。
 *   重跑幂等前提与 #493 那个 spec 相同（每轮重启后端重灌种子；不加 per-run nonce 是
 *   #551 §4 的定案，理由见 `trade-in-transit.spec.ts` 的 `isolatedPortfolioCode` doc 块）。
 * - 净值走后端种子夹具：`seed_e2e_active` 以 ORM 写入 `000300.OF`/`CN_OTC` 的 D..D+4
 *   （1.5000/1.5500/1.6000/1.6000/1.6000），是**全局** PriceRecord，隔离组合可直接用：
 *   D/D1 快照取价、买入确认日 D1 取 1.5500 ⇒ 6451.61 份、卖出确认日 D3 取 1.6000 ⇒ 3200 元，
 *   全部落在夹具覆盖的 D..D+4 之内。改净值口径须与种子两侧同步
 *   （`backend/tests/integration/test_seed_contract.py` 锁定）。
 * - 日期一律经 `/api/trading-calendar` 推导（#468：不写死年份、不假设周末、不用
 *   `new Date()` 当交易日）。
 * - 下单日 D2/D3 均晚于真实 today，列表默认「近1年」区间（trade_date_end = today）会把
 *   被测两腿全部滤掉，故 UI 侧先清除日期区间（移动端先展开折叠筛选面板）。
 */
import { test, expect, type Page, type TestInfo } from '@playwright/test';
import {
  authHeaders,
  collectPageErrors,
  gotoPortfolioSubpage,
  openFilterPanelIfMobile,
  toISODate,
  type PortfolioCode,
} from './helpers';

type Headers = { Authorization: string };

/** 被测场外基金（种子产品，confirm_days=1 → 确认日 = 下单日下一交易日） */
const OTC_PRODUCT = { code: '000300.OF', market: 'CN_OTC', name: '沪深300联接A' };
/** 交易平台与现金转移的对手平台（种子 4 平台之二） */
const FUND_PLATFORM = 'HBZQ';
const OTHER_PLATFORM = 'TTJJ';

const CASH_INJECT = 40000;
const BUY_AMOUNT = 10000;
const SELL_SHARES = 2000;
/** 单笔转移金额：只需造行，金额刻意取小以免吃光可用现金（9 + 1 笔共 1000） */
const TRANSFER_AMOUNT = 100;
/** D3（= 卖出组现金腿所在日）当天的转移笔数：18 条腿 + 现金腿 = 19，把页边界推到 20/21 之间 */
const TRANSFERS_ON_CASH_LEG_DATE = 9;

/** 前端 `pageSize` 缺省档（`TradesContent.tsx`，选择器最小值） */
const PAGE_SIZE = 20;
/** 组合内 trade 行总数（推导见文件头 ①..⑤）：1 + 2 + 2 + 18 + 2 */
const TOTAL_ROWS = 25;

/** 取「today 起最近一个交易日」（含 today）；当年尚无开市日则回看上一年末（#468 口径） */
async function nearestTradingDay(page: Page, headers: Headers): Promise<string> {
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

/** from 之后的第 n 个交易日（后端 /trading-calendar/next 为唯一事实来源） */
async function nextTradingDays(
  page: Page,
  headers: Headers,
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
 * 长度：`E2E734`(6) + 日期(8) + project(1) + worker(1) + retry(1) = 17。
 * project / worker / retry 三段缺一不可（两个 project 内 workerIndex 各自从 0 计数、
 * CI 主 e2e job 带 retries），理由与 #493 的 `isolatedPortfolioCode` 完全相同。
 * 本文件只有一条用例，故不再追加用例后缀。
 */
function isolatedPortfolioCode(testInfo: {
  project: { name: string };
  workerIndex: number;
  retry: number;
}): string {
  const day = toISODate(new Date()).replace(/-/g, '');
  const proj = testInfo.project.name === 'mobile' ? 'M' : 'D';
  return `E2E734${day}${proj}${testInfo.workerIndex}${testInfo.retry}`;
}

/** 统一 POST：非 2xx 即响亮失败并带上响应体（造数失败必须指名道姓，不留给下游断言猜） */
async function apiPost(
  page: Page,
  url: string,
  data: unknown,
  headers: Headers,
  label: string,
): Promise<unknown> {
  const resp =
    data === undefined
      ? await page.request.post(url, { headers })
      : await page.request.post(url, { data, headers });
  expect(resp.ok(), `${label}失败 ${resp.status()} ${await resp.text()}`).toBeTruthy();
  return resp.json();
}

interface TradeItem {
  id: number;
  product_code: string;
  market: string;
  trade_type: string;
  status: string;
  trade_date: string;
  confirm_date: string | null;
  transfer_group: string;
  amount: number | null;
}

/** 不带日期筛选地取某一页（UI 侧清除「近1年」区间后发的就是这个形状的请求） */
async function fetchTradesPage(
  page: Page,
  headers: Headers,
  code: string,
  pageNumber: number,
): Promise<{ items: TradeItem[]; total: number }> {
  const resp = await page.request.get(
    `/api/trades?portfolio_code=${code}&page=${pageNumber}&page_size=${PAGE_SIZE}`,
    { headers },
  );
  expect(
    resp.ok(),
    `查询交易列表第 ${pageNumber} 页失败 ${resp.status()} ${await resp.text()}`,
  ).toBeTruthy();
  return (await resp.json()) as { items: TradeItem[]; total: number };
}

/** 组合全部 trade 行（造数后核对数据前提用，一页取全） */
async function fetchAllTrades(
  page: Page,
  headers: Headers,
  code: string,
): Promise<TradeItem[]> {
  const resp = await page.request.get(
    `/api/trades?portfolio_code=${code}&page_size=100`,
    { headers },
  );
  expect(resp.ok(), `查询交易列表失败 ${resp.status()}`).toBeTruthy();
  return ((await resp.json()) as { items: TradeItem[] }).items;
}

/**
 * 造数：建组合 + 首购注入现金（申请日 = D 的前一工作日 ⇒ 确认日 = D）→ 买入并确认
 * → **生成 D / D1 两日快照**（卖出份额校验的唯一基线，且把「最新快照日」钉在 D1）
 * → 卖出（D2 下单、D3 确认）并确认（**产生跨日同组两腿**）→ 铺满页边界所需的转移腿。
 * 返回被测卖出组两条腿的 id。
 */
async function seedCrossDaySellGroup(
  page: Page,
  headers: Headers,
  code: string,
  days: { d: string; d1: string; d2: string; d3: string },
): Promise<{ fundLegId: number; cashLegId: number; transferGroup: string }> {
  const { d, d1, d2, d3 } = days;

  await apiPost(
    page,
    '/api/portfolios',
    { code, name: `E2E 交易列表分页 ${code}` },
    headers,
    '创建隔离组合',
  );

  // ① 首购申赎：申请日取 D 的前一工作日，使确认日（= 下一交易日）恰落 D；
  //    确认后组合转 active、生成 `sub_` 组 CASH 腿（trade_date = 申请日 D0）
  const apply = new Date(`${d}T00:00:00`);
  do {
    apply.setDate(apply.getDate() - 1);
  } while (apply.getDay() === 0 || apply.getDay() === 6);
  const sub = (await apiPost(
    page,
    '/api/subscriptions',
    {
      portfolio_code: code,
      investor_code: 'ADMIN',
      platform_code: FUND_PLATFORM,
      sub_type: 'subscribe',
      amount: CASH_INJECT,
      apply_date: toISODate(apply),
    },
    headers,
    '注入现金：创建申购',
  )) as { id: number; confirm_date: string };
  expect(sub.confirm_date, '首购确认日应为 D 当天（申请日的下一交易日）').toBe(d);
  await apiPost(page, `/api/subscriptions/${sub.id}/confirm`, undefined, headers, '注入现金：确认申购');

  // ② 买入（创建即生成 confirmed 扣款腿，trade_date 同为 D）→ 确认（取 D1 净值 1.5500）
  const buy = (await apiPost(
    page,
    '/api/trades',
    {
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
    '创建买入',
  )) as { id: number };
  await apiPost(page, `/api/trades/${buy.id}/confirm`, undefined, headers, '确认买入');

  // ②b 生成 D、D1 两日单日快照（连续性校验要求逐日）：D1 = 买入确认日 ⇒ 份额已入账，
  //     成为卖出份额校验的基线。刻意**停在 D1**：此后一切 trade_date / transfer_date /
  //     确认日都必须严格晚于最新快照日，被测卖出因此只能排在 D2、其现金腿落 D3。
  for (const day of [d, d1]) {
    await apiPost(
      page,
      '/api/snapshots/generate',
      { portfolio_code: code, target_date: day },
      headers,
      `生成 ${day} 快照`,
    );
  }

  // ③ 卖出：下单日 D2 ⇒ 确认日 C = D3（confirm_days=1）。创建期只有基金腿，
  //    确认时才补 CASH 到账腿，其 trade_date = C = D3 ≠ 基金腿 D2 —— **这就是跨日同组**
  const sell = (await apiPost(
    page,
    '/api/trades',
    {
      portfolio_code: code,
      product_code: OTC_PRODUCT.code,
      market: OTC_PRODUCT.market,
      platform_code: FUND_PLATFORM,
      trade_type: 'sell',
      trade_date: d2,
      shares: SELL_SHARES,
      fee: 0,
    },
    headers,
    '创建卖出',
  )) as { id: number };
  await apiPost(page, `/api/trades/${sell.id}/confirm`, undefined, headers, '确认卖出');

  // ④⑤ 现金转移：当天完成（cross_day 缺省 false）⇒ 每笔 2 条腿、trade_date 同为转移日、
  //    组号为裸 12 位 hex（字典序恒在 `rebal_` 之前，页边界算术的确定性来源）
  for (let i = 0; i < TRANSFERS_ON_CASH_LEG_DATE; i++) {
    await apiPost(
      page,
      `/api/portfolios/${code}/cash-transfer`,
      {
        from_platform: FUND_PLATFORM,
        to_platform: OTHER_PLATFORM,
        amount: TRANSFER_AMOUNT,
        transfer_date: d3,
      },
      headers,
      `创建 D3 现金转移 #${i + 1}`,
    );
  }
  await apiPost(
    page,
    `/api/portfolios/${code}/cash-transfer`,
    {
      from_platform: FUND_PLATFORM,
      to_platform: OTHER_PLATFORM,
      amount: TRANSFER_AMOUNT,
      transfer_date: d2,
    },
    headers,
    '创建 D2 现金转移',
  );

  const all = await fetchAllTrades(page, headers, code);
  const fundLeg = all.find((t) => t.id === sell.id);
  expect(fundLeg, '卖出基金腿缺失').toBeTruthy();
  const cashLeg = all.find(
    (t) => t.transfer_group === fundLeg!.transfer_group && t.product_code === 'CASH',
  );
  expect(cashLeg, '卖出配对 CASH 到账腿缺失').toBeTruthy();

  // 数据前提（不成立则本用例失去意义、必须响亮失败而不是静默变绿）：
  // 同组、两腿跨日、且现金腿日期 = 组内 max ⇒ 组锚点由现金腿决定
  expect(fundLeg!.trade_date, '卖出基金腿 trade_date 应为下单日 D2').toBe(d2);
  expect(cashLeg!.trade_date, 'CASH 到账腿 trade_date 应为基金确认日 D3').toBe(d3);
  expect(fundLeg!.transfer_group).toMatch(/^rebal_/);
  expect(all, '组合内 trade 行数应与页边界算术的前提一致').toHaveLength(TOTAL_ROWS);

  return {
    fundLegId: fundLeg!.id,
    cashLegId: cashLeg!.id,
    transferGroup: fundLeg!.transfer_group,
  };
}

/**
 * 取认证头前**必须先落到应用同源页面**：`authHeaders` 经 `page.evaluate` 读
 * `window.localStorage`，而新开的 page 停在 `about:blank`（不透明源）会抛 SecurityError。
 * 与 `trade-in-transit.spec.ts` 的 `openAppAndAuth` 同口径。
 */
async function openAppAndAuth(page: Page): Promise<Headers> {
  await page.goto('/');
  await expect(page.locator('body')).toBeVisible();
  return authHeaders(page);
}

/**
 * 清除交易列表默认「近1年」区间（trade_date_end = today）：被测两腿下单日均晚于 today，
 * 不清除则整组被滤出列表。筛选状态只存组件 state，每次重新挂载即复位（移动端先展开面板）。
 */
async function clearTradeDateRange(page: Page, testInfo: TestInfo): Promise<void> {
  await openFilterPanelIfMobile(page, testInfo);
  await page.getByRole('button', { name: '清除日期区间' }).click();
}

/** 卖出基金主行（按产品名 + 方向收窄：现金子行首列是「现金…」标签，不含产品名） */
function sellFundRow(page: Page) {
  return page
    .getByRole('row')
    .filter({ hasText: OTC_PRODUCT.name })
    .filter({ hasText: '卖出' });
}

test.describe('交易列表分页与组锚点排序（#734）', () => {
  test('跨日同组两腿不被页边界拆开：第 1 页完整结对、第 2 页无重复', async ({
    page,
  }, testInfo) => {
    // 造数 ~20 次 REST 往返（含两次快照生成）+ 两页 UI 渲染，mobile webkit 约为 chromium
    // 的 3～4 倍耗时（同 `trade-in-transit.spec.ts` 卖出用例的口径），故单独放宽到 60s
    test.setTimeout(60_000);
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const d = await nearestTradingDay(page, headers);
    const [d1, d2, d3] = await nextTradingDays(page, headers, d, 3);
    const code = isolatedPortfolioCode(testInfo);
    const sell = await seedCrossDaySellGroup(page, headers, code, { d, d1, d2, d3 });

    // ---- 1. API 层：total 未被 outerjoin 放大，两腿同在首页且相邻 ----
    const page1 = await fetchTradesPage(page, headers, code, 1);
    expect(
      page1.total,
      'total 必须仍是真实行数（组锚点子查询是 outerjoin，不得放大计数）',
    ).toBe(TOTAL_ROWS);
    expect(page1.items).toHaveLength(PAGE_SIZE);
    const fundIdx = page1.items.findIndex((t) => t.id === sell.fundLegId);
    const cashIdx = page1.items.findIndex((t) => t.id === sell.cashLegId);
    expect(fundIdx, '卖出基金腿应在第 1 页').toBeGreaterThanOrEqual(0);
    expect(cashIdx, '卖出 CASH 腿应在第 1 页').toBeGreaterThanOrEqual(0);
    expect(
      Math.abs(fundIdx - cashIdx),
      '同组两腿须在结果集中相邻（页边界不得落在它们之间）',
    ).toBe(1);

    const page2 = await fetchTradesPage(page, headers, code, 2);
    expect(page2.items).toHaveLength(TOTAL_ROWS - PAGE_SIZE);
    expect(
      page2.items.some((t) => t.id === sell.fundLegId || t.id === sell.cashLegId),
      '第 2 页不得再出现被测组的任何一条腿（拆页即会重复渲染同一笔卖出）',
    ).toBe(false);

    // ---- 2. UI 层第 1 页：真实结对行（主行 + 现金子行），零派生子行 ----
    await gotoPortfolioSubpage(page, code as PortfolioCode, 'trades');
    await clearTradeDateRange(page, testInfo);

    const mainRow = sellFundRow(page);
    await expect(mainRow, '第 1 页应有且仅有一行卖出基金主行').toHaveCount(1, {
      timeout: 15_000,
    });
    // 紧邻其后的是**真实**现金子行（trade-cash-subrow）；被拆页时这一格会变成
    // #719 的派生对手腿子行（trade-counterpart-subrow），故下一条断言同时钉住两者
    await expect(
      mainRow.first().locator('xpath=following-sibling::tr[1]'),
      '卖出主行的下一行必须是真实现金子行（同组两腿同页相邻）',
    ).toHaveAttribute('data-testid', 'trade-cash-subrow');
    await expect(
      page.getByTestId('trade-counterpart-subrow'),
      '第 1 页每个组都完整 ⇒ 不应出现任何派生对手腿子行',
    ).toHaveCount(0);

    // ---- 3. UI 层第 2 页：不重复渲染那笔卖出，也没有被拆开的组 ----
    const [page2Resp] = await Promise.all([
      page.waitForResponse(
        (r) =>
          r.request().method() === 'GET' &&
          /\/api\/trades\?/.test(r.url()) &&
          /[?&]page=2(&|$)/.test(r.url()),
        { timeout: 15_000 },
      ),
      page.getByRole('button', { name: '下一页' }).click(),
    ]);
    expect(page2Resp.ok(), `第 2 页请求失败 ${page2Resp.status()}`).toBeTruthy();

    await expect(
      sellFundRow(page),
      '第 2 页不得再出现同一笔卖出（拆页时它会以派生 pair 的形态重复出现）',
    ).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByTestId('trade-counterpart-subrow')).toHaveCount(0);
    // 第 2 页仍是 3 个完整行（D1 转移对 + 买入对 + 申赎 CASH 孤儿），不是空页
    await expect(page.getByTestId('trade-cash-subrow')).toHaveCount(2);

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
