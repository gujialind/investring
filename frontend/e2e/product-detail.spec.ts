import { test, expect } from "@playwright/test";
import { E2E_ACTIVE, dialogByTitle, gotoPortfolioDetail, productTrigger } from "./helpers";

/**
 * #595 步骤③ 持仓产品详情页（M3/D3，双端共享 ProductDetailContent）。
 *
 * 数据说明（种子契约，backend/tests/seed_base.py / seed_e2e.py）：
 * - E2E_ACTIVE：单平台 HBZQ（华宝证券）持有 510300.SH（沪深300ETF）15,000 份，
 *   连续 2 日快照（D2=4.0000 / D3=4.2000，仅 unit_price，accumulated_nav/pct_change
 *   为 NULL）→ 市值 ¥63,000.00、持有/最新收益 +¥3,000.00。
 * - 净值夹具无累计净值 → 累计净值曲线走「暂无净值数据」空态、六窗区间收益率
 *   全为占位「--」（#637：None 语义，历史不足或数据缺失不硬凑数）。
 * - 断言为可见性与关系式，不硬绑定快照日期；两端共用组件，mobile project 同跑。
 */

const PRODUCT_PATH = `/portfolio/${E2E_ACTIVE}/product/510300.SH?market=CN_EXCHANGE`;

