import { test, expect, type Page } from "@playwright/test";
import { E2E_ACTIVE, collectPageErrors, dialogByTitle, gotoPortfolioDetail, openPopover, productTrigger, openAppAndAuth, mockNavHistoryStress, expectNavHistoryGeometry, expectNavHistoryHScroll, measureNavDetailLayout, expectNavDetailColumns, expectTopNavDetailLayout, expectProductDetailIdentity, expectFullNameReadable, expectProductHeaderCollapsed, scrollWindowTo, captureNavCardThemes, expectNavCurveGeometry, expectNavRangeLayout, mockProductNavAnalysisCurve, navCurveInCard, setNavTextScale, NAV_CURVE_LONG } from "./helpers";

/**
 * #595 步骤③ 持仓产品详情页（M3/D3，双端共享 ProductDetailContent）。
 *
 * 数据说明（种子契约，backend/tests/seed_base.py / seed_e2e.py）：
 * - E2E_ACTIVE：单平台 HBZQ（华宝证券）持有 510300.SH（沪深300ETF）15,000 份，
 *   连续 2 日快照（D2=4.0000 / D3=4.2000，仅 unit_price，accumulated_nav/pct_change
 *   为 NULL）→ 市值 ¥63,000.00、持有/最新收益 +¥3,000.00。
 * - 净值夹具无累计净值 → nav-analysis 回退单位净值口径（`nav_basis="unit"`，#703）：
 *   场内产品即收盘价（市价），曲线有两点、标题与注记明示口径；只有曲线与六窗**都**
 *   取不到数时才是「暂无净值数据」+ 全「--」空态（#637：None 语义，历史不足或数据
 *   缺失不硬凑数）。六窗只有「成立以来」有值：两行夹具只跨一个交易日，30/90/180/365
 *   天窗口起点早于首条记录即不足窗口期（返回 None → 「--」是正确显示，不是回退失败）。
 * - 断言为可见性与关系式，不硬绑定快照日期；两端共用组件，mobile project 同跑。
 */

const PRODUCT_PATH = `/portfolio/${E2E_ACTIVE}/product/510300.SH?market=CN_EXCHANGE`;

