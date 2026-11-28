import { expect, test, type Page } from '@playwright/test';
import type { Position } from '../src/types/position';
import { E2E_ACTIVE, gotoPortfolioDetail } from './helpers';

const EXPECTED = [
  { code: 'ASSET_STOCK', name: '股票', value: 400, percent: 38.1 },
  { code: 'ASSET_CASH', name: '现金', value: 250, percent: 23.8 },
  { code: 'ASSET_BOND', name: '债券', value: 200, percent: 19 },
  { code: '__IN_TRANSIT__', name: '在途', value: 100, percent: 9.5 },
  // 「其他」折叠未知 50（4.8%）与另类 20（1.9%）；保留 JS 相加的浮点结果。
  { code: '__OTHER__', name: '其他', value: 70, percent: 4.8 + 1.9 },
  { code: 'ASSET_COMMODITY', name: '商品', value: 30, percent: 2.9 },
] as const;

const ASSET_CLASSES = [
  { code: 'ASSET_STOCK', name: '股票', sort_order: 1 },
  { code: 'ASSET_BOND', name: '债券', sort_order: 2 },
  { code: 'ASSET_COMMODITY', name: '商品', sort_order: 3 },
  { code: 'ASSET_CASH', name: '现金', sort_order: 4 },
  { code: 'ASSET_ALTERNATIVE', name: '另类', sort_order: 5 },
].map((item) => ({
  ...item,
  dimension: 'asset_class',
  is_active: true,
  applicable_asset_classes: [],
}));

const POSITIONS: Position[] = [
  {
    id: 9101,
    portfolio_code: E2E_ACTIVE,
    product_code: 'STOCK_FIXTURE',
    market_value: 400,
    asset_class_code: 'ASSET_STOCK',
  },
  {
    id: 9102,
    portfolio_code: E2E_ACTIVE,
    product_code: 'BOND_FIXTURE',
    market_value: 200,
    asset_class_code: 'ASSET_BOND',
  },
  {
    id: 9103,
    portfolio_code: E2E_ACTIVE,
    product_code: 'CASH',
    cash_amount: 250,
    asset_class_code: 'ASSET_CASH',
  },
  {
    id: 9104,
    portfolio_code: E2E_ACTIVE,
    product_code: 'IN_TRANSIT_BUY',
    cash_amount: 100,
  },
  {
    id: 9105,
    portfolio_code: E2E_ACTIVE,
    product_code: 'UNKNOWN_FIXTURE',
    market_value: 50,
    asset_class_code: 'ASSET_UNKNOWN',
  },
  {
    id: 9106,
    portfolio_code: E2E_ACTIVE,
    product_code: 'COMMODITY_FIXTURE',
    market_value: 30,
    asset_class_code: 'ASSET_COMMODITY',
  },
  {
    id: 9107,
    portfolio_code: E2E_ACTIVE,
    product_code: 'ALTERNATIVE_FIXTURE',
    market_value: 20,
    asset_class_code: 'ASSET_ALTERNATIVE',
  },
].map((position) => ({ ...position, snapshot_date: '2026-11-26' }));

async function mockAllocationData(page: Page, positions = POSITIONS) {
  await page.route(
    (url) => url.pathname === '/api/asset-classifications' && url.searchParams.get('dimension') === 'asset_class',
    (route) => route.fulfill({
      json: { items: ASSET_CLASSES, dimension_rules: {}, total: ASSET_CLASSES.length },
    }),
  );
  await page.route(
    (url) => url.pathname === '/api/positions' && url.searchParams.get('portfolio_code') === E2E_ACTIVE,
    (route) =>
      route.fulfill({
        status: 200,
        json: { items: positions, total: positions.length, page: 1, page_size: 100 },
      })
  );
}

async function openComposition(page: Page) {
  await gotoPortfolioDetail(page, E2E_ACTIVE);
  const card = page.getByTestId('asset-composition-card');
  await expect(card).toBeVisible();
  await expect(card.getByTestId('asset-allocation-row')).toHaveCount(EXPECTED.length);
  return card;
}

