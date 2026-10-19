/**
 * 前端 E2E 测试：交易列表的配对现金信息（#719「只补信息、不补行」）
 *
 * 覆盖四件事，全部走**种子契约组合** E2E_ACTIVE（只读展示，不做生命周期写入）：
 *   1. 未筛选形态逐行不变（真实腿结对、派生子行 0 条）——「零回归」与「真实行优先」两条验收断言；
 *   2. 状态筛选把对手腿挡在结果集外时，落单行各自补出「↳ 对手腿」派生子行，
 *      申赎腿（`sub_` 组）保持「现金 · 申赎确认」不补对手；派生行无任何操作入口；
 *   3. **两个派生方向各钉一条**（#721 评审 S2）：`status=confirmed` 留下现金锚点、补基金对手腿；
 *      `status=pending` 留下基金锚点、补现金对手腿——后者同时钉住 §8 的派生形态
 *      （`↳ 转出 平台名`、金额不带 ±，#721 评审 S1）。
 *   4. 现金腿在**主行**的份额/价格两槽都是 `--`（#732，与 #719 卡片侧同口径），
 *      且两类子行的首列标签不折行（#733）。
 *
 * 为什么用状态筛选而不是产品筛选现金档：产品筛选弹层**不列 CASH**——`/api/products`
 * 默认排除虚拟产品（#327），弹层取数不传 `include_virtual`，故「现金」类型档选中恒空、
 * 已整档移除（#736）；「按产品筛出现金腿」**当前 UI 无入口**（弹层不列 CASH；深链空
 * `market` 段被 `TradesContent` 的 `normalizedInitialProduct` 归一丢弃），`tradePairs` 的
 * 派生逻辑保留。本文件钉的是**读侧派生本身**，
 * 与对手腿被哪一种筛选挡掉无关。平台筛选同理无法造出落单：种子 E2E_ACTIVE 的腿全部落在 HBZQ，
 * 按 `platform_code` 筛等于不筛（CASH↔CASH 的派生形态由 `tradePairs.test.ts` 的
 * 「双 CASH 转移组只剩一条腿」单测钉住，种子侧无跨平台转移）。
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
 * 未筛选 = 5 条真实行 → 2 对（主行 + 现金子行）+ 1 落单现金行；条数按预测式推导，不写死。
 */
import { test, expect, type Locator, type Page, type TestInfo } from "@playwright/test";
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
 * 由响应行集算出界面**应该**出现的行数，与前端 groupTradeRows + 派生兜底**同一条判据**
 * （#721 评审 S2：旧实现只在现金行上计数，等于把「只有现金锚点会补派生」这个与实现相反
 * 的结论写死进预测器——基金锚点 + 被筛掉的现金对手同样多出一个 tr，pending 筛选态正是
 * 这一形态，旧预测器在那儿比屏幕少 1，红了会把人引向「实现错了」）。
 * - 真实两腿（同组两腿都在结果集内）折成 1 主 + 1 子，其中现金腿不独立成行；
 * - **任何落单行**（基金或现金，`sub_` 组除外）只要 `paired_leg` 非空且 id 不在页内 → 补 1 条；
 * - 屏幕总 tr = **结果集行数 + 派生子行数**。被结对折掉的现金腿仍然占一个 tr（它是那条
 *   结对的真实现金子行），折进/落单只改变它落在哪一行，不改变总数。
 */
function predictRowCounts(rows: RespTrade[]) {
  const ids = new Set(rows.map((r) => r.id));
  const derived = rows.filter(
    (r) =>
      !r.transfer_group?.startsWith("sub_") &&
      r.paired_leg != null &&
      !ids.has(r.paired_leg.id),
  ).length;
  return { tr: rows.length + derived, derived };
}

/** 结果集里会作为**真实现金子行**出现的行数（结对折进主行，不各自成行） */
function realCashSubRows(rows: RespTrade[]) {
  return rows.filter(
    (r) => r.product_code === CASH_CODE && !r.transfer_group?.startsWith("sub_"),
  ).length;
}

/**
 * 子行首列标签「只占一行」（#733）。
 * 判据取**盒高 vs 行高**而不是 `getClientRects().length`：行盒数随引擎与文本节点数变化
 * （同一行里 `{label}{platformName}` 两个文本节点在 chromium 下就回 2 个 rect），
 * 只有高度是两端一致的事实。折行时高度是行高的整数倍（实测 2 行 35px、3 行 55px）。
 */