test.describe("产品详情页", () => {
  test("页头与概览卡：短页眉+全文身份区、代码·市场、市值、份额与收益", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    const portfolioCrumb = page.getByRole("link", { name: "返回组合详情" });
    await expect(portfolioCrumb).toHaveText("E2E 活跃组合");
    await expect(portfolioCrumb).toHaveAttribute("href", /\/portfolio\/E2E_ACTIVE$/);
    // #935：短 h1「产品详情」+ 正文 h2 全名（20/16 字号档由 helper 单点钉住）
    await expectProductDetailIdentity(page, { name: "沪深300ETF", meta: "510300 · A股场内" });
    // #705 身份说明出显示码（去 tushare 后缀），不再直吐机器码 510300.SH
    await expect(page.getByText("510300.SH")).toHaveCount(0);

    const overview = page.getByTestId("product-overview-card");
    await expect(overview).toBeVisible();
    await expect(overview).toContainText("63,000.00");
    await expect(overview).toContainText("15,000.00");
    // #729 统计卡主数值与标签同侧左对齐（visual-spec §5）：修复前挂 number-cell utility
    // 自带 text-right，大字被推到卡片右侧；本断言钉住调用点的实际对齐，防退回模板串
    await expect(page.getByTestId("overview-market-value")).toHaveCSS("text-align", "left");
    // #935 A4：24px 重点金额属数值专属角色，标题迁移不得误改
    await expect(page.getByTestId("overview-market-value")).toHaveCSS("font-size", "24px");
    // #724：持仓产品卡 row3 已双端移除「持有份额」列，本条目是份额信息未丢的正面证据
    await expect(overview).toContainText("持有份额");
    // 持有收益（含 %）、累计收益、最新收益三处收益（+¥3,000.00 至少出现一次）
    await expect(overview.getByText(/\+¥3,000\.00/).first()).toBeVisible();
    await expect(overview).toContainText("(+5.00%)");
    // 单位净值行（最新一条净值，日期随种子滚动只断言数值与标签）
    await expect(overview).toContainText("单位净值 4.2000");
    // 累计收益说明默认收起，真实按钮承载可访问语义；展开只解释含已卖出实现盈亏，
    // 不再声称与持仓卡口径不同（#944 V2-16 起两者同 basis）。
    await expect(overview).toContainText("累计收益");
    const tipTrigger = overview.getByRole("button", { name: "查看累计收益说明" });
    const tipContent = page.getByTestId("info-tip-content");
    await expect(tipTrigger).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByText("累计收益含已卖出实现盈亏", { exact: true })).toHaveCount(0);
    await openPopover(page, tipTrigger, tipContent);
    await expect(tipContent).toHaveText("累计收益含已卖出实现盈亏");
    await expect(tipContent).not.toContainText("与持仓卡");
    await page.keyboard.press("Escape");
    await expect(tipContent).toHaveCount(0);
  });

  test("夹具无累计净值 → 曲线回退市价口径且区间收益率按同 basis 计算（#703）", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    const curve = page.getByTestId("product-curve-card");
    // 承重点：修复前 `accumulated_nav IS NOT NULL` 把种子里两行完好的 unit_price 整段
    // 过滤掉，本卡恒为「暂无净值数据」、六窗恒为「--」，而响应是 200（失败态不触发）。
    // 场内回退口径是收盘价（市价），标题与注记必须自己说明，不得冒充累计净值。
    await expect(
      curve.getByRole("heading", { name: "收盘价走势（市价）" })
    ).toBeVisible();
    await expect(curve.getByTestId("nav-basis-note")).toContainText("不含分红再投资");
    // 默认近6月选中
    await expect(page.getByTestId("nav-range-6m")).toHaveAttribute("aria-pressed", "true");
    await expect(curve).not.toContainText("暂无净值数据");
    await expectNavCurveGeometry(curve, "场内市价口径曲线");

    const returns = page.getByTestId("product-returns-card");
    await expect(
      returns.getByRole("heading", { name: "区间收益率（市价）" })
    ).toBeVisible();
    await expect(returns.getByTestId("returns-basis-note")).toContainText("收盘价");
    // 两行夹具 4.0000 → 4.2000 只跨一个交易日：「成立以来」以首条记录为基准 = +5.00%；
    // 30/90/180/365 天窗口的起点早于首条记录即「不足窗口期」→ None → 「--」（#637：不
    // 硬凑数，这正是回退后该有的形态，不是回退失败）。ytd 是变长窗口（clip 到首条），
    // 种子跨年与否会改基准，按「不硬绑定快照日期」的既有约定不断言其数值。
    await expect(returns.getByTestId("return-all")).toContainText("+5.00%");
    for (const field of ["m1", "m3", "m6", "y1"]) {
      await expect(returns.getByTestId(`return-${field}`)).toContainText("--");
    }
  });

  test("曲线与六窗皆空才落空态，且此时仍报累计净值口径（#703）", async ({ page }) => {
    // 空态与失败态、回退态三者必须可区分：这里 mock「产品存在但整段无价」的响应，
    // 标题回到累计净值走势、不加口径注记（空态与口径无关，不给前端第三种取值）。
    await page.route(/\/nav-analysis(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        json: {
          curve: [],
          interval_returns: { m1: null, m3: null, m6: null, y1: null, ytd: null, all: null },
          nav_basis: "accumulated",
        },
      })
    );
    await page.goto(PRODUCT_PATH);
    const curve = page.getByTestId("product-curve-card");
    await expect(curve.getByRole("heading", { name: "累计净值走势" })).toBeVisible();
    await expect(curve.getByTestId("nav-basis-note")).toHaveCount(0);
    await expect(curve).toContainText("暂无净值数据", { timeout: 10_000 });

    const returns = page.getByTestId("product-returns-card");
    await expect(returns.getByTestId("returns-basis-note")).toHaveCount(0);
    for (const field of ["m1", "m3", "m6", "y1", "ytd", "all"]) {
      await expect(returns.getByTestId(`return-${field}`)).toContainText("--");
    }
  });

  test("累计净值口径不加注记，标题与注记由 nav_basis 驱动（#703）", async ({ page }, testInfo) => {
    await page.route(/\/nav-analysis(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        json: {
          curve: [
            { date: "2020-01-02", accumulated_nav: 1.0 },
            { date: "2020-06-30", accumulated_nav: 1.5 },
          ],
          interval_returns: { m1: 1.0, m3: 2.0, m6: 3.0, y1: 4.0, ytd: 5.0, all: 6.0 },
          nav_basis: "accumulated",
        },
      })
    );
    await page.goto(PRODUCT_PATH);
    const curve = page.getByTestId("product-curve-card");
    // 场内产品拿到累计净值时仍走原口径：回退是「整段无累计净值」才触发，不按市场一刀切
    await expect(
      curve.getByRole("heading", { name: "累计净值走势" })
    ).toBeVisible();
    await expect(curve.getByTestId("nav-basis-note")).toHaveCount(0);
    await expectNavCurveGeometry(curve, "累计净值口径曲线", { requireReferenceLabel: true });
    await expect(
      page
        .getByTestId("product-returns-card")
        .getByRole("heading", { name: "区间收益率", exact: true })
    ).toBeVisible();
    await captureNavCardThemes(page, curve, testInfo.outputPath("nav941-product-accumulated-nav"));
  });

  test("区间 Tab 切换", async ({ page }) => {
    const navRequest = (range: string) => page.waitForRequest((request) => {
      const url = new URL(request.url());
      return url.pathname.endsWith('/nav-analysis') && url.searchParams.get('range') === range;
    });
    const initial = navRequest('6m');
    await page.goto(PRODUCT_PATH);
    const initialUrl = new URL((await initial).url());
    expect(initialUrl.pathname).toContain('/510300.SH/CN_EXCHANGE/');
    await expect(page.getByTestId("nav-range-6m")).toHaveAttribute("aria-pressed", "true");

    for (const key of ["1m", "3m", "1y"]) {
      const requested = navRequest(key);
      await page.getByTestId(`nav-range-${key}`).click();
      const url = new URL((await requested).url());
      expect(url.pathname).toContain('/510300.SH/CN_EXCHANGE/');
      expect(url.searchParams.get('range')).toBe(key);
      await expect(page.getByTestId(`nav-range-${key}`)).toHaveAttribute("aria-pressed", "true");
      for (const other of ["1m", "3m", "6m", "1y"].filter((item) => item !== key)) {
        await expect(page.getByTestId(`nav-range-${other}`)).toHaveAttribute("aria-pressed", "false");
      }
    }
    await page.getByTestId("nav-range-6m").click();
    await expect(page.getByTestId("nav-range-6m")).toHaveAttribute("aria-pressed", "true");
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
    // S7：行点击 → 平台-产品详情页（步骤④接线）；#935 起平台上下文与产品名分别断言
    await rows.first().getByRole("link").click();
    await page.waitForURL(/\/platforms\/HBZQ\/products\/510300\.SH/);
    await expectProductDetailIdentity(page, {
      name: "沪深300ETF",
      meta: "510300 · A股场内",
      platformContext: "华宝证券",
    });
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
    // #907 移动卡化后无表格：双形态定位（桌面 tr / 移动卡），只断「列表非空」
    await expect(page.locator('table tbody tr, [data-testid="trade-card"]').first()).toBeVisible({ timeout: 10_000 });
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
    // 产品与市场已预填：触发框回显「…510300 · A股场内」（#705 显示码去后缀）。名称缓存
    // 未命中时只回显显示码（SearchableProductSelect 懒加载），故断显示码与市场后缀两段、不断产品名
    const prefilledTrigger = productTrigger(dlg, "510300");
    await expect(prefilledTrigger).toBeVisible();
    await expect(prefilledTrigger).not.toContainText("510300.SH");
    await expect(productTrigger(dlg, "A股场内")).toBeVisible();
    // 预填值仍可当场改选（验收第 5 条）：预填只作 useState 初值、无 effect 回灌，
    // 故改方向后既有预填不被重置
    await dlg.getByRole("button", { name: "卖出" }).click();
    await expect(dlg.getByTestId("sell-arrival-hint")).toBeVisible();
    await expect(productTrigger(dlg, "510300")).toBeVisible();
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
    await expect(productTrigger(dlg, "510300")).toBeVisible();
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
    await expect(productTrigger(dlg, "510300")).toBeVisible();
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
    // 见 frontend/e2e/AGENTS.md §1 与 frontend/AGENTS.md §5「目检」）。「不传 className」靠代码评审与三处渲染同一个
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

  test("未知产品 → 真·空态「未找到该产品持仓」，不是失败态（#682 待确认项 4）", async ({ page }) => {
    // 与上一条配对：那条钉「失败不落空态」，本条钉「真空不落失败态」。平台两页早有同形用例
    // （platform-detail.spec.ts 的「未知平台」「未知产品」），产品页此前缺位——把该空集合分支
    // 改成报错、或把「未找到」文案改掉，都不会让任何用例变红。不 mock，走真实后端的空结果。
    await page.goto(`/portfolio/${E2E_ACTIVE}/product/NONEXISTENT?market=CN_EXCHANGE`);
    await expect(page.getByText("未找到该产品持仓")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("加载失败")).toHaveCount(0);
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

  // #719：现金行读得出「从哪来、到哪去」，且不再把现金腿渲成「份额 @ 净值」
  // （旧副行的 `shares !== undefined` 判据对 shares=null 成立 → `-- @ 1.0000`，
  //  虚假净值 1.0000 把现金说成净值型产品）
  test("交易记录卡：现金行标注资金流向并补出对手腿", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    const card = page.getByTestId("product-trades-card");
    const rows = card.getByTestId("product-trade-row");
    // 行集 = 本组合的现金腿：2 条调仓扣款腿 + 1 条申购确认腿
    await expect(rows).toHaveCount(3);
    // 调仓扣款腿：主行说资金流向 + 本平台，副行说去处（对手基金腿 + 对手腿状态）
    await expect(rows.filter({ hasText: "现金扣款 · 华宝证券" })).toHaveCount(2);
    await expect(rows.filter({ hasText: "↳ 买入 沪深300ETF" })).toHaveCount(2);
    // 申赎腿：对手方是 subscription 记录、不是 trade 行 → 保持来源标注，不补 ↳
    await expect(rows.filter({ hasText: "现金 · 申赎确认" })).toHaveCount(1);
    await expect(rows.filter({ hasText: "↳" })).toHaveCount(2);
    // 现金腿无份额无净值语义：整卡不出现「份额 @ 净值」形态（含被渲成 1.0000 的 price）
    await expect(card).not.toContainText("份 @");
    await expect(card).not.toContainText("-- @");
    await expect(card).not.toContainText("1.0000");
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
    test.setTimeout(60_000);
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
      nav_basis: "accumulated",
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

    // 直接派发 click，避免 WebKit 在并行负载下丢失物理点击；承重断言仍是区间状态与在途标记。
    await page.getByTestId("nav-range-1m").dispatchEvent("click");
    await expect(page.getByTestId("nav-range-1m")).toHaveAttribute("aria-pressed", "true", {
      timeout: 30_000,
    });
    // 承重：两卡都必须显式标出「本区间正在重取」，不得让旧值无标记地冒充当前区间
    // 反证③的落点：删掉组件里 isRefetching 门控，这两句红
    await expect(curve).toHaveAttribute("aria-busy", "true");
    await expect(returns).toHaveAttribute("aria-busy", "true");
  });
});

test.describe("净值曲线与区间布局（#941）", () => {
  test("A4 产品区间：320/360/390 与 125% 下文字不拆行，常规宽度四颗同排", async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    await mockProductNavAnalysisCurve(page);
    await page.goto(PRODUCT_PATH);
    const card = page.getByTestId("product-curve-card");
    const group = card.getByRole("group", { name: "净值区间" });
    const height = page.viewportSize()?.height ?? 720;
    const isMobile = testInfo.project.name === "mobile";
    const normalWidth = isMobile ? 390 : 1280;
    const widths = isMobile ? [320, 360, 390] : [768, 1024, 1280];

    await page.setViewportSize({ width: normalWidth, height });
    await expectNavRangeLayout(group, `产品区间 ${normalWidth} 常规字号`, { fontSize: 14, singleRow: true });
    for (const scale of [100, 125] as const) {
      await setNavTextScale(page, scale);
      for (const width of widths) {
        await page.setViewportSize({ width, height });
        await expect(async () => {
          await expectNavCurveGeometry(card, `产品图表 ${width}px/${scale}%`, { minSvgWidth: 0 });
          await expectNavRangeLayout(group, `产品区间 ${width}px/${scale}%`, {
            fontSize: scale === 125 ? 17.5 : 14,
          });
        }).toPass({ timeout: 5_000 });
      }
    }
    await page.setViewportSize({ width: isMobile ? 320 : 768, height });
    await expectNavCurveGeometry(card, `产品图表 ${isMobile ? 320 : 768}px/125% 最终态`, { minSvgWidth: 0 });
    await expectNavRangeLayout(group, `产品区间 ${isMobile ? 320 : 768}px/125% 最终态`, { fontSize: 17.5 });
    await captureNavCardThemes(page, card, testInfo.outputPath(`nav941-product-ranges-${isMobile ? 320 : 768}-125`));
  });

  test("A6/A9 长序列保持日期/刻度/Tooltip，单点与恒值不新增报错", async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const errors = collectPageErrors(page);
    await mockProductNavAnalysisCurve(page, "accumulated", NAV_CURVE_LONG);
    await page.goto(PRODUCT_PATH);
    const card = page.getByTestId("product-curve-card");
    const curve = navCurveInCard(card);
    await expectNavCurveGeometry(card, "180 点长序列", { requireReferenceLabel: true });
    await curve.scrollIntoViewIfNeeded();
    const box = await curve.boundingBox();
    if (!box) throw new Error("长序列曲线没有可见盒");
    await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.45);
    await expect(card.locator(".recharts-tooltip-wrapper")).toBeVisible();
    await expect(curve.locator(".recharts-active-dot")).toBeVisible();
    await captureNavCardThemes(page, card, testInfo.outputPath("nav941-product-long-series"));

    const boundaries = [
      { name: "单点", points: [{ date: "2024-01-02", nav: 1 }], longTicks: false },
      { name: "恒值", points: [
        { date: "2024-01-02", nav: 1 },
        { date: "2024-02-01", nav: 1 },
        { date: "2024-03-01", nav: 1 },
      ], longTicks: false },
      { name: "长刻度", points: [
        { date: "2024-01-02", nav: 12345.6789 },
        { date: "2024-02-01", nav: 12000 },
        { date: "2024-03-01", nav: 13000 },
      ], longTicks: true },
    ];
    for (const fixture of boundaries) {
      await mockProductNavAnalysisCurve(page, "accumulated", fixture.points);
      await page.goto(PRODUCT_PATH);
      await expect(card.getByRole("heading", { name: "累计净值走势" })).toBeVisible();
      const geometry = await expectNavCurveGeometry(card, fixture.name, {
        maxLeftReserve: fixture.longTicks ? 160 : 60,
        requireReferenceLabel: !fixture.longTicks,
      });
      if (fixture.longTicks) {
        expect(geometry.leftReserve, "长格式化刻度必须自动扩宽 Y 轴").toBeGreaterThan(60);
        expect(geometry.yTickTexts.some((text) => text.includes(","))).toBe(true);
        await captureNavCardThemes(page, card, testInfo.outputPath("nav941-product-long-ticks"));
      }
    }
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });
});