test.describe("产品详情页", () => {
  test("页头与概览卡：产品名、代码·市场、市值、份额与收益", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    await expect(
      page.getByRole("heading", { name: "沪深300ETF" })
    ).toBeVisible();
    await expect(page.getByText("510300.SH")).toBeVisible();

    const overview = page.getByTestId("product-overview-card");
    await expect(overview).toBeVisible();
    await expect(overview).toContainText("63,000.00");
    await expect(overview).toContainText("15,000.00");
    // 持有收益（含 %）、累计收益、最新收益三处收益（+¥3,000.00 至少出现一次）
    await expect(overview.getByText(/\+¥3,000\.00/).first()).toBeVisible();
    await expect(overview).toContainText("(+5.00%)");
    // 单位净值行（最新一条净值，日期随种子滚动只断言数值与标签）
    await expect(overview).toContainText("单位净值 4.2000");
    // 累计收益注记（口径说明，琥珀警示色 token）
    await expect(overview).toContainText("*累计收益含已卖出实现盈亏");
  });

  test("净值曲线空态与六窗区间收益率占位", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    const curve = page.getByTestId("product-curve-card");
    await expect(curve.getByRole("heading", { name: "累计净值走势" })).toBeVisible();
    // 默认近6月选中；ETF 夹具无累计净值 → 图表空态文案
    await expect(page.getByTestId("nav-range-6m")).toHaveAttribute("aria-pressed", "true");
    await expect(curve).toContainText("暂无净值数据");

    const returns = page.getByTestId("product-returns-card");
    for (const field of ["m1", "m3", "m6", "y1", "ytd", "all"]) {
      await expect(returns.getByTestId(`return-${field}`)).toContainText("--");
    }
  });

  test("区间 Tab 切换", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    await page.getByTestId("nav-range-1m").click();
    await expect(page.getByTestId("nav-range-1m")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("nav-range-6m")).toHaveAttribute("aria-pressed", "false");
  });

  test("历史净值：日期降序两行、累计净值/日涨跌占位、无查看更多", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    const history = page.getByTestId("product-history-card");
    const rows = history.getByTestId(/^product-history-row/);
    // seed 仅 D2/D3 两条：首行（较新交易日）unit 4.2000 在前
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText("4.2000");
    await expect(rows.nth(1)).toContainText("4.0000");
    // 累计净值与日涨跌夹具为 NULL → 占位 --
    await expect(rows.nth(0).getByText("--").first()).toBeVisible();
    await expect(history.getByTestId("history-load-more")).toHaveCount(0);
  });

  test("历史净值请求失败 → 失败态与重试，而非空态（#647）", async ({ page }) => {
    // 拦截 nav-history 返 500：失败必须可见（失败文案 + 重试入口），不得伪装成
    // 「暂无净值数据」——静默失败会把后端故障/权限过期读成「产品无历史」。
    // retry: 1（providers.tsx）下失败态约 1-2s 内显现，断言留 10s 余量
    await page.route("**/market-data/products/**/nav-history*", (route) =>
      route.fulfill({ status: 500, json: { detail: "internal error" } })
    );
    await page.goto(PRODUCT_PATH);
    const history = page.getByTestId("product-history-card");
    await expect(history).toContainText("加载失败", { timeout: 10_000 });
    await expect(history).not.toContainText("暂无净值数据");
    await expect(history.getByRole("button", { name: "重试" })).toBeVisible();
  });

  test("历史净值空响应 → 空态而非失败态（#647 两态分别钉住）", async ({ page }) => {
    // 种子无「组合持有但产品无净值记录」形态（E2E_ACTIVE 仅持 510300.SH 且有夹具），
    // 空分支以空响应钉住——与上一条失败分支分别断言、不互相覆盖
    await page.route("**/market-data/products/**/nav-history*", (route) =>
      route.fulfill({
        status: 200,
        json: { items: [], total: 0, page: 1, page_size: 5 },
      })
    );
    await page.goto(PRODUCT_PATH);
    const history = page.getByTestId("product-history-card");
    await expect(history).toContainText("暂无净值数据", { timeout: 10_000 });
    await expect(history).not.toContainText("加载失败");
  });

  test("平台分布：单平台一行、市值与占比、行可点击进入平台-产品详情", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    const card = page.getByTestId("platform-distribution-card");
    const rows = card.getByTestId("platform-distribution-row");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("华宝证券");
    await expect(rows.first()).toContainText("15,000.00");
    await expect(rows.first()).toContainText("63,000.00");
    await expect(rows.first()).toContainText("100.0%");
    // S7：行点击 → 平台-产品详情页（步骤④接线）
    await rows.first().getByRole("link").click();
    await page.waitForURL(/\/platforms\/HBZQ\/products\/510300\.SH/);
    await expect(
      page.getByRole("heading", { name: /华宝证券.*沪深300ETF/ })
    ).toBeVisible();
  });

  test("交易记录卡：该产品跨平台记录与查看全部链接", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    const card = page.getByTestId("product-trades-card");
    const rows = card.getByTestId("product-trade-row");
    await expect(rows.first()).toBeVisible();
    await expect(rows.first()).toContainText("买入");
    // 查看全部 → trades 产品预填 URL（#595 操作行同一预填契约）
    await expect(card.getByRole("link", { name: "查看全部" })).toHaveAttribute(
      "href",
      new RegExp(`/trades\\?product=510300\\.SH&market=CN_EXCHANGE`)
    );
  });

  test("操作行买入：trades 页产品预填且列表非空", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    await page.getByTestId("product-action-row").getByRole("link", { name: "买入" }).click();
    await page.waitForURL(/\/trades\?/);
    expect(page.url()).toContain("product=510300.SH");
    expect(page.url()).toContain("market=CN_EXCHANGE");
    expect(page.url()).toContain("trade_type=buy");
    // 预填后服务端过滤生效：510300.SH 的已确认买入在列表中
    await expect(page.locator("table tbody tr").first()).toBeVisible({ timeout: 10_000 });
  });

  test("操作行买入：跳转即开录入 Dialog，方向与产品/市场已预填（#646）", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    await page.getByTestId("product-action-row").getByRole("link", { name: "买入" }).click();
    await page.waitForURL(/\/trades\?/);
    expect(page.url()).toContain("action=create");
    const dlg = dialogByTitle(page, "提交交易");
    await dlg.waitFor({ timeout: 10_000 });
    // 方向=买入：买入/卖出各有专属提示 Alert，据此判定（不靠按钮 variant 样式）
    await expect(dlg.getByTestId("buy-deduct-hint")).toBeVisible();
    await expect(dlg.getByTestId("sell-arrival-hint")).not.toBeVisible();
    // 产品与市场已预填：触发框回显「…510300.SH · A股场内」。名称缓存未命中时只回显
    // code（SearchableProductSelect 懒加载），故断 code 与市场后缀两段、不断产品名
    await expect(productTrigger(dlg, "510300.SH")).toBeVisible();
    await expect(productTrigger(dlg, "A股场内")).toBeVisible();
    // 预填值仍可当场改选（验收第 5 条）：预填只作 useState 初值、无 effect 回灌，
    // 故改方向后既有预填不被重置
    await dlg.getByRole("button", { name: "卖出" }).click();
    await expect(dlg.getByTestId("sell-arrival-hint")).toBeVisible();
    await expect(productTrigger(dlg, "510300.SH")).toBeVisible();
  });

  test("操作行卖出：录入方向为卖出，不被重置成买入（#646）", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    await page.getByTestId("product-action-row").getByRole("link", { name: "卖出" }).click();
    await page.waitForURL(/\/trades\?/);
    expect(page.url()).toContain("trade_type=sell");
    const dlg = dialogByTitle(page, "提交交易");
    await dlg.waitFor({ timeout: 10_000 });
    await expect(dlg.getByTestId("sell-arrival-hint")).toBeVisible();
    await expect(dlg.getByTestId("buy-deduct-hint")).not.toBeVisible();
    await expect(productTrigger(dlg, "510300.SH")).toBeVisible();
  });

  test("操作行事件：落事件录入页并开 Dialog，产品已预填（#646）", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    // #656 L2 S1：URL 参数与 trades 页同口径——既预填录入表单也驱动列表筛选，
    // 故事件列表请求应带 products=<code>|<market>（| 编码为 %7C，只断前缀）
    const listReq = page.waitForRequest(
      (r) =>
        r.url().includes("/api/share-change-events") &&
        r.url().includes("products=510300.SH"),
      { timeout: 10_000 },
    );
    // 原 href 是不带参数的裸 tradesLink（与「查看全部」相同），落到没有事件录入的调仓列表
    await page.getByTestId("product-action-row").getByRole("link", { name: "事件" }).click();
    await listReq;
    await page.waitForURL(/\/share-change-events\?/);
    expect(page.url()).toContain("product=510300.SH");
    expect(page.url()).toContain("market=CN_EXCHANGE");
    expect(page.url()).toContain("action=create");
    const dlg = dialogByTitle(page, "新建份额变动事件");
    await dlg.waitFor({ timeout: 10_000 });
    await expect(productTrigger(dlg, "510300.SH")).toBeVisible();
  });

  test("从组合详情产品卡点击进入（深链起点）", async ({ page }) => {
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await page.getByRole("link", { name: /沪深300ETF/ }).first().click();
    await page.waitForURL(/\/product\/510300\.SH/);
    await expect(
      page.getByRole("heading", { name: "沪深300ETF" })
    ).toBeVisible();
  });

  test("缺 market 参数的非现金产品 EmptyState（R-4）", async ({ page }) => {
    // R-4：market 迁移为 query 后可缺，参数问题须与「未找到持仓」区分（同平台-产品页 S4 形态）
    await page.goto(`/portfolio/${E2E_ACTIVE}/product/510300.SH`);
    await expect(page.getByText("缺少 market 参数")).toBeVisible();
  });

  test("整页读取失败 → 页级失败态与重试，而非「未找到该产品持仓」空态（#681）", async ({ page }) => {
    // 本条断的是「后端消息上桌 / 不落空态 / 重试真重发」，**不**断言像素：多传紧凑
    // `className` 让页级形态退回卡内，这条照样绿（E2E 只断定位与文本，视觉层归目检，
    // 见 frontend/AGENTS.md §4 与 §目检）。「不传 className」靠代码评审与三处渲染同一个
    // 组件缺省分支来保证。
    // 判据 scoped 到 holdings/by-product，绝不写 `**/api/**`：本页还发 nav-analysis、
    // nav-history、trades 与 /api/platforms，写宽会把它们一并打成 500，用例就不再只对
    // by-product 这一条聚合的失败呈现归因；`usePlatformList` 失败还会额外弹一条 toast
    // （标题「平台列表加载失败」，与本条断言串不重叠、不会串台），多出一个无关失败出口。
    await page.route(
      /\/api\/positions\/portfolio\/[^/]+\/holdings\/by-product(\?|$)/,
      (route) =>
        route.fulfill({
          status: 500,
          json: {
            detail: { error: "INTERNAL", message: "产品持仓聚合服务暂不可用" },
          },
        })
    );
    await page.goto(PRODUCT_PATH);
    await expect(
      page.getByText("加载失败：产品持仓聚合服务暂不可用")
    ).toBeVisible({ timeout: 10_000 });
    // 「已清仓」只出现在该空态分支的 description，断言取 message 文案
    await expect(page.getByText("未找到该产品持仓")).toHaveCount(0);
    const retry = page.getByRole("button", { name: "重试" });
    await expect(retry).toBeVisible();
    const refetched = page.waitForRequest(
      (r) => r.method() === "GET" && /holdings\/by-product(\?|$)/.test(r.url()),
      { timeout: 10_000 }
    );
    await retry.click();
    await refetched;
  });
});

