/**
 * 前端 E2E 测试：产品「类型」筛选档位（#736）
 *
 * 钉两份类型清单的**刻意差异**（同一份 label 集合服务两种语义，不共享常量）：
 *   1. 调仓页产品筛选弹层（`ProductFilterDialog`）只有「全部类型 / ETF / 开放式基金 / LOF」——
 *      CASH 是虚拟产品，`/api/products` 默认排除（#327），而弹层取数不传 `include_virtual`，
 *      故「现金」档选中恒得空结果（死选项），已整档移除；「全部类型」默认路径行为不变。
 *   2. 产品管理页（`ProductsContent`）仍含「现金」——它显式传 `include_virtual: true`，
 *      现金产品在该页是合法的管理对象。误删这份清单会让本用例变红。
 *
 * 定位一律走 data-testid：调仓页筛选栏**另有一个**「全部类型」下拉（交易类型 buy/sell），
 * 弹层内外同名文案会撞车（教训见 `mobile-share-change-events.spec.ts` 的 `getByText('全部类型')`）。
 *
 * 数据：种子 E2E_ACTIVE（只读展示，不写库、不做生命周期操作）。
 *
 * #910 追加：高级筛选弹窗（ProductFilterDialog）外层限高/overflow 移除后的
 * 单滚动容器契约（调仓入口四档视口；事件入口桌面 1280×600 + mobile 原生）、
 * 40vh 结果列表局部滚动保留、长列表/合法空态与草稿语义（确定应用、取消/Escape
 * 不应用、重开恢复、全选仅当前结果）。列表用精确成功响应夹具按请求条件返回，
 * 不写业务数据；两个调用页面（调仓/份额变动事件）双端入口都覆盖。
 */
import { test, expect, type Locator, type Page } from "@playwright/test";
import {
  E2E_ACTIVE,
  E2E_PORT,
  collectPageErrors,
  dialogByTitle,
  dialogTestViewports,
  expectDialogSingleScroll,
  gotoPortfolioSubpage,
  openFilterPanelIfMobile,
  closeFilterPanelIfMobile,
  openPopover,
  settlePopovers,
} from "./helpers";

/** 打开类型下拉并返回其选项（Radix Select 的浮层挂在 body，不在触发器的父级里） */
async function openTypeOptions(page: Page, trigger: Locator): Promise<Locator> {
  const listbox = page.getByRole("listbox");
  await openPopover(page, trigger, listbox);
  return listbox.getByRole("option");
}

