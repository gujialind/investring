import { test, expect, type Locator, type Page } from "@playwright/test";
import {
  E2E_ACTIVE,
  gotoPortfolioDetail,
  authHeaders,
  dialogByTitle,
  pickCalendarDay,
  toISODate,
} from "./helpers";

/**
 * #595 步骤④ 平台详情页（M4/D4）+ 平台-产品详情页（M5/D5），双端共享组件。
 *
 * 数据说明（种子契约，backend/tests/seed_base.py / seed_e2e.py）：
 * - E2E_ACTIVE：单平台 HBZQ（华宝证券）持有 510300.SH 15,000 份 + 现金余额 ¥40,000。
 *   连续 2 日快照（D2=4.0000 / D3=4.2000）→ 产品市值 ¥63,000.00，平台总市值 ¥103,000.00。
 *   交易：confirmed 买入 15,000@4.0（¥60,000）+ pending 买入 2,000@4.1（¥8,200）+ 配对 CASH 腿。
 *   平台级交易列表经 groupTradeRows 结对展示（CASH 腿折叠为子行），2 对 + 1 孤儿 = 3 行。
 * - 多平台形态当前种子不可达（seed_e2e_active 只写 HBZQ），allPlatformsCard 与
 *   「当前平台行不可点」分支在 API 层由后端 test_holding_aggregation.py HAGG_D 覆盖。
 * - 断言为可见性与关系式，不硬绑定快照日期；两端共用组件，mobile project 同跑。
 */

const PLATFORM_PATH = `/portfolio/${E2E_ACTIVE}/platforms/HBZQ`;
const PLATFORM_PRODUCT_PATH = `/portfolio/${E2E_ACTIVE}/platforms/HBZQ/products/510300.SH?market=CN_EXCHANGE`;
const CASH_PRODUCT_PATH = `/portfolio/${E2E_ACTIVE}/platforms/HBZQ/products/CASH?market=`;

/**
 * 拉交易日历中 ≤ 今天的全部日历行（升序，#468 口径：交易日经 /api/trading-calendar
 * 取，不写死、不假设周末）。Blocker 1 回归、现金市值写入日锚定与非交易日拒绝共用。
 * 日期口径用 **本地** `toISODate`（与组件侧 `toDateOnly()` 同一时钟，#640 U-2）。
 */
async function calendarRowsUptoToday(
  page: Page,
): Promise<{ calendar_date: string; is_open: boolean }[]> {
  const today = toISODate(new Date());
  const year = Number(today.slice(0, 4));
  const headers = await authHeaders(page);
  const calendars = await Promise.all(
    [year - 1, year, year + 1].map(async (y) => {
      const resp = await page.request.get(`/api/trading-calendar?year=${y}`, { headers });
      await expect(resp).toBeOK();
      return (await resp.json()) as { calendar_date: string; is_open: boolean }[];
    }),
  );
  return calendars
    .flat()
    .filter((d) => d.calendar_date <= today)
    .sort((a, b) => a.calendar_date.localeCompare(b.calendar_date));
}

/** ≤ 今天的全部交易日（升序） */
async function tradingDaysUptoToday(page: Page): Promise<string[]> {
  return (await calendarRowsUptoToday(page))
    .filter((d) => d.is_open)
    .map((d) => d.calendar_date);
}

/** 最近一个非交易日（≤ 今天）——非交易日拒绝路径的选日目标 */
async function lastNonTradingDayUptoToday(page: Page): Promise<string> {
  const closed = (await calendarRowsUptoToday(page))
    .filter((d) => !d.is_open)
    .map((d) => d.calendar_date);
  expect(closed.length, "交易日历里找不到 ≤ 今天的非交易日").toBeGreaterThan(0);
  return closed.at(-1)!;
}

/**
 * Dialog 内 DatePicker 触发按钮。锚点取控件 id（`CashMarketValueUpdateDialog` 的
 * `id="cash-update-date"` + `<Label htmlFor>`），与 `#trade_date` / `#cash_confirm_date`
 * 同形（#640 N-2）。此前靠文案正则 `/选择日期|\d{4}-\d{2}-\d{2}/` + `.first()`，
 * 无主标签（Label 无 htmlFor）才是只能用正则的原因；补 id 后正则退役——
 * 「已存在覆盖」状态下新增改日用例时，文案正则可能一次命中多个 button 而静默取错。
 * scope 必须是 `dialogByTitle` 的结果——**不能**在已取得的 dialog 上再套一层
 * `getByRole("dialog")`：弹层内没有第二个 dialog，那样恒为 0 命中（#640 T-1 未落地的根因）。
 */
