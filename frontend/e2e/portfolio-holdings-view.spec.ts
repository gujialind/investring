import { test, expect, type Locator, type Page, type TestInfo } from '@playwright/test';
import {
  E2E_ACTIVE,
  E2E_PORT,
  activatePortfolioViaApi,
  authHeaders,
  closePortfolioViaApi,
  gotoPortfolioDetail,
  openPopover,
  openPortfolioMoreMenu,
  toISODate,
} from './helpers';

/**
 * #595 组合详情页持仓明细双视图：「按产品 / 按平台」分段切换 + URL ?view= 持久化（D-7）。
 *
 * 数据说明（种子契约，backend/tests/seed_base.py）：
 * - E2E_ACTIVE：active 组合，单平台 HBZQ（华宝证券）+ 510300.SH（沪深300ETF）持仓
 *   + 现金 + 连续 2 日快照 + 1 笔 pending 场内买入（晚于最新快照日，快照不含在途行）
 *   → 按产品视图有 1 张产品聚合卡 + 1 张现金聚合卡；按平台视图有 1 张华宝证券
 *   平台卡（1 只产品 · 现金，市值含在途）。
 * - E2E_PORT：draft 组合，无持仓 → 不渲染持仓明细区。
 *   ⚠️ 草稿态不可作为全量套件断言前提：CI 按 project-major 顺序跑（全部 chromium
 *   先于 mobile），trade-buy-amount-linkage 用例 7 经 API「申购+确认」会把共享
 *   种子 E2E_PORT 置为活跃（#636 CI 截图实证），mobile 半区运行时它已不是 draft。
 *   故 draft 用例自建组合取证（见该用例注释），不复用 E2E_PORT 的草稿态。
 * - 在途聚合卡（#636 L2 评审决策补回）：需最新快照含 IN_TRANSIT 行，E2E_ACTIVE
 *   不满足（禁止对共享种子推进快照），故在途用例自建「现金 + pending 买入 +
 *   T 日快照」组合取证（见该用例注释）。
 * 断言为可见性与关系式，不硬绑定快照数字；两端共用组件，mobile project 同跑。
 */

/**
 * 经 API 自建「现金 + pending 买入 + T 日快照」组合：T 日快照写入 IN_TRANSIT_BUY 行
 * （买入在途，trades 不自动确认），返回组合 code。code 区分 project/retry。
 * 残留由每轮重建的 E2E 库吸收——#820 补的 DELETE 只放行「空壳 draft」，本夹具已激活并
 * 生成快照，删不掉；「不加分 per-run nonce」仍是既有定案（helpers.isolatedPortfolioCode）。
 */