test.describe("产品「类型」筛选档位（#736）", () => {
  test("调仓页产品筛选弹层：类型档无「现金」，全部类型路径仍列出产品", async ({
    page,
  }, testInfo) => {
    const errors = collectPageErrors(page);
    await gotoPortfolioSubpage(page, E2E_ACTIVE, "trades");
    // 移动端筛选栏默认折叠，「高级筛选」入口在折叠面板内（桌面端 no-op，#383 口径）
    await openFilterPanelIfMobile(page, testInfo);
    await page.getByRole("button", { name: "高级筛选" }).click();
    const dialog = dialogByTitle(page, "筛选产品");
    await expect(dialog).toBeVisible();

    // 「全部类型」= 未选类型的默认路径：仍能列出可交易产品（移除现金档不影响它）
    await expect(dialog.getByRole("checkbox").first()).toBeVisible({ timeout: 10_000 });

    await expect(
      await openTypeOptions(page, dialog.getByTestId("product-filter-type-select")),
    ).toHaveText(["全部类型", "ETF", "开放式基金", "LOF"]);
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("产品管理页：类型筛选仍含「现金」（该页显式取虚拟产品）", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await page.goto("/products");
    await expect(page.getByTestId("products-list-card")).toBeVisible({ timeout: 15_000 });
    await openFilterPanelIfMobile(page, testInfo);

    await expect(await openTypeOptions(page, page.getByTestId("products-type-filter"))).toHaveText([
      "全部类型",
      "ETF",
      "开放式基金",
      "LOF",
      "现金",
    ]);
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });
});

// ===========================================================================
// #910 高级筛选弹窗：滚动契约与草稿语义
// ===========================================================================

/** 长列表夹具：30 条（ETF/OEF 交替），足够撑出 40vh 局部滚动；LOF 恒空集 */
const FILTER_ITEMS = Array.from({ length: 30 }, (_, i) => ({
  code: `E2E9${String(i).padStart(2, "0")}.OF`,
  market: "CN_OTC",
  name: `筛选夹具基金${i}`,
  product_type: i % 2 === 0 ? "ETF" : "OEF",
  display_code: `E2E9${String(i).padStart(2, "0")}`,
  hk_sale_code: null,
  confirm_days: 1,
  nav_lag_days: 0,
  is_qdii: false,
  is_active: true,
  data_source: "tushare",
  data_source_status: "success",
}));

/** 精确成功响应夹具：按请求条件（keyword/product_type）同步返回，不用 sleep */
async function mockFilterProducts(page: Page) {
  await page.route(/\/api\/products(\?|$)/, (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const params = new URL(route.request().url()).searchParams;
    const keyword = params.get("keyword") ?? "";
    const type = params.get("product_type");
    let items = FILTER_ITEMS;
    if (keyword) {
      items = items.filter(
        (p) => p.name.includes(keyword) || p.code.toLowerCase().includes(keyword.toLowerCase())
      );
    }
    if (type) items = items.filter((p) => p.product_type === type);
    return route.fulfill({
      status: 200,
      json: { items, total: items.length, page: 1, page_size: 100 },
    });
  });
}

test.describe("高级筛选弹窗滚动契约（#910）", () => {
  test("调仓入口：外层不滚动、40vh 列表可滚至末项、空态同高（四档视口）", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await mockFilterProducts(page);
    await gotoPortfolioSubpage(page, E2E_ACTIVE, "trades");
    for (const vp of dialogTestViewports(testInfo, page)) {
      await page.setViewportSize(vp);
      const label = `高级筛选 ${vp.width}×${vp.height}`;
      // 移动调仓页入口在筛选 Sheet 内（桌面筛选栏恒展开，no-op）
      await openFilterPanelIfMobile(page, testInfo);
      await page.getByRole("button", { name: "高级筛选" }).click();
      const dlg = dialogByTitle(page, "筛选产品");
      const results = dlg.getByTestId("product-filter-results");
      await expect(results.getByRole("checkbox").first()).toBeVisible({ timeout: 10_000 });

      // 外层契约 + 底部确定可达（先断言，再做任何自动滚动）
      await expectDialogSingleScroll(dlg, label, {
        bottomAction: dlg.getByRole("button", { name: "确定", exact: true }),
      });
      // 结果列表是独立局部滚动区：长内容可滚至末项并选中（不算双滚动条缺陷）
      const listMetrics = await results.evaluate((el) => ({
        overflow: el.scrollHeight - el.clientHeight,
      }));
      expect(listMetrics.overflow, `${label}: 30 条夹具应撑出列表局部滚动`).toBeGreaterThan(1);
      const longBox = await results.boundingBox();
      await results.evaluate((el) => {
        el.scrollTop = el.scrollHeight;
      });
      await results.getByRole("checkbox", { name: "选择 筛选夹具基金29", exact: true }).click();
      await expect(dlg.getByText("已选 1")).toBeVisible();

      // 合法空态：列表同高（#163 不跳变）、外层契约不变、全选禁用
      await dlg.getByPlaceholder("搜索产品代码/名称").fill("不存在的关键字");
      await expect(results.getByText("无符合条件的产品")).toBeVisible({ timeout: 10_000 });
      const emptyBox = await results.boundingBox();
      expect(
        Math.abs(emptyBox!.height - longBox!.height),
        `${label}: 空态与长列表应同高`
      ).toBeLessThanOrEqual(1);
      await expect(dlg.getByRole("button", { name: "全选", exact: true })).toBeDisabled();
      await expectDialogSingleScroll(dlg, `${label} 空态`);

      await dlg.getByRole("button", { name: "取消", exact: true }).click();
      await expect(dlg).toHaveCount(0);
      // 关闭子弹窗后宿主 Sheet/筛选栏可继续操作
      await closeFilterPanelIfMobile(page, testInfo);
    }
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("调仓入口：确定应用、取消/Escape 不应用、重开恢复、全选仅当前结果", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await mockFilterProducts(page);
    await gotoPortfolioSubpage(page, E2E_ACTIVE, "trades");
    await openFilterPanelIfMobile(page, testInfo);
    await page.getByRole("button", { name: "高级筛选" }).click();
    const dlg = dialogByTitle(page, "筛选产品");
    const results = dlg.getByTestId("product-filter-results");
    await expect(results.getByRole("checkbox").first()).toBeVisible({ timeout: 10_000 });

    // 实际打开类型 Select 选 ETF：条件查询生效、弹窗不误关
    const listbox = page.getByRole("listbox");
    await openPopover(page, dlg.getByTestId("product-filter-type-select"), listbox);
    const etfQuery = page.waitForRequest(
      (r) =>
        /\/api\/products\?/.test(r.url()) &&
        new URL(r.url()).searchParams.get("product_type") === "ETF"
    );
    await listbox.getByRole("option", { name: "ETF", exact: true }).click();
    await etfQuery;
    await settlePopovers(page);
    await expect(dlg).toBeVisible();
    await expect(results.getByRole("checkbox")).toHaveCount(15);

    // 勾选首行 → 关键字改条件（ETF 偶数序号中含「1」：10/12/14/16/18）→ 已选跨结果保留
    await results.getByRole("checkbox", { name: "选择 筛选夹具基金0", exact: true }).click();
    await expect(dlg.getByText("已选 1")).toBeVisible();
    await dlg.getByPlaceholder("搜索产品代码/名称").fill("基金1");
    await expect(results.getByRole("checkbox")).toHaveCount(5);
    await expect(dlg.getByText("已选 1")).toBeVisible();
    // 全选/取消全选仅作用当前结果：5 条 + 保留的基金0 = 6；取消全选后保留项仍在
    await dlg.getByRole("button", { name: "全选", exact: true }).click();
    await expect(dlg.getByText("已选 6")).toBeVisible();
    await dlg.getByRole("button", { name: "取消全选", exact: true }).click();
    await expect(dlg.getByText("已选 1")).toBeVisible();

    // 清关键字回 15 条，确定 → 父级查询应用（products=code|market）且触发器回显
    await dlg.getByPlaceholder("搜索产品代码/名称").fill("");
    await expect(results.getByRole("checkbox")).toHaveCount(15);
    const tradesQuery = page.waitForRequest(
      (r) =>
        /\/api\/trades\?/.test(r.url()) &&
        (new URL(r.url()).searchParams.get("products") ?? "").includes("E2E900.OF|CN_OTC")
    );
    await dlg.getByRole("button", { name: "确定", exact: true }).click();
    await tradesQuery;
    await expect(dlg).toHaveCount(0);
    await expect(page.getByRole("button", { name: /产品 · 已选 1/ })).toBeVisible();

    // 重开恢复已确认选择；改选后 Escape 不应用草稿
    await page.getByRole("button", { name: "高级筛选" }).click();
    const reopened = dialogByTitle(page, "筛选产品");
    const results2 = reopened.getByTestId("product-filter-results");
    await expect(results2.getByRole("checkbox", { name: "选择 筛选夹具基金0", exact: true })).toBeChecked();
    await results2.getByRole("checkbox", { name: "选择 筛选夹具基金2", exact: true }).click();
    await expect(reopened.getByText("已选 2")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(reopened).toHaveCount(0);
    await expect(page.getByRole("button", { name: /产品 · 已选 1/ })).toBeVisible();

    // 再开：草稿已丢弃、仅恢复已确认选择；取消路径同样不应用
    await page.getByRole("button", { name: "高级筛选" }).click();
    const third = dialogByTitle(page, "筛选产品");
    const results3 = third.getByTestId("product-filter-results");
    await expect(results3.getByRole("checkbox", { name: "选择 筛选夹具基金0", exact: true })).toBeChecked();
    await expect(results3.getByRole("checkbox", { name: "选择 筛选夹具基金2", exact: true })).not.toBeChecked();
    await results3.getByRole("checkbox", { name: "选择 筛选夹具基金2", exact: true }).click();
    await third.getByRole("button", { name: "取消", exact: true }).click();
    await expect(third).toHaveCount(0);
    await expect(page.getByRole("button", { name: /产品 · 已选 1/ })).toBeVisible();

    // 关闭子弹窗后宿主面板/入口可继续操作
    await closeFilterPanelIfMobile(page, testInfo);
    await expect(page.getByRole("button", { name: "提交交易" }).first()).toBeVisible();
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("事件入口：滚动契约与草稿确定/取消（桌面 1280×600 / mobile 原生）", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await mockFilterProducts(page);
    await gotoPortfolioSubpage(page, E2E_PORT, "share-change-events");
    if (testInfo.project.name !== "mobile") {
      await page.setViewportSize({ width: 1280, height: 600 });
    }
    // 移动事件页入口在折叠筛选面板内（桌面恒展开，no-op）
    await openFilterPanelIfMobile(page, testInfo);
    await page.getByRole("button", { name: "高级筛选" }).click();
    const dlg = dialogByTitle(page, "筛选产品");
    const results = dlg.getByTestId("product-filter-results");
    await expect(results.getByRole("checkbox").first()).toBeVisible({ timeout: 10_000 });
    await expectDialogSingleScroll(dlg, "事件入口高级筛选", {
      bottomAction: dlg.getByRole("button", { name: "确定", exact: true }),
    });

    // 长列表滚至末项勾选 → 关键字改条件 → 已选保留
    await results.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    await results.getByRole("checkbox", { name: "选择 筛选夹具基金29", exact: true }).click();
    await dlg.getByPlaceholder("搜索产品代码/名称").fill("基金2");
    await expect(dlg.getByText("已选 1")).toBeVisible();

    // 类型 Select 选 LOF：夹具无 LOF → 合法空态，弹窗不误关、已选保留
    const listbox = page.getByRole("listbox");
    await openPopover(page, dlg.getByTestId("product-filter-type-select"), listbox);
    await listbox.getByRole("option", { name: "LOF", exact: true }).click();
    await settlePopovers(page);
    await expect(dlg).toBeVisible();
    await expect(results.getByText("无符合条件的产品")).toBeVisible({ timeout: 10_000 });
    await expect(dlg.getByText("已选 1")).toBeVisible();

    // 确定 → 父级事件查询应用
    const eventsQuery = page.waitForRequest(
      (r) =>
        /\/api\/share-change-events\?/.test(r.url()) &&
        (new URL(r.url()).searchParams.get("products") ?? "").includes("E2E929.OF|CN_OTC")
    );
    await dlg.getByRole("button", { name: "确定", exact: true }).click();
    await eventsQuery;
    await expect(dlg).toHaveCount(0);
    await expect(page.getByRole("button", { name: /产品 · 已选 1/ })).toBeVisible();

    // 重开改选后取消不应用；宿主面板/入口可继续操作
    await page.getByRole("button", { name: "高级筛选" }).click();
    const reopened = dialogByTitle(page, "筛选产品");
    await reopened
      .getByTestId("product-filter-results")
      .getByRole("checkbox", { name: "选择 筛选夹具基金0", exact: true })
      .click();
    await reopened.getByRole("button", { name: "取消", exact: true }).click();
    await expect(reopened).toHaveCount(0);
    await expect(page.getByRole("button", { name: /产品 · 已选 1/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "新建事件" })).toBeVisible();
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });
});
