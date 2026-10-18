/**
 * 前端 E2E 测试：交易列表的配对现金信息（#719「只补信息、不补行」）
 *
 * 覆盖两件事，全部走**种子契约组合** E2E_ACTIVE（只读展示，不做生命周期写入）：
 *   1. 未筛选形态逐行不变（真实腿结对、派生子行 0 条）——「零回归」与「真实行优先」两条验收断言；
 *   2. 状态筛选收窄到「已确认」使现金腿成为锚点行时，各自补出「↳ 对手基金腿」派生子行，
 *      申赎腿（`sub_` 组）保持「现金 · 申赎确认」不补对手；派生行无任何操作入口。
 *
 * 为什么用状态筛选而不是产品筛选现金档：产品筛选里的「现金」档（快捷下拉现金项 +
 * `include_virtual=true` 取数 + `?product=CASH&market=` 空段放行）不属本 PR 范围，
 * 由 #725 PR-2 承接（届时深链形态另补用例）。本文件钉的是**读侧派生本身**，与对手腿
 * 被哪一种筛选挡掉无关；状态 = 已确认恰好给出「2 条 confirmed 扣款腿 + 1 条申赎现金腿
 * 成锚点、两条基金腿（1 confirmed + 1 pending）不在行集」这一最小可断形态。
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
 * 状态 = 已确认 → 4 条真实行（2 confirmed 基金/现金对中的 1 基金主行 + 1 现金子行、
 * 1 confirmed 扣款腿孤儿、1 申赎现金腿）+ 2 条派生对手腿 = 6 tr。
 */
import { test, expect, type Page } from "@playwright/test";
import {
  E2E_ACTIVE,
  collectPageErrors,
  gotoPortfolioSubpage,
  openFilterPanelIfMobile,
  openPopover,
  settlePopovers,
} from "./helpers";

const FUND_NAME = "沪深300ETF";

/** 主表格的行（主行与子行都是 tr，故只用于「总数不变」这类形态断言） */
function tableRows(page: Page) {
  return page.locator("table tbody tr");
}

/** 状态筛选生效的判据：请求带上 `status=confirmed` */
function waitForConfirmedTradeQuery(page: Page) {
  return page.waitForRequest(
    (r) =>
      r.method() === "GET" &&
      /\/api\/trades(\?|$)/.test(r.url()) &&
      /[?&]status=confirmed(&|$)/.test(r.url()),
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

  test("筛已确认使现金腿成锚点行：现金行补出对手腿", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await gotoPortfolioSubpage(page, E2E_ACTIVE, "trades");
    await expect(tableRows(page).first()).toBeVisible({ timeout: 10_000 });
    await openFilterPanelIfMobile(page, testInfo);

    // 状态收窄到「已确认」：两条买入的基金腿一条 pending 被滤掉、另一条与其现金腿同页
    //（该组不产派生子行），confirmed 扣款腿单独成行 → 现金锚点行
    const confirmedQuery = waitForConfirmedTradeQuery(page);
    const statusTrigger = page.getByRole("combobox").filter({ hasText: "全部状态" });
    const confirmedOption = page.getByRole("option", { name: "已确认" });
    await openPopover(page, statusTrigger, confirmedOption);
    await confirmedOption.click({ timeout: 10_000 });
    await settlePopovers(page);
    await confirmedQuery;

    // 行集 = 3 条真实现金腿（1 条折进 D2 买入的现金子行、2 条独立成行）+ 1 基金主行
    //        + 2 条派生对手腿（两条落单扣款腿各一条）= 6 tr。
    //        两条对手腿状态恒为「已确认」——状态筛选下 pending 基金腿不入行集，
    //        「半确认组里读到对手腿自身状态」那一面由 backend 集成测试
    //        test_trades_paired_leg.py 钉，本用例不重复。
    await expect(tableRows(page)).toHaveCount(6);
    const counterparts = page.getByTestId("trade-counterpart-subrow");
    await expect(counterparts).toHaveCount(2);
    // 落单扣款腿的对手是基金腿：带产品名与**对手腿自身状态**（半确认组同样可见，#493 同源不变量）
    await expect(counterparts.filter({ hasText: `↳ 买入 ${FUND_NAME}` })).toHaveCount(2);
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
});