function dialogDateTrigger(dlg: Locator): Locator {
  return dlg.locator("button#cash-update-date");
}

test.describe("平台详情页", () => {
  test("页头与概览卡：平台名、代码·类型、市值、指标行", async ({ page }) => {
    await page.goto(PLATFORM_PATH);
    await expect(
      page.getByRole("heading", { name: "华宝证券" })
    ).toBeVisible();
    await expect(page.getByText("HBZQ")).toBeVisible();

    const overview = page.getByTestId("platform-overview-card");
    await expect(overview).toBeVisible();
    // 持仓市值大数字（含该平台现金与在途）
    await expect(overview).toContainText(/\d{2,},\d{3}\.\d{2}/);
    // 4 项指标标签
    await expect(overview).toContainText("持有收益");
    await expect(overview).toContainText("现金余额");
    await expect(overview).toContainText("持仓产品数");
    await expect(overview).toContainText("占组合比");
  });

  test("操作行买入：trades 页平台预填且列表非空", async ({ page }) => {
    await page.goto(PLATFORM_PATH);
    await page.getByTestId("platform-action-row").getByRole("link", { name: "买入" }).click();
    await page.waitForURL(/\/trades\?/);
    expect(page.url()).toContain("platform=HBZQ");
    expect(page.url()).toContain("trade_type=buy");
    await expect(page.locator("table tbody tr").first()).toBeVisible({ timeout: 10_000 });
  });

  test("持仓明细：510300.SH 产品卡可点击进入平台-产品详情", async ({ page }) => {
    await page.goto(PLATFORM_PATH);
    const holdings = page.getByTestId("platform-holdings-section");
    await expect(holdings).toBeVisible();
    // 510300.SH 产品卡存在
    await expect(holdings.getByText("沪深300ETF")).toBeVisible();
    // 点击产品卡 → 平台-产品详情页
    await holdings.getByRole("link", { name: /沪深300ETF/ }).first().click();
    await page.waitForURL(/\/platforms\/HBZQ\/products\/510300\.SH/);
    await expect(
      page.getByRole("heading", { name: /华宝证券.*沪深300ETF/ })
    ).toBeVisible();
  });

  test("交易记录卡：该平台交易结对展示与查看全部链接", async ({ page }) => {
    await page.goto(PLATFORM_PATH);
    const card = page.getByTestId("platform-trades-card");
    const rows = card.getByTestId("platform-trade-row");
    await expect(rows.first()).toBeVisible();
    // 结对展示：2 笔买入各配对 CASH 腿 + 1 笔申赎 CASH 孤儿 = 3 行
    await expect(rows).toHaveCount(3);
    // 首行为基金腿（买入 · 沪深300ETF），非 CASH 腿
    await expect(rows.first()).toContainText("买入");
    await expect(rows.first()).toContainText("沪深300ETF");
    // 子行含现金到账/扣款标注
    await expect(rows.first()).toContainText(/现金/);
    // R4-S1：CASH 孤儿行走 cashOrphanLabel（「现金 · 申赎确认」），与 TradesContent 同口径
    await expect(rows.nth(2)).toContainText(/现金 · 申赎确认/);
    // Blocker 1 回归：pending 场外价格不应渲染成 0.0000
    await expect(card).not.toContainText("0.0000");
    await expect(card.getByRole("link", { name: "查看全部" })).toHaveAttribute(
      "href",
      new RegExp(`/trades\\?platform=HBZQ`)
    );
  });

  test("从组合详情按平台视图平台卡点击进入（深链起点）", async ({ page }) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    // 切换到按平台视图
    await page.getByTestId("holdings-tab-platform").click();
    await page.getByRole("link", { name: /华宝证券/ }).first().click();
    await page.waitForURL(/\/platforms\/HBZQ/);
    await expect(
      page.getByRole("heading", { name: "华宝证券" })
    ).toBeVisible();
  });

  test("现金切片页直达：无净值请求、概览显示现金余额", async ({ page }) => {
    // S1/S7：现金页不应发 nav-history 请求（enabled guard），概览显示 "--" 份额
    const navRequests: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/nav-history") || req.url().includes("/nav-analysis")) {
        navRequests.push(req.url());
      }
    });
    await page.goto(CASH_PRODUCT_PATH);
    await expect(
      page.getByTestId("platform-product-overview-card")
    ).toBeVisible();
    // 现金页持有份额显示 "--"
    await expect(page.getByTestId("platform-product-overview-card")).toContainText("--");
    // S1：不应有 nav-history/nav-analysis 请求
    expect(navRequests).toHaveLength(0);
  });

  test("未知平台 EmptyState", async ({ page }) => {
    await page.goto(`/portfolio/${E2E_ACTIVE}/platforms/NONEXISTENT`);
    await expect(page.getByText("未找到该平台持仓")).toBeVisible();
  });

  test("在途脚注负向：本平台最新快照日无在途时不出现（#641）", async ({ page }) => {
    // E2E_ACTIVE 的 HBZQ 最新快照（D3）无在途行（D4 的 pending 买入尚未进快照）
    // → 平台级 in_transit_market_value == 0.0，脚注不渲染。
    // 防 B2 换形态复现：恒渲染或借组合级数字都会让本用例红。
    // 归属本 describe（#654 L2 S1）：用例跑的是平台详情页，此前误放在
    // 「平台-产品详情页」分组下，按分组筛平台页回归会漏掉它
    await page.goto(PLATFORM_PATH);
    const holdings = page.getByTestId("platform-holdings-section");
    await expect(holdings).toBeVisible();
    await expect(page.getByTestId("platform-in-transit-note")).toHaveCount(0);
  });
});

