import { test, expect } from "@playwright/test";
import { E2E_ACTIVE, gotoPortfolioDetail } from "./helpers";

/**
 * #595 步骤④ 平台详情页（M4/D4）+ 平台-产品详情页（M5/D5），双端共享组件。
 *
 * 数据说明（种子契约，backend/tests/seed_base.py / seed_e2e.py）：
 * - E2E_ACTIVE：单平台 HBZQ（华宝证券）持有 510300.SH 15,000 份 + 现金余额。
 *   连续 2 日快照（D2=4.0000 / D3=4.2000）→ 市值 ¥63,000.00。
 *   2 笔交易：confirmed 买入 15,000@4.0（¥60,000）+ pending 买入 2,000@4.1（¥8,200）。
 * - 断言为可见性与关系式，不硬绑定快照日期；两端共用组件，mobile project 同跑。
 */

const PLATFORM_PATH = `/portfolio/${E2E_ACTIVE}/platforms/HBZQ`;
const PLATFORM_PRODUCT_PATH = `/portfolio/${E2E_ACTIVE}/platforms/HBZQ/products/510300.SH?market=CN_EXCHANGE`;

test.describe("平台详情页", () => {
  test("页头与概览卡：平台名、代码·类型、市值、指标行", async ({ page }) => {
    await page.goto(PLATFORM_PATH);
    await expect(
      page.getByRole("heading", { name: "华宝证券" })
    ).toBeVisible();
    await expect(page.getByText("HBZQ")).toBeVisible();

    const overview = page.getByTestId("platform-overview-card");
    await expect(overview).toBeVisible();
    // 持仓市值大数字（含该平台现金与在途）
    await expect(overview).toContainText(/\d{2,},\d{3}\.\d{2}/);
    // 4 项指标标签
    await expect(overview).toContainText("持有收益");
    await expect(overview).toContainText("现金余额");
    await expect(overview).toContainText("持仓产品数");
    await expect(overview).toContainText("占组合比");
  });

  test("操作行买入：trades 页平台预填且列表非空", async ({ page }) => {
    await page.goto(PLATFORM_PATH);
    await page.getByTestId("platform-action-row").getByRole("link", { name: "买入" }).click();
    await page.waitForURL(/\/trades\?/);
    expect(page.url()).toContain("platform=HBZQ");
    expect(page.url()).toContain("trade_type=buy");
    await expect(page.locator("table tbody tr").first()).toBeVisible({ timeout: 10_000 });
  });

  test("持仓明细：510300.SH 产品卡可点击进入平台-产品详情", async ({ page }) => {
    await page.goto(PLATFORM_PATH);
    const holdings = page.getByTestId("platform-holdings-section");
    await expect(holdings).toBeVisible();
    // 510300.SH 产品卡存在
    await expect(holdings.getByText("沪深300ETF")).toBeVisible();
    // 点击产品卡 → 平台-产品详情页
    await holdings.getByRole("link", { name: /沪深300ETF/ }).first().click();
    await page.waitForURL(/\/platforms\/HBZQ\/products\/510300\.SH/);
    await expect(
      page.getByRole("heading", { name: /华宝证券.*沪深300ETF/ })
    ).toBeVisible();
  });

  test("交易记录卡：该平台交易与查看全部链接", async ({ page }) => {
    await page.goto(PLATFORM_PATH);
    const card = page.getByTestId("platform-trades-card");
    const rows = card.getByTestId("platform-trade-row");
    await expect(rows.first()).toBeVisible();
    // 种子含买入 + 配对 CASH 卖出腿，首行可能是任一方向；断言至少有交易行
    await expect(rows.first()).toContainText(/买入|卖出/);
    await expect(card.getByRole("link", { name: "查看全部" })).toHaveAttribute(
      "href",
      new RegExp(`/trades\\?platform=HBZQ`)
    );
  });

  test("从组合详情按平台视图平台卡点击进入（深链起点）", async ({ page }) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    // 切换到按平台视图
    await page.getByTestId("holdings-tab-platform").click();
    await page.getByRole("link", { name: /华宝证券/ }).first().click();
    await page.waitForURL(/\/platforms\/HBZQ/);
    await expect(
      page.getByRole("heading", { name: "华宝证券" })
    ).toBeVisible();
  });
});

test.describe("平台-产品详情页", () => {
  test("页头与概览卡：平台名·产品名、切片市值、份额、收益、占产品比", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    await expect(
      page.getByRole("heading", { name: /华宝证券.*沪深300ETF/ })
    ).toBeVisible();
    await expect(page.getByText("510300.SH")).toBeVisible();

    const overview = page.getByTestId("platform-product-overview-card");
    await expect(overview).toBeVisible();
    await expect(overview).toContainText("63,000.00");
    await expect(overview).toContainText("15,000.00");
    await expect(overview).toContainText("100.0%");
  });

  test("操作行买入：trades 页平台+产品预填", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    await page.getByTestId("platform-product-action-row").getByRole("link", { name: "买入" }).click();
    await page.waitForURL(/\/trades\?/);
    expect(page.url()).toContain("product=510300.SH");
    expect(page.url()).toContain("platform=HBZQ");
    expect(page.url()).toContain("trade_type=buy");
    await expect(page.locator("table tbody tr").first()).toBeVisible({ timeout: 10_000 });
  });

  test("净值曲线空态与六窗区间收益率占位", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    const curve = page.getByTestId("platform-product-curve-card");
    await expect(curve).toContainText("暂无净值数据");

    const returns = page.getByTestId("platform-product-returns-card");
    for (const field of ["m1", "m3", "m6", "y1", "ytd", "all"]) {
      await expect(returns.getByTestId(`return-${field}`)).toContainText("--");
    }
  });

  test("历史净值：两行降序、占位符", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    const history = page.getByTestId("platform-product-history-card");
    const rows = history.getByTestId(/^platform-product-history-row/);
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText("4.2000");
    await expect(rows.nth(1)).toContainText("4.0000");
  });

  test("交易记录卡：该产品在该平台的交易", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    const card = page.getByTestId("platform-product-trades-card");
    const rows = card.getByTestId("platform-product-trade-row");
    await expect(rows.first()).toBeVisible();
    await expect(rows.first()).toContainText(/买入|卖出/);
  });

  test("「查看该产品全部平台持仓」链接 → 产品详情页", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    const link = page.getByTestId("view-all-platforms-link");
    // 单平台时该链接仍存在（指向产品详情页）
    await expect(link).toBeVisible();
    await link.click();
    await page.waitForURL(/\/product\/CN_EXCHANGE\/510300\.SH/);
    await expect(
      page.getByRole("heading", { name: "沪深300ETF" })
    ).toBeVisible();
  });

  test("从平台详情产品卡点击进入（深链起点）", async ({ page }) => {
    await page.goto(PLATFORM_PATH);
    const holdings = page.getByTestId("platform-holdings-section");
    await holdings.getByRole("link", { name: /沪深300ETF/ }).first().click();
    await page.waitForURL(/\/platforms\/HBZQ\/products\/510300\.SH/);
    await expect(
      page.getByRole("heading", { name: /华宝证券.*沪深300ETF/ })
    ).toBeVisible();
  });
});
