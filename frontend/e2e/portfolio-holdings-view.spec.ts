import { test, expect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { E2E_ACTIVE, E2E_PORT, authHeaders, gotoPortfolioDetail } from './helpers';

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
 * （买入在途，trades 不自动确认），返回组合 code。code 区分 project/retry
 * （组合无 DELETE 端点，残留由每轮重建的 E2E 库吸收，属既有接受的定案）。
 */
async function setupInTransitPortfolio(page: Page, testInfo: TestInfo): Promise<string> {
  const code = `E595IT_${testInfo.project.name}_${testInfo.retry}`;
  await gotoPortfolioDetail(page, E2E_ACTIVE);
  const headers = await authHeaders(page);

  // 交易日锚定经 /api/trading-calendar（#468）：apply = 最近交易日的前一交易日，
  // 申购确认日 = 最近交易日 T（T+1）；pending 买入 trade_date = T，T 日快照含在途行。
  const today = new Date().toISOString().slice(0, 10);
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

test.describe('#595 持仓明细双视图（按产品 / 按平台）', () => {
  test('默认按产品视图：URL 无 view 参数，产品聚合卡与现金聚合卡渲染', async ({ page }) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByTestId('holdings-view-tabs')).toBeVisible({ timeout: 10_000 });

    expect(page.url()).not.toContain('view=');
    await expect(page.getByTestId('holdings-tab-product')).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    // 产品聚合卡（跨平台合计，卡内带持有份额/累计收益/最新收益三列）
    const productCard = page
      .getByTestId('holding-product-card')
      .filter({ hasText: '沪深300ETF' });
    await expect(productCard).toBeVisible();
    await expect(productCard).toContainText('510300.SH');
    await expect(productCard).toContainText('持有份额');
    await expect(productCard).toContainText('占比');

    // 现金聚合卡（可用现金 · 平台名）
    const cashCard = page
      .getByTestId('holding-product-card')
      .filter({ hasText: '可用现金' });
    await expect(cashCard).toBeVisible();
    await expect(cashCard).toContainText('华宝证券');
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

    const productCards = page.getByTestId('holding-product-card');
    const inTransitCard = page.getByTestId('holding-intransit-card');
    await expect(productCards.first()).toBeVisible({ timeout: 10_000 });
    // T 日快照含 IN_TRANSIT_BUY 行 → 在途计市值、在途卡渲染（#636 评审补回）
    await expect(inTransitCard).toBeVisible();
    await expect(inTransitCard).toContainText('占比');

    // 关系式：现金卡 + 在途卡的行级占比（1 位小数）加总恒为 100.0
    const extractPercents = async (locator: Locator): Promise<number[]> => {
      const texts = await locator.allInnerTexts();
      return texts.map((t) => {
        const m = /占比\s*([\d.]+)%/.exec(t);
        expect(m, `卡片文本应含「占比 x.x%」：${t}`).toBeTruthy();
        return Number(m![1]);
      });
    };
    const all = [
      ...(await extractPercents(productCards)),
      ...(await extractPercents(inTransitCard)),
    ];
    const sum = all.reduce((s, v) => s + v, 0);
    expect(Math.abs(sum - 100.0)).toBeLessThanOrEqual(0.05);
  });

  test('切换按平台视图：URL 写入 ?view=platform，平台卡字段齐全', async ({ page }) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByTestId('holdings-view-tabs')).toBeVisible({ timeout: 10_000 });

    await page.getByTestId('holdings-tab-platform').click();
    await expect(page).toHaveURL(/[?&]view=platform/);
    await expect(page.getByTestId('holdings-tab-platform')).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    const platformCard = page
      .getByTestId('holding-platform-card')
      .filter({ hasText: '华宝证券' });
    await expect(platformCard).toBeVisible();
    await expect(platformCard).toContainText('持仓市值');
    await expect(platformCard).toContainText('持有收益');
    await expect(platformCard).toContainText('1 只产品');
    await expect(platformCard).toContainText('现金 ¥');
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
    await expect(page.getByTestId('holding-platform-card').first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test('刷新后视图保持（URL 驱动），切回按产品后 view 参数移除', async ({ page }) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByTestId('holdings-view-tabs')).toBeVisible({ timeout: 10_000 });

    await page.getByTestId('holdings-tab-platform').click();
    await expect(page).toHaveURL(/[?&]view=platform/);

    await page.reload();
    await expect(page.getByTestId('holdings-tab-platform')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(page.getByTestId('holding-platform-card').first()).toBeVisible();

    await page.getByTestId('holdings-tab-product').click();
    // router.replace 为异步：用轮询断言等 URL 收敛，不做即时 page.url() 快照
    await expect(page).toHaveURL((url) => !url.search.includes('view='));
    await expect(page.getByTestId('holding-product-card').first()).toBeVisible();
  });

  test('管理入口列表渲染（桌面端 #595 新增、移动端原有，双端共享卡）', async ({ page }) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByRole('link', { name: '持仓管理' })).toBeVisible();
    await expect(page.getByRole('link', { name: '申购赎回记录' })).toBeVisible();
    await expect(page.getByRole('link', { name: '调仓交易记录' })).toBeVisible();
    await expect(page.getByRole('link', { name: '份额变动事件' })).toBeVisible();
    await expect(page.getByRole('link', { name: '快照管理' })).toBeVisible();
  });

  test('draft 组合不渲染持仓明细区', async ({ page }, testInfo) => {
    // 自建 draft 组合而非复用种子 E2E_PORT 的草稿态：全量套件按 project-major 顺序
    // 执行，chromium 半区的 trade-buy-amount-linkage 用例 7 会经 API「申购+确认」
    // 激活共享种子 E2E_PORT，mobile 半区运行时它已是「活跃」（#636 CI 截图实证），
    // 依赖全局种子的 draft 断言会在 mobile 半区必红。按 share-change-cash-pay-date
    // 的自建组合模式造 fixture，code 区分 project/retry（组合无 DELETE 端点，残留
    // 由每轮重建的 E2E 库吸收，属既有接受的定案）。
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
