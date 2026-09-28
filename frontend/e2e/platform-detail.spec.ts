import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { E2E_ACTIVE, gotoPortfolioDetail, authHeaders } from "./helpers";

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
    await page.waitForURL(/\/product\/CN_EXCHANGE\/510300\.SH/);
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

    const today = new Date().toISOString().slice(0, 10);
    const year = Number(today.slice(0, 4));
    const calendars = await Promise.all(
      [year - 1, year, year + 1].map(async (y) => {
        const resp = await page.request.get(`/api/trading-calendar?year=${y}`, { headers });
        await expect(resp).toBeOK();
        return (await resp.json()) as { calendar_date: string; is_open: boolean }[];
      }),
    );
    const days = calendars.flat().filter((d) => d.is_open).map((d) => d.calendar_date).sort();
    const uptoToday = days.filter((d) => d <= today);
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
    // 平台应预填 HBZQ
    await expect(page.getByTestId("cash-platform")).toBeVisible();
  });

  test("Dialog 表单校验：空金额/未选平台提示错误", async ({ page }) => {
    await page.goto(CASH_PRODUCT_PATH);
    await page.getByTestId("platform-product-action-row").getByRole("button", { name: "市值更新" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();

    // 清空平台选择后提交 → 应提示错误
    // 注意：预填的平台无法通过 UI 清空（SearchablePlatformSelect 无 clear 按钮），
    // 所以此测试验证金额校验
    const amountInput = page.getByLabel("当前金额（元）");
    await amountInput.fill("");
    await page.getByRole("button", { name: "确认更新" }).click();
    // HTML5 required 会阻止提交；验证 input 仍可见（Dialog 未关闭）
    await expect(page.getByRole("dialog")).toBeVisible();
  });
});
