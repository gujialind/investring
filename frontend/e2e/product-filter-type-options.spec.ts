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
 */
import { test, expect, type Locator, type Page } from "@playwright/test";
import {
  E2E_ACTIVE,
  collectPageErrors,
  dialogByTitle,
  gotoPortfolioSubpage,
  openFilterPanelIfMobile,
  openPopover,
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