// #595 §4.5：现金产品详情页操作行为转入/转出 + 市值更新
// B-6 方案 B：路由迁移为 /product/[productCode]?market=，CASH 产品以空 market 表示
const CASH_PRODUCT_PATH = `/portfolio/${E2E_ACTIVE}/product/CASH?market=`;

test.describe("现金产品详情页", () => {
  test("操作行：转入/转出/市值更新（非买入/卖出/事件）", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    const actionRow = page.getByTestId("product-action-row");
    await expect(actionRow).toBeVisible();
    await expect(actionRow.getByRole("button", { name: "转入" })).toBeVisible();
    await expect(actionRow.getByRole("button", { name: "转出" })).toBeVisible();
    await expect(actionRow.getByRole("button", { name: "市值更新" })).toBeVisible();
    // 不应有买入/卖出/事件按钮
    await expect(actionRow.getByRole("link", { name: "买入" })).not.toBeVisible();
    await expect(actionRow.getByRole("link", { name: "卖出" })).not.toBeVisible();
  });

  test("操作行：转入/转出打开现金转移 Dialog（聚合视角无平台预填）", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    const actionRow = page.getByTestId("product-action-row");
    // 转入：打开 Dialog，转出/转入平台均显示 placeholder（聚合视角无平台上下文）
    await actionRow.getByRole("button", { name: "转入" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("heading", { name: "平台间现金转移" })).toBeVisible();
    await expect(dialog.getByText("选择转出平台")).toBeVisible();
    await expect(dialog.getByText("选择转入平台")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    // 转出：同样打开 Dialog（聚合视角两按钮同入口，无方向预填）
    await actionRow.getByRole("button", { name: "转出" }).click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("heading", { name: "平台间现金转移" })).toBeVisible();
    await expect(dialog.getByText("选择转出平台")).toBeVisible();
    await expect(dialog.getByText("选择转入平台")).toBeVisible();
  });

  test("市值更新按钮打开 Dialog", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    await page.getByTestId("product-action-row").getByRole("button", { name: "市值更新" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("heading", { name: "更新现金市值" })).toBeVisible();
  });

  test("净值相关卡片不显示（现金无净值）", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    await expect(page.getByTestId("product-overview-card")).toBeVisible();
    // 净值走势/区间收益/历史净值卡片不应出现
    await expect(page.getByTestId("product-curve-card")).not.toBeVisible();
    await expect(page.getByTestId("product-returns-card")).not.toBeVisible();
    await expect(page.getByTestId("product-history-card")).not.toBeVisible();
  });
});

