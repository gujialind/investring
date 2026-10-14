/**
 * #705 产品标准码展示（显示码只上人眼文案，标识符仍用库内原始码）。
 *
 * 为什么走 route mock：E2E 后端由 `create_all + seed_base` 建库、**不跑 alembic**，
 * 港互认种子 `1001767344` 的 `hk_sale_code` 恒为 NULL → 显示码只能回退原始码，
 * 无法在真实种子上验「968050（1001767344）」这条口径。派生与回填逻辑由后端
 * `tests/unit/test_product_code.py` / `tests/unit/test_migration_0021.py` /
 * `tests/integration/test_product_display_code.py` 覆盖；本文件只钉**前端消费面**：
 * 后端给了 display_code 时各展示站点用它，且 href/key 等标识符不被显示码污染。
 *
 * 种子契约（`backend/tests/seed_base.py`）：E2E_ACTIVE 持 510300.SH + 现金；
 * 本文件的 mock 只替换 by-product / trades 两个读端点的响应，不写任何数据。
 */
import { expect, test } from '@playwright/test';
import { E2E_ACTIVE, collectPageErrors, gotoPortfolioDetail, gotoPortfolioSubpage } from './helpers';

/** 港互认行：显示码 = 6 位内地销售码，与 10 位机器码不同 → 双码并列 */
const HK_CODE = '1001767344';
const HK_SALE_CODE = '968050';
const HK_NAME = '港互认样本基金';
/** 场内 ETF 行：显示码 = 去 tushare 后缀 */
const ETF_CODE = '510300.SH';
const ETF_DISPLAY = '510300';

const PLATFORM_SLICE = {
  platform_code: 'HBZQ',
  platform_name: '华宝证券',
  ratio_in_product: 1.0,
};

const HK_AGGREGATE = {
  product_code: HK_CODE,
  market: 'HK_MUTUAL',
  product_name: HK_NAME,
  display_code: HK_SALE_CODE,
  market_value: 12000,
  ratio: 0.16,
  shares: 1000,
  platforms: [{ ...PLATFORM_SLICE, market_value: 12000, shares: 1000 }],
};

const ETF_AGGREGATE = {
  product_code: ETF_CODE,
  market: 'CN_EXCHANGE',
  product_name: '沪深300ETF',
  display_code: ETF_DISPLAY,
  market_value: 63000,
  ratio: 0.84,
  shares: 15000,
  platforms: [{ ...PLATFORM_SLICE, market_value: 63000, shares: 15000 }],
};

test.describe('#705 产品标准码展示', () => {
  test('持仓产品卡：港互认双码并列、场内去后缀，卡片链接仍用原始码', async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.route(
      /\/api\/positions\/portfolio\/[^/]+\/holdings\/by-product(\?|$)/,
      (route) =>
        route.fulfill({
          status: 200,
          json: {
            portfolio_code: E2E_ACTIVE,
            snapshot_date: '2026-01-05',
            total_market_value: 75000,
            in_transit_market_value: 0,
            products: [HK_AGGREGATE, ETF_AGGREGATE],
          },
        })
    );
    await gotoPortfolioDetail(page, E2E_ACTIVE);

    const hkCard = page.getByTestId('holding-product-card').filter({ hasText: HK_NAME });
    await expect(hkCard).toBeVisible({ timeout: 10_000 });
    // 双码并列：销售码在前（人眼口径），机器码在括号内可核对
    await expect(hkCard).toContainText(`${HK_SALE_CODE}（${HK_CODE}）`);
    await expect(hkCard).toContainText('香港互认');

    const etfCard = page.getByTestId('holding-product-card').filter({ hasText: '沪深300ETF' });
    await expect(etfCard).toBeVisible();
    await expect(etfCard).toContainText(`${ETF_DISPLAY} · A股场内`);
    await expect(etfCard).not.toContainText(ETF_CODE);

    // 标识符不变量：卡片 href 仍是原始码（换成显示码会让港互认/LOF 链接失效）。
    // 用尾部正则而非全等——mobile project 经 middleware 重定向到 /m/... 前缀
    const hkLink = page.locator('a', { has: hkCard });
    await expect(hkLink).toHaveAttribute(
      'href',
      new RegExp(`/portfolio/${E2E_ACTIVE}/product/${HK_CODE}\\?market=HK_MUTUAL$`)
    );

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('调仓列表产品列：显示码来自后端 display_code，机器码不出现在展示位', async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.route(/\/api\/trades(\?|$)/, (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      return route.fulfill({
        status: 200,
        json: {
          items: [
            {
              id: 9001,
              portfolio_code: E2E_ACTIVE,
              product_code: ETF_CODE,
              market: 'CN_EXCHANGE',
              platform_code: 'HBZQ',
              trade_type: 'buy',
              shares: 15000,
              amount: 60000,
              price: 4.0,
              fee: 0,
              trade_date: '2026-01-05',
              confirm_date: '2026-01-05',
              status: 'confirmed',
              transfer_group: 'rebal_display_code_etf',
              product_name: '沪深300ETF',
              display_code: ETF_DISPLAY,
            },
            {
              id: 9002,
              portfolio_code: E2E_ACTIVE,
              product_code: HK_CODE,
              market: 'HK_MUTUAL',
              platform_code: 'HBZQ',
              trade_type: 'sell',
              shares: 100,
              amount: 1200,
              price: 12.0,
              fee: 0,
              trade_date: '2026-01-06',
              status: 'pending',
              transfer_group: 'sub_9002',
              product_name: HK_NAME,
              display_code: HK_SALE_CODE,
            },
          ],
          total: 2,
          page: 1,
          page_size: 20,
        },
      });
    });
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'trades');

    // ProductCell 次行：`显示码 · 市场名`（#355 收敛点，调仓/份额事件两页共用）
    const etfRow = page.getByRole('row').filter({ hasText: '沪深300ETF' }).first();
    await expect(etfRow).toBeVisible({ timeout: 10_000 });
    await expect(etfRow).toContainText(`${ETF_DISPLAY} · A股场内`);
    await expect(etfRow).not.toContainText(ETF_CODE);

    const hkRow = page.getByRole('row').filter({ hasText: HK_NAME }).first();
    await expect(hkRow).toBeVisible();
    await expect(hkRow).toContainText(`${HK_SALE_CODE}（${HK_CODE}）`);
    await expect(hkRow).toContainText('香港互认');

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
