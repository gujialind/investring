/**
 * 前端 E2E 测试：交易列表的配对现金信息（#719「只补信息、不补行」）
 *
 * 覆盖三件事，全部走**种子契约组合** E2E_ACTIVE（只读展示，不做生命周期写入）：
 *   1. 未筛选形态逐行不变（真实腿结对、派生子行 0 条）——「零回归」与「真实行优先」两条验收断言；
 *   2. `?product=CASH&market=` 深链进入即「已筛现金」：现金腿各自补出「↳ 对手基金腿」派生子行，
 *      申赎腿（`sub_` 组）保持「现金 · 申赎确认」不补对手；派生行无任何操作入口；
 *   3. 产品筛选器能搜到并勾选「现金」（勾中后按 `products=CASH|` 生效），且**不**混入
 *      三条在途虚拟产品（#327 的默认排除没有被无条件放开）。
 *
 * 为什么用 E2E_ACTIVE 而非自建隔离组合（与 trade-in-transit.spec.ts 的分工）：本文件断言的是
 * **行集形态与文案**，不需要「零快照 → 逐日生成」的起点；E2E_ACTIVE 的种子恰好给出
 * 「2 笔调仓（各 1 基金腿 + 1 现金扣款腿）+ 1 笔申赎现金腿」这一最小完整形态。
 * 红线仍生效：**禁止对 E2E_ACTIVE 跑 recalculate/catch-up/generate-next**（#354）。
 *
 * 数据（backend/tests/seed_base.py::seed_e2e_active，平台恒 HBZQ）：
 * - D2 confirmed 买入 15,000@4.0（¥60,000）+ 配对 CASH sell（confirmed、现金日 D2）；
 * - D4 pending 买入 2,000@4.1（¥8,200）+ 配对 CASH sell（#493：创建即扣款 confirmed）；
 * - D1 申购 ¥100,000 → D2 确认，配对 CASH buy 腿（`sub_` 组）。
 * 未筛选 = 5 条真实行 → 2 对（主行 + 现金子行）+ 1 落单现金行 = 3 主 tr + 2 子 tr = 5 tr。
 */
import { test, expect, type Page } from "@playwright/test";
import {
  E2E_ACTIVE,
  collectPageErrors,
  gotoPortfolioSubpage,
  openFilterPanelIfMobile,
  openPopover,
  portfolioPath,
  settlePopovers,
} from "./helpers";

const FUND_NAME = "沪深300ETF";

/** 主表格的行（主行与子行都是 tr，故只用于「总数不变」这类形态断言） */
function tableRows(page: Page) {
  return page.locator("table tbody tr");
}

/** 现金筛选生效的判据：请求带上 `products=CASH|`（空 market 段是有效值，不是「未传」） */
function waitForCashTradeQuery(page: Page) {
  return page.waitForRequest(
    (r) =>
      r.method() === "GET" &&
      /\/api\/trades(\?|$)/.test(r.url()) &&
      /products=CASH(%7C|\|)/.test(r.url()),
    { timeout: 10_000 },
  );
}

