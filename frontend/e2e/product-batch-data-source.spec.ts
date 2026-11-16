import { test, expect, type Page, type Locator } from "@playwright/test";
import { collectPageErrors, openPopover } from "./helpers";

/**
 * #882 产品数据源批量切换（SPEC v1.1 Block 3 / V4）。
 *
 * 用 route mock 替换列表/批量受理/job 状态与台账端点（与 product-data-source.spec.ts 同手法），
 * 不向 E2E 库注入、不跑真实上游探测。断言：多选 + 操作条 + 确认弹窗（只数/目标源/后果/清单预览）、
 * 提交请求体含显式清单与目标源、终态 partial 失败清单可见、虚拟产品不可选、港互认目标源收窄、
 * 提交失败透传中文消息且对话框不关。两端共用组件，mobile project 同跑。
 */

const mkProduct = (over: Record<string, unknown>) => ({
  code: "000001.OF", market: "CN_OTC", name: "华夏成长混合", product_type: "OEF",
  display_code: "000001", hk_sale_code: null, confirm_days: 1, nav_lag_days: 0,
  is_qdii: false, is_active: true, data_source: "tushare", data_source_status: "success",
  last_sync_at: null, created_at: "2025-01-01T00:00:00",
  asset_class_code: "ASSET_STOCK", region_code: "REGION_CN", style_code: "STYLE_BALANCED",
  size_code: "SIZE_LARGE", segment_code: "SEG_COMPOSITE",
  ...over,
});

const P1 = mkProduct({});
const P2 = mkProduct({ code: "000005.OF", name: "华夏红利混合", display_code: "000005" });
const P3 = mkProduct({ code: "510300.SH", market: "CN_EXCHANGE", name: "沪深300ETF", product_type: "ETF", display_code: "510300", confirm_days: 0 });
const HK = mkProduct({ code: "1001767344", market: "HK_MUTUAL", name: "摩根国际债券", display_code: "968050", hk_sale_code: "968050", data_source: "akshare", nav_lag_days: 1 });
const CASH = mkProduct({ code: "CASH", market: "", name: "现金类资产", product_type: "CASH", display_code: "CASH", data_source: null, data_source_status: null, confirm_days: 0 });

const LIST_BODY = { items: [P1, P2, P3, HK, CASH], total: 5, page: 1, page_size: 20 };

const JOB_PARTIAL = {
  id: 901, job_type: "data_source_switch", status: "partial",
  params: { products: [["000001.OF", "CN_OTC"], ["000005.OF", "CN_OTC"], ["510300.SH", "CN_EXCHANGE"]], data_source: "akshare", results: [] },
  total: 3, done: 3, success_count: 2, failed_count: 0, skipped_count: 1,
  error_message: "000005.OF(CN_OTC): 目标数据源未返回该产品的净值，请核对代码与市场（no_data）",
  triggered_by: "manual", created_at: "2026-11-16T00:00:00", started_at: "2026-11-16T00:00:01", finished_at: "2026-11-16T00:00:05",
};

const DETAILS_BODY = {
  job: JOB_PARTIAL,
  details: [
    { code: "000001.OF", market: "CN_OTC", old_source: "tushare", new_source: "akshare", status: "success", reason: null },
    { code: "000005.OF", market: "CN_OTC", old_source: null, new_source: "akshare", status: "skipped", reason: "目标数据源未返回该产品的净值，请核对代码与市场（no_data）" },
    { code: "510300.SH", market: "CN_EXCHANGE", old_source: "tushare", new_source: "akshare", status: "success", reason: null },
  ],
};

async function mockList(page: Page) {
  await page.route(/\/api\/products(\?|$)/, (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ status: 200, json: LIST_BODY })
      : route.continue()
  );
}

async function gotoProducts(page: Page) {
  await page.goto("/products");
  await expect(page.getByTestId("products-list-card")).toBeVisible({ timeout: 15_000 });
}

function rowCheckbox(page: Page, name: string) {
  return page
    .getByTestId("products-list-card")
    .getByRole("row")
    .filter({ hasText: name })
    .first()
    .getByTestId("batch-row-checkbox");
}

async function pickTarget(page: Page, dlg: Locator, source: string) {
  const listbox = page.getByRole("listbox");
  await openPopover(page, dlg.getByTestId("batch-target-source"), listbox);
  await listbox.getByRole("option", { name: source, exact: true }).click();
}

// AlertDialog 的 role 是 alertdialog（非 dialog），dialogByTitle 不适用；按组件 testid 定位，
// 确认段/进度段共用同一根节点
const batchDialog = (page: Page) => page.getByTestId("batch-data-source-dialog");