// #683 B/D：本页交易卡与两页共用的 useNavAnalysis 从未接入失败态。
// 每条新用例各取一个互不重复的后端 message 串（platform-detail.spec.ts 立的惯例），
// 红的时候能直接看出哪条 route 生效；判据一律 scoped，绝不写 **/api/**。
test.describe("读侧失败伪装成空态（#683 B 交易卡 / D 净值分析）", () => {
  test("交易记录卡读取失败 → 卡内失败态与重试，而非「暂无交易记录」（#683 B1）", async ({ page }) => {
    // 同形的 PlatformDetailContent 交易卡早在 #663① 就接了失败态，本页是漏掉的那一个。
    // 该组合明明有已确认交易（E2E_ACTIVE 即如此），5xx 时卡里却说「暂无交易记录」——
    // 用户读成「这个产品没成交过」，可能重复录入一笔买入。
    await page.route(/\/api\/trades(\?|$)/, (route) =>
      route.fulfill({
        status: 500,
        json: { detail: { error: "INTERNAL", message: "产品交易列表服务暂不可用" } },
      })
    );
    await page.goto(PRODUCT_PATH);
    const card = page.getByTestId("product-trades-card");
    await expect(card).toContainText("加载失败：产品交易列表服务暂不可用", { timeout: 10_000 });
    await expect(card).not.toContainText("暂无交易记录");
    const retry = card.getByRole("button", { name: "重试" });
    await expect(retry).toBeVisible();
    const refetched = page.waitForRequest(
      (r) => r.method() === "GET" && /\/api\/trades(\?|$)/.test(r.url()),
      { timeout: 10_000 }
    );
    await retry.click();
    await refetched;
    // 正向对照：同页历史净值卡仍拿到种子净值，证明判据没串台到 nav 系请求
    await expect(page.getByTestId("product-history-card")).toContainText("4.2000");
  });

  test("净值分析读取失败 → 曲线卡与区间收益率卡都不落空态/全 --（#683 D-2）", async ({ page }) => {
    // 两个卡消费同一个查询，各卡各一个失败块（判定 2）。失败原先落两处：曲线进 NavCurve 内置的
    // 「暂无净值数据」，六窗经 formatReturnRate(undefined) 全塌成「--」——后端故障被说成
    // 「这个区间没净值」。判据取 nav-analysis 末段，与既有 nav-history 的 glob 不重叠。
    await page.route(/\/nav-analysis(\?|$)/, (route) =>
      route.fulfill({
        status: 500,
        json: { detail: { error: "INTERNAL", message: "净值分析服务暂不可用" } },
      })
    );
    await page.goto(PRODUCT_PATH);
    const curve = page.getByTestId("product-curve-card");
    const returns = page.getByTestId("product-returns-card");
    await expect(curve).toContainText("加载失败：净值分析服务暂不可用", { timeout: 10_000 });
    await expect(curve).not.toContainText("暂无净值数据");
    // 反证②的落点：失败块若只接了曲线卡，下面两句红在未被覆盖的收益率卡
    await expect(returns).toContainText("加载失败：净值分析服务暂不可用");
    await expect(returns.getByTestId("return-m1")).toHaveCount(0);
    const retry = curve.getByRole("button", { name: "重试" });
    await expect(retry).toBeVisible();
    const refetched = page.waitForRequest(
      (r) => r.method() === "GET" && /\/nav-analysis(\?|$)/.test(r.url()),
      { timeout: 10_000 }
    );
    await retry.click();
    await refetched;
    // 同页 nav-history 系不受这条 route 影响（历史净值卡仍出种子值）
    await expect(page.getByTestId("product-history-card")).toContainText("4.2000");
  });

  test("切换区间在途不得把上一个区间的数据当成当前区间呈现（#683 D-1）", async ({ page }) => {
    // useNavAnalysis 配 placeholderData: keepPreviousData，换 range 键时先保留上一个键的返回值
    // （筛选/翻页不闪烁的既有行为，本批按判定**不动它**）。实测 query-core 5.102.8 的时序：
    // 点「近1个月」后该请求在途期间 isError=false、isPlaceholderData=true、isFetching=true，
    // data 仍是 6m 的值——chip 的 aria-pressed 已切到 1m，而六窗数值仍是 6m 的旧值，屏上原本
    // 没有任何「这不是当前区间」的标记。对记账系统这是「把旧数读成新数」，比落空态更严重。
    // 形态取 docs/design/visual-spec.md §14 既有的局部加载态（容器 opacity-50 + 右上角 Loader2），
    // 并把该状态显式写成 aria-busy 供断言：定位器契约禁止按 Tailwind 工具类定位（同 #650 用
    // aria-current 而不按高亮类名断言的手法）。**旧值仍在屏上是刻意的**，本用例不断它缺席。
    const SIX_M = {
      curve: [
        { date: "2020-01-02", accumulated_nav: 1.0 },
        { date: "2020-06-30", accumulated_nav: 9.8765 },
      ],
      // m1 取一个可识别值作旧值基线（六窗是同一查询的另一个消费点，且是纯 DOM 文本；
      // 曲线数值落在 recharts 的 SVG 刻度里、不保证以文本呈现，故不作基线）
      interval_returns: { m1: 1.11, m3: 2.0, m6: 3.0, y1: 4.0, ytd: 5.0, all: 6.0 },
    };
    await page.route(/\/nav-analysis\?range=6m/, (route) =>
      route.fulfill({ status: 200, json: SIX_M })
    );
    // 1m 既不 fulfill 也不 continue：请求恒在途，唯一可见形态就是 placeholder，不依赖竞态
    await page.route(/\/nav-analysis\?range=1m/, async () => {
      await new Promise(() => undefined);
    });
    await page.goto(PRODUCT_PATH);
    const curve = page.getByTestId("product-curve-card");
    const returns = page.getByTestId("product-returns-card");
    // 基线：6m 的独有值已上桌，且 settled 态不标 busy
    await expect(returns.getByTestId("return-m1")).toContainText("+1.11%", { timeout: 10_000 });
    await expect(curve).toHaveAttribute("aria-busy", "false");
    await expect(returns).toHaveAttribute("aria-busy", "false");

    await page.getByTestId("nav-range-1m").click();
    await expect(page.getByTestId("nav-range-1m")).toHaveAttribute("aria-pressed", "true");
    // 承重：两卡都必须显式标出「本区间正在重取」，不得让旧值无标记地冒充当前区间
    // 反证③的落点：删掉组件里 isRefetching 门控，这两句红
    await expect(curve).toHaveAttribute("aria-busy", "true");
    await expect(returns).toHaveAttribute("aria-busy", "true");
  });
});