test.describe("交易列表的配对现金信息（#719）", () => {
  test("未筛选：真实腿结对，不出现派生子行（零回归 + 真实行优先）", async ({ page }) => {
    const errors = collectPageErrors(page);
    await gotoPortfolioSubpage(page, E2E_ACTIVE, "trades");
    await expect(tableRows(page).first()).toBeVisible({ timeout: 10_000 });
    // 3 主行 + 2 现金子行；现金腿作为真实行已折进子行，不再各自成行
    await expect(tableRows(page)).toHaveCount(5);
    // 同组对手腿已在结果集内 → 绝不重复渲染派生子行（同一组现金信息只出现一次）
    await expect(page.getByTestId("trade-counterpart-subrow")).toHaveCount(0);
    const cashSubRows = page.getByTestId("trade-cash-subrow");
    await expect(cashSubRows).toHaveCount(2);
    await expect(cashSubRows.first()).toContainText("现金扣款");
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("筛现金深链 ?product=CASH&market=：进入即已筛现金，现金行补出对手腿", async (
    { page },
    testInfo,
  ) => {
    const errors = collectPageErrors(page);
    const cashQuery = waitForCashTradeQuery(page);
    await page.goto(`${portfolioPath(E2E_ACTIVE, "trades")}?product=CASH&market=`);
    await cashQuery;

    // 筛选栏回显「已选 1」而不是 placeholder（移动端筛选栏默认折叠，桌面 no-op，#383 口径）
    await openFilterPanelIfMobile(page, testInfo);
    await expect(page.getByRole("button", { name: "产品 · 已选 1" })).toBeVisible({
      timeout: 10_000,
    });

    // 行集 = 3 条真实现金腿（2 调仓扣款 + 1 申赎确认）+ 2 条派生对手腿 = 5 tr
    await expect(tableRows(page)).toHaveCount(5);
    const counterparts = page.getByTestId("trade-counterpart-subrow");
    await expect(counterparts).toHaveCount(2);
    // 调仓扣款腿的对手是基金腿：带产品名与**对手腿自身状态**（半确认组同样可见，#493 同源不变量）
    await expect(counterparts.filter({ hasText: `↳ 买入 ${FUND_NAME}` })).toHaveCount(2);
    await expect(counterparts.filter({ hasText: "已确认）" })).toHaveCount(1);
    await expect(counterparts.filter({ hasText: "待确认）" })).toHaveCount(1);
    // 份额 @ 净值取自对手基金腿，费率无来源则不写（现金腿自身无份额无净值语义）
    await expect(counterparts.filter({ hasText: "份 @" })).toHaveCount(2);
    // 主行「产品」列保持现状（仍走 cashOrphanLabel 的来源口径，本设计只新增子行）
    await expect(page.getByRole("row").filter({ hasText: "现金 · 调仓" })).toHaveCount(2);
    // 申赎腿（sub_ 组）刻意不补对手：对手方是 subscription 记录、不是 trade 行
    await expect(page.getByRole("row").filter({ hasText: "现金 · 申赎确认" })).toHaveCount(1);
    await expect(counterparts.filter({ hasText: "申赎" })).toHaveCount(0);
    // 派生快照不是可导航的真实行：不露任何生命周期入口
    for (const title of ["确认", "取消确认", "取消", "删除", "修改"]) {
      await expect(counterparts.locator(`button[title="${title}"]`)).toHaveCount(0);
    }
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("产品筛选器可选现金；不混入在途虚拟产品", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await gotoPortfolioSubpage(page, E2E_ACTIVE, "trades");
    await expect(tableRows(page).first()).toBeVisible({ timeout: 10_000 });
    await openFilterPanelIfMobile(page, testInfo);

    const trigger = page.getByRole("button", { name: "全部产品" });
    const searchBox = page.getByPlaceholder("搜索产品代码/名称");
    await openPopover(page, trigger, searchBox);
    // 现金单独取数（product_type=CASH + include_virtual=true），与真实产品之间一条分隔线
    const cashOption = page.getByRole("checkbox", { name: "选择 现金类资产" });
    await expect(cashOption).toBeVisible({ timeout: 10_000 });
    // #327 的默认排除未被无条件放开：三条在途虚拟产品（从不出现在 trade 表）不得成为死选项
    await expect(
      page.getByRole("checkbox", { name: /选择 .*(买入|卖出|分红)在途资金/ }),
    ).toHaveCount(0);

    // 等请求的 waiter 必须先于点击挂上：点击后换键的请求可能在本行执行前就已完成
    const cashQuery = waitForCashTradeQuery(page);
    await cashOption.click();
    // 勾选即时生效（筛选栏无「查询」按钮，§9），弹层仍开着——收掉它再断言行集
    await page.keyboard.press("Escape");
    await settlePopovers(page);
    await cashQuery;
    await expect(page.getByTestId("trade-counterpart-subrow")).toHaveCount(2, {
      timeout: 10_000,
    });
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });
});
