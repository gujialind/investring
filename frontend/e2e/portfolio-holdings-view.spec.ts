import { test, expect } from '@playwright/test';
import { E2E_ACTIVE, E2E_PORT, authHeaders, gotoPortfolioDetail } from './helpers';

/**
 * #595 组合详情页持仓明细双视图：「按产品 / 按平台」分段切换 + URL ?view= 持久化（D-7）。
 *
 * 数据说明（种子契约，backend/tests/seed_base.py）：
 * - E2E_ACTIVE：active 组合，单平台 HBZQ（华宝证券）+ 510300.SH（沪深300ETF）持仓
 *   + 现金 + 连续 2 日快照 → 按产品视图有 1 张产品聚合卡 + 1 张现金聚合卡；
 *   按平台视图有 1 张华宝证券平台卡（1 只产品 · 现金）。
 * - E2E_PORT：draft 组合，无持仓 → 不渲染持仓明细区。
 *   ⚠️ 草稿态不可作为全量套件断言前提：CI 按 project-major 顺序跑（全部 chromium
 *   先于 mobile），trade-buy-amount-linkage 用例 7 经 API「申购+确认」会把共享
 *   种子 E2E_PORT 置为活跃（#636 CI 截图实证），mobile 半区运行时它已不是 draft。
 *   故 draft 用例自建组合取证（见该用例注释），不复用 E2E_PORT 的草稿态。
 * 断言为可见性与关系式，不硬绑定快照数字；两端共用组件，mobile project 同跑。
 */

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