async function setupInTransitPortfolio(page: Page, testInfo: TestInfo): Promise<string> {
  const code = `E595IT_${testInfo.project.name}_${testInfo.retry}`;
  await gotoPortfolioDetail(page, E2E_ACTIVE);
  const headers = await authHeaders(page);

  // 交易日锚定经 /api/trading-calendar（#468）：apply = 最近交易日的前一交易日，
  // 申购确认日 = 最近交易日 T（T+1）；pending 买入 trade_date = T，T 日快照含在途行。
  // 本地口径（helpers.toISODate），与组件侧 toDateOnly() 同一时钟：**不用
  // toISOString().slice(0,10)**——它按 UTC 切日，本机 TZ=UTC+8 的 00:00–07:59
  // 会让上界比页面少一天（#640 S-2，与 U-2 同根因）
  const today = toISODate(new Date());
  const year = Number(today.slice(0, 4));
  const calendars = await Promise.all(
    [year - 1, year, year + 1].map(async (y) => {
      const resp = await page.request.get(`/api/trading-calendar?year=${y}`, { headers });
      await expect(resp).toBeOK();
      return (await resp.json()) as { calendar_date: string; is_open: boolean }[];
    }),
  );
  const days = calendars
    .flat()
    .filter((d) => d.is_open)
    .map((d) => d.calendar_date)
    .sort();
  const uptoToday = days.filter((d) => d <= today);
  expect(uptoToday.length, '种子日历须覆盖今天之前的至少两个交易日').toBeGreaterThanOrEqual(2);
  const [apply, target] = uptoToday.slice(-2);

  const post = async <T = unknown>(path: string, data?: unknown): Promise<T> => {
    const resp = await page.request.post(path, { headers, data });
    await expect(resp, `${path} 返回 ${resp.status()} ${await resp.text()}`).toBeOK();
    return resp.json();
  };

  await post('/api/portfolios', { code, name: `在途视图 ${code}` });
  const sub = await post<{ id: number }>('/api/subscriptions', {
    portfolio_code: code,
    investor_code: 'ADMIN',
    platform_code: 'HBZQ',
    sub_type: 'subscribe',
    amount: 100000,
    apply_date: apply,
  });
  await post(`/api/subscriptions/${sub.id}/confirm`);
  // 场外产品（confirm_days=1）：pending 买入的 confirm_date 为 T 下一交易日，
  // 快照目标 T 的依赖校验（拒绝 confirm_date<=T 的待确认交易）不阻断；
  // 场内 510300.SH confirm_days=0 会被拒（实测 422 VALIDATION_FAILED）
  await post('/api/trades', {
    portfolio_code: code,
    product_code: '000300.OF',
    market: 'CN_OTC',
    platform_code: 'HBZQ',
    trade_type: 'buy',
    trade_date: target,
    amount: 8200,
    fee: 0,
  });
  await post('/api/snapshots/generate', { portfolio_code: code, target_date: target });
  return code;
}

/**
 * 视图 tab 点击放置（#915 B2 稳定性收口）：mobile webkit 从页顶滚动态首击 tab 时，
 * Playwright 协议滚动与合成器 commit 竞态会让 hit-test 落在分区头上（拦截重试烧满
 * actionTimeout；套件内间歇、单跑恒绿、trace-on 恒绿＝时序竞态签名）。先经 evaluate
 * 滚到视口中央（一次瞬时跳变＋round-trip 帧边界），Playwright 再测盒时布局已 commit。
 * 先例：display-config-dialog.spec.ts::openDisplayConfigDialog 的同款放置注释。
 * 只改点击放置，不改任何断言。
 */
async function clickViewTab(page: Page, key: 'product' | 'platform'): Promise<void> {
  const tab = page.getByTestId(`holdings-tab-${key}`);
  await tab.evaluate((el) => el.scrollIntoView({ block: 'center' }));
  await tab.click();
}

function platformHoldings(page: Page): Locator {
  return page
    .getByTestId('holding-platform-card')
    .or(page.getByTestId('holding-platform-row'));
}