test.describe("历史净值窄宽布局（#934）", () => {
  test("A1/A3 列对齐与文字间距：桌面连续尺寸 / 移动多档", async ({ page }, testInfo) => {
    test.setTimeout(60_000);
    await openAppAndAuth(page);
    const { dates } = await mockNavHistoryStress(page);
    await page.goto(PRODUCT_PATH);
    const history = page.getByTestId("product-history-card");
    await expect(history.getByTestId("product-history-row")).toHaveCount(5, { timeout: 10_000 });

    const isMobile = testInfo.project.name === "mobile";
    const height = page.viewportSize()?.height ?? 720;
    const widths = isMobile ? [390, 375, 320] : [600, 640, 700, 768, 900, 1024, 1152, 1280];
    for (const width of widths) {
      await page.setViewportSize({ width, height });
      // TopNav 在 lg 边界切换显示，等布局稳定再断言（#934/#944 A2 口径）
      await expect(async () => {
        await expectNavHistoryGeometry(history, "product", `视口${width}`);
      }).toPass({ timeout: 5_000 });
    }
    const dateTexts = await history
      .getByTestId("product-history-row")
      .evaluateAll((rows) => rows.map((r) => (r.children[0].textContent ?? "").trim()));
    expect(dateTexts, "日期降序与 API 原值一致").toEqual(dates.slice(0, 5));
  });

  test("A6 查看更多长值同列：卡片增高、无卡内纵滚、末页按钮消失", async ({ page }) => {
    await openAppAndAuth(page);
    const { dates } = await mockNavHistoryStress(page);
    await page.goto(PRODUCT_PATH);
    const history = page.getByTestId("product-history-card");
    const rows = history.getByTestId("product-history-row");
    await expect(rows).toHaveCount(5, { timeout: 10_000 });
    const before = await history.boundingBox();
    await expectNavHistoryGeometry(history, "product", "首屏");

    await history.getByTestId("history-load-more").click();
    // #763：低核数机器双 worker 下 webkit 帧率饥饿会拖慢点击→取数→渲染链路，只放宽预算不改条件
    await expect(rows).toHaveCount(7, { timeout: 15_000 });
    const g = await expectNavHistoryGeometry(history, "product", "加载更多后");
    expect(g.dateTexts, "加载后日期降序与 API 原值一致").toEqual(dates);
    await expect(rows.nth(5)).toContainText("12,345.6789");
    await expect(rows.nth(5)).toContainText("-100.00%");
    const after = await history.boundingBox();
    if (!before || !after) throw new Error("历史卡没有可见盒，无法比较高度");
    expect(after.height, "新增行应撑高卡片（页面纵滚承接）").toBeGreaterThan(before.height);
    await expect(history.getByTestId("history-load-more")).toHaveCount(0);
  });

  test("A2 桌面父布局：容器 799/800 降栏与顶部导航几何", async ({ page }, testInfo) => {
    test.skip(
      testInfo.project.name === "mobile",
      "移动 variant 无桌面双栏布局（功能确实缺，#934 S4 仅改 desktop 分支）"
    );
    test.setTimeout(60_000);
    await page.goto(PRODUCT_PATH);
    const history = page.getByTestId("product-history-card");
    await expect(history).toBeVisible({ timeout: 10_000 });

    for (const width of [700, 1024, 1280]) {
      await page.setViewportSize({ width, height: 720 });
      await expect(async () => {
        const m = await measureNavDetailLayout(history, `视口${width}`);
        expectNavDetailColumns(m, `视口${width}`);
      }).toPass({ timeout: 5_000 });
    }

    // 在同一 <lg（main p-4）档内反推边界，避免跨 lg 后 padding 从 32px 变 64px。
    await page.setViewportSize({ width: 900, height: 720 });
    const base = await measureNavDetailLayout(history, "900 基线");
    const viewportAt800 = 900 + (800 - base.containerWidth);
    await page.setViewportSize({ width: viewportAt800, height: 720 });
    await expect(async () => {
      const m = await measureNavDetailLayout(history, "容器800");
      expect(m.containerWidth).toBe(800);
      expectNavDetailColumns(m, "容器800");
      expect(m.tracks.length).toBe(2);
    }).toPass({ timeout: 5_000 });
    await page.setViewportSize({ width: viewportAt800 - 1, height: 720 });
    await expect(async () => {
      const m = await measureNavDetailLayout(history, "容器799");
      expect(m.containerWidth).toBe(799);
      expectNavDetailColumns(m, "容器799");
      expect(m.tracks.length).toBe(1);
    }).toPass({ timeout: 5_000 });

    await page.setViewportSize({ width: 1024, height: 720 });
    await expectTopNavDetailLayout(page, history, "1024 顶部导航");
  });

  test("A5 局部横滚：首末列可达、表头同步、键盘可滚、标题不动", async ({ page }, testInfo) => {
    test.setTimeout(60_000);
    const isMobile = testInfo.project.name === "mobile";
    await openAppAndAuth(page);
    await mockNavHistoryStress(page);
    await page.goto(PRODUCT_PATH);
    const history = page.getByTestId("product-history-card");
    await expect(history.getByTestId("product-history-row")).toHaveCount(5, { timeout: 10_000 });

    const outside = await history.evaluate((card) => {
      const scroll = card.querySelector('[data-testid="product-history-scroll"]');
      const more = card.querySelector('[data-testid="history-load-more"]');
      const h3 = card.querySelector("h3");
      return !!scroll && !!more && !!h3 && !scroll.contains(more) && !scroll.contains(h3);
    });
    expect(outside, "标题与查看更多应位于横滚区域之外").toBe(true);

    await history.getByTestId("history-load-more").click();
    await expect(history.getByTestId("product-history-row")).toHaveCount(7, { timeout: 15_000 }); // #763 预算放宽
    const width = isMobile ? 320 : 400;
    await page.setViewportSize({ width, height: page.viewportSize()?.height ?? 720 });
    await expect(async () => {
      await expectNavHistoryHScroll(history, "product", `宽${width}`, { keyboard: !isMobile });
    }).toPass({ timeout: 5_000 });

    // 目检证据：窄宽 + 长值，浅/暗双主题（A8；next-themes 默认 system 档）。
    // 按卡元素截图（fullPage 会把固定 BottomNav 叠进卡内）；显式归零再拍，
    // 另拍滚到最右一张证明末列可达。
    const scroll = history.getByTestId("product-history-scroll");
    // 卡片滚到视口顶部附近，避免固定 BottomNav 遮住卡底行（元素截图按视口合成）
    await history.evaluate((el) => {
      window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 80);
    });
    await scroll.evaluate((el) => {
      el.scrollLeft = 0;
    });
    await expect.poll(() => scroll.evaluate((el) => el.scrollLeft)).toBe(0);
    await history.screenshot({ path: testInfo.outputPath(`nav-history-${width}-light.png`) });
    await page.emulateMedia({ colorScheme: "dark" });
    await history.screenshot({ path: testInfo.outputPath(`nav-history-${width}-dark.png`) });
    await scroll.evaluate((el) => {
      el.scrollLeft = el.scrollWidth;
    });
    await history.screenshot({ path: testInfo.outputPath(`nav-history-${width}-dark-end.png`) });
  });
});