async function expectSingleLineLabel(label: Locator, what: string) {
  await expect(label).toHaveCSS("white-space", "nowrap");
  const box = await label.boundingBox();
  expect(box, `${what}：标签应有可见盒`).not.toBeNull();
  const lineHeight = await label.evaluate((el) => parseFloat(getComputedStyle(el).lineHeight));
  expect(
    box!.height,
    `${what}：首列标签应只占一行（实测高 ${box!.height}px、行高 ${lineHeight}px）`,
  ).toBeLessThanOrEqual(lineHeight * 1.5);
}

/** 按状态下筛并返回该次响应行集（waiter 先于点击挂上：换键请求可能早于下一行完成） */
async function filterByStatus(
  page: Page,
  testInfo: TestInfo,
  status: "confirmed" | "pending",
): Promise<RespTrade[]> {
  await openFilterPanelIfMobile(page, testInfo);
  const query = page.waitForResponse(
    (r) =>
      r.url().includes("/api/trades") &&
      new RegExp(`[?&]status=${status}(&|$)`).test(r.url()) &&
      r.status() === 200,
    { timeout: 10_000 },
  );
  const statusTrigger = page.getByRole("combobox").filter({ hasText: "全部状态" });
  const option = page.getByRole("option", {
    name: status === "confirmed" ? "已确认" : "待确认",
  });
  await openPopover(page, statusTrigger, option);
  await option.click({ timeout: 10_000 });
  await settlePopovers(page);
  const body = (await (await query).json()) as { items: RespTrade[] };
  return body.items;
}

