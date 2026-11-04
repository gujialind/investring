import { test, expect } from "@playwright/test";
import { collectPageErrors } from "./helpers";

/**
 * #824 产品管理页列合并与净值型产品详情页。
 *
 * 数据说明（种子契约，backend/tests/seed_base.py）：产品目录含 510300.SH（沪深300ETF，
 * CN_EXCHANGE，五维 ASSET_STOCK/REGION_CN/STYLE_BALANCED/SIZE_LARGE/SEG_COMPOSITE）与
 * 虚拟产品 CASH（现金类资产）。510300.SH 有连续 2 日净值夹具（4.0000→4.2000，仅 unit_price）
 * → nav-analysis 回退市价口径（#703），与 product-detail.spec.ts 同源。
 * 断言为可见性与导航关系，不硬绑定快照日期；两端共用组件，mobile project 同跑
 * （mobile 经 src/proxy.ts 重定向到 /m 前缀，href 断言用 (m/)? 容忍）。
 */

const DETAIL_PATH = "/products/510300.SH?market=CN_EXCHANGE";

test.describe("产品管理表格列合并（#824 Part 1）", () => {
  test("三列合并为单一「产品名称」列，次行为 显示码 · 中文市场名", async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.goto("/products");
    const card = page.getByTestId("products-list-card");
    await expect(card).toBeVisible({ timeout: 15_000 });

    // 旧三列消失、新单列存在（columnheader 角色双端同形）
    await expect(page.getByRole("columnheader", { name: "产品名称" })).toBeVisible();
    for (const removed of ["代码", "市场", "名称"]) {
      await expect(page.getByRole("columnheader", { name: removed, exact: true })).toHaveCount(0);
    }

    // 单元格两行：主行产品名、次行 `显示码 · 中文市场名`（不再吐英文枚举 CN_EXCHANGE）
    const etfRow = card.getByRole("row").filter({ hasText: "沪深300ETF" }).first();
    await expect(etfRow).toContainText("510300 · A股场内");
    await expect(etfRow).not.toContainText("CN_EXCHANGE");

    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("净值型产品名可点击跳详情页，虚拟产品名无链接", async ({ page }) => {
    await page.goto("/products");
    await expect(page.getByTestId("products-list-card")).toBeVisible({ timeout: 15_000 });

    // 净值型（ETF）：产品名是链接，点击落详情页
    const etfLink = page
      .getByTestId("product-detail-link")
      .filter({ hasText: "沪深300ETF" })
      .first();
    await expect(etfLink).toBeVisible();
    await etfLink.click();
    await page.waitForURL(/\/(m\/)?products\/510300\.SH\?market=CN_EXCHANGE/);
    await expect(page.getByTestId("product-basic-info-card")).toBeVisible({ timeout: 10_000 });

    // 回到列表：虚拟产品（CASH）产品名**不**是链接（无详情页）
    await page.goto("/products");
    await expect(page.getByTestId("products-list-card")).toBeVisible({ timeout: 15_000 });
    const cashRow = page
      .getByTestId("products-list-card")
      .getByRole("row")
      .filter({ hasText: "现金类资产" })
      .first();
    await expect(cashRow).toBeVisible();
    await expect(cashRow.getByTestId("product-detail-link")).toHaveCount(0);
  });
});

test.describe("净值型产品详情页（#824 Part 3）", () => {
  test("四卡齐备：基本信息 / 分类维度 / 同步操作 / 净值三卡", async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.goto(DETAIL_PATH);

    // 页头：产品名 + 显示码 · 中文市场名
    await expect(page.getByRole("heading", { name: "沪深300ETF" }).first()).toBeVisible();
    await expect(page.getByText("510300 · A股场内").first()).toBeVisible();

    // 基本信息卡：元信息字段（显示码去后缀、市场中文名、类型）
    const basic = page.getByTestId("product-basic-info-card");
    await expect(basic).toBeVisible({ timeout: 10_000 });
    await expect(basic).toContainText("510300");
    await expect(basic).toContainText("A股场内");
    await expect(basic).toContainText("ETF");

    // 分类维度卡：五维码值经字典映射为中文名（种子 510300 = ASSET_STOCK/REGION_CN/...），
    // 断言映射结果而非机器码——映射失效会回落成 ASSET_STOCK 而变红
    const dim = page.getByTestId("product-dimension-card");
    await expect(dim).toBeVisible();
    await expect(dim).toContainText("股票");
    await expect(dim).not.toContainText("ASSET_STOCK");

    // 同步操作区：净值型产品有两个同步入口
    const sync = page.getByTestId("product-sync-card");
    await expect(sync).toBeVisible();
    await expect(sync.getByRole("button", { name: /同步价格/ })).toBeVisible();
    await expect(sync.getByRole("button", { name: /同步历史/ })).toBeVisible();

    // 复用 ProductNavCards 的净值三卡（与组合详情-产品详情卡同组件同 testid）
    await expect(page.getByTestId("product-curve-card")).toBeVisible();
    await expect(page.getByTestId("product-returns-card")).toBeVisible();
    await expect(page.getByTestId("product-history-card")).toBeVisible();
    // 夹具无累计净值 → 回退市价口径（#703），标题自带说明
    await expect(
      page.getByTestId("product-curve-card").getByRole("heading", { name: "收盘价走势（市价）" })
    ).toBeVisible();

    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("返回按钮回产品管理列表", async ({ page }) => {
    await page.goto(DETAIL_PATH);
    await expect(page.getByTestId("product-basic-info-card")).toBeVisible({ timeout: 10_000 });
    await page.getByRole("link", { name: "返回" }).click();
    await page.waitForURL(/\/(m\/)?products(\?|$)/);
    await expect(page.getByTestId("products-list-card")).toBeVisible({ timeout: 15_000 });
  });

  test("缺 market 参数 → EmptyState，不说成「无数据」", async ({ page }) => {
    await page.goto("/products/510300.SH");
    await expect(page.getByText("缺少 market 参数")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("未找到该产品")).toHaveCount(0);
  });

  test("产品不存在 → 真·空态，不是失败态", async ({ page }) => {
    await page.goto("/products/NONEXISTENT?market=CN_EXCHANGE");
    await expect(page.getByText("未找到该产品")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("加载失败")).toHaveCount(0);
  });

  test("读取失败 → 失败态与重试，不伪装成空态（§1.2 口径①）", async ({ page }) => {
    // 判据 scoped 到单产品读端点 GET /products/{code}/{market}：本页还打维度字典与
    // nav 系端点，写宽会把它们一并打成 500、用例不再只对这一条聚合的失败呈现归因。
    await page.route(/\/api\/products\/[^/]+\/[^/]+(\?|$)/, (route) =>
      route.fulfill({
        status: 500,
        json: { detail: { error: "INTERNAL", message: "产品详情服务暂不可用" } },
      })
    );
    await page.goto(DETAIL_PATH);
    await expect(page.getByText("加载失败：产品详情服务暂不可用")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText("未找到该产品")).toHaveCount(0);
    const retry = page.getByRole("button", { name: "重试" });
    await expect(retry).toBeVisible();
    const refetched = page.waitForRequest(
      (r) => r.method() === "GET" && /\/api\/products\/[^/]+\/[^/]+(\?|$)/.test(r.url()),
      { timeout: 10_000 }
    );
    await retry.click();
    await refetched;
  });
});