test.describe("平台-产品详情页", () => {
  test("页头与概览卡：平台名·产品名、切片市值、份额、收益、占产品比", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    await expect(
      page.getByRole("heading", { name: /华宝证券.*沪深300ETF/ })
    ).toBeVisible();
    await expect(page.getByText("510300.SH")).toBeVisible();

    const overview = page.getByTestId("platform-product-overview-card");
    await expect(overview).toBeVisible();
    await expect(overview).toContainText("63,000.00");
    await expect(overview).toContainText("15,000.00");
    await expect(overview).toContainText("100.0%");
    // S6：累计收益带 * 口径注记
    await expect(overview).toContainText("累计收益*");
    await expect(overview).toContainText("*累计收益含已卖出实现盈亏");
  });

  test("操作行买入：trades 页平台+产品预填且过滤生效", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    await page.getByTestId("platform-product-action-row").getByRole("link", { name: "买入" }).click();
    await page.waitForURL(/\/trades\?/);
    expect(page.url()).toContain("product=510300.SH");
    expect(page.url()).toContain("platform=HBZQ");
    expect(page.url()).toContain("trade_type=buy");
    // S7：产品过滤后只剩 2 条基金腿（不含 CASH 腿）
    await expect(page.locator("table tbody tr")).toHaveCount(2, { timeout: 10_000 });
  });

  test("净值曲线空态与六窗区间收益率占位", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    const curve = page.getByTestId("platform-product-curve-card");
    await expect(curve).toContainText("暂无净值数据");

    const returns = page.getByTestId("platform-product-returns-card");
    for (const field of ["m1", "m3", "m6", "y1", "ytd", "all"]) {
      await expect(returns.getByTestId(`return-${field}`)).toContainText("--");
    }
  });

  test("历史净值：两行降序、累计净值/日涨跌占位", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    const history = page.getByTestId("platform-product-history-card");
    const rows = history.getByTestId(/^platform-product-history-row/);
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText("4.2000");
    await expect(rows.nth(1)).toContainText("4.0000");
    // 累计净值与日涨跌夹具为 NULL → 占位 --
    await expect(rows.nth(0).getByText("--").first()).toBeVisible();
    // 无查看更多（仅 2 行 < page_size 5）
    await expect(history.getByTestId("history-load-more")).toHaveCount(0);
  });

  test("交易记录卡：该产品在该平台的交易（不含 CASH 腿）", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    const card = page.getByTestId("platform-product-trades-card");
    const rows = card.getByTestId("platform-product-trade-row");
    await expect(rows.first()).toBeVisible();
    // 产品级过滤不含 CASH 腿，只有 2 条基金腿
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText("买入");
  });

  test("「查看该产品全部平台持仓」链接 → 产品详情页", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    const link = page.getByTestId("view-all-platforms-link");
    // 单平台时该链接仍存在（指向产品详情页）
    await expect(link).toBeVisible();
    await link.click();
    await page.waitForURL(/\/product\/510300\.SH/);
    await expect(
      page.getByRole("heading", { name: "沪深300ETF" })
    ).toBeVisible();
  });

  test("从平台详情产品卡点击进入（深链起点）", async ({ page }) => {
    await page.goto(PLATFORM_PATH);
    const holdings = page.getByTestId("platform-holdings-section");
    await holdings.getByRole("link", { name: /沪深300ETF/ }).first().click();
    await page.waitForURL(/\/platforms\/HBZQ\/products\/510300\.SH/);
    await expect(
      page.getByRole("heading", { name: /华宝证券.*沪深300ETF/ })
    ).toBeVisible();
  });

  test("缺 market 参数的非现金产品 EmptyState", async ({ page }) => {
    // S4：非现金产品缺 market 参数应提示缺少参数，而非「未找到」
    await page.goto(`/portfolio/${E2E_ACTIVE}/platforms/HBZQ/products/510300.SH`);
    await expect(page.getByText("缺少 market 参数")).toBeVisible();
  });

  test("未知产品 EmptyState", async ({ page }) => {
    await page.goto(`/portfolio/${E2E_ACTIVE}/platforms/HBZQ/products/NONEXISTENT?market=CN_EXCHANGE`);
    await expect(page.getByText("未找到该平台产品持仓")).toBeVisible();
  });
});