test.describe("交易列表的配对现金信息（#719）", () => {
  test("未筛选：真实腿结对，不出现派生子行（零回归 + 真实行优先）", async ({ page }) => {
    const errors = collectPageErrors(page);
    // 首屏响应即未筛选态：行集、应结对的真实现金腿数都从这一次响应推导
    const firstQuery = page.waitForResponse(
      (r) => r.url().includes("/api/trades") && r.status() === 200,
      { timeout: 15_000 },
    );
    await gotoPortfolioSubpage(page, E2E_ACTIVE, "trades");
    await expect(tableRows(page).first()).toBeVisible({ timeout: 10_000 });
    const rows = (await (await firstQuery).json()).items as RespTrade[];
    const expect_ = predictRowCounts(rows);
    // 前提自证：种子形态未退化（有结对的真实现金腿），否则下面的断言会空转
    expect(rows.length, "种子未筛响应为空").toBeGreaterThan(0);
    expect(realCashSubRows(rows), "种子应含至少一条结对真实现金腿").toBeGreaterThan(0);
    // 同组对手腿已在结果集内 → 绝不重复渲染派生子行（同一组现金信息只出现一次）
    expect(expect_.derived, "未筛选态不应有落单对手腿").toBe(0);
    // 主行 + 子行总数 = 结果集行数（现金腿折进子行仍占一个 tr，不增不减）
    await expect(tableRows(page)).toHaveCount(expect_.tr);
    await expect(page.getByTestId("trade-counterpart-subrow")).toHaveCount(0);
    const cashSubRows = page.getByTestId("trade-cash-subrow");
    await expect(cashSubRows).toHaveCount(realCashSubRows(rows));
    await expect(cashSubRows.first()).toContainText("现金扣款");
    // 子行首列标签不折行（#733）：首列是表内最窄的一档，CJK 逐字断行会把「华宝证券」切成两半
    await expectSingleLineLabel(
      cashSubRows.first().locator("td").first().locator("span"),
      "现金子行",
    );

    // 现金腿作**主行**（落单 `sub_` 组）：份额与价格两槽都必须是 `--`。#732 前价格槽渲
    // `1.0000`（后端 price 恒为记账单位 1），与卡片侧（#719 已修）两种口径，把现金读成净值型产品
    const cashMainCount = rows.filter(
      (r) => r.product_code === CASH_CODE && r.transfer_group?.startsWith("sub_"),
    ).length;
    expect(cashMainCount, "种子应含一条落单现金主行").toBeGreaterThan(0);
    const cashMain = page.getByRole("row").filter({ hasText: "现金 · 申赎确认" }).first();
    // 主行列数 = §8 槽位算术的对侧：cell 数一变，子行的 colSpan 折叠就整体错位
    await expect(cashMain.getByRole("cell")).toHaveCount(10);
    // 断言分级（#740 L2 S1）：份额槽与 `-- @ 1.0000` 是**既有行为护栏（修复前即绿）**——
    // CASH 腿 shares 写入侧本就为 NULL（trade_service / subscription_service 均不设 shares），
    // `-- @ 1.0000` 是详情卡形态、交易表两个槽位分开渲染，修复前后都不可能出现在表里；
    // 保留它们是防形态漂移，不是 #732 的承重点。承重点只有价格槽（nth(6)，修复前为红）。
    await expect(cashMain.getByRole("cell").nth(4)).toHaveText("--"); // 份额（既有行为护栏）
    await expect(cashMain.getByRole("cell").nth(6)).toHaveText("--"); // 价格（#732 承重）
    await expect(page.locator("table")).not.toContainText("-- @ 1.0000"); // 既有行为护栏
    // 原整表 not.toContainText("1.0000") 已收窄为「现金主行价格单元格集合」：整表子串否定
    // 会在种子未来新增任何净值 1.0000 的基金腿（货基/新基金面值）时误红，而 #732 的
    // 承重面只在现金主行的价格槽
    const cashMainRows = page.getByRole("row").filter({ hasText: "现金 · 申赎确认" });
    await expect(cashMainRows).toHaveCount(cashMainCount);
    for (const row of await cashMainRows.all()) {
      await expect(row.getByRole("cell").nth(6)).toHaveText("--");
    }
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("筛已确认：现金锚点行补出基金对手腿", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await gotoPortfolioSubpage(page, E2E_ACTIVE, "trades");
    await expect(tableRows(page).first()).toBeVisible({ timeout: 10_000 });
    const items = await filterByStatus(page, testInfo, "confirmed");
    const expect_ = predictRowCounts(items);
    // 前提自证：这轮筛选确实造出了「落单行 + 对手腿被挡」的形态，否则下面的
    // toHaveCount(预测值) 会退化成恒真的空断言
    expect(expect_.derived, "本轮筛选未造出落单行，用例形态已退化").toBeGreaterThan(0);

    // 屏上行数 = 同一次响应推导出的预测值
    await expect(tableRows(page)).toHaveCount(expect_.tr);
    const counterparts = page.getByTestId("trade-counterpart-subrow");
    await expect(counterparts).toHaveCount(expect_.derived);
    // 派生子行的份额 @ 净值取自对手基金腿（现金腿自身无份额无净值语义，不该出现在子行槽位外）
    await expect(counterparts.filter({ hasText: "份 @" })).toHaveCount(expect_.derived);
    // 派生子行首列同样不折行（#733）：`↳ 买入 沪深300ETF（待确认）` 是种子里最长的一档
    await expectSingleLineLabel(
      counterparts.first().locator("td").first().locator("span"),
      "对手腿子行",
    );
    // 申赎腿（sub_ 组）刻意不补对手：对手方是 subscription 记录、不是 trade 行
    const subCash = items.filter(
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

  /**
   * #721 评审 S1 + S2 的另一半：筛选留下**基金锚点**、把配对现金腿挡在外面。
   * 此时派生子行的对手是现金腿，按 visual-spec §8 只能是 `↳ 转出 平台名`、金额不带 ±
   * （± 前缀只属于结果集里的真实现金子行），且无份额无净值。旧实现走 renderCashSubRow，
   * 渲成「现金到账 · 平台 +¥N」，与三处详情卡两种形态；旧预测器在这里比屏幕少 1。
   */
  test("筛待确认：基金锚点行补出现金对手腿（↳ 形态、无 ± 前缀）", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await gotoPortfolioSubpage(page, E2E_ACTIVE, "trades");
    await expect(tableRows(page).first()).toBeVisible({ timeout: 10_000 });
    const items = await filterByStatus(page, testInfo, "pending");
    const expect_ = predictRowCounts(items);
    // 前提自证：这轮筛选确实留下基金锚点并把现金对手腿挡在外面
    expect(
      items.filter((r) => r.product_code !== CASH_CODE).length,
      "pending 筛选未留下基金锚点，用例形态已退化",
    ).toBeGreaterThan(0);
    expect(expect_.derived, "pending 筛选未造出落单基金行，用例形态已退化").toBeGreaterThan(0);

    await expect(tableRows(page)).toHaveCount(expect_.tr);
    const counterparts = page.getByTestId("trade-counterpart-subrow");
    await expect(counterparts).toHaveCount(expect_.derived);
    // 形态：首列 ↳ 转出/转入 + 平台名；金额格不带 +¥/-¥（± 是现金子行的资金流向语义）
    await expect(counterparts.filter({ hasText: "↳ 转出" })).toHaveCount(expect_.derived);
    await expect(counterparts.filter({ hasText: "+¥" })).toHaveCount(0);
    await expect(counterparts.filter({ hasText: "-¥" })).toHaveCount(0);
    // 现金腿无份额无净值：份额·手续费·价格槽位留空
    await expect(counterparts.filter({ hasText: "份 @" })).toHaveCount(0);
    // 真实现金子行的形态（± 前缀）不该在这一态出现：现金腿整条被筛掉了
    await expect(page.getByTestId("trade-cash-subrow")).toHaveCount(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });
});
