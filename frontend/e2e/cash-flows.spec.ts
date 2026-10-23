/**
 * 前端 E2E：现金流水页（issue #725，PR-2 双端）
 *
 * 只验证**前端可观测行为**（后端账本口径由 PR-1 的 backend/tests/integration/test_cash_flows_*
 * 承担）：页面从 `GET /portfolios/{code}/cash-flows` 渲染、概览四卡、(平台,现金日期) rowSpan
 * 合并段（桌面）/ 每卡重复平台（移动）、「当日另有 N 笔」+ 当日弹窗、流水详情（对账块 +
 * 预计到账投影 + 前端推算文案）、按平台构成弹窗（看明细钻穿 / 更新预选平台 / 合计行无更新）、
 * 在途行「不计入余额」注记、ManageLinksCard 第 6 项入口；#727 评审补充：四卡/余额列/
 * 投影金额断言、写入口（现金转移）提交成功后读侧免刷新自动更新、对账块不被状态筛选污染。
 * #727 第二轮 L2 补充：存量档金额的负向断言（子串匹配区分不了 `¥x` 与 `+¥x`）、「当日
 * 另有 N 笔」N=0 不渲染、当日弹窗「当前筛选命中」标注、同日查询失败态优先于在屏行。
 *
 * 数据红线（M11 / helpers 种子契约）：**自建隔离组合**造数，绝不对 E2E_ACTIVE 做
 * recalculate/generate-next；导航用例（无写操作）才复用 E2E_ACTIVE 详情页只读点击。
 * 隔离组合 code 区分 project/worker/retry（照 trade-in-transit.spec.ts 的槽位口径），
 * 「跑 E2E 前重启后端重灌种子」是幂等前提。日期经 /api/trading-calendar 锚定（#468），
 * 净值夹具由后端 seed_e2e_active 提供（000300.OF/CN_OTC）。
 */