test.describe("产品数据源批量切换（#882）", () => {
  test("多选 + 操作条 + 确认弹窗：只数/目标源/后果/清单预览", async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockList(page);
    await gotoProducts(page);

    await rowCheckbox(page, "华夏成长混合").click();
    await rowCheckbox(page, "沪深300ETF").click();
    const bar = page.getByTestId("batch-action-bar");
    await expect(bar).toContainText("已选 2 只");

    await page.getByTestId("batch-switch-button").click();
    const dlg = batchDialog(page);
    await expect(dlg).toBeVisible();
    await expect(dlg.getByTestId("batch-preview").locator("> div")).toHaveCount(2);
    await expect(dlg.getByTestId("batch-switch-hint")).toContainText("切换后新净值来自新数据源");
    // 清单含场外 → 附口径差与分红后果
    await expect(dlg.getByTestId("batch-switch-hint")).toContainText("日涨跌列口径随源变化");
    // 未选目标源时提交禁用
    await expect(dlg.getByTestId("batch-submit")).toBeDisabled();
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("全选当前页（虚拟产品除外）+ 清空选择", async ({ page }) => {
    await mockList(page);
    await gotoProducts(page);
    await page.getByTestId("batch-select-all").click();
    // 5 行中 CASH 虚拟产品不可选 → 选中 4 只
    await expect(page.getByTestId("batch-action-bar")).toContainText("已选 4 只");
    await expect(rowCheckbox(page, "现金类资产")).toBeDisabled();
    await page.getByRole("button", { name: "清空选择" }).click();
    await expect(page.getByTestId("batch-action-bar")).toHaveCount(0);
  });

  test("提交批量切换：请求体含显式清单与目标源，终态 partial 失败清单可见", async ({ page }) => {
    await mockList(page);
    await page.route(/\/api\/products\/batch-data-source(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: { job_id: 901, status: "pending", message: "任务已提交" } })
    );
    await page.route(/\/api\/sync-jobs\/901(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: JOB_PARTIAL })
    );
    await page.route(/\/api\/sync-jobs\/901\/details(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: DETAILS_BODY })
    );
    await gotoProducts(page);

    await rowCheckbox(page, "华夏成长混合").click();
    await rowCheckbox(page, "华夏红利混合").click();
    await rowCheckbox(page, "沪深300ETF").click();
    await page.getByTestId("batch-switch-button").click();
    const dlg = batchDialog(page);
    await pickTarget(page, dlg, "akshare");

    const post = page.waitForRequest((r) => r.method() === "POST" && /\/api\/products\/batch-data-source/.test(r.url()));
    await dlg.getByTestId("batch-submit").click();
    // 直接从请求对象读 body（route handler 副作用与 waitForRequest 有竞态，不可靠）
    const postBody = (await post).postDataJSON();
    expect(postBody.data_source).toBe("akshare");
    expect(postBody.products).toEqual([
      { code: "000001.OF", market: "CN_OTC" },
      { code: "000005.OF", market: "CN_OTC" },
      { code: "510300.SH", market: "CN_EXCHANGE" },
    ]);

    // 进度段：终态 partial + 失败清单（跳过原因可见）
    const progress = batchDialog(page);
    await expect(progress.getByTestId("batch-job-status")).toContainText("部分成功", { timeout: 10_000 });
    const failures = progress.getByTestId("batch-failure-list");
    await expect(failures).toContainText("000005.OF");
    await expect(failures).toContainText("跳过");
    await expect(failures).toContainText("no_data");
    // 成功条目不进失败清单
    await expect(failures).not.toContainText("000001.OF");
  });

  test("港互认选中时目标源只 akshare", async ({ page }) => {
    await mockList(page);
    await gotoProducts(page);
    await rowCheckbox(page, "摩根国际债券").click();
    await page.getByTestId("batch-switch-button").click();
    const dlg = batchDialog(page);
    await expect(dlg.getByText("清单含港互认基金")).toBeVisible();
    const listbox = page.getByRole("listbox");
    await openPopover(page, dlg.getByTestId("batch-target-source"), listbox);
    await expect(listbox.getByRole("option")).toHaveCount(1);
    await expect(listbox.getByRole("option").first()).toHaveText("akshare");
  });

  test("提交失败（预校验 422）：toast 透传中文消息，对话框不关", async ({ page }) => {
    await mockList(page);
    await page.route(/\/api\/products\/batch-data-source(\?|$)/, (route) =>
      route.fulfill({
        status: 422,
        json: { detail: { error: "INVALID_PARAM", message: "清单含不存在的产品: 000005.OF(CN_OTC)" } },
      })
    );
    await gotoProducts(page);
    await rowCheckbox(page, "华夏成长混合").click();
    await page.getByTestId("batch-switch-button").click();
    const dlg = batchDialog(page);
    await pickTarget(page, dlg, "akshare");
    await dlg.getByTestId("batch-submit").click();
    // modal Dialog 开着时 toast 走 DOM testid（#640 口径）
    const toast = page.getByTestId("toast-card").filter({ hasText: "清单含不存在的产品" });
    await expect(toast).toBeVisible({ timeout: 10_000 });
    await expect(dlg).toBeVisible();
  });
});