import { test, expect, type Locator } from "@playwright/test";
import { collectPageErrors, expectSingleLine } from "./helpers";

/**
 * #824 产品管理页列合并与净值型产品详情页。
 *
 * 数据说明（种子契约，backend/tests/seed_base.py）：产品目录含 510300.SH（沪深300ETF，
 * CN_EXCHANGE，五维 ASSET_STOCK/REGION_CN/STYLE_BALANCED/SIZE_LARGE/SEG_COMPOSITE）与
 * 虚拟产品 CASH（现金类资产）。510300.SH 有连续 2 日净值夹具（4.0000→4.2000，仅 unit_price）
 * → nav-analysis 回退市价口径（#703），与 product-detail.spec.ts 同源。
 * 断言为可见性与导航关系，不硬绑定快照日期；两端共用组件，mobile project 同跑
 * （mobile 经 src/proxy.ts 重定向到 /m 前缀，href 断言用 (m/)? 容忍）。
 */

const DETAIL_PATH = "/products/510300.SH?market=CN_EXCHANGE";

/**
 * #829 代表性行夹具：只替换列表端点 GET /api/products，不改种子。
 *
 * 港互认双码行是真实种子无法给出的最坏宽度样本（种子 hk_sale_code 恒为 NULL，
 * 见 product-display-code.spec.ts 顶部说明）；display_code 是后端派生后的 HTTP
 * 形态，故直接给出，不依赖前端重新推导。
 */
const PRODUCT_LIST_RESPONSE = {
  items: [
    {
      code: "1001767344",
      market: "HK_MUTUAL",
      name: "摩根国际债券人民币对冲",
      product_type: "OEF",
      hk_sale_code: "968050",
      display_code: "968050",
      confirm_days: 1,
      nav_lag_days: 0,
      is_qdii: false,
      data_source: "akshare",
      data_source_status: "success",
    },
    {
      code: "161017.SZ",
      market: "CN_EXCHANGE",
      name: "富国中证500指数增强(LOF)A",
      product_type: "LOF",
      hk_sale_code: null,
      display_code: "161017",
      confirm_days: 0,
      nav_lag_days: 0,
      is_qdii: false,
      data_source: "tushare",
      data_source_status: "success",
    },
    {
      code: "510300.SH",
      market: "CN_EXCHANGE",
      name: "沪深300ETF",
      product_type: "ETF",
      hk_sale_code: null,
      display_code: "510300",
      confirm_days: 0,
      nav_lag_days: 0,
      is_qdii: false,
      data_source: "tushare",
      data_source_status: "success",
    },
    {
      code: "CASH",
      market: "",
      name: "现金类资产",
      product_type: "CASH",
      hk_sale_code: null,
      display_code: "CASH",
      confirm_days: 0,
      nav_lag_days: 0,
      is_qdii: false,
      data_source_status: null,
    },
  ],
  total: 4,
  page: 1,
  page_size: 20,
};

const LAYOUT_ROWS = [
  { name: "摩根国际债券人民币对冲", expectedMeta: "968050（1001767344） · 香港互认" },
  { name: "富国中证500指数增强(LOF)A", expectedMeta: "161017 · A股场内" },
  { name: "沪深300ETF", expectedMeta: "510300 · A股场内" },
  { name: "现金类资产", expectedMeta: "CASH" },
];

async function expectProductMetaLayout(row: Locator, label: string) {
  const meta = row.getByTestId("product-cell-meta");
  // A：次行只占一行，由公共 helper 统一断言（#853）
  const single = await expectSingleLine(meta, `${label}：次行`);

  // B：次行自身不横向溢出（scrollWidth 含 overflow:hidden/ellipsis 的内容宽）
  expect(
    single.scrollWidth - single.clientWidth,
    `${label}：次行 scrollWidth=${single.scrollWidth} 应 ≤ clientWidth=${single.clientWidth}`
  ).toBeLessThanOrEqual(1);

  // C：首列不窄于该行次行内容宽（关系式，不写死像素阈值）
  const cell = await row.getByRole("cell").first().boundingBox();
  if (!cell) throw new Error(`${label}：首列没有可见盒`);
  expect(
    cell.width,
    `${label}：首列宽 ${cell.width}px 应 ≥ 次行内容宽 ${single.scrollWidth}px`
  ).toBeGreaterThanOrEqual(single.scrollWidth - 1);
}