import { test, expect, type Page, type TestInfo } from '@playwright/test';
import {
  collectPageErrors,
  dialogByTitle,
  gotoPortfolioDetail,
  gotoPortfolioSubpage,
  isolatedPortfolioCode,
  nearestTradingDay,
  openAppAndAuth,
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

/**
 * #730：改写主列表响应的 `overview` 四值，造宽值 / 窄值概览卡。概览四卡的降档判据是
 * 「格式化后字符串长度」（移动端 ≥15、桌面 ≥16，见 #745），只有千万级乃至亿级带符号金额
 * 才触发，E2E 种子金额远达不到该量级，
 * 故在浏览器侧 mock——不落库、不碰共享 E2E_ACTIVE 的快照契约（红线见文件头）。
 *
 * URL 判据必须带 `/api/` 前缀：页面自身的导航请求 `/portfolio/<code>/cash-flows`（移动
 * `/m/portfolio/...`）与端点同后缀，只按 `endsWith('/cash-flows')` 收口会连文档导航一起
 * 拦下，`resp.json()` 解 HTML 抛错并把 goto abort 掉。主列表 `page_size` 缺省 20，同日
 * 钻穿查询独用 100，故再排除 100 即只剩主列表。
 */
async function mockOverviewValues(page: Page, values: Record<string, string>): Promise<void> {
  await page.route(
    (url) =>
      /\/api\/portfolios\/[^/]+\/cash-flows$/.test(url.pathname) &&
      url.searchParams.get('page_size') !== '100',
    async (route) => {
      const resp = await route.fetch();
      const json = (await resp.json()) as { overview: Record<string, string> };
      json.overview = { ...json.overview, ...values };
      await route.fulfill({ response: resp, body: JSON.stringify(json) });
    },
  );
}

/**
 * #745：量概览四卡主数值的**文本墨迹**——对文本节点取 `Range.getBoundingClientRect()`。
 * 不能用 `locator.boundingBox()`：主数值 span 是 flex 子项、被 `align-items: stretch`
 * 拉满卡内容宽，盒宽恒定，量不出与邻卡的真实间隙（#730 验收项①的失真来源）。
 * `top` 取卡片盒的上缘，用于判「是否同排」。
 */
async function statValueInks(page: Page, keys: string[]) {
  return page.evaluate((ks: string[]) => {
    return ks.map((k) => {
      const card = document.querySelector(`[data-testid="stat-${k}"]`);
      const span =
        card &&
        Array.from(card.querySelectorAll('span')).find((s) =>
          /^[-+]?¥/.test((s.textContent ?? '').trim()),
        );
      const node = span?.firstChild;
      if (!card || !node) throw new Error(`stat-${k} 主数值文本节点缺失`);
      const range = document.createRange();
      range.selectNodeContents(node);
      const ink = range.getBoundingClientRect();
      return { key: k, left: ink.left, right: ink.right, top: card.getBoundingClientRect().top };
    });
  }, keys);
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
    const code = isolatedPortfolioCode('E2E725', testInfo, 'R');

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
    // 存量档不补 +（#727 L2 N4）：`toContainText('¥30,000.00')` 是子串匹配，把
    // formatBalanceCurrency 换成 formatSignedCurrency 渲染成 `+¥30,000.00` 也不会红，
    // 故存量两卡补负向断言（流量两卡本就带 ±，不在此列）。
    await expect(page.getByTestId('stat-available')).not.toContainText('+¥');
    await expect(page.getByTestId('stat-in_transit')).not.toContainText('+¥');

    const isMobile = testInfo.project.name === 'mobile';
    if (isMobile) {
      // #2 移动：不做 rowSpan，每卡重复显示平台；同日两笔各成一张卡
      const cards = page.getByTestId('cash-flow-row');
      await expect(cards).toHaveCount(2);
      await expect(cards.filter({ hasText: '申购 · 注资入账' }).first()).toBeVisible();
      await expect(cards.filter({ hasText: '调仓 · 买入扣款' }).first()).toBeVisible();
      // 余额列（卡片形态：日终余额行）= calculate_available_cash(as_of=d, HBZQ)（#727 S5）
      await expect(cards.filter({ hasText: '申购 · 注资入账' }).first()).toContainText('¥30,000.00');
      // 同卡金额列是流量档 `+¥40,000.00`，故负向断言只针对余额串本身（#727 L2 N4）
      await expect(cards.filter({ hasText: '申购 · 注资入账' }).first()).not.toContainText(
        '+¥30,000.00'
      );
    } else {
      // #2 桌面：同 (平台,日) 两笔合并 → 2 行、1 个组格（rowspan=2）、1 个余额格
      await expect(page.getByTestId('cash-flow-row')).toHaveCount(2);
      await expect(page.getByTestId('group-cell')).toHaveCount(1);
      await expect(page.getByTestId('balance-cell')).toHaveCount(1);
      await expect(page.getByTestId('group-cell')).toHaveAttribute('rowspan', '2');
      // 合并余额格的值（#727 S5）
      await expect(page.getByTestId('balance-cell')).toContainText('¥30,000.00');
      await expect(page.getByTestId('balance-cell')).not.toContainText('+¥');
    }

    // #7 的另一半（N=0 不渲染）：筛选前同组两行都在屏 → 无「当日另有 N 笔」次行。
    // `cashFlows.test.ts` 只钉纯函数返回 0，钉不住组件的 `others > 0` 渲染守卫，
    // 删掉守卫这条会红（#728 L2 S3）。
    await expect(page.getByTestId('same-day-link')).toHaveCount(0);

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
    // #8 的「标注命中行」半边：弹窗内只标注被点中的那一行（组件侧 `rowKey` 比对），
    // 此前 e2e 全文无「命中」断言，删掉标注不会变红（#728 L2 S3）
    await expect(sameDay.getByText('当前筛选命中')).toHaveCount(1);
    // 同日取数未触后端 page_size 上限时不得出现截断提示（#727 L2 N3 的负向面）
    await expect(sameDay.getByTestId('day-truncated')).toHaveCount(0);
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
    const code = isolatedPortfolioCode('E2E725', testInfo, 'T');

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
    // 存量两卡不补 +（#727 L2 N4）
    await expect(page.getByTestId('stat-available')).not.toContainText('+¥');
    await expect(page.getByTestId('stat-in_transit')).not.toContainText('+¥');
    // 余额列（申购行所在 (HBZQ, d) 组）：日终 = 40,000 - 5,000（转出腿已生效）
    await expect(
      page.getByTestId('cash-flow-row').filter({ hasText: '申购 · 注资入账' }).first()
    ).toContainText('¥35,000.00');
    // 同卡/同行的金额列是流量档，故负向断言只钉余额串（#727 L2 N4）
    await expect(
      page.getByTestId('cash-flow-row').filter({ hasText: '申购 · 注资入账' }).first()
    ).not.toContainText('+¥35,000.00');

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
    const code = isolatedPortfolioCode('E2E725', testInfo, 'F');

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
    const code = isolatedPortfolioCode('E2E725', testInfo, 'S');

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

  // #727 L2 S3：弹窗内同日查询失败必须**压过**在屏行/缓存——失败态优先，不以单行数据
  // 兜底冒充全组对账（判序同主列表 `isError ? QueryErrorState : …`，lib `dayQueryNotice`
  // 钉纯判定、这条钉接线）。URL 判据收口在同日查询自身：`page_size=100` 是它独有的
  // （主列表用页内 pageSize），故主列表请求不受影响。
  test('同日查询失败时弹窗走失败态而非旧数据（#727 L2 S3）', async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const d = await nearestTradingDay(page, headers);
    const code = isolatedPortfolioCode('E2E725', testInfo, 'E');

    await post(page, headers, '/api/portfolios', { code, name: `现金流水失败态 ${code}` });
    await seedSubscription(page, headers, code, d);

    await gotoPortfolioSubpage(page, code as PortfolioCode, 'cash-flows');
    await expect(page.getByTestId('cash-flow-row')).toHaveCount(1, { timeout: 15_000 });

    let failDay = true;
    const isDayQuery = (url: URL) =>
      url.pathname.endsWith('/cash-flows') && url.searchParams.get('page_size') === '100';
    await page.route(isDayQuery, (route) =>
      failDay
        ? route.fulfill({
            status: 500,
            json: { detail: { error: 'INTERNAL', message: '同日流水取数暂不可用' } },
          })
        : route.continue()
    );

    await page.getByTestId('row-action').first().click();
    const detail = dialogByTitle(page, '流水详情');
    await detail.waitFor({ timeout: 10_000 });
    // 后端 detail.message 上桌（不是本地兜底文案）
    await expect(detail.getByTestId('day-query-error')).toContainText('同日流水取数暂不可用', {
      timeout: 15_000,
    });
    // 失败优先：列表里那一行不得被当成全组对账渲染出来
    await expect(detail.getByTestId('recon-block')).toHaveCount(0);

    // 放行后点重试 → 对账块落地（失败态不是终局）
    failDay = false;
    await detail.getByRole('button', { name: '重试' }).click();
    await expect(detail.getByTestId('recon-block')).toBeVisible({ timeout: 15_000 });
    await expect(detail.getByTestId('day-query-error')).toHaveCount(0);
    await detail.getByRole('button', { name: '关闭' }).click();
    await expect(detail).toBeHidden({ timeout: 15_000 });

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // #730：宽值（`-¥12,345,678.90` = 15 字符）下移动端概览四卡由双列降单列。修复前双列
  // 栅格卡内容宽 146px 装不下 162px 的数值，与邻卡零间隙相接；桌面栅格与该判据无关。
  // 只把 outflow 设成宽值、其余三卡保留种子值：判据是 `.some`（任一卡宽即降档），四卡
  // 同宽会让 `.some`/`.every` 实现不可区分（L2 评审 #739 S1）。
  test('概览四卡：移动端 8 位带符号宽值降单列（#730）', async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await mockOverviewValues(page, { outflow: '-12345678.90' });

    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'cash-flows');

    const available = page.getByTestId('stat-available');
    const inTransit = page.getByTestId('stat-in_transit');
    await expect(page.getByTestId('stat-outflow')).toContainText('-¥12,345,678.90');

    const first = await available.boundingBox();
    const second = await inTransit.boundingBox();
    expect(first, 'stat-available 应已渲染').not.toBeNull();
    expect(second, 'stat-in_transit 应已渲染').not.toBeNull();
    if (testInfo.project.name === 'mobile') {
      // 单列：第二卡换到下一行且与首卡同列（修复前为同 y、x 更大，两值相接）
      expect(second!.y).toBeGreaterThan(first!.y);
      expect(second!.x).toBe(first!.x);
      // 栅格降档不带动字号降档（#730 取舍）：移动端主数值保持 text-lg = 18px
      await expect(
        page.getByTestId('stat-outflow').getByText('-¥12,345,678.90')
      ).toHaveCSS('font-size', '18px');
    } else {
      // 1280 视口命中 lg 四列：仍是同一行、第二卡在右
      expect(second!.y).toBe(first!.y);
      expect(second!.x).toBeGreaterThan(first!.x);
    }

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // #730 反向用例：`-¥1,234,567.89` = 14 字符未达阈值，不得过度降档——移动双列 /
  // 桌面四列同排（桌面 1280 命中 lg）。钉住「判据取展示串长度」不会把 7 位金额也拖进单列。
  test('概览四卡：移动端 7 位带符号仍双列（#730 不过度降档）', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockOverviewValues(page, {
      available: '-1234567.89',
      in_transit: '-1234567.89',
      inflow: '-1234567.89',
      outflow: '-1234567.89',
    });

    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'cash-flows');

    const available = page.getByTestId('stat-available');
    const inTransit = page.getByTestId('stat-in_transit');
    await expect(available).toContainText('-¥1,234,567.89');
    await expect(inTransit).toContainText('-¥1,234,567.89');

    const first = await available.boundingBox();
    const second = await inTransit.boundingBox();
    expect(first, 'stat-available 应已渲染').not.toBeNull();
    expect(second, 'stat-in_transit 应已渲染').not.toBeNull();
    expect(second!.y).toBe(first!.y);
    expect(second!.x).toBeGreaterThan(first!.x);

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // #745：桌面 1280 四列 + text-2xl + nowrap 下，四值同注 9 位带符号时邻卡墨迹只剩约
  // 1px、10 位起叠印（实测 −27.75px）、11 位第 4 卡墨迹右缘 1281.16px 越过 1280 视口。
  // 修复把桌面阈值（≥16 字符）接进栅格：降两列后卡内容宽约 448px，三档均充裕。
  // 无修复时本用例必红——既有的 #730 桌面 `else` 分支只断卡片位置关系（同排、第二卡在右），
  // 叠印下依然成立，量不到横向间隙。
  const WIDE_DESKTOP_CASES = [
    { digits: 9, raw: '-123456789.01', rendered: '-¥123,456,789.01' },
    { digits: 10, raw: '-1234567890.12', rendered: '-¥1,234,567,890.12' },
    { digits: 11, raw: '-12345678901.23', rendered: '-¥12,345,678,901.23' },
  ] as const;

  for (const c of WIDE_DESKTOP_CASES) {
    test(`概览四卡：${c.digits} 位带符号不与邻卡墨迹相碰（#745）`, async ({ page }, testInfo) => {
      const errors = collectPageErrors(page);
      await mockOverviewValues(page, {
        available: c.raw,
        in_transit: c.raw,
        inflow: c.raw,
        outflow: c.raw,
      });

      await gotoPortfolioSubpage(page, E2E_ACTIVE, 'cash-flows');
      await expect(page.getByTestId('stat-available')).toContainText(c.rendered);

      const inks = await statValueInks(page, ['available', 'in_transit', 'inflow', 'outflow']);
      const viewport = page.viewportSize();
      expect(viewport, '视口尺寸应已设置').not.toBeNull();

      // 同排邻卡：卡片上缘相差 < 2px 视为同一行，只取「b 在 a 右侧」的有序对
      const gaps = inks.flatMap((a) =>
        inks
          .filter((b) => b.key !== a.key && Math.abs(b.top - a.top) < 2 && b.left > a.left)
          .map((b) => b.left - a.right),
      );

      if (testInfo.project.name === 'mobile') {
        // ≥15 字符降单列（#730）：四卡各占一行，不存在同排邻卡
        expect(gaps, `移动端应降单列，实测同排间隙 ${gaps.join(' / ')}`).toHaveLength(0);
      } else {
        // 桌面 ≥16 字符降两列（#745）：两排各一对邻卡
        gaps.forEach((g) =>
          expect(g, `邻卡主数值墨迹间隙应 ≥8px，实测 [${gaps.map((v) => v.toFixed(2)).join(', ')}]`).toBeGreaterThanOrEqual(8),
        );
        expect(gaps, '桌面应降两列、量到 2 对同排邻卡').toHaveLength(2);
        expect(
          Math.max(...inks.map((i) => i.right)),
          '末卡墨迹右缘不应越过视口宽',
        ).toBeLessThanOrEqual(viewport!.width);
      }

      expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
    });
  }
});

/**
 * #772：窄桌面档（`lg` 下界 1024）的页面级形态。此前 `main` 作为 flex 子项带内容自动
 * 最小宽，被明细表的最小内容宽（实测 861.84px，七列表头/`whitespace-nowrap` 单元格之和）
 * 钉在 975.84px → 先挤 sidebar（1024 档只剩 99px）、再挤破视口（`scrollWidth` 1075）。
 * 本用例钉住修复后的三件事：整页不横滚、sidebar 保住 `w-64`、四卡在该档降两列后仍无叠印。
 * 四卡取 #745/#770 同一 15 字符夹具（`-¥12,345,678.90`，墨迹 216.05px）——它是四列档
 * 的临界串长，两列下余量最大，故同时是「不该再给四列」的判据。
 */
test.describe('概览页窄桌面档（#772，1024 视口）', () => {
  test.use({ viewport: { width: 1024, height: 900 } });

  test('1024 档：无整页横向滚动、sidebar 保 256px、四卡降两列不叠印', async ({ page }, testInfo) => {
    test.skip(
      testInfo.project.name !== 'chromium',
      '1024 属桌面 `lg` 档：mobile project 是 390px iPhone 13 视口，该档无 sidebar 与四列栅格（移动形态由上方 #730 用例覆盖）',
    );
    const errors = collectPageErrors(page);
    const wide = '-12345678.90';
    await mockOverviewValues(page, {
      available: wide,
      in_transit: wide,
      inflow: wide,
      outflow: wide,
    });

    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'cash-flows');
    await expect(page.getByTestId('stat-available')).toContainText('-¥12,345,678.90');

    // ① 整页不横滚：明细表的溢出由表自身的滚动容器承接，不外溢到文档
    const box = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(
      box.scrollWidth,
      `1024 档整页 scrollWidth=${box.scrollWidth} 应 ≤ 视口宽 ${box.innerWidth}`,
    ).toBeLessThanOrEqual(box.innerWidth);

    // ② sidebar 不被压窄（`w-64 shrink-0` = 256px；修复前实测 99px）
    const aside = await page.locator('aside').boundingBox();
    expect(aside, '1024 档应渲染桌面 sidebar').not.toBeNull();
    expect(Math.abs(aside!.width - 256), `sidebar 宽应为 256px，实测 ${aside!.width}`).toBeLessThan(1);

    // ③ 四卡：降两列后同排墨迹间隙仍为正，末卡墨迹不越视口
    const inks = await statValueInks(page, ['available', 'in_transit', 'inflow', 'outflow']);
    const gaps = inks.flatMap((a) =>
      inks
        .filter((b) => b.key !== a.key && Math.abs(b.top - a.top) < 2 && b.left > a.left)
        .map((b) => b.left - a.right),
    );
    gaps.forEach((g) =>
      expect(
        g,
        `1024 档邻卡主数值墨迹间隙应 ≥8px，实测 [${gaps.map((v) => v.toFixed(2)).join(', ')}]`,
      ).toBeGreaterThanOrEqual(8),
    );
    expect(gaps, '1024 档容器 654px 不足四列（需 ≥848px），应降两列、量到 2 对同排邻卡').toHaveLength(2);
    expect(
      Math.max(...inks.map((i) => i.right)),
      '末卡墨迹右缘不应越过视口宽',
    ).toBeLessThanOrEqual(box.innerWidth);

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
