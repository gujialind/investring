/**
 * 前端 E2E 测试：交易列表的配对现金信息（#719「只补信息、不补行」）
 *
 * 覆盖两件事，全部走**种子契约组合** E2E_ACTIVE（只读展示，不做生命周期写入）：
 *   1. 未筛选形态逐行不变（真实腿结对、派生子行 0 条）——「零回归」与「真实行优先」两条验收断言；
 *   2. 状态筛选把对手腿挡在结果集外时，现金锚点行各自补出「↳ 对手腿」派生子行，
 *      申赎腿（`sub_` 组）保持「现金 · 申赎确认」不补对手；派生行无任何操作入口。
 *
 * 为什么用状态筛选而不是产品筛选现金档：产品筛选里的「现金」档（快捷下拉现金项 +
 * `include_virtual=true` 取数 + `?product=CASH&market=` 空段放行）不属本 PR 范围，
 * 由 #725 PR-2 承接（届时深链形态另补用例）。本文件钉的是**读侧派生本身**，与对手腿
 * 被哪一种筛选挡掉无关。
 *
 * 行集**不写死绝对条数**（#719 裁剪时踩到的坑）：seed_e2e_active 的交易日 D1..D4 锚定
 * **建栈当天**，而 pending 买入的预计确认日会随日历推进越过 status=confirmed 的区间下界
 * ——同一份用例在不同日期跑，落单的 confirmed 扣款腿可能是 1 条也可能是 2 条。故本用例
 * 从**同一次响应**里算出应补的派生行数，再断言屏上与预测一致：这样钉的是「哪些行该补、
 * 补几行」这条契约本身，而不是某一天的种子快照。
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
  settlePopovers,
} from "./helpers";

const CASH_CODE = "CASH";

/** 主表格的行（主行与子行都是 tr，故只用于「总数不变」这类形态断言） */
function tableRows(page: Page) {
  return page.locator("table tbody tr");
}

/** 列表响应的 trade 行（只取本用例要推导的四个字段） */
interface RespTrade {
  id: number;
  product_code: string;
  trade_type: string;
  transfer_group: string | null;
  paired_leg: { id: number } | null;
}

/**
 * 由响应行集算出界面**应该**出现的行数，与前端 groupTradeRows + 派生兜底同判据：
 * - 真实两腿（同组基金腿与现金腿都在结果集内）折成 1 主 + 1 子，其中现金腿不独立成行；
 * - **只有现金锚点行**补派生子行：其 `paired_leg` 非空且 id 不在页内 → 补 1 条。
 *   基金主行即便带 `paired_leg`（指向同组现金腿）也不补——真实腿优先；
 * - 屏幕总 tr = **结果集行数 + 派生子行数**。被结对折掉的现金腿仍然占一个 tr（它是那条
 *   结对的真实现金子行），折进/落单只改变它落在哪一行，不改变总数——未筛选态 5 行 =
 *   3 主 + 2 真实现金子行，正是同一条算式。
 */
function predictRowCounts(rows: RespTrade[]) {
  const ids = new Set(rows.map((r) => r.id));
  const derived = rows.filter(
    (r) =>
      r.product_code === CASH_CODE &&
      r.paired_leg != null &&
      !ids.has(r.paired_leg.id),
  ).length;
  return { tr: rows.length + derived, derived };
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

  test("筛已确认：对手腿被挡在结果集外的现金锚点行补出对手腿", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await gotoPortfolioSubpage(page, E2E_ACTIVE, "trades");
    await expect(tableRows(page).first()).toBeVisible({ timeout: 10_000 });
    await openFilterPanelIfMobile(page, testInfo);

    // waiter 必须先于点击挂上：点击后换键的请求可能在本行执行前就已完成
    const confirmedQuery = page.waitForResponse(
      (r) =>
        r.url().includes("/api/trades") &&
        /[?&]status=confirmed(&|$)/.test(r.url()) &&
        r.status() === 200,
      { timeout: 10_000 },
    );
    const statusTrigger = page.getByRole("combobox").filter({ hasText: "全部状态" });
    const confirmedOption = page.getByRole("option", { name: "已确认" });
    await openPopover(page, statusTrigger, confirmedOption);
    await confirmedOption.click({ timeout: 10_000 });
    await settlePopovers(page);
    const body = (await (await confirmedQuery).json()) as { items: RespTrade[] };
    const expect_ = predictRowCounts(body.items);
    // 前提自证：这轮筛选确实造出了「现金腿落单 + 对手腿被挡」的形态，否则下面的
    // toHaveCount(预测值) 会退化成恒真的空断言
    expect(expect_.derived, "本轮筛选未造出落单现金腿，用例形态已退化").toBeGreaterThan(0);

    // 屏上行数 = 同一次响应推导出的预测值
    await expect(tableRows(page)).toHaveCount(expect_.tr);
    const counterparts = page.getByTestId("trade-counterpart-subrow");
    await expect(counterparts).toHaveCount(expect_.derived);
    // 派生子行的份额 @ 净值取自对手基金腿（现金腿自身无份额无净值语义，不该出现在子行槽位外）
    await expect(counterparts.filter({ hasText: "份 @" })).toHaveCount(expect_.derived);
    // 申赎腿（sub_ 组）刻意不补对手：对手方是 subscription 记录、不是 trade 行
    const subCash = body.items.filter(
      (r) => r.product_code === CASH_CODE && r.transfer_group?.startsWith("sub_"),
    );
    expect(subCash.length, "种子应含至少一条申赎现金腿").toBeGreaterThan(0);
    await expect(page.getByRole("row").filter({ hasText: "现金 · 申赎确认" })).toHaveCount(
      subCash.length,
    );
    await expect(counterparts.filter({ hasText: "申赎" })).toHaveCount(0);
    // 派生快照不是可导航的真实行：不露任何生命周期入口
    for (const title of ["确认", "取消确认", "取消", "删除", "修改"]) {
      await expect(counterparts.locator(`button[title="${title}"]`)).toHaveCount(0);
    }
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });
});