test.describe("产品管理表格列合并（#824 Part 1）", () => {
  test("三列合并为单一「产品名称」列，次行为 显示码 · 中文市场名", async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.goto("/products");
    const card = page.getByTestId("products-list-card");
    await expect(card).toBeVisible({ timeout: 15_000 });

    // 旧三列消失、新单列存在（columnheader 角色双端同形）
    await expect(page.getByRole("columnheader", { name: "产品名称" })).toBeVisible();
    for (const removed of ["代码", "市场", "名称"]) {
      await expect(page.getByRole("columnheader", { name: removed, exact: true })).toHaveCount(0);
    }

    // 单元格两行：主行产品名、次行 `显示码 · 中文市场名`（不再吐英文枚举 CN_EXCHANGE）
    const etfRow = card.getByRole("row").filter({ hasText: "沪深300ETF" }).first();
    await expect(etfRow).toContainText("510300 · A股场内");
    await expect(etfRow).not.toContainText("CN_EXCHANGE");

    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("产品名称合并列：代表性行次行单行、不溢出、首列不窄于内容（#829）", async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.route(/\/api\/products(\?|$)/, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(PRODUCT_LIST_RESPONSE),
          })
        : route.continue()
    );
    await page.goto("/products");

    const card = page.getByTestId("products-list-card");
    await expect(card).toBeVisible({ timeout: 15_000 });

    for (const { name, expectedMeta } of LAYOUT_ROWS) {
      const row = card.getByRole("row").filter({ hasText: name });
      await expect(row, `${name}：应唯一命中一行`).toHaveCount(1);
      await expect(row.getByTestId("product-cell-meta")).toContainText(expectedMeta);
      await expectProductMetaLayout(row, name);
    }

    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("净值型产品名可点击跳详情页，虚拟产品名无链接", async ({ page }) => {
    await page.goto("/products");
    await expect(page.getByTestId("products-list-card")).toBeVisible({ timeout: 15_000 });

    // 净值型（ETF）：产品名是链接，点击落详情页
    const etfLink = page
      .getByTestId("product-detail-link")
      .filter({ hasText: "沪深300ETF" })
      .first();
    await expect(etfLink).toBeVisible();
    await etfLink.click();
    await page.waitForURL(/\/(m\/)?products\/510300\.SH\?market=CN_EXCHANGE/);
    await expect(page.getByTestId("product-basic-info-card")).toBeVisible({ timeout: 10_000 });

    // 回到列表：虚拟产品（CASH）产品名**不**是链接（无详情页）
    await page.goto("/products");
    await expect(page.getByTestId("products-list-card")).toBeVisible({ timeout: 15_000 });
    const cashRow = page
      .getByTestId("products-list-card")
      .getByRole("row")
      .filter({ hasText: "现金类资产" })
      .first();
    await expect(cashRow).toBeVisible();
    await expect(cashRow.getByTestId("product-detail-link")).toHaveCount(0);
  });
});

