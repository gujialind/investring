import { test, expect, type Locator, type Page } from "@playwright/test";
import {
  E2E_ACTIVE,
  gotoPortfolioDetail,
  authHeaders,
  dialogByTitle,
  pickCalendarDay,
  platformTrigger,
  productTrigger,
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
 *   需要「组合级 ≠ 平台级」的形态只能自建：见下方 Blocker 1 用例的 MYCF 负向断言
 *   （#654 L2 A）。因此**没有任何基于 E2E_ACTIVE 的断言能区分两个在途粒度**。
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

  test("操作行买入：跳转即开录入 Dialog，平台已预填（#646）", async ({ page }) => {
    await page.goto(PLATFORM_PATH);
    await page.getByTestId("platform-action-row").getByRole("link", { name: "买入" }).click();
    await page.waitForURL(/\/trades\?/);
    expect(page.url()).toContain("action=create");
    const dlg = dialogByTitle(page, "提交交易");
    await dlg.waitFor({ timeout: 10_000 });
    // 交易平台回显「华宝证券 (HBZQ)」（平台列表未加载完时退化为 code，两者都含 HBZQ）
    await expect(platformTrigger(dlg, "HBZQ")).toBeVisible();
    // 平台级来源页没有产品上下文 → 产品保持未选（占位文案在），不拿筛选参数硬凑
    await expect(productTrigger(dlg, "请选择产品")).toBeVisible();
  });

  test("操作行事件：落事件录入页并开 Dialog，平台已预填（#646）", async ({ page }) => {
    await page.goto(PLATFORM_PATH);
    // #656 L2 S1：URL 参数与 trades 页同口径——列表也按平台筛选
    const listReq = page.waitForRequest(
      (r) =>
        r.url().includes("/api/share-change-events") &&
        r.url().includes("platform_code=HBZQ"),
      { timeout: 10_000 },
    );
    await page.getByTestId("platform-action-row").getByRole("link", { name: "事件" }).click();
    await listReq;
    await page.waitForURL(/\/share-change-events\?/);
    expect(page.url()).toContain("platform=HBZQ");
    expect(page.url()).toContain("action=create");
    const dlg = dialogByTitle(page, "新建份额变动事件");
    await dlg.waitFor({ timeout: 10_000 });
    // 默认事件类型 cash_dividend 属平台级 → 平台控件渲染且已预填
    await expect(platformTrigger(dlg, "HBZQ")).toBeVisible();
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

  test("交易记录读取失败 → 透出后端消息与重试入口，而非空态（#663①）", async ({ page }) => {
    // 本页的失败/空态**判序**是各写一份的（组件只统一了文案入口），而 components 不在
    // vitest 覆盖率分母内——把 QueryErrorState 分支改回 `length === 0` 优先不会让任何
    // 静态门禁变红，这条断言就是那张网（#655/#663 的教训）。
    // URL 判据收口在列表本身：`/api/trades/{id}`、`/preview` 不得被误伤。
    await page.route(/\/api\/trades(\?|$)/, (route) =>
      route.fulfill({
        status: 500,
        json: { detail: { error: "INTERNAL", message: "交易服务暂不可用" } },
      })
    );
    await page.goto(PLATFORM_PATH);
    const card = page.getByTestId("platform-trades-card");
    // 断的是后端 detail.message 上桌（不是本地兜底文案），且每端点用不同 message，
    // 使红/绿能归因到具体卡片
    await expect(card).toContainText("加载失败：交易服务暂不可用", { timeout: 10_000 });
    await expect(card).not.toContainText("暂无交易记录");
    const retry = card.getByRole("button", { name: "重试" });
    await expect(retry).toBeVisible();
    // 同页持仓卡不受影响 → 证明 route 没有串台到 holdings 的两条请求
    await expect(page.getByTestId("platform-holdings-section")).toContainText("沪深300ETF");
    // 点重试确实重发 GET、且只有 GET（注册必须在失败态可见之后，否则会撞上
    // react-query 的自动重试，在「没点重试」的情况下也算绿）
    const refetched = page.waitForRequest(
      (r) => r.method() === "GET" && /\/api\/trades(\?|$)/.test(r.url()),
      { timeout: 10_000 }
    );
    await retry.click();
    await refetched;
  });

  test("整页读取失败：重试必须两条聚合都重发，页面才恢复（#681）", async ({ page }) => {
    // 本条的全部价值在最后三行「恢复」断言，而不是重试点击本身。页级守卫是
    // `isError = productError || platformError`（PlatformDetailContent.tsx:102），
    // 只要有一条聚合没重发，整页就仍留在失败态——所以「页面恢复了」对两个 refetch
    // 是**联合归因**的判据。issue #681 点名的退化（把 onRetry 写成 `refetchProduct`
    // 单条）恰好是页面看起来仍正常、lint/tsc/build/单测四层全碰不到的那种；它给的
    // 单 URL 配方在那种形态下依然绿，故这里两个端点各注册一条 waitForRequest，
    // 负责点名缺的是哪一条，恢复断言负责拦住它。
    let fail = true;
    // 判据严格 scoped 到 holdings/by-*：写成 `**/api/**` 会连带打掉 useProduct/usePlatform
    // 的端点，把「本页失败态无 toast 串台、故无需 data-testid」的前提整体作废。
    // 放行侧用 route.continue() 而非 route.fallback()——后者是 Playwright 1.63 才加入的
    // API，而 @playwright/test 正钉在 ^1.63.0 的下边界。
    const mockHoldings = (url: RegExp, message: string) =>
      page.route(url, (route) =>
        fail
          ? route.fulfill({
              status: 500,
              json: { detail: { error: "INTERNAL", message } },
            })
          : route.continue()
      );
    mockHoldings(
      /\/api\/positions\/portfolio\/[^/]+\/holdings\/by-product(\?|$)/,
      "平台持仓产品聚合服务暂不可用"
    );
    mockHoldings(
      /\/api\/positions\/portfolio\/[^/]+\/holdings\/by-platform(\?|$)/,
      "平台概览聚合服务暂不可用"
    );
    await page.goto(PLATFORM_PATH);

    // 整页 early return，页面上不可能有别处含该串，所以按文案直达、不补 testid（#678 的
    // 分寸：testid 是长期维护的契约面，只在真有串台时才补）。断 by-product 的消息同时
    // 钉住 `error={productErr ?? platformErr}` 的取序。
    await expect(
      page.getByText("加载失败：平台持仓产品聚合服务暂不可用")
    ).toBeVisible({ timeout: 10_000 });
    // 失败不得伪装成空态（§1.2 口径①）。platform-holdings-section 位于 early return
    // **之后**（:193），失败态渲染时根本不存在——这条负向就是「为什么不能用它当 scope」。
    await expect(page.getByText("未找到该平台持仓")).toHaveCount(0);
    await expect(page.getByTestId("platform-holdings-section")).toHaveCount(0);

    const retry = page.getByRole("button", { name: "重试" });
    await expect(retry).toBeVisible();
    // 注册必须在失败态**已可见之后**：`retry: 1`（providers.tsx）的自动重试发生在可见
    // 之前，注册早了会变成「没点重试也算绿」。两个 Promise 一起 await，最坏一次 10s。
    const refetchedProduct = page.waitForRequest(
      (r) => r.method() === "GET" && /holdings\/by-product(\?|$)/.test(r.url()),
      { timeout: 10_000 }
    );
    const refetchedPlatform = page.waitForRequest(
      (r) => r.method() === "GET" && /holdings\/by-platform(\?|$)/.test(r.url()),
      { timeout: 10_000 }
    );
    // 翻转开关必须在点击**之前**：点击触发的才是真实 refetch，两条都放行页面才可能恢复。
    fail = false;
    await retry.click();
    await Promise.all([refetchedProduct, refetchedPlatform]);

    await expect(page.getByTestId("platform-overview-card")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByTestId("platform-holdings-section")).toContainText(
      "沪深300ETF"
    );
    await expect(page.getByText("加载失败")).toHaveCount(0);
  });

  test("整页读取失败：仅 by-platform 挂掉也整页替换，且透出的是失败那条的消息（#681）", async ({ page }) => {
    // 钉住两件单侧失败时才暴露的事：
    // ① `error={productErr ?? platformErr}` 的回退取序——写成 `error={productErr}` 时
    //    productErr 为 null，页面**仍然渲染一个失败块**（本地兜底串「请刷新重试」），
    //    双向对称的那条用例（上一条）抓不到这个错，只有失败侧不在首位时才显形；
    // ② #681「本次刻意不做」的既有契约：by-product 已成功、by-platform 失败时整页仍被
    //    替换，已取到的持仓随之消失。改成局部降级是需要业务判定的独立决策（另开条目），
    //    届时这条会响亮地红，而不是让降级静默通过。
    await page.route(
      /\/api\/positions\/portfolio\/[^/]+\/holdings\/by-platform(\?|$)/,
      (route) =>
        route.fulfill({
          status: 500,
          json: {
            detail: { error: "INTERNAL", message: "平台概览聚合服务暂不可用" },
          },
        })
    );
    await page.goto(PLATFORM_PATH);
    await expect(
      page.getByText("加载失败：平台概览聚合服务暂不可用")
    ).toBeVisible({ timeout: 10_000 });
    // 页头平台名同样来自 by-platform，随整页一起消失 = 替换成立（而非局部降级）
    await expect(page.getByRole("heading", { name: "华宝证券" })).toHaveCount(0);
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
    // 视图经 URL ?view= 异步切换：先确认分段按钮已置为「按平台」，否则「按产品」视图
    // 的现金卡（单平台时其可及名同样含平台名）会被 .first() 抢先命中，点进
    // /product/CASH 而非平台详情（#654 聚合 + #646 预填用例改变渲染时序后此竞态显形）
    await expect(page.getByTestId("holdings-tab-platform")).toHaveAttribute(
      "aria-pressed",
      "true"
    );
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
    // 本用例只防「恒渲染」，防不了「借组合级数字」：E2E_ACTIVE 只有 HBZQ 单平台，
    // 该日组合级与平台级同为 0，借数后渲染的仍是不渲染。两粒度区分的机器保护
    // 在 Blocker 1 用例的 MYCF 负向断言（#654 L2 A）。
    // 先钉「有卡分支已到达」：platform-holdings-section 无条件渲染、「暂无持仓」
    // 也在其内，而脚注在有卡分支——不钉则分支未到时 toHaveCount(0) 以错误理由变绿
    // （#654 L2 B）
    // 归属本 describe（#654 L2 S1）：用例跑的是平台详情页，此前误放在
    // 「平台-产品详情页」分组下，按分组筛平台页回归会漏掉它
    await page.goto(PLATFORM_PATH);
    const holdings = page.getByTestId("platform-holdings-section");
    await expect(holdings).toBeVisible();
    await expect(holdings.getByText("沪深300ETF")).toBeVisible();
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

  test("操作行买入：跳转即开录入 Dialog，平台+产品均已预填（#646）", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    await page.getByTestId("platform-product-action-row").getByRole("link", { name: "买入" }).click();
    await page.waitForURL(/\/trades\?/);
    expect(page.url()).toContain("action=create");
    const dlg = dialogByTitle(page, "提交交易");
    await dlg.waitFor({ timeout: 10_000 });
    await expect(platformTrigger(dlg, "HBZQ")).toBeVisible();
    await expect(productTrigger(dlg, "510300.SH")).toBeVisible();
    await expect(productTrigger(dlg, "A股场内")).toBeVisible();
    // 扣款平台按 #646 契约不预填（用户自选），仍显示占位/特殊项文案
    await expect(platformTrigger(dlg, "同交易平台")).toBeVisible();
  });

  test("操作行事件：落事件录入页并开 Dialog，平台+产品均已预填（#646）", async ({ page }) => {
    await page.goto(PLATFORM_PRODUCT_PATH);
    await page.getByTestId("platform-product-action-row").getByRole("link", { name: "事件" }).click();
    await page.waitForURL(/\/share-change-events\?/);
    expect(page.url()).toContain("product=510300.SH");
    expect(page.url()).toContain("platform=HBZQ");
    expect(page.url()).toContain("action=create");
    const dlg = dialogByTitle(page, "新建份额变动事件");
    await dlg.waitFor({ timeout: 10_000 });
    await expect(platformTrigger(dlg, "HBZQ")).toBeVisible();
    await expect(productTrigger(dlg, "510300.SH")).toBeVisible();
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

  test("历史净值请求失败 → 失败态与重试，而非空态（#647）", async ({ page }) => {
    // 与 product-detail.spec.ts 同形：两页共用 QueryErrorState 只统一了文案入口，
    // 失败/空态的**判序**各写一份，而组件不在覆盖率分母内——把 historyError 分支
    // 改回 `length === 0` 优先，#647 的原始症状会在 CI 全绿下复发（#655 L2 ①）
    await page.route("**/market-data/products/**/nav-history*", (route) =>
      route.fulfill({ status: 500, json: { detail: "internal error" } })
    );
    await page.goto(PLATFORM_PRODUCT_PATH);
    const history = page.getByTestId("platform-product-history-card");
    await expect(history).toContainText("加载失败", { timeout: 10_000 });
    await expect(history).not.toContainText("暂无净值数据");
    await expect(history.getByRole("button", { name: "重试" })).toBeVisible();
  });

  test("交易记录读取失败 → 失败态与重试，同页净值卡不受影响（#663②）", async ({ page }) => {
    // 与上一条同形但落在另一页：两页各写一份判序，缺一条就等于那一页没有网。
    // message 与上一条不同串，红的时候能直接看出是哪页的 route 生效了。
    await page.route(/\/api\/trades(\?|$)/, (route) =>
      route.fulfill({
        status: 500,
        json: { detail: { error: "INTERNAL", message: "产品交易服务暂不可用" } },
      })
    );
    await page.goto(PLATFORM_PRODUCT_PATH);
    const card = page.getByTestId("platform-product-trades-card");
    await expect(card).toContainText("加载失败：产品交易服务暂不可用", { timeout: 10_000 });
    await expect(card).not.toContainText("暂无交易记录");
    await expect(card.getByRole("button", { name: "重试" })).toBeVisible();
    // 历史净值卡照常出数：两卡失败文案同以「加载失败」开头，没有这条正向对照，
    // route 判据写宽（误伤 nav-history）也会被当成"本页失败态正常"而看不出来
    await expect(page.getByTestId("platform-product-history-card")).toContainText("4.2000");
  });

  test("整页读取失败 → 页级失败态与重试，而非「未找到该平台产品持仓」空态（#681）", async ({ page }) => {
    // 本页整页 early return 由**单条**聚合决定（useHoldingsByProduct），所以一条
    // waitForRequest 已经密不透风，不必像平台详情页那样加恢复断言。
    // message 与同文件其它用例逐条不同串（:369 立的惯例），红的时候能直接看出哪条 route 生效。
    await page.route(
      /\/api\/positions\/portfolio\/[^/]+\/holdings\/by-product(\?|$)/,
      (route) =>
        route.fulfill({
          status: 500,
          json: {
            detail: { error: "INTERNAL", message: "平台产品切片服务暂不可用" },
          },
        })
    );
    await page.goto(PLATFORM_PRODUCT_PATH);
    await expect(
      page.getByText("加载失败：平台产品切片服务暂不可用")
    ).toBeVisible({ timeout: 10_000 });
    // 负向取紧接其后的两个空态分支之一「未找到该平台产品持仓」（PlatformProductDetailContent
    // 的 `!product || !slice` 分支）——把后端故障说成「没有持仓」正是 §1.2 口径①的本体。
    await expect(page.getByText("未找到该平台产品持仓")).toHaveCount(0);
    const retry = page.getByRole("button", { name: "重试" });
    await expect(retry).toBeVisible();
    const refetched = page.waitForRequest(
      (r) => r.method() === "GET" && /holdings\/by-product(\?|$)/.test(r.url()),
      { timeout: 10_000 }
    );
    await retry.click();
    await refetched;
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
 *
 * 该用例同时是 #641 在途**两粒度**的唯一 E2E 保护处：隔离组合里 HBZQ 有 8,200 买入
 * 在途、MYCF 只有已确认现金无在途，故「组合级 8,200 / 平台级 0」在同一快照上可区分
 * （#654 L2 A）。E2E_ACTIVE 是单平台种子、最新快照日两粒度同为 0，区分不了。
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
    // 第二平台 MYCF：只放**已确认申购**（现金行不需净值夹具），无在途。
    // 于是同一快照上「组合级在途 8,200 / MYCF 平台级在途 0」两个粒度可区分，
    // 供末尾负向断言抓「借组合级数字」（#654 L2 A）
    const mycfSub = await post<{ id: number }>("/api/subscriptions", {
      portfolio_code: code, investor_code: "ADMIN", platform_code: "MYCF",
      sub_type: "subscribe", amount: 50000, apply_date: apply,
    });
    await post(`/api/subscriptions/${mycfSub.id}/confirm`);
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

    // #654 L2 A：真负向——本平台（MYCF）在途为 0，而**组合级**在途为 8,200。
    // 这是当前唯一能区分两粒度的 E2E 形态：组件若借组合级字段（或加「平台级缺失
    // 则回落组合级」兜底），该页会渲染出 ¥8,200.00 而当场红。
    // 先等现金卡可见：既钉住「MYCF 平台卡已发布」（未发布时页面走
    // 「未找到该平台持仓」，toHaveCount(0) 会以错误理由空过），也钉住「进入了
    // 脚注所在的有卡分支」（同 L2 B 口径）
    await page.goto(`/portfolio/${code}/platforms/MYCF`);
    await expect(
      page.getByTestId("platform-holdings-section").getByTestId("platform-cash-card")
    ).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("platform-in-transit-note")).toHaveCount(0);
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
    // R-3：锚点取无条件出的成功 toast，而非绑在条件性 requires_snapshot_regen 上的 Alert 分支
    // （条件分支的界面出口由下面 #645 那两条单独钉，两者不互替）
    await amountInput.fill("40000");
    await page.getByRole("button", { name: "确认更新" }).click();
    const postBody = JSON.parse((await postReq).postData() ?? "{}") as { update_date?: string };
    expect(postBody.update_date).toBe(target);

    // B-2/B-5 网：写完面板必须当场见覆盖记录块
    await expect(
      page.getByTestId("toast-card").getByText("现金市值已更新")
    ).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("当日已有覆盖记录")).toBeVisible({ timeout: 10_000 });

    // #645 写入侧真算的界面出口。**实测本形态走 warnings 分支，不是「✓ 更新成功」短文案**：
    // 种子 E2E_ACTIVE 在写入日有 1 笔已确认现金交易，而短分支条件是
    // `!regen && warnings 为空`（CashMarketValueUpdateDialog 的 submitResult 块），
    // 日期一支成立不代表整条件成立。
    // 承重的判别网是「不含需重新生成快照」：regen 若退化成 #645 前的恒真、或后端把
    // true 写死，长文案 Alert（"覆盖已写入，需重新生成快照才能…"）会渲染并判红。
    // 两支的完整真/假组合由后端 REST 参数化用例钉（test_issue_88_90_91 的
    // test_rest_post_returns_real_requires_snapshot_regen）。
    await expect(dlg.getByText(/覆盖层将压制其效果/)).toBeVisible();
    await expect(dlg).not.toContainText("需重新生成快照");

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
    // requires_snapshot_regen=False（position_service::delete_manual_cash_override 经共用
    // 判据 ::_cash_override_requires_regen 真算，不写行号——该函数体量会变），故应为短文案且不含
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
  // 是对用户作出的承诺，此前两侧均无断言锁定它——后端 position_service::update_cash_position
  // 抛 NON_TRADING_DAY（符号锚点，不写行号：该函数体量会变）对 cash-position 这条路径没有任何测试，前端也只有文案。
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

  test("两支提交反馈的正向可见性：长文案 + 跳转按钮 与 ✓ 更新成功（#660）", async ({ page }, testInfo) => {
    // 为什么必须自建组合：共享 E2E_ACTIVE 上两支的正向分支都不可达——它在写入日有 1 笔
    // 已确认现金交易（warnings 恒非空 ⇒ 落 warnings 支，短文案永不渲染），而固定快照形态
    // 使 regen 恒 false（长文案支永不渲染）。禁止对 E2E_ACTIVE 跑 recalculate/catch-up/
    // generate-next 来凑形态（frontend/AGENTS.md §4 红线：固定快照与可编辑窗口是种子契约）。
    // 选日依据：申购确认同样产生 confirmed CASH 腿，日期 = apply_date 的 T+1
    // （backend/app/services/subscription_service.py 的 get_next_trading_day），所以
    // 「不造交易」并不能让 warnings 为空，必须把写入日错开到确认日之后：
    //   d1 申购 → d2 确认（CASH 腿落 d2）→ 快照 d2、d3 ⇒ 最新快照日 = d3
    //   支 A 写 d3（≤ 最新快照日 ⇒ regen=true，长文案支）
    //   支 B 写 d4（> 最新快照日 且 当日无 CASH 腿 ⇒ regen=false + warnings 空，短文案支）
    // mobile/webkit 比 chromium 慢 3~4 倍（trade-in-transit.spec.ts 的 CI 实测与同款放宽）
    test.setTimeout(60_000);
    const code = `C660_${testInfo.project.name}_${testInfo.retry}`;
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    const headers = await authHeaders(page);
    const post = async <T = unknown>(path: string, data?: unknown): Promise<T> => {
      const resp = await page.request.post(path, { headers, data });
      await expect(resp, `${path} ${resp.status()} ${await resp.text()}`).toBeOK();
      return resp.json();
    };

    const upto = await tradingDaysUptoToday(page);
    expect(upto.length, "交易日历里取不到 4 个 ≤ 今天的交易日").toBeGreaterThanOrEqual(4);
    const [d1, d2, d3, d4] = upto.slice(-4);
    // 数据前提先钉住：错把 d2 当写入日会落进 warnings 支，用例该红在前提而不是文案上
    expect(d1 < d2 && d2 < d3 && d3 < d4).toBe(true);

    await post("/api/portfolios", { code, name: `#660 现金覆盖 ${code}` });
    const sub = await post<{ id: number }>("/api/subscriptions", {
      portfolio_code: code, investor_code: "ADMIN", platform_code: "HBZQ",
      sub_type: "subscribe", amount: 100000, apply_date: d1,
    });
    await post(`/api/subscriptions/${sub.id}/confirm`);
    await post("/api/snapshots/generate", { portfolio_code: code, target_date: d2 });
    await post("/api/snapshots/generate", { portfolio_code: code, target_date: d3 });

    await page.goto(`/portfolio/${code}/platforms/HBZQ/products/CASH?market=`);
    await page.getByTestId("platform-product-action-row")
      .getByRole("button", { name: "市值更新" }).click();
    const dlg = dialogByTitle(page, "更新现金市值");
    await expect(dlg).toBeVisible();
    const amount = dlg.getByLabel("当前金额（元）");

    // ---- 支 A：写入日 == 最新快照日 → 长文案 + 「前往快照管理」跳转 ----
    await pickCalendarDay(page, dialogDateTrigger(dlg), d3);
    await amount.fill("30000");
    await dlg.getByRole("button", { name: "确认更新" }).click();
    await expect(
      dlg.getByText("覆盖已写入，需重新生成快照才能在持仓中生效。")
    ).toBeVisible({ timeout: 10_000 });
    // 判别性负向：分支条件写反时两支会同时出现或互换，只断正向的那条抓不住
    await expect(dlg).not.toContainText("✓ 更新成功");
    // modal 内的链接可以按 role 定位：Radix 的 aria-hidden 只加在 DialogContent **之外**
    // 的子树（与文件末尾 toast 那条注释的口径相反面）
    await expect(dlg.getByRole("link", { name: "前往快照管理" })).toBeVisible();

    // ---- 支 B：写入日 > 最新快照日且当日无 confirmed CASH 腿 → 短文案 ----
    // 不关开 Dialog：onSelect 已把上一支的 submitResult 清掉（省一次浮层开合，#524 面）；
    // 但 onSuccess 会清空金额，必须重填
    await pickCalendarDay(page, dialogDateTrigger(dlg), d4);
    await amount.fill("30000");
    await dlg.getByRole("button", { name: "确认更新" }).click();
    // 带「✓ 」、且 scope 到 dlg：简化成「更新成功」会同时命中挂在 app root 的 toast
    //（getByText 是 DOM 口径，不受 aria-hidden 保护），Dialog 内没渲染也算绿
    await expect(dlg.getByText("✓ 更新成功")).toBeVisible({ timeout: 10_000 });
    await expect(dlg).not.toContainText("需重新生成快照");
    // 不再断成功 toast：它是无条件出口，已由上面「表单校验、成功反馈…」那条钉住
    //（R-3 口径），且本用例提交过两次，同文案 toast 会有两条同时存活。
  });
});