for (const theme of ['light', 'dark'] as const) {
  test.describe(`资产构成 ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((value) => localStorage.setItem('theme', value), theme);
      await mockAllocationData(page);
    });

    test('默认条形图按市值降序、占比合计 100.0，焦点与 hover 显示金额', async ({ page }) => {
      const card = await openComposition(page);
      const barToggle = card.getByTestId('asset-composition-view-bar');
      const pieToggle = card.getByTestId('asset-composition-view-pie');
      await expect(barToggle).toHaveAttribute('aria-pressed', 'true');
      await expect(pieToggle).toHaveAttribute('aria-pressed', 'false');
      await expect(card.getByTestId('asset-allocation-pie')).toHaveCount(0);

      const rows = card.getByTestId('asset-allocation-row');
      expect(await rows.evaluateAll((elements) => elements.map((element) => element.getAttribute('data-allocation-code'))))
        .toEqual(EXPECTED.map((item) => item.code));

      const percents = await rows.evaluateAll((elements) =>
        elements.map((element) => Number(element.getAttribute('aria-valuenow')))
      );
      expect(percents).toEqual(EXPECTED.map((item) => item.percent));
      expect(percents.reduce((sum, percent) => sum + percent, 0)).toBeCloseTo(100, 6);

      for (const [index, item] of EXPECTED.entries()) {
        const row = rows.nth(index);
        await expect(row).toContainText(item.name);
        await expect(row).toContainText(`${item.percent.toFixed(1)}%`);
        await expect(row).toContainText(`¥${item.value.toFixed(2)}`);
        await expect(row).toHaveAttribute('aria-valuemin', '0');
        await expect(row).toHaveAttribute('aria-valuemax', '100');
      }

      const geometry = await card.getByTestId('asset-allocation-bar').evaluate((bar) => {
        const tracks = Array.from(bar.querySelectorAll<HTMLElement>('[data-testid="asset-allocation-track"]'));
        const widths = tracks.map((track) => track.getBoundingClientRect().width);
        const ratios = tracks.map((track) => {
          const fill = track.querySelector<HTMLElement>('[data-testid="asset-allocation-fill"]')!;
          return (fill.getBoundingClientRect().width / track.getBoundingClientRect().width) * 100;
        });
        const firstRow = tracks[0].parentElement!;
        const rowCenter = firstRow.getBoundingClientRect().y + firstRow.getBoundingClientRect().height / 2;
        const centers = Array.from(firstRow.children).map((child) => {
          const box = child.getBoundingClientRect();
          return box.y + box.height / 2;
        });
        return { widths, ratios, rowCenter, centers };
      });
      expect(Math.max(...geometry.widths) - Math.min(...geometry.widths)).toBeLessThanOrEqual(1);
      geometry.ratios.forEach((ratio, index) => expect(ratio).toBeCloseTo(EXPECTED[index].percent, 0));
      geometry.centers.forEach((center) => expect(Math.abs(center - geometry.rowCenter)).toBeLessThanOrEqual(1));

      const firstRow = rows.first();
      await firstRow.focus();
      await expect(firstRow).toBeFocused();
      const ring = await firstRow.evaluate((element) => getComputedStyle(element).boxShadow);
      expect(ring).not.toBe('none');
      await expect(page.getByRole('tooltip')).toContainText('¥400.00');
      await firstRow.evaluate((element) => (element as HTMLElement).blur());
      await page.mouse.move(0, 0);
      await expect(page.getByRole('tooltip')).toHaveCount(0);
      const secondRow = rows.nth(1);
      await secondRow.evaluate((element) => element.scrollIntoView({ block: 'center' }));
      await secondRow.hover();
      await expect(page.getByRole('tooltip')).toContainText('¥250.00');
    });

    test('切换饼图后数据、顺序与身份色不变，URL 不变且 SVG 无条形焦点环', async ({ page }) => {
      const card = await openComposition(page);
      const fills = card.getByTestId('asset-allocation-fill');
      const barColors = await fills.evaluateAll((elements) =>
        elements.map((element) => getComputedStyle(element).backgroundColor)
      );
      const urlBefore = page.url();

      await card.getByTestId('asset-composition-view-pie').click();
      await expect(card.getByTestId('asset-composition-view-pie')).toHaveAttribute('aria-pressed', 'true');
      await expect(card.getByTestId('asset-composition-view-bar')).toHaveAttribute('aria-pressed', 'false');
      await expect(card.getByTestId('asset-allocation-bar')).toHaveCount(0);
      expect(page.url()).toBe(urlBefore);

      const pie = card.getByTestId('asset-allocation-pie');
      await expect(pie).toBeVisible();
      const legendItems = pie.getByTestId('asset-allocation-legend-item');
      await expect(legendItems).toHaveCount(EXPECTED.length);
      expect(await legendItems.evaluateAll((elements) => elements.map((element) => element.getAttribute('data-allocation-code'))))
        .toEqual(EXPECTED.map((item) => item.code));
      for (const [index, item] of EXPECTED.entries()) {
        await expect(legendItems.nth(index)).toContainText(item.name);
        await expect(legendItems.nth(index)).toContainText(`${item.percent.toFixed(1)}%`);
      }

      const sectors = pie.locator('.recharts-pie-sector path');
      await expect(sectors).toHaveCount(EXPECTED.length);
      await expect.poll(async () =>
        sectors.evaluateAll((elements) =>
          elements.map((element) => getComputedStyle(element).fill)
        )
      ).toEqual(barColors);
      await expect.poll(async () =>
        sectors.evaluateAll((elements) =>
          elements.map((element) => ({
            outline: getComputedStyle(element).outlineStyle,
            shadow: getComputedStyle(element).boxShadow,
          }))
        )
      ).toEqual(EXPECTED.map(() => ({ outline: 'none', shadow: 'none' })));
    });

    test('360px 资产栏不横向溢出，可选饼图强制上下排列', async ({ page }) => {
      await page.setViewportSize({ width: 360, height: 800 });
      await mockAllocationData(page, [{ ...POSITIONS[0], market_value: 1_234_567.89 }]);
      await gotoPortfolioDetail(page, E2E_ACTIVE);
      const card = page.getByTestId('asset-composition-card');
      await expect(card).toBeVisible();
      const row = card.getByTestId('asset-allocation-row');
      await expect(row).toHaveCount(1);
      await expect(row).toContainText('100.0%');
      await expect(row).toContainText('¥1,234,567.89');
      const columns = await row.evaluate((element) => {
        const children = Array.from(element.children) as HTMLElement[];
        const track = children[1].getBoundingClientRect();
        const percent = children[2].getBoundingClientRect();
        const amount = children[3].getBoundingClientRect();
        const box = element.getBoundingClientRect();
        return {
          percentAfterTrack: percent.left >= track.right,
          amountInsideRow: amount.right <= box.right + 1,
        };
      });
      expect(columns.percentAfterTrack).toBe(true);
      expect(columns.amountInsideRow).toBe(true);
      const cardBox = await card.boundingBox();
      expect(cardBox).not.toBeNull();
      expect(cardBox!.width).toBeLessThanOrEqual(360.5);
      const parentContentWidth = await card.evaluate((element) => {
        const parent = element.parentElement!;
        const style = getComputedStyle(parent);
        return parent.clientWidth - Number.parseFloat(style.paddingLeft) - Number.parseFloat(style.paddingRight);
      });
      expect(Math.abs(cardBox!.width - parentContentWidth)).toBeLessThanOrEqual(1);
      const barOverflow = await card.getByTestId('asset-allocation-bar').evaluate((element) =>
        element.scrollWidth - element.clientWidth
      );
      expect(barOverflow).toBeLessThanOrEqual(1);

      await card.getByTestId('asset-composition-view-pie').click();
      const pie = card.getByTestId('asset-allocation-pie');
      await expect(pie).toHaveCSS('flex-direction', 'column');
      const chartBox = await pie.locator('.recharts-responsive-container').first().boundingBox();
      const legendBox = await pie.getByTestId('asset-allocation-legend-item').first().boundingBox();
      expect(chartBox).not.toBeNull();
      expect(legendBox).not.toBeNull();
      expect(chartBox!.y + chartBox!.height).toBeLessThanOrEqual(legendBox!.y);
      const pageOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(pageOverflow).toBeLessThanOrEqual(1);
    });
  });
}
