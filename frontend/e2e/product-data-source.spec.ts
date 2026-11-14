import { test, expect, type Page } from "@playwright/test";
import { collectPageErrors, dialogByTitle } from "./helpers";

/**
 * #880 产品数据源切换（SPEC v1.1 Block 1+2）。
 *
 * 用 route mock 替换单产品读/写端点与列表端点（与 product-manage-detail.spec.ts 同手法），
 * 不向 E2E 库注入、不跑真实上游探测。断言：净值型表单含数据源下拉与后果提示、港互认选项
 * 收窄为 akshare、切换保存请求体含 data_source 且详情回读新源/待验证、探测失败透传中文
 * 消息且对话框不关、虚拟产品不显示控件。两端共用组件，mobile project 同跑。
 */

const ETF_DETAIL = {
  code: "510300.SH", market: "CN_EXCHANGE", name: "沪深300ETF", product_type: "ETF",
  display_code: "510300", hk_sale_code: null, confirm_days: 0, nav_lag_days: 0,
  is_qdii: false, is_active: true, data_source: "tushare", data_source_status: "success",
  last_sync_at: null, created_at: "2025-01-01T00:00:00",
  asset_class_code: "ASSET_STOCK", region_code: "REGION_CN", style_code: "STYLE_BALANCED",
  size_code: "SIZE_LARGE", segment_code: "SEG_COMPOSITE",
};
const HK_DETAIL = {
  ...ETF_DETAIL, code: "1001767344", market: "HK_MUTUAL", name: "摩根国际债券",
  product_type: "OEF", display_code: "968050", hk_sale_code: "968050",
  data_source: "akshare", nav_lag_days: 1, confirm_days: 1,
};

const ETF_ENDPOINT = /\/api\/products\/510300\.SH\/CN_EXCHANGE(\?|$)/;
const HK_ENDPOINT = /\/api\/products\/1001767344\/HK_MUTUAL(\?|$)/;
const ETF_PATH = "/products/510300.SH?market=CN_EXCHANGE";
const HK_PATH = "/products/1001767344?market=HK_MUTUAL";

async function openEditFromDetail(page: Page, path: string) {
  await page.goto(path);
  await expect(page.getByTestId("product-basic-info-card")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  return dialogByTitle(page, "编辑产品");
}

test.describe("产品数据源切换表单（#880）", () => {
  test("净值型编辑表单含数据源下拉（两源）与后果提示", async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.route(ETF_ENDPOINT, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, json: ETF_DETAIL })
        : route.continue()
    );
    const dlg = await openEditFromDetail(page, ETF_PATH);
    const ds = dlg.locator("select#data_source");
    await expect(ds).toBeVisible();
    await expect(ds.locator("option")).toHaveCount(2);
    await expect(ds.locator("option").nth(0)).toHaveText("tushare");
    await expect(ds.locator("option").nth(1)).toHaveText("akshare");
    // 后果提示（V1 修订版：日涨跌列系统性口径差）
    await expect(dlg.getByText("日涨跌列口径随源变化")).toBeVisible();
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("港互认产品数据源下拉只 akshare（tushare 不支持）", async ({ page }) => {
    await page.route(HK_ENDPOINT, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, json: HK_DETAIL })
        : route.continue()
    );
    const dlg = await openEditFromDetail(page, HK_PATH);
    const ds = dlg.locator("select#data_source");
    await expect(ds).toBeVisible();
    await expect(ds.locator("option")).toHaveCount(1);
    await expect(ds.locator("option").nth(0)).toHaveText("akshare");
  });

  test("切换数据源保存：PUT 含 data_source，详情回读新源与待验证", async ({ page }) => {
    let current = { ...ETF_DETAIL };
    await page.route(ETF_ENDPOINT, (route) => {
      const method = route.request().method();
      if (method === "GET") return route.fulfill({ status: 200, json: current });
      if (method === "PUT") {
        const body = route.request().postDataJSON();
        current = { ...current, data_source: body.data_source, data_source_status: "pending" };
        return route.fulfill({ status: 200, json: current });
      }
      return route.continue();
    });
    const dlg = await openEditFromDetail(page, ETF_PATH);
    await dlg.locator("select#data_source").selectOption("akshare");
    const put = page.waitForRequest(
      (r) => r.method() === "PUT" && ETF_ENDPOINT.test(r.url())
    );
    await dlg.getByRole("button", { name: "保存修改" }).click();
    const body = (await put).postDataJSON();
    expect(body.data_source).toBe("akshare");
    // 对话框关闭，详情回读新源 + 状态待验证（V5）
    await expect(dlg).toHaveCount(0);
    const basic = page.getByTestId("product-basic-info-card");
    await expect(basic).toContainText("akshare", { timeout: 10_000 });
    await expect(basic).toContainText("待验证");
  });

  test("探测失败：错误 toast 透传中文消息，对话框不关", async ({ page }) => {
    await page.route(ETF_ENDPOINT, (route) => {
      const method = route.request().method();
      if (method === "GET") return route.fulfill({ status: 200, json: ETF_DETAIL });
      if (method === "PUT") {
        return route.fulfill({
          status: 422,
          json: { detail: { error: "DATA_SOURCE_PROBE_FAILED", message: "数据源暂时不可达，请稍后重试" } },
        });
      }
      return route.continue();
    });
    const dlg = await openEditFromDetail(page, ETF_PATH);
    await dlg.locator("select#data_source").selectOption("akshare");
    await dlg.getByRole("button", { name: "保存修改" }).click();
    // modal Dialog 开着时 toast 走 DOM testid（#640 口径）
    const toast = page.getByTestId("toast-card").filter({ hasText: "数据源暂时不可达" });
    await expect(toast).toBeVisible({ timeout: 10_000 });
    await expect(dlg).toBeVisible();
  });

  test("虚拟产品编辑表单不显示数据源控件", async ({ page }) => {
    const cashRow = {
      code: "CASH", market: "", name: "现金类资产", product_type: "CASH",
      hk_sale_code: null, display_code: "CASH", confirm_days: 0, nav_lag_days: 0,
      is_qdii: false, is_active: true, data_source_status: null,
    };
    await page.route(/\/api\/products(\?|$)/, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, json: { items: [cashRow], total: 1, page: 1, page_size: 20 } })
        : route.continue()
    );
    await page.goto("/products");
    const card = page.getByTestId("products-list-card");
    await expect(card).toBeVisible({ timeout: 15_000 });
    const row = card.getByRole("row").filter({ hasText: "现金类资产" }).first();
    await row.getByRole("button", { name: "更多操作" }).click();
    await page.getByRole("menuitem", { name: "编辑" }).click();
    const dlg = dialogByTitle(page, "编辑产品");
    await expect(dlg).toBeVisible();
    await expect(dlg.locator("select#data_source")).toHaveCount(0);
  });
});