test.describe("净值型产品详情页（#824 Part 3）", () => {
  test("四卡齐备：基本信息 / 分类维度 / 同步操作 / 净值三卡", async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.goto(DETAIL_PATH);

    // 页头：产品名 + 显示码 · 中文市场名
    await expect(page.getByRole("heading", { name: "沪深300ETF" }).first()).toBeVisible();
    await expect(page.getByText("510300 · A股场内").first()).toBeVisible();

    // 基本信息卡：元信息字段（显示码去后缀、市场中文名、类型）
    const basic = page.getByTestId("product-basic-info-card");
    await expect(basic).toBeVisible({ timeout: 10_000 });
    await expect(basic).toContainText("510300");
    await expect(basic).toContainText("A股场内");
    await expect(basic).toContainText("ETF");

    // 分类维度卡：五维码值经字典映射为中文名（种子 510300 = ASSET_STOCK/REGION_CN/
    // STYLE_BALANCED/SIZE_LARGE/SEG_COMPOSITE，中文名 股票/中国/平衡/大盘/综合，
    // backend/app/constants/asset_dimensions.py）。五维逐维断言映射结果而非机器码——
    // 任一维映射失效会回落成英文码而变红（#825 L2 Finding 2：曾只钉住 asset_class 一维）。
    const dim = page.getByTestId("product-dimension-card");
    await expect(dim).toBeVisible();
    for (const [cn, code] of [
      ["股票", "ASSET_STOCK"],
      ["中国", "REGION_CN"],
      ["平衡", "STYLE_BALANCED"],
      ["大盘", "SIZE_LARGE"],
      ["综合", "SEG_COMPOSITE"],
    ] as const) {
      await expect(dim).toContainText(cn);
      await expect(dim).not.toContainText(code);
    }

    // 同步操作区：净值型产品有两个同步入口
    const sync = page.getByTestId("product-sync-card");
    await expect(sync).toBeVisible();
    await expect(sync.getByRole("button", { name: /同步价格/ })).toBeVisible();
    await expect(sync.getByRole("button", { name: /同步历史/ })).toBeVisible();

    // 复用 ProductNavCards 的净值三卡（与组合详情-产品详情卡同组件同 testid）
    await expect(page.getByTestId("product-curve-card")).toBeVisible();
    await expect(page.getByTestId("product-returns-card")).toBeVisible();
    await expect(page.getByTestId("product-history-card")).toBeVisible();
    // 夹具无累计净值 → 回退市价口径（#703），标题自带说明
    await expect(
      page.getByTestId("product-curve-card").getByRole("heading", { name: "收盘价走势（市价）" })
    ).toBeVisible();

    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("返回按钮回产品管理列表", async ({ page }) => {
    await page.goto(DETAIL_PATH);
    await expect(page.getByTestId("product-basic-info-card")).toBeVisible({ timeout: 10_000 });
    await page.getByRole("link", { name: "返回" }).click();
    await page.waitForURL(/\/(m\/)?products(\?|$)/);
    await expect(page.getByTestId("products-list-card")).toBeVisible({ timeout: 15_000 });
  });

  test("缺 market 参数 → EmptyState，不说成「无数据」，且不发单段自动解析请求", async ({ page }) => {
    // #825 L2 Nit：useProduct 的 enabled 门控 market——缺 market 时组件直接渲染参数
    // 空态，不得再打 GET /api/products/{code}（单段端点，一码多市场会先撞 422 重试）。
    // 判据只圈「单段」路径：/api/products/{code} 的 pathname 恰为 3 段，两段端点
    // （/api/products/{code}/{market}）与其他 /api/products 系（列表）都不命中。
    const singleSegmentProductReqs: string[] = [];
    page.on("request", (req) => {
      if (req.method() !== "GET") return;
      const url = new URL(req.url());
      const segs = url.pathname.split("/").filter(Boolean);
      if (segs.length === 3 && segs[0] === "api" && segs[1] === "products") {
        singleSegmentProductReqs.push(url.pathname);
      }
    });
    await page.goto("/products/510300.SH");
    await expect(page.getByText("缺少 market 参数")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("未找到该产品")).toHaveCount(0);
    expect(singleSegmentProductReqs, `缺 market 时仍发出了单段产品请求: ${singleSegmentProductReqs.join(" | ")}`).toHaveLength(0);
  });

  test("产品不存在 → 真·空态，不是失败态", async ({ page }) => {
    await page.goto("/products/NONEXISTENT?market=CN_EXCHANGE");
    await expect(page.getByText("未找到该产品")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("加载失败")).toHaveCount(0);
  });

  test("维度字典加载失败 → 维度卡回落机器码且全局 toast 可见，不静默伪装成功", async ({ page }) => {
    // #825 L2 Finding 2 反例：字典 500 时 dimLabel 回落 `?? dimCode` 显示英文码是
    // 现行口径（宁可露码不假翻译），但失败必须对用户可见——useAssetClassifications
    // 的 #214 全局 toast 是唯一失败信号。若将来回落改成静默吞错（无 toast）或
    // 伪装成功（空值/占位），本用例变红。判据 scoped 到字典端点，产品/nav 端点照常。
    await page.route(/\/api\/asset-classifications(\?|$)/, (route) =>
      route.fulfill({
        status: 500,
        json: { detail: { error: "INTERNAL", message: "维度字典服务暂不可用" } },
      })
    );
    await page.goto(DETAIL_PATH);

    // 失败可见：全局 toast 弹「维度字典加载失败」，含后端消息。toast 3s 自动消失，
    // 断言排在维度卡之前——route mock 的字典 500 比真实产品端点先回，toast 几乎必然
    // 先于维度卡可见出现，expect 轮询窗口内可命中。
    const toast = page.getByTestId("toast-card").filter({ hasText: "维度字典加载失败" });
    await expect(toast).toBeVisible();
    await expect(toast).toContainText("维度字典服务暂不可用");

    const dim = page.getByTestId("product-dimension-card");
    await expect(dim).toBeVisible({ timeout: 10_000 });
    // 回落形态：维度值露出机器码而非中文名
    await expect(dim).toContainText("REGION_CN");
    await expect(dim).not.toContainText("股票");
  });

  test("读取失败 → 失败态与重试，不伪装成空态（§1.2 口径①）", async ({ page }) => {
    // 判据 scoped 到单产品读端点 GET /products/{code}/{market}：本页还打维度字典与
    // nav 系端点，写宽会把它们一并打成 500、用例不再只对这一条聚合的失败呈现归因。
    await page.route(/\/api\/products\/[^/]+\/[^/]+(\?|$)/, (route) =>
      route.fulfill({
        status: 500,
        json: { detail: { error: "INTERNAL", message: "产品详情服务暂不可用" } },
      })
    );
    await page.goto(DETAIL_PATH);
    await expect(page.getByText("加载失败：产品详情服务暂不可用")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText("未找到该产品")).toHaveCount(0);
    const retry = page.getByRole("button", { name: "重试" });
    await expect(retry).toBeVisible();
    const refetched = page.waitForRequest(
      (r) => r.method() === "GET" && /\/api\/products\/[^/]+\/[^/]+(\?|$)/.test(r.url()),
      { timeout: 10_000 }
    );
    await retry.click();
    await refetched;
  });
});