test.describe('#595 持仓明细双视图（按产品 / 按平台）', () => {
  test('持仓明细读取失败 → 后端消息与重试入口，而非「暂无持仓记录」（#663③）', async ({ page }) => {
    // 只钉默认的按产品视图：按平台走同一个 activeQuery.isError 分支（同一段代码），
    // 未单独钉是刻意取舍而非静默省略——改坏那一支时本用例同样会红。
    await page.route(
      /\/api\/positions\/portfolio\/[^/]+\/holdings\/by-product(\?|$)/,
      (route) =>
        route.fulfill({
          status: 500,
          json: { detail: { error: 'INTERNAL', message: '持仓聚合服务暂不可用' } },
        })
    );
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    const area = page.getByTestId('portfolio-holdings');
    await expect(area).toContainText('加载失败：持仓聚合服务暂不可用', { timeout: 10_000 });
    // 旧文案「加载失败，请刷新重试」把重试动作交回用户手动刷新；现在必须有可点入口
    await expect(area).not.toContainText('暂无持仓记录');
    const retry = area.getByRole('button', { name: '重试' });
    await expect(retry).toBeVisible();
    const refetched = page.waitForRequest(
      (r) => r.method() === 'GET' && /holdings\/by-product/.test(r.url()),
      { timeout: 10_000 },
    );
    await retry.click();
    await refetched;
  });

  test('默认按产品视图：URL 无 view 参数，桌面账表与移动聚合行渲染', async ({ page }, testInfo) => {
    // 种子没有已卖出仓位，持有/累计收益恰好同值；读侧注入异号哨兵值，钉住累计收益
    // 确实取 cumulative_profit，并同时覆盖数值与涨跌色两个绑定。
    await page.route(/\/api\/positions\/portfolio\/[^/]+\/holdings\/by-product(\?|$)/, async (route) => {
      const resp = await route.fetch();
      const json = (await resp.json()) as {
        products: Array<{
          product_code: string;
          holding_profit: number | null;
          cumulative_profit: number | null;
        }>;
      };
      const target = json.products.find((product) => product.product_code === '510300.SH');
      expect(target, '种子须包含 510300.SH 产品聚合').toBeDefined();
      target!.holding_profit = -1234.5;
      target!.cumulative_profit = 9876.5;
      const headers = { ...resp.headers() };
      delete headers['content-length'];
      delete headers['content-encoding'];
      await route.fulfill({ status: resp.status(), headers, json });
    });
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByTestId('holdings-view-tabs')).toBeVisible({ timeout: 10_000 });

    expect(page.url()).not.toContain('view=');
    await expect(page.getByTestId('holdings-tab-product')).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    const isMobile = testInfo.project.name === 'mobile';
    const rowTestId = isMobile ? 'holding-product-card' : 'holding-product-row';
    const productRow = page.getByTestId(rowTestId).filter({ hasText: '沪深300ETF' });
    await expect(productRow).toBeVisible();
    await expect(productRow).toContainText('510300 · ');
    await expect(productRow).not.toContainText('510300.SH');
    await expect(productRow).not.toContainText('持有份额');

    const totalValue = productRow.getByTestId('total-profit-value');
    await expect(totalValue).toHaveText('+9,876.50');
    await expect(totalValue).not.toHaveText('-1,234.50');
    await expect(totalValue).toHaveClass(/text-gain/);
    await expect(totalValue).not.toHaveClass(/text-loss/);

    if (isMobile) {
      await expect(productRow).toContainText(/最新收益\(\d{2}\/\d{2}\)/);
      const totalTitle = productRow.getByText('累计收益', { exact: true });
      const dailyTitle = productRow.getByText(/^最新收益\(\d{2}\/\d{2}\)$/);
      const totalBox = await totalTitle.boundingBox();
      const dailyBox = await dailyTitle.boundingBox();
      expect(totalBox, '「累计收益」标题未渲染出 boundingBox').not.toBeNull();
      expect(dailyBox, '「最新收益(MM/DD)」标题未渲染出 boundingBox').not.toBeNull();
      expect(Math.abs(totalBox!.y - dailyBox!.y), '两列标题须同一行').toBeLessThanOrEqual(1);
      expect(Math.abs(totalBox!.height - dailyBox!.height), '两列标题须同高').toBeLessThanOrEqual(1);
      await expect(totalValue).toHaveCSS('text-overflow', 'clip');
      await expect(productRow.getByTestId('daily-profit-value')).toHaveCSS('text-overflow', 'clip');
    } else {
      const table = page.getByTestId('holding-product-table').filter({ hasText: '沪深300ETF' });
      await expect(table.getByRole('columnheader')).toHaveText([
        '产品',
        '市值',
        '占比',
        /累计收益/,
        /最新收益\(\d{2}\/\d{2}\)/,
      ]);
      await expect(productRow.getByTestId('holding-ratio-value')).toHaveText(/\d+\.\d%/);
    }

    const tipScope = isMobile
      ? productRow
      : page.getByTestId('holding-product-table').filter({ hasText: '沪深300ETF' });
    const tipTrigger = tipScope.getByRole('button', { name: '查看累计收益说明' });
    const tipContent = page.getByTestId('info-tip-content');
    await expect(tipTrigger).toHaveAttribute('aria-expanded', 'false');
    const tipBox = await tipTrigger.boundingBox();
    expect(tipBox).not.toBeNull();
    expect(tipBox!.width, '累计收益说明触点至少 44px 宽').toBeGreaterThanOrEqual(44);
    expect(tipBox!.height, '累计收益说明触点至少 44px 高').toBeGreaterThanOrEqual(44);
    if (isMobile) {
      const totalValueBox = await totalValue.boundingBox();
      expect(totalValueBox).not.toBeNull();
      expect(tipBox!.y + tipBox!.height, '说明触点不得覆盖下方收益值').toBeLessThanOrEqual(totalValueBox!.y + 1);
    }
    await expect(page.getByText('累计收益含已卖出实现盈亏', { exact: true })).toHaveCount(0);
    const pageUrl = page.url();
    await openPopover(page, tipTrigger, tipContent);
    await expect(tipContent).toHaveText('累计收益含已卖出实现盈亏');
    expect(page.url(), '打开持仓说明不得跳转产品详情').toBe(pageUrl);
    await page.keyboard.press('Escape');
    await expect(tipContent).toHaveCount(0);

    const cashRow = page.getByTestId(rowTestId).filter({ hasText: '可用现金' });
    await expect(cashRow).toBeVisible();
    await expect(cashRow.getByText('可用现金', { exact: true })).toBeVisible();
    await expect(cashRow).not.toContainText('华宝证券');
    if (!isMobile) {
      await expect(cashRow.getByTestId('total-profit-value')).toBeEmpty();
      await expect(cashRow.getByTestId('daily-profit-value')).toBeEmpty();
    }
  });

  test('桌面账表与移动聚合行均铺满维度组，不再保留桌面引导线缩进（#944 Phase 7.1）', async ({
    page,
  }, testInfo) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    const group = page
      .getByTestId('asset-group')
      .filter({ has: page.getByTestId('asset-group-header') })
      .first();
    const grid = group.getByTestId('asset-group-cards');
    await expect(grid).toBeVisible({ timeout: 10_000 });

    const groupBox = await group.boundingBox();
    const gridBox = await grid.boundingBox();
    expect(groupBox, '维度组容器未渲染出 boundingBox').not.toBeNull();
    expect(gridBox, '产品容器未渲染出 boundingBox').not.toBeNull();
    expect(gridBox!.x - groupBox!.x, '产品容器铺满维度组').toBeLessThanOrEqual(1);
    await expect(grid).toHaveCSS('border-left-width', '0px');
    if (testInfo.project.name === 'mobile') {
      await expect(group.getByTestId('holding-product-card').first()).toBeVisible();
    } else {
      await expect(group.getByTestId('holding-product-table')).toBeVisible();
      await expect(group.getByTestId('holding-product-row').first()).toBeVisible();
    }
  });

  test('按产品视图：在途聚合卡渲染，全部卡行级占比加总 = 100.0（§4 最大余数法）', async ({
    page,
  }, testInfo) => {
    const code = await setupInTransitPortfolio(page, testInfo);
    // gotoPortfolioDetail 的 code 刻意收窄为种子组合字面量，自建组合直导航 +
    // 同款就绪信号（页头 h1 可见）
    await page.goto(`/portfolio/${code}`);
    await page
      .getByRole('heading', { level: 1 })
      .first()
      .waitFor({ state: 'visible', timeout: 15_000 });

    const isMobile = testInfo.project.name === 'mobile';
    const productRows = page.getByTestId(isMobile ? 'holding-product-card' : 'holding-product-row');
    const inTransitCard = page.getByTestId('holding-intransit-card');
    await expect(productRows.first()).toBeVisible({ timeout: 10_000 });
    // T 日快照含 IN_TRANSIT_BUY 行 → 在途计市值、在途卡渲染（#636 评审补回）
    await expect(inTransitCard).toBeVisible();
    await expect(inTransitCard).toContainText('占比');

    const extractPercents = async (locator: Locator, labeled: boolean): Promise<number[]> => {
      const texts = labeled ? await locator.allInnerTexts() : await locator.allTextContents();
      return texts.map((t) => {
        const m = labeled ? /占比\s*([\d.]+)%/.exec(t) : /([\d.]+)%/.exec(t);
        expect(m, `行文本应含占比：${t}`).toBeTruthy();
        return Number(m![1]);
      });
    };
    const productPercents = isMobile
      ? await extractPercents(productRows, true)
      : await extractPercents(productRows.getByTestId('holding-ratio-value'), false);
    const all = [
      ...productPercents,
      ...(await extractPercents(inTransitCard, true)),
    ];
    const sum = all.reduce((s, v) => s + v, 0);
    expect(Math.abs(sum - 100.0)).toBeLessThanOrEqual(0.05);
  });

  test('切换按平台视图：URL 写入 ?view=platform，桌面账表与移动平台卡字段齐全', async ({ page }, testInfo) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByTestId('holdings-view-tabs')).toBeVisible({ timeout: 10_000 });

    await clickViewTab(page, 'platform');
    await expect(page).toHaveURL(/[?&]view=platform/);
    await expect(page.getByTestId('holdings-tab-platform')).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    const platformRow = platformHoldings(page).filter({ hasText: '华宝证券' });
    await expect(platformRow).toBeVisible();
    await expect(platformRow).toContainText('1 只产品');
    await expect(platformRow).toContainText('现金 ¥');
    if (testInfo.project.name === 'mobile') {
      await expect(platformRow).toContainText('持仓市值');
      await expect(platformRow).toContainText('持有收益');
    } else {
      const table = page.getByTestId('holding-platform-table');
      await expect(table.getByRole('columnheader')).toHaveText([
        '平台',
        '市值',
        '占比',
        '持有收益',
        '产品与现金',
      ]);
      await expect(page.getByTestId('platform-group-header')).toContainText('平台合计');
    }
  });

  test('深链 ?view=platform 直接进入按平台视图（URL 驱动，/m 重定向保参回归）', async ({
    page,
  }) => {
    // gotoPortfolioDetail 不带 query，深链场景刻意直导航 + 同款就绪信号（页头 h1 可见）
    await page.goto(`/portfolio/${E2E_ACTIVE}?view=platform`);
    await page
      .getByRole('heading', { level: 1 })
      .first()
      .waitFor({ state: 'visible', timeout: 15_000 });

    await expect(page).toHaveURL(/[?&]view=platform/);
    await expect(page.getByTestId('holdings-tab-platform')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(platformHoldings(page).first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test('刷新后视图保持（URL 驱动），切回按产品后 view 参数移除', async ({ page }) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByTestId('holdings-view-tabs')).toBeVisible({ timeout: 10_000 });

    await clickViewTab(page, 'platform');
    await expect(page).toHaveURL(/[?&]view=platform/);

    await page.reload();
    await expect(page.getByTestId('holdings-tab-platform')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(platformHoldings(page).first()).toBeVisible();

    await clickViewTab(page, 'product');
    // router.replace 为异步：用轮询断言等 URL 收敛，不做即时 page.url() 快照
    await expect(page).toHaveURL((url) => !url.search.includes('view='));
    await expect(page
      .getByTestId('holding-product-card')
      .or(page.getByTestId('holding-product-row'))
      .first()).toBeVisible();
  });

  test('active 管理操作统一收进更多菜单，主页不再重复管理入口（#943 S4，双端）', async ({ page }, testInfo) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByRole('link', { name: '申购赎回', exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: '调仓', exact: true })).toBeVisible();
    await expect(page.getByTestId('manage-links-card')).toHaveCount(0);

    await openPortfolioMoreMenu(page, testInfo);
    const menu = page.getByTestId('portfolio-more-menu');
    await expect(menu.locator('a')).toHaveText(['份额变动事件', '快照管理', '现金流水']);
    await expect(menu.getByRole('menuitem', { name: '申购赎回记录' })).toHaveCount(0);
    await expect(menu.getByRole('menuitem', { name: '调仓交易记录' })).toHaveCount(0);
    await expect(
      page.getByRole('menuitem', { name: '份额变动事件' }).or(page.getByRole('link', { name: '份额变动事件' })),
    ).toHaveCount(1);
    await expect(
      page.getByRole('menuitem', { name: '快照管理' }).or(page.getByRole('link', { name: '快照管理' })),
    ).toHaveCount(1);
    await expect(page.getByRole('link', { name: '持仓管理' })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(menu).toBeHidden();
  });

  test('draft/closed 更多菜单保留历史入口、快照与危险操作（#943 S4，双端）', async ({ page }, testInfo) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    const headers = await authHeaders(page);
    const suffix = `${testInfo.project.name}_${testInfo.retry}`;
    const draftCode = `E943D_${suffix}`;
    const draftResp = await page.request.post('/api/portfolios', {
      data: { code: draftCode, name: `更多菜单 draft ${draftCode}` },
      headers,
    });
    await expect(draftResp, `创建 draft 组合失败 ${draftResp.status()}`).toBeOK();

    const closedCode = `E943C_${suffix}`;
    const closedResp = await page.request.post('/api/portfolios', {
      data: { code: closedCode, name: `更多菜单 closed ${closedCode}` },
      headers,
    });
    await expect(closedResp, `创建 closed 组合失败 ${closedResp.status()}`).toBeOK();
    await activatePortfolioViaApi(page, headers, closedCode);
    await closePortfolioViaApi(page, headers, closedCode);

    for (const code of [draftCode, closedCode]) {
      await page.goto(`/portfolio/${code}`);
      await page.getByTestId('page-header').waitFor({ state: 'visible', timeout: 15_000 });
      await expect(page.getByTestId('manage-links-card')).toHaveCount(0);
      await expect(page.getByRole('link', { name: '持仓管理' })).toHaveCount(0);
      await expect(
        page.getByRole('button', { name: code === draftCode ? '首次申购激活' : '重新激活' }),
      ).toBeVisible();
      await openPortfolioMoreMenu(page, testInfo);
      const menu = page.getByTestId('portfolio-more-menu');
      await expect(menu.locator('a')).toHaveText([
        '申购赎回记录',
        '调仓交易记录',
        '份额变动事件',
        '快照管理',
        '现金流水',
      ]);
      await expect(menu.getByText('危险操作')).toBeVisible();
      await expect(page.getByTestId(`portfolio-delete-${code}`)).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(menu).toBeHidden();
    }
  });

  test('draft 组合不渲染持仓明细区', async ({ page }, testInfo) => {
    // 自建 draft 组合而非复用种子 E2E_PORT 的草稿态：全量套件按 project-major 顺序
    // 执行，chromium 半区的 trade-buy-amount-linkage 用例 7 会经 API「申购+确认」
    // 激活共享种子 E2E_PORT，mobile 半区运行时它已是「活跃」（#636 CI 截图实证），
    // 依赖全局种子的 draft 断言会在 mobile 半区必红。按 share-change-cash-pay-date
    // 的自建组合模式造 fixture，code 区分 project/retry。本用例的空壳 draft 虽已可经
    // #820 的 DELETE 清理，但清理策略不变：残留由每轮重建的 E2E 库吸收，删除端点只在
    // portfolio-delete.spec.ts 的显式用例里被测到（不在每个夹具用例里顺手加清理动作）。
    const code = `E595_${testInfo.project.name}_${testInfo.retry}`;
    await gotoPortfolioDetail(page, E2E_PORT);
    const headers = await authHeaders(page);
    const createResp = await page.request.post('/api/portfolios', {
      data: { code, name: `draft 视图 ${code}` },
      headers,
    });
    await expect(createResp, `创建 draft 组合失败 ${createResp.status()}`).toBeOK();

    // gotoPortfolioDetail 的 code 刻意收窄为种子组合字面量，自建组合直导航 +
    // 同款就绪信号（页头 h1 可见）
    await page.goto(`/portfolio/${code}`);
    await page
      .getByRole('heading', { level: 1 })
      .first()
      .waitFor({ state: 'visible', timeout: 15_000 });

    await expect(page.getByText('组合尚未激活')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('holdings-view-tabs')).toHaveCount(0);
  });
});
