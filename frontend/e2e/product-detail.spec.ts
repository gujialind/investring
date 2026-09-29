import { test, expect } from "@playwright/test";
import { E2E_ACTIVE, gotoPortfolioDetail } from "./helpers";

/**
 * #595 步骤③ 持仓产品详情页（M3/D3，双端共享 ProductDetailContent）。
 *
 * 数据说明（种子契约，backend/tests/seed_base.py / seed_e2e.py）：
 * - E2E_ACTIVE：单平台 HBZQ（华宝证券）持有 510300.SH（沪深300ETF）15,000 份，
 *   连续 2 日快照（D2=4.0000 / D3=4.2000，仅 unit_price，accumulated_nav/pct_change
 *   为 NULL）→ 市值 ¥63,000.00、持有/最新收益 +¥3,000.00。
 * - 净值夹具无累计净值 → 累计净值曲线走「暂无净值数据」空态、六窗区间收益率
 *   全为占位「--」（#637：None 语义，历史不足或数据缺失不硬凑数）。
 * - 断言为可见性与关系式，不硬绑定快照日期；两端共用组件，mobile project 同跑。
 */

const PRODUCT_PATH = `/portfolio/${E2E_ACTIVE}/product/510300.SH?market=CN_EXCHANGE`;

test.describe("产品详情页", () => {
  test("页头与概览卡：产品名、代码·市场、市值、份额与收益", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    await expect(
      page.getByRole("heading", { name: "沪深300ETF" })
    ).toBeVisible();
    await expect(page.getByText("510300.SH")).toBeVisible();

    const overview = page.getByTestId("product-overview-card");
    await expect(overview).toBeVisible();
    await expect(overview).toContainText("63,000.00");
    await expect(overview).toContainText("15,000.00");
    // 持有收益（含 %）、累计收益、最新收益三处收益（+¥3,000.00 至少出现一次）
    await expect(overview.getByText(/\+¥3,000\.00/).first()).toBeVisible();
    await expect(overview).toContainText("(+5.00%)");
    // 单位净值行（最新一条净值，日期随种子滚动只断言数值与标签）
    await expect(overview).toContainText("单位净值 4.2000");
    // 累计收益注记（口径说明，琥珀警示色 token）
    await expect(overview).toContainText("*累计收益含已卖出实现盈亏");
  });

  test("净值曲线空态与六窗区间收益率占位", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    const curve = page.getByTestId("product-curve-card");
    await expect(curve.getByRole("heading", { name: "累计净值走势" })).toBeVisible();
    // 默认近6月选中；ETF 夹具无累计净值 → 图表空态文案
    await expect(page.getByTestId("nav-range-6m")).toHaveAttribute("aria-pressed", "true");
    await expect(curve).toContainText("暂无净值数据");

    const returns = page.getByTestId("product-returns-card");
    for (const field of ["m1", "m3", "m6", "y1", "ytd", "all"]) {
      await expect(returns.getByTestId(`return-${field}`)).toContainText("--");
    }
  });

  test("区间 Tab 切换", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    await page.getByTestId("nav-range-1m").click();
    await expect(page.getByTestId("nav-range-1m")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("nav-range-6m")).toHaveAttribute("aria-pressed", "false");
  });

  test("历史净值：日期降序两行、累计净值/日涨跌占位、无查看更多", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    const history = page.getByTestId("product-history-card");
    const rows = history.getByTestId(/^product-history-row/);
    // seed 仅 D2/D3 两条：首行（较新交易日）unit 4.2000 在前
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText("4.2000");
    await expect(rows.nth(1)).toContainText("4.0000");
    // 累计净值与日涨跌夹具为 NULL → 占位 --
    await expect(rows.nth(0).getByText("--").first()).toBeVisible();
    await expect(history.getByTestId("history-load-more")).toHaveCount(0);
  });

  test("平台分布：单平台一行、市值与占比、行可点击进入平台-产品详情", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    const card = page.getByTestId("platform-distribution-card");
    const rows = card.getByTestId("platform-distribution-row");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("华宝证券");
    await expect(rows.first()).toContainText("15,000.00");
    await expect(rows.first()).toContainText("63,000.00");
    await expect(rows.first()).toContainText("100.0%");
    // S7：行点击 → 平台-产品详情页（步骤④接线）
    await rows.first().getByRole("link").click();
    await page.waitForURL(/\/platforms\/HBZQ\/products\/510300\.SH/);
    await expect(
      page.getByRole("heading", { name: /华宝证券.*沪深300ETF/ })
    ).toBeVisible();
  });

  test("交易记录卡：该产品跨平台记录与查看全部链接", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    const card = page.getByTestId("product-trades-card");
    const rows = card.getByTestId("product-trade-row");
    await expect(rows.first()).toBeVisible();
    await expect(rows.first()).toContainText("买入");
    // 查看全部 → trades 产品预填 URL（#595 操作行同一预填契约）
    await expect(card.getByRole("link", { name: "查看全部" })).toHaveAttribute(
      "href",
      new RegExp(`/trades\\?product=510300\\.SH&market=CN_EXCHANGE`)
    );
  });

  test("操作行买入：trades 页产品预填且列表非空", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    await page.getByTestId("product-action-row").getByRole("link", { name: "买入" }).click();
    await page.waitForURL(/\/trades\?/);
    expect(page.url()).toContain("product=510300.SH");
    expect(page.url()).toContain("market=CN_EXCHANGE");
    expect(page.url()).toContain("trade_type=buy");
    // 预填后服务端过滤生效：510300.SH 的已确认买入在列表中
    await expect(page.locator("table tbody tr").first()).toBeVisible({ timeout: 10_000 });
  });

  test("从组合详情产品卡点击进入（深链起点）", async ({ page }) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await page.getByRole("link", { name: /沪深300ETF/ }).first().click();
    await page.waitForURL(/\/product\/510300\.SH/);
    await expect(
      page.getByRole("heading", { name: "沪深300ETF" })
    ).toBeVisible();
  });
});