/**
 * S1'：自建隔离组合造一笔场外（CN_OTC）pending 调仓（price=NULL），
 * 正向断言平台交易卡该行显示 "--"（而非 "0.0000"）。
 * 模式复用 portfolio-holdings-view.spec.ts 的 setupInTransitPortfolio。
 */
test.describe("Blocker 1 回归：pending 场外价格渲染", () => {
  test("场外 pending 调仓价格显示 -- 而非 0.0000", async ({ page }, testInfo) => {
    const code = `E595P_${testInfo.project.name}_${testInfo.retry}`;
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    const headers = await authHeaders(page);

    const uptoToday = await tradingDaysUptoToday(page);
    expect(uptoToday.length).toBeGreaterThanOrEqual(2);
    const [apply, target] = uptoToday.slice(-2);

    const post = async <T = unknown>(path: string, data?: unknown): Promise<T> => {
      const resp = await page.request.post(path, { headers, data });
      await expect(resp, `${path} ${resp.status()}`).toBeOK();
      return resp.json();
    };

    await post("/api/portfolios", { code, name: `B1回归 ${code}` });
    const sub = await post<{ id: number }>("/api/subscriptions", {
      portfolio_code: code, investor_code: "ADMIN", platform_code: "HBZQ",
      sub_type: "subscribe", amount: 100000, apply_date: apply,
    });
    await post(`/api/subscriptions/${sub.id}/confirm`);
    // 场外 pending 买入：不传 price → 落库 NULL
    await post("/api/trades", {
      portfolio_code: code, product_code: "000300.OF", market: "CN_OTC",
      platform_code: "HBZQ", trade_type: "buy", trade_date: target,
      amount: 8200, fee: 0,
    });
    // 生成 T 日快照（含在途行，使平台聚合有数据；OTC confirm_days=1 不会被自动确认）
    await post("/api/snapshots/generate", { portfolio_code: code, target_date: target });

    // 直达平台详情页
    await page.goto(`/portfolio/${code}/platforms/HBZQ`);
    const card = page.getByTestId("platform-trades-card");
    await expect(card).toBeVisible({ timeout: 10_000 });
    // R4-N1：锚到基金腿行断言价格位 "--"（formatNav(null) → "--"），
    // 而非依赖孤儿 CASH 腿的份额占位（结对后子行不含份额/价格位）
    const fundRow = card.getByTestId("platform-trade-row").filter({ hasText: "沪深300联接" }).first();
    await expect(fundRow).toContainText("@ --");
    // 不应出现 0.0000
    await expect(card).not.toContainText("0.0000");

    // #641 正向：本平台有买入在途 8200（pending 场外买入的 CASH 腿已扣、基金腿未生效）
    // → 持仓明细区出现口径脚注，数字为本平台卡的 in_transit_market_value。
    // 按脚注元素断言（#654 L2 S2）：整段 toContainText 在明细区出现同值金额时会变松
    const note = page.getByTestId("platform-in-transit-note");
    await expect(note).toContainText("持仓市值含在途资金");
    await expect(note).toContainText("8,200.00");
  });
});