/**
 * #935 全文身份与吸顶（组合内产品详情）。长名夹具只改 holdings/by-product 响应的
 * product_name（route.fetch 回读真实种子再改名），不向 E2E 库注入、不改种子契约。
 */
const LONG_NAME_935 = "华泰柏瑞沪深300ETF联接基金A类人民币份额长名称样例";

async function mockLongProductName935(page: Page) {
  await page.route(/\/holdings\/by-product(\?|$)/, async (route) => {
    const res = await route.fetch();
    const json = await res.json();
    for (const p of json.products ?? []) {
      if (p.product_code !== "CASH") p.product_name = LONG_NAME_935;
    }
    await route.fulfill({ response: res, json });
  });
}

test.describe("全文身份与吸顶（#935）", () => {
  test("A1/A2/A7 长名多档宽度全文可读，末尾不裁剪", async ({ page }, testInfo) => {
    test.setTimeout(60_000);
    await mockLongProductName935(page);
    await page.goto(PRODUCT_PATH);
    await expect(page.getByTestId("product-full-name")).toHaveText(LONG_NAME_935, { timeout: 10_000 });

    const isMobile = testInfo.project.name === "mobile";
    const height = page.viewportSize()?.height ?? 720;
    const widths = isMobile ? [320, 375, 390, 430] : [1024, 1280];
    for (const width of widths) {
      await page.setViewportSize({ width, height });
      const m = await expectFullNameReadable(page, `视口${width}`);
      if (isMobile && width === 320) {
        expect(m.lines, "320px 下长名应自然换行（≥2 行），不限行不缩字号").toBeGreaterThanOrEqual(2);
      }
    }
  });

  test("A5 吸顶折叠只剩返回+短标题：阈值两侧、下滑、回顶", async ({ page }) => {
    await page.goto(PRODUCT_PATH);
    await expectProductDetailIdentity(page, { name: "沪深300ETF", meta: "510300 · A股场内" });
    // 身份区非吸顶：随正文滚走
    await expect(page.getByTestId("product-identity")).toHaveCSS("position", "static");

    // 阈值两侧：32px 不折叠、33px 折叠（window.scrollY > 32 契约不变）
    await scrollWindowTo(page, 32);
    await expect(page.getByTestId("page-header").getByRole("navigation", { name: "面包屑" })).toBeVisible();
    await scrollWindowTo(page, 33);
    await expectProductHeaderCollapsed(page, {
      backAria: "返回组合列表",
      absentTexts: ["沪深300ETF", "510300"],
    });

    // 持续下滑（滚到页底，避免写死 scrollY 超出短页可滚范围）：身份区滚出视口，页眉仍只有返回+短标题
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(32);
    const identityTop = await page.getByTestId("product-identity").evaluate((el) => el.getBoundingClientRect().top);
    expect(identityTop, "身份区应随正文滚走（顶缘 < 0）").toBeLessThan(0);
    await expectProductHeaderCollapsed(page, {
      backAria: "返回组合列表",
      absentTexts: ["沪深300ETF", "510300"],
    });

    // 回顶：展开态恢复，身份区可再次完整阅读
    await scrollWindowTo(page, 0);
    await expectProductDetailIdentity(page, { name: "沪深300ETF", meta: "510300 · A股场内" });
    await expectFullNameReadable(page, "回顶后");
  });
});