// #595 §4.5：现金产品详情页操作行为转入/转出 + 市值更新
// B-6 方案 B：路由迁移为 /product/[productCode]?market=，CASH 产品以空 market 表示
const CASH_PRODUCT_PATH = `/portfolio/${E2E_ACTIVE}/product/CASH?market=`;

test.describe("现金产品详情页", () => {
  test("操作行：转入/转出/市值更新（非买入/卖出/事件）", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    const actionRow = page.getByTestId("product-action-row");
    await expect(actionRow).toBeVisible();
    await expect(actionRow.getByRole("button", { name: "转入" })).toBeVisible();
    await expect(actionRow.getByRole("button", { name: "转出" })).toBeVisible();
    await expect(actionRow.getByRole("button", { name: "市值更新" })).toBeVisible();
    // 不应有买入/卖出/事件按钮
    await expect(actionRow.getByRole("link", { name: "买入" })).not.toBeVisible();
    await expect(actionRow.getByRole("link", { name: "卖出" })).not.toBeVisible();
  });

  test("操作行：转入/转出打开现金转移 Dialog（聚合视角无平台预填）", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    const actionRow = page.getByTestId("product-action-row");
    // 转入：打开 Dialog，转出/转入平台均显示 placeholder（聚合视角无平台上下文）
    await actionRow.getByRole("button", { name: "转入" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("heading", { name: "平台间现金转移" })).toBeVisible();
    await expect(dialog.getByText("选择转出平台")).toBeVisible();
    await expect(dialog.getByText("选择转入平台")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    // 转出：同样打开 Dialog（聚合视角两按钮同入口，无方向预填）
    await actionRow.getByRole("button", { name: "转出" }).click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("heading", { name: "平台间现金转移" })).toBeVisible();
    await expect(dialog.getByText("选择转出平台")).toBeVisible();
    await expect(dialog.getByText("选择转入平台")).toBeVisible();
  });

  test("市值更新按钮打开 Dialog", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    await page.getByTestId("product-action-row").getByRole("button", { name: "市值更新" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("heading", { name: "更新现金市值" })).toBeVisible();
  });

  test("缺 market 参数的非现金产品 EmptyState（R-4）", async ({ page }) => {
    // R-4：market 迁移为 query 后可缺，参数问题须与「未找到持仓」区分（同平台-产品页 S4 形态）
    await page.goto(`/portfolio/${E2E_ACTIVE}/product/510300.SH`);
    await expect(page.getByText("缺少 market 参数")).toBeVisible();
  });

  test("净值相关卡片不显示（现金无净值）", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    await expect(page.getByTestId("product-overview-card")).toBeVisible();
    // 净值走势/区间收益/历史净值卡片不应出现
    await expect(page.getByTestId("product-curve-card")).not.toBeVisible();
    await expect(page.getByTestId("product-returns-card")).not.toBeVisible();
    await expect(page.getByTestId("product-history-card")).not.toBeVisible();
  });
});