// #595 §4.5：现金市值更新 Dialog 验收
test.describe("现金市值更新 Dialog", () => {
  test("平台-产品详情页（现金）：操作行含市值更新按钮，点击打开 Dialog", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    const actionRow = page.getByTestId("platform-product-action-row");
    await expect(actionRow).toBeVisible();
    await expect(actionRow.getByRole("button", { name: "转入" })).toBeVisible();
    await expect(actionRow.getByRole("button", { name: "转出" })).toBeVisible();
    await expect(actionRow.getByRole("button", { name: "市值更新" })).toBeVisible();

    // 点击打开 Dialog
    await actionRow.getByRole("button", { name: "市值更新" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("heading", { name: "更新现金市值" })).toBeVisible();
    // 平台应预填 HBZQ（Dialog 的 Label htmlFor="cash-platform"，走 a11y 锚点而非元素类型 + id）
    await expect(page.getByLabel("平台")).toContainText("华宝证券 (HBZQ)");
  });

  test("表单校验、成功反馈、写入日请求体锚定与撤销两段式", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    await page.getByTestId("platform-product-action-row").getByRole("button", { name: "市值更新" }).click();
    // 作用域单点取 dialogByTitle：日历弹层也带 role=dialog，page.getByRole("dialog")
    // 在弹层打开期间命中 2 个（#640 U-4 的坑），后续所有 Dialog 内定位一律走 dlg
    const dlg = dialogByTitle(page, "更新现金市值");
    await expect(dlg).toBeVisible();

    // S-2：空金额提交——HTML5 required 阻止表单提交，Dialog 不关闭且无成功反馈。
    // 预填平台无法经 UI 清空（SearchablePlatformSelect 无 clear 按钮），故只覆盖金额校验。
    const amountInput = page.getByLabel("当前金额（元）");
    await amountInput.fill("");
    await page.getByRole("button", { name: "确认更新" }).click();
    await expect(dlg).toBeVisible();
    await expect(page.getByTestId("toast-card").getByText("现金市值已更新")).not.toBeVisible();

    // T-1（#640 第三轮收口）：写入日锚定最近交易日，且**每次跑都显式选日**。
    // 上一轮的 `if (target !== today)` 让选日分支在工作日根本不执行（只有周末才第一次
    // 跑），而该分支当时恒为 0 命中——等于「周末必红」的缺陷从未被执行过。
    const target = (await tradingDaysUptoToday(page)).at(-1)!;
    await pickCalendarDay(page, dialogDateTrigger(dlg), target);

    // T-2：R-1 回归锚在请求体——POST 的 update_date 必须非空且等于预期写入日。
    // 仅断言面板覆盖块（下一条）在同时钟环境锁不住时钟错开，请求体才是真网
    const postReq = page.waitForRequest(
      (r) => r.method() === "POST" && r.url().includes("cash-position"),
    );
    // B-5 回归：写入成功必须出现可见反馈（原实现 requires_snapshot_regen 死分支导致无出口）。
    // R-3：锚点取无条件出的成功 toast，而非绑在恒真 requires_snapshot_regen 上的 Alert 分支
    await amountInput.fill("40000");
    await page.getByRole("button", { name: "确认更新" }).click();
    const postBody = JSON.parse((await postReq).postData() ?? "{}") as { update_date?: string };
    expect(postBody.update_date).toBe(target);

    // B-2/B-5 网：写完面板必须当场见覆盖记录块
    await expect(
      page.getByTestId("toast-card").getByText("现金市值已更新")
    ).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("当日已有覆盖记录")).toBeVisible({ timeout: 10_000 });

    // T-3：撤销覆盖两段式（B-8）——取消不发 DELETE，确认才删；目标记录就在屏上
    let deleteCount = 0;
    page.on("request", (r) => {
      if (r.method() === "DELETE") deleteCount += 1;
    });
    const undoBtn = page.getByRole("button", { name: "撤销覆盖" });
    const confirmDialog = page.getByRole("alertdialog");
    await undoBtn.click();
    await expect(confirmDialog.getByText("撤销现金覆盖")).toBeVisible();
    await confirmDialog.getByRole("button", { name: "取消" }).click();
    expect(deleteCount).toBe(0);
    await expect(page.getByText("当日已有覆盖记录")).toBeVisible(); // 记录未删
    await undoBtn.click();
    await confirmDialog.getByRole("button", { name: "确认撤销" }).click();
    // U-3：message 两支都要钉住——只断言标题时，后端字段读错键（requires_snapshot_regen
    // 拼错）会无声退化成另一条文案。本形态下 target > 最新快照日 →
    // requires_snapshot_regen=False（position_service:974 真算），故应为短文案且不含
    // 「需重新生成快照」；短文案是长文案的前缀，所以「不含」那条才是判别支的承重断言。
    // ⚠️ 定位一律 testid + hasText（DOM 口径），**不用 toastByTitle**：Radix modal Dialog
    // 打开期间把其余子树 aria-hidden，基于 role 的定位器看不见挂在 app root 的 toast
    const undoToast = page.getByTestId("toast-card").filter({ hasText: "已撤销" });
    await expect(undoToast).toBeVisible({ timeout: 10_000 });
    await expect(undoToast).toContainText("覆盖记录已删除，回退到自然计算值");
    await expect(undoToast).not.toContainText("需重新生成快照");
    await expect(page.getByText("当日已有覆盖记录")).not.toBeVisible();
  });

  // 待确认 2（#640 第三轮显式处置：做）：Dialog 内提示「只能选择交易日，非交易日将被拒绝」
  // 是对用户作出的承诺，此前两侧均无断言锁定它——后端 position_service:807 抛
  // NON_TRADING_DAY 对 cash-position 这条路径没有任何测试，前端也只有文案。
  // 拒绝由后端作出（不落库、无残留），故本用例可在共享 E2E_ACTIVE 上跑。
  test("非交易日提交被可见拒绝：透出后端文案、表单不关、无成功反馈", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    await page.getByTestId("platform-product-action-row").getByRole("button", { name: "市值更新" }).click();
    const dlg = dialogByTitle(page, "更新现金市值");
    await expect(dlg).toBeVisible();

    // 本 Dialog 的 DatePicker 未传 dayDisabled（date-picker.tsx:95 硬禁用仅在给了谓词时
    // 启用），故非交易日确实可点选——这条拒绝路径在 UI 上真实可达，不是死分支
    await pickCalendarDay(page, dialogDateTrigger(dlg), await lastNonTradingDayUptoToday(page));
    await page.getByLabel("当前金额（元）").fill("40000");
    await page.getByRole("button", { name: "确认更新" }).click();

    // 定位口径同上方撤销 toast 的注释（Dialog 开着时 role 定位器看不到它）
    const failToast = page.getByTestId("toast-card").filter({ hasText: "更新失败" });
    await expect(failToast).toBeVisible({ timeout: 10_000 });
    // 断的是后端 detail.message 经 request()→ApiException→getErrorMessage 原样透出
    // ——出现「非交易日」即证明走了一趟服务端并被拒，而不是前端自己拦下
    await expect(failToast).toContainText("非交易日");
    await expect(dlg).toBeVisible(); // 拒绝不得连带关掉用户刚填完的表单
    await expect(page.getByTestId("toast-card").getByText("现金市值已更新")).not.toBeVisible();
  });

  test("转入/转出打开现金转移 Dialog 且按方向预填本平台", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    const actionRow = page.getByTestId("platform-product-action-row");
    const triggers = page.getByRole("dialog").getByTestId("platform-trigger");

    // 转出：from 预填 HBZQ，to 留空
    await actionRow.getByRole("button", { name: "转出" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("heading", { name: "平台间现金转移" })).toBeVisible();
    await expect(triggers.nth(0)).toContainText("华宝证券 (HBZQ)");
    await expect(triggers.nth(1)).toContainText("选择转入平台");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).not.toBeVisible();

    // 转入：to 预填 HBZQ，from 留空
    await actionRow.getByRole("button", { name: "转入" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(triggers.nth(0)).toContainText("选择转出平台");
    await expect(triggers.nth(1)).toContainText("华宝证券 (HBZQ)");
  });
});
