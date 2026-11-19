import { test, expect, type Page, type Locator } from "@playwright/test";
import { collectPageErrors, openFilterPanelIfMobile, closeFilterPanelIfMobile, openPopover } from "./helpers";

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
  // #904 P1d：移动卡化后行=卡片（product-card），桌面仍表格行；.or 双形态单定位
  return page
    .getByTestId("products-list-card")
    .getByRole("row")
    .filter({ hasText: name })
    .first()
    .getByTestId("batch-row-checkbox")
    .or(
      page
        .getByTestId("product-card")
        .filter({ hasText: name })
        .first()
        .getByTestId("batch-row-checkbox"),
    );
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

  // ---- L2 6925267091 B2–B5 回归（可控延迟 / 故障注入，双端） ----

  test("B2 列表请求在途：旧行禁选禁提交；新响应后清单/计数/预览一致且只提交新查询产品", async ({ page }, testInfo) => {
    let listCalls = 0;
    let release: (() => void) | null = null;
    const gate = new Promise<void>((r) => { release = r; });
    await page.route(/\/api\/products(\?|$)/, async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      listCalls += 1;
      if (listCalls === 2) await gate;
      return route.fulfill({
        status: 200,
        json: listCalls === 1 ? LIST_BODY : { items: [P3], total: 1, page: 1, page_size: 20 },
      });
    });
    let postBody: { products: { code: string; market: string }[] } | null = null;
    await page.route(/\/api\/products\/batch-data-source(\?|$)/, (route) => {
      postBody = route.request().postDataJSON();
      return route.fulfill({ status: 200, json: { job_id: 901, status: "pending", message: "任务已提交" } });
    });
    await page.route(/\/api\/sync-jobs\/901(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: { ...JOB_PARTIAL, status: "success", success_count: 1, skipped_count: 0, total: 1, done: 1 } })
    );
    await page.route(/\/api\/sync-jobs\/901\/details(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: { job: JOB_PARTIAL, details: [DETAILS_BODY.details[2]] } })
    );
    await gotoProducts(page);
    await rowCheckbox(page, "华夏成长混合").click();
    await expect(page.getByTestId("batch-action-bar")).toContainText("已选 1 只");

    // 触发新查询（关键字防抖 300ms 后发 GET），第二次响应被闸门扣住；
    // 移动端筛选栏默认折叠，先展开（#383 口径）
    await openFilterPanelIfMobile(page, testInfo);
    await page.getByPlaceholder("搜索产品代码/名称").fill("沪深");
    // 设完筛选回列表再断言行集（modal sheet 开着时表格外 aria-hidden，#904 P1b）
    await closeFilterPanelIfMobile(page, testInfo);
    await expect.poll(() => listCalls).toBe(2);
    // 在途窗口：旧行勾选/全选禁用；参数变化已清空选择 → 无批量提交入口（旧缓存不得确认新查询）
    await expect(rowCheckbox(page, "华夏成长混合")).toBeDisabled();
    await expect(page.getByTestId("batch-select-all")).toBeDisabled();
    await expect(page.getByTestId("batch-action-bar")).toHaveCount(0);

    release!();
    // #904 P1d：移动卡化后行=卡片，桌面仍表格行；.or 双形态单定位
    await expect(
      page
        .getByTestId("products-list-card")
        .getByRole("row")
        .filter({ hasText: "沪深300ETF" })
        .first()
        .or(
          page
            .getByTestId("product-card")
            .filter({ hasText: "沪深300ETF" })
            .first(),
        ),
    ).toBeVisible();
    // 新响应后选择集清空（旧查询产品不会残留进清单）
    await expect(page.getByTestId("batch-action-bar")).toHaveCount(0);
    await rowCheckbox(page, "沪深300ETF").click();
    await expect(page.getByTestId("batch-action-bar")).toContainText("已选 1 只");
    await page.getByTestId("batch-switch-button").click();
    const dlg = batchDialog(page);
    await expect(dlg.getByTestId("batch-preview").locator("> div")).toHaveCount(1);
    await expect(dlg.getByTestId("batch-preview")).toContainText("510300");
    await expect(dlg.getByTestId("batch-preview")).not.toContainText("000001");
    await pickTarget(page, dlg, "akshare");
    await dlg.getByTestId("batch-submit").click();
    await expect.poll(() => postBody).not.toBeNull();
    expect(postBody!.products).toEqual([{ code: "510300.SH", market: "CN_EXCHANGE" }]);
  });

  test("B3 迟到受理回执不接管重开后的新会话，旧 job 不清新选择", async ({ page }) => {
    await mockList(page);
    let release: (() => void) | null = null;
    const gate = new Promise<void>((r) => { release = r; });
    await page.route(/\/api\/products\/batch-data-source(\?|$)/, async (route) => {
      await gate;
      return route.fulfill({ status: 200, json: { job_id: 901, status: "pending", message: "任务已提交" } });
    });
    await page.route(/\/api\/sync-jobs\/901(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: JOB_PARTIAL })
    );
    await page.route(/\/api\/sync-jobs\/901\/details(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: DETAILS_BODY })
    );
    await gotoProducts(page);
    await rowCheckbox(page, "华夏成长混合").click();
    await page.getByTestId("batch-switch-button").click();
    const dlg = batchDialog(page);
    await pickTarget(page, dlg, "akshare");
    await dlg.getByTestId("batch-submit").click();
    // 提交在途时关闭 → 改选 → 重开（新会话）
    await dlg.getByRole("button", { name: "取消" }).click();
    await expect(dlg).toHaveCount(0);
    await page.getByRole("button", { name: "清空选择" }).click();
    await rowCheckbox(page, "沪深300ETF").click();
    await expect(page.getByTestId("batch-action-bar")).toContainText("已选 1 只");
    await page.getByTestId("batch-switch-button").click();
    await expect(dlg).toBeVisible();
    // 放行旧回执：不得把新会话切到旧 job 进度
    release!();
    await page.waitForTimeout(1200);
    await expect(dlg.getByTestId("batch-submit")).toBeVisible();
    await expect(dlg.getByTestId("batch-job-progress")).toHaveCount(0);
    // 旧 job 即便到终态也不得清掉新选择
    await page.waitForTimeout(2500);
    await expect(page.getByTestId("batch-action-bar")).toContainText("已选 1 只");
    await expect(dlg.getByTestId("batch-preview")).toContainText("510300");
  });

  test("B4a 状态查询失败：destructive Alert + 后端消息 + 重试恢复，不伪装提交中", async ({ page }) => {
    await mockList(page);
    let statusFail = true;
    await page.route(/\/api\/products\/batch-data-source(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: { job_id: 901, status: "pending", message: "任务已提交" } })
    );
    await page.route(/\/api\/sync-jobs\/901(\?|$)/, (route) =>
      statusFail
        ? route.fulfill({ status: 500, json: { detail: { error: "INTERNAL", message: "模拟状态故障" } } })
        : route.fulfill({ status: 200, json: JOB_PARTIAL })
    );
    await page.route(/\/api\/sync-jobs\/901\/details(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: DETAILS_BODY })
    );
    await gotoProducts(page);
    await rowCheckbox(page, "华夏成长混合").click();
    await page.getByTestId("batch-switch-button").click();
    const dlg = batchDialog(page);
    await pickTarget(page, dlg, "akshare");
    await dlg.getByTestId("batch-submit").click();
    const alert = dlg.getByTestId("batch-status-error");
    await expect(alert).toBeVisible({ timeout: 20_000 });
    await expect(alert).toContainText("模拟状态故障");
    await expect(dlg.getByTestId("batch-job-status")).toHaveCount(0);
    statusFail = false;
    await dlg.getByTestId("batch-status-retry").click();
    await expect(dlg.getByTestId("batch-job-status")).toContainText("部分成功", { timeout: 10_000 });
    await expect(alert).toHaveCount(0);
  });

  test("B4b 台账查询失败：destructive Alert + 重试恢复失败清单，不伪装空清单", async ({ page }) => {
    await mockList(page);
    let detailsFail = true;
    await page.route(/\/api\/products\/batch-data-source(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: { job_id: 901, status: "pending", message: "任务已提交" } })
    );
    await page.route(/\/api\/sync-jobs\/901(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: JOB_PARTIAL })
    );
    await page.route(/\/api\/sync-jobs\/901\/details(\?|$)/, (route) =>
      detailsFail
        ? route.fulfill({ status: 500, json: { detail: { error: "INTERNAL", message: "模拟台账故障" } } })
        : route.fulfill({ status: 200, json: DETAILS_BODY })
    );
    await gotoProducts(page);
    await rowCheckbox(page, "华夏成长混合").click();
    await page.getByTestId("batch-switch-button").click();
    const dlg = batchDialog(page);
    await pickTarget(page, dlg, "akshare");
    await dlg.getByTestId("batch-submit").click();
    const alert = dlg.getByTestId("batch-details-error");
    await expect(alert).toBeVisible({ timeout: 10_000 });
    await expect(alert).toContainText("模拟台账故障");
    await expect(dlg.getByTestId("batch-failure-list")).toHaveCount(0);
    detailsFail = false;
    await dlg.getByTestId("batch-details-retry").click();
    await expect(dlg.getByTestId("batch-failure-list")).toContainText("000005.OF", { timeout: 10_000 });
    await expect(alert).toHaveCount(0);
  });

  test("B5 异常 failed 保留已成功 checkpoint：按成功计数说真话，不得称全部未切换", async ({ page }) => {
    await mockList(page);
    const failedWithSuccess = {
      ...JOB_PARTIAL, status: "failed", success_count: 1, done: 1, skipped_count: 0, failed_count: 1,
      error_message: "000005.OF(CN_OTC): 模拟第二只 checkpoint 提交失败",
    };
    await page.route(/\/api\/products\/batch-data-source(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: { job_id: 901, status: "pending", message: "任务已提交" } })
    );
    await page.route(/\/api\/sync-jobs\/901(\?|$)/, (route) =>
      route.fulfill({ status: 200, json: failedWithSuccess })
    );
    await page.route(/\/api\/sync-jobs\/901\/details(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        json: {
          job: failedWithSuccess,
          details: [
            DETAILS_BODY.details[0],
            { code: "000005.OF", market: "CN_OTC", old_source: null, new_source: "akshare", status: "failed", reason: "模拟第二只 checkpoint 提交失败" },
          ],
        },
      })
    );
    await gotoProducts(page);
    await rowCheckbox(page, "华夏成长混合").click();
    await rowCheckbox(page, "华夏红利混合").click();
    await page.getByTestId("batch-switch-button").click();
    const dlg = batchDialog(page);
    await pickTarget(page, dlg, "akshare");
    await dlg.getByTestId("batch-submit").click();
    const status = dlg.getByTestId("batch-job-status");
    await expect(status).toContainText("已成功 1", { timeout: 10_000 });
    await expect(status).toContainText("任务异常终止");
    await expect(status).not.toContainText("全部未切换");
    const failures = dlg.getByTestId("batch-failure-list");
    await expect(failures).toContainText("000005.OF");
    await expect(failures).not.toContainText("000001.OF");
  });
});