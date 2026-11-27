import { test, expect, type Locator, type Page, type Request } from "@playwright/test";
import { subMonths } from "date-fns";
import { collectPageErrors, expectSingleLine, expectDialogSingleScroll, dialogByTitle, pickCalendarDay, toISODate, openAppAndAuth, mockNavHistoryStress, expectNavHistoryGeometry, expectNavHistoryHScroll, measureNavDetailLayout, expectNavDetailColumns, expectTopNavDetailLayout, expectProductDetailIdentity, expectFullNameReadable, expectProductHeaderCollapsed, scrollWindowTo, captureNavCardThemes, expectNavCurveGeometry, expectNavRangeLayout } from "./helpers";

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
      is_active: true,
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
      is_active: true,
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
      is_active: true,
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
      is_active: true,
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

  // C：产品名称列不窄于该行次行内容宽（关系式，不写死像素阈值）。
  // #882 起批量勾选列位于首列，故按组件锚点取名称列而非位置首列；断言意图不变（#829 防名称列挤压）
  const cell = await row.getByTestId("product-name-cell").boundingBox();
  if (!cell) throw new Error(`${label}：产品名称列没有可见盒`);
  expect(
    cell.width,
    `${label}：产品名称列宽 ${cell.width}px 应 ≥ 次行内容宽 ${single.scrollWidth}px`
  ).toBeGreaterThanOrEqual(single.scrollWidth - 1);
}

// #904 P1d：移动卡化后行=卡片（product-card），桌面仍表格行；.or 双形态单定位
function productRow(card: Locator, name: string) {
  return card
    .getByRole("row")
    .filter({ hasText: name })
    .first()
    .or(card.getByTestId("product-card").filter({ hasText: name }).first());
}

test.describe("产品管理表格列合并（#824 Part 1）", () => {
  test("三列合并为单一「产品名称」列，次行为 显示码 · 中文市场名", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await page.goto("/products");
    const card = page.getByTestId("products-list-card");
    await expect(card).toBeVisible({ timeout: 15_000 });

    if (testInfo.project.name === "mobile") {
      // 卡形态无表头：旧列名不得作为独立 kv 标签出现（名称/代码/市场已并入名称单元格）
      for (const removed of ["代码", "市场", "名称"]) {
        await expect(card.getByText(removed, { exact: true })).toHaveCount(0);
      }
    } else {
      // 旧三列消失、新单列存在（columnheader 角色双端同形）
      await expect(page.getByRole("columnheader", { name: "产品名称" })).toBeVisible();
      for (const removed of ["代码", "市场", "名称"]) {
        await expect(page.getByRole("columnheader", { name: removed, exact: true })).toHaveCount(0);
      }
    }

    // 单元格两行：主行产品名、次行 `显示码 · 中文市场名`（不再吐英文枚举 CN_EXCHANGE）
    const etfRow = productRow(card, "沪深300ETF");
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
      const row = productRow(card, name);
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
    const cashRow = productRow(page.getByTestId("products-list-card"), "现金类资产");
    await expect(cashRow).toBeVisible();
    await expect(cashRow.getByTestId("product-detail-link")).toHaveCount(0);
  });
});

test.describe("净值型产品详情页（#824 Part 3）", () => {
  test("四卡齐备：基本信息 / 分类维度 / 同步操作 / 净值三卡", async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.goto(DETAIL_PATH);

    // #935：短 h1「产品详情」+ 正文 h2 全名 + 显示码 · 中文市场名（字号档由 helper 钉住）
    await expectProductDetailIdentity(page, { name: "沪深300ETF", meta: "510300 · A股场内" });

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

    // 同步操作区（#856）：周期下拉 + 同步历史按钮，「同步价格」入口已下线
    const sync = page.getByTestId("product-sync-card");
    await expect(sync).toBeVisible();
    await expect(sync.getByTestId("sync-period-select")).toBeVisible();
    await expect(sync.getByTestId("sync-history-button")).toBeVisible();
    await expect(sync.getByRole("button", { name: /同步价格/ })).toHaveCount(0);
    // 默认周期「成立以来」
    await expect(sync.getByTestId("sync-period-select")).toContainText("成立以来");

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

/**
 * #856 详情页管理动作夹具（#935 起动作行在正文身份区下方）：只替换单产品读/写端点
 * GET·PUT·DELETE /products/{code}/{market}，nav 系与维度字典照常 hit 真实种子
 * （510300.SH 有净值夹具）。is_active 由测试内可变变量驱动，模拟后端 PUT 后 GET
 * 回读翻转——不向 E2E 库直接注入（种子契约不可改）。
 */
const PRODUCT_DETAIL_RESPONSE = {
  code: "510300.SH",
  market: "CN_EXCHANGE",
  name: "沪深300ETF",
  product_type: "ETF",
  display_code: "510300",
  hk_sale_code: null,
  confirm_days: 0,
  nav_lag_days: 0,
  is_qdii: false,
  is_active: true,
  data_source: "tushare",
  data_source_status: "success",
  last_sync_at: null,
  created_at: "2025-01-01T00:00:00",
  asset_class_code: "ASSET_STOCK",
  region_code: "REGION_CN",
  style_code: "STYLE_BALANCED",
  size_code: "SIZE_LARGE",
  segment_code: "SEG_COMPOSITE",
};

const DETAIL_ENDPOINT = /\/api\/products\/510300\.SH\/CN_EXCHANGE(\?|$)/;
const SYNC_HISTORY_ENDPOINT =
  /\/api\/market-data\/products\/510300\.SH\/CN_EXCHANGE\/sync-history(\?|$)/;

test.describe("产品行「更多」菜单与停用徽章（#856 块1/块2）", () => {
  // 列表端点夹具复用 #829 的 PRODUCT_LIST_RESPONSE（已含 is_active:true）
  async function mockList(page: Page) {
    await page.route(/\/api\/products(\?|$)/, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PRODUCT_LIST_RESPONSE) })
        : route.continue()
    );
  }

  test("每行仅一个「更多操作」按钮，菜单恰为 编辑/删除（同步/走势已下线），虚拟产品行同形", async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockList(page);
    await page.goto("/products");
    const card = page.getByTestId("products-list-card");
    await expect(card).toBeVisible({ timeout: 15_000 });

    // 净值型（ETF）与虚拟产品（CASH）行同形：行内唯一按钮即「更多操作」
    for (const name of ["沪深300ETF", "现金类资产"]) {
      const row = productRow(card, name);
      await expect(row.getByRole("button")).toHaveCount(1);
      await row.getByRole("button", { name: "更多操作" }).click();
      const menu = page.getByRole("menu");
      await expect(menu.getByRole("menuitem")).toHaveCount(2);
      await expect(menu.getByRole("menuitem", { name: "编辑" })).toBeVisible();
      await expect(menu.getByRole("menuitem", { name: "删除" })).toBeVisible();
      // 旧行内同步/走势入口彻底下线
      for (const gone of ["同步价格", "同步历史", "走势"]) {
        await expect(menu.getByRole("menuitem", { name: gone })).toHaveCount(0);
      }
      await page.keyboard.press("Escape");
      await expect(menu).toHaveCount(0);
    }
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("编辑菜单项打开产品表单对话框", async ({ page }) => {
    await mockList(page);
    await page.goto("/products");
    const card = page.getByTestId("products-list-card");
    await expect(card).toBeVisible({ timeout: 15_000 });
    const row = productRow(card, "沪深300ETF");
    await row.getByRole("button", { name: "更多操作" }).click();
    await page.getByRole("menuitem", { name: "编辑" }).click();
    await expect(dialogByTitle(page, "编辑产品")).toBeVisible();
  });

  test("删除菜单项经确认对话框，确认后该行消失", async ({ page }) => {
    // 状态化列表夹具：DELETE 后 GET 回读少一行，验证「确认→行消失」闭环
    let items = [...PRODUCT_LIST_RESPONSE.items];
    await page.route(/\/api\/products(\?|$)/, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items, total: items.length, page: 1, page_size: 20 }) })
    );
    await page.route(DETAIL_ENDPOINT, (route) => {
      if (route.request().method() === "DELETE") {
        items = items.filter((i) => i.code !== "510300.SH");
        return route.fulfill({ status: 200, json: { message: "产品已删除" } });
      }
      return route.continue();
    });
    await page.goto("/products");
    const card = page.getByTestId("products-list-card");
    await expect(card).toBeVisible({ timeout: 15_000 });
    const row = productRow(card, "沪深300ETF");
    await expect(row).toHaveCount(1);
    await row.getByRole("button", { name: "更多操作" }).click();
    await page.getByRole("menuitem", { name: "删除" }).click();

    const confirm = page.getByRole("alertdialog").filter({ hasText: "删除产品" });
    await expect(confirm).toBeVisible();
    const deleted = page.waitForRequest((r) => r.method() === "DELETE" && DETAIL_ENDPOINT.test(r.url()));
    await confirm.getByRole("button", { name: "删除", exact: true }).click();
    await deleted;
    await expect(productRow(card, "沪深300ETF")).toHaveCount(0);
  });

  test("停用产品主行内联「已停用」徽章，启用产品无", async ({ page }) => {
    // 把 ETF 行置为停用，其余保持启用：徽章只应出现在停用行
    const inactiveList = {
      ...PRODUCT_LIST_RESPONSE,
      items: PRODUCT_LIST_RESPONSE.items.map((i) =>
        i.code === "510300.SH" ? { ...i, is_active: false } : i
      ),
    };
    await page.route(/\/api\/products(\?|$)/, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(inactiveList) })
        : route.continue()
    );
    await page.goto("/products");
    const card = page.getByTestId("products-list-card");
    await expect(card).toBeVisible({ timeout: 15_000 });
    const inactiveRow = productRow(card, "沪深300ETF");
    await expect(inactiveRow.getByText("已停用")).toBeVisible();
    const activeRow = productRow(card, "富国中证500指数增强(LOF)A");
    await expect(activeRow.getByText("已停用")).toHaveCount(0);
  });
});

test.describe("详情页管理动作与同步周期（#856 块2/块3）", () => {
  test("编辑动作打开产品表单对话框", async ({ page }) => {
    await page.route(DETAIL_ENDPOINT, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, json: PRODUCT_DETAIL_RESPONSE })
        : route.continue()
    );
    await page.goto(DETAIL_PATH);
    await expect(page.getByTestId("product-basic-info-card")).toBeVisible({ timeout: 10_000 });
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    const dlg = dialogByTitle(page, "编辑产品");
    await expect(dlg).toBeVisible();
    // #910：详情编辑入口复用同一滚动契约（长表单仅内层滚动、保存可达），
    // 完整表单行为由 product-data-source.spec.ts 覆盖，此处不重复
    await expectDialogSingleScroll(dlg, "详情编辑", {
      bottomAction: dlg.getByRole("button", { name: "保存修改" }),
    });
  });

  test("停用/启用经确认对话框即时翻转：身份区徽章与按钮文案随 is_active 切换", async ({ page }) => {
    let active = true;
    await page.route(DETAIL_ENDPOINT, (route) => {
      const method = route.request().method();
      if (method === "GET") {
        return route.fulfill({ status: 200, json: { ...PRODUCT_DETAIL_RESPONSE, is_active: active } });
      }
      if (method === "PUT") {
        active = route.request().postDataJSON().is_active;
        return route.fulfill({ status: 200, json: { ...PRODUCT_DETAIL_RESPONSE, is_active: active } });
      }
      return route.continue();
    });
    await page.goto(DETAIL_PATH);
    await expect(page.getByTestId("product-basic-info-card")).toBeVisible({ timeout: 10_000 });

    // 启用态：无徽章，头部按钮为「停用」
    await expect(page.getByTestId("product-inactive-badge")).toHaveCount(0);
    await page.getByRole("button", { name: "停用", exact: true }).click();
    const deactivate = page.getByRole("alertdialog").filter({ hasText: "停用产品" });
    await expect(deactivate).toBeVisible();
    await deactivate.getByRole("button", { name: "停用", exact: true }).click();

    // 停用后：徽章出现，按钮翻成「启用」
    await expect(page.getByTestId("product-inactive-badge")).toBeVisible();
    await expect(page.getByRole("button", { name: "停用", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "启用", exact: true }).click();
    const activate = page.getByRole("alertdialog").filter({ hasText: "启用产品" });
    await expect(activate).toBeVisible();
    await activate.getByRole("button", { name: "启用", exact: true }).click();

    // 重新启用：徽章消失，按钮回到「停用」
    await expect(page.getByTestId("product-inactive-badge")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "停用", exact: true })).toBeVisible();
  });

  test("删除经确认对话框，确认后回产品管理列表", async ({ page }) => {
    await page.route(DETAIL_ENDPOINT, (route) => {
      const method = route.request().method();
      if (method === "GET") return route.fulfill({ status: 200, json: PRODUCT_DETAIL_RESPONSE });
      if (method === "DELETE") return route.fulfill({ status: 200, json: { message: "产品已删除" } });
      return route.continue();
    });
    await page.goto(DETAIL_PATH);
    await expect(page.getByTestId("product-basic-info-card")).toBeVisible({ timeout: 10_000 });
    await page.getByRole("button", { name: "删除", exact: true }).click();
    const confirm = page.getByRole("alertdialog").filter({ hasText: "删除产品" });
    await expect(confirm).toBeVisible();
    await confirm.getByRole("button", { name: "删除", exact: true }).click();
    await page.waitForURL(/\/(m\/)?products(\?|$)/);
    await expect(page.getByTestId("products-list-card")).toBeVisible({ timeout: 15_000 });
  });

  test("同步周期：默认成立以来，自定义显示开始日期且未选禁用、选后启用", async ({ page }) => {
    await page.route(DETAIL_ENDPOINT, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, json: PRODUCT_DETAIL_RESPONSE })
        : route.continue()
    );
    await page.goto(DETAIL_PATH);
    const sync = page.getByTestId("product-sync-card");
    await expect(sync).toBeVisible({ timeout: 10_000 });
    await expect(sync.getByTestId("sync-period-select")).toContainText("成立以来");
    await expect(sync.getByTestId("sync-history-button")).toBeEnabled();

    // 切自定义：出现开始日期 DatePicker，未选时同步按钮禁用。
    // 触发器按 id 锚定（#640 N-2 同 platform-detail 的 button#cash-update-date）：
    // 选日后按钮文案由 placeholder 变为日期，按 {name:'开始日期'} 取会在 pickCalendarDay 末尾失配。
    await sync.getByTestId("sync-period-select").click();
    await page.getByRole("option", { name: "自定义" }).click();
    const startTrigger = sync.locator("button#sync-start-date");
    await expect(startTrigger).toBeVisible();
    await expect(startTrigger).toHaveText("开始日期");
    await expect(sync.getByTestId("sync-history-button")).toBeDisabled();

    // 选当月 1 日（恒在初始月视图、恒 ≤ 今天，且可能是非交易日——未传 showTradingDays 故可选）
    const today = new Date();
    const target = toISODate(new Date(today.getFullYear(), today.getMonth(), 1));
    await pickCalendarDay(page, startTrigger, target);
    await expect(sync.getByTestId("sync-history-button")).toBeEnabled();
  });

  test("同步历史：成立以来无请求体、近1月发区间日期，toast 含新增条数", async ({ page }) => {
    await page.route(DETAIL_ENDPOINT, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, json: PRODUCT_DETAIL_RESPONSE })
        : route.continue()
    );
    const posts: Request[] = [];
    await page.route(SYNC_HISTORY_ENDPOINT, (route) => {
      posts.push(route.request());
      return route.fulfill({ status: 200, json: { message: "同步完成", synced_count: 5, new_count: 2 } });
    });
    await page.goto(DETAIL_PATH);
    const sync = page.getByTestId("product-sync-card");
    await expect(sync).toBeVisible({ timeout: 10_000 });
    const btn = sync.getByTestId("sync-history-button");

    // 默认成立以来 → POST 不带请求体（后端 end 缺省补今天、start 无下界）
    await btn.click();
    await expect.poll(() => posts.length).toBe(1);
    const body0 = posts[0].postData();
    expect(body0 === null || body0 === "", `成立以来应不发请求体，实际: ${body0}`).toBe(true);
    // toast 透出 synced_count 与 new_count（#856 新增条数）
    const toast = page.getByTestId("toast-card").filter({ hasText: "同步成功" });
    await expect(toast).toContainText("同步 5 条（新增 2 条）");

    // 近1月 → start=今天回推 1 自然月、end=今天（与组件 subMonths/toDateOnly 同口径）
    await expect(btn).toBeEnabled();
    await sync.getByTestId("sync-period-select").click();
    await page.getByRole("option", { name: "近1月" }).click();
    await btn.click();
    await expect.poll(() => posts.length).toBe(2);
    const body1 = posts[1].postDataJSON();
    const today = new Date();
    expect(body1.end_date).toBe(toISODate(today));
    expect(body1.start_date).toBe(toISODate(subMonths(today, 1)));
  });
});

test.describe("产品管理净值曲线集成（#941）", () => {
  test("A1/A4 场外单位净值口径使用共享几何与同排区间，不因标题不含「净值」漏验", async ({ page }, testInfo) => {
    test.setTimeout(60_000);
    const errors = collectPageErrors(page);
    await page.goto("/products/000300.OF?market=CN_OTC");
    const card = page.getByTestId("product-curve-card");
    await expect(card.getByRole("heading", { name: "单位净值走势" })).toBeVisible();
    await expect(card.getByTestId("nav-basis-note")).toContainText("不含分红再投资");
    await expectNavCurveGeometry(card, "产品管理单位净值曲线");
    await expectNavRangeLayout(card.getByRole("group", { name: "净值区间" }), "产品管理单位净值区间", {
      fontSize: 14,
      singleRow: true,
    });
    await captureNavCardThemes(page, card, testInfo.outputPath("nav941-product-manage-unit-nav"));
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });
});

test.describe("历史净值窄宽布局（#934）", () => {
  test("A1/A3 列对齐与文字间距：桌面连续尺寸 / 移动多档", async ({ page }, testInfo) => {
    test.setTimeout(60_000);
    const errors = collectPageErrors(page);
    await openAppAndAuth(page);
    const { dates } = await mockNavHistoryStress(page);
    await page.goto(DETAIL_PATH);
    const history = page.getByTestId("product-history-card");
    await expect(history.getByTestId("product-history-row")).toHaveCount(5, { timeout: 10_000 });

    const isMobile = testInfo.project.name === "mobile";
    const height = page.viewportSize()?.height ?? 720;
    const widths = isMobile ? [390, 375, 320] : [600, 640, 700, 768, 900, 1024, 1152, 1280];
    let lastGap = 0;
    for (const width of widths) {
      await page.setViewportSize({ width, height });
      // TopNav 在 lg 边界切换显示，等布局稳定再断言（#934/#944 A2 口径）
      await expect(async () => {
        const g = await expectNavHistoryGeometry(history, "product", `视口${width}`);
        lastGap = g.minTextGap;
      }).toPass({ timeout: 5_000 });
    }
    expect(lastGap, "相邻文字间距应 ≥8px（容差 0.5px）").toBeGreaterThanOrEqual(7.5);
    const dateTexts = await history
      .getByTestId("product-history-row")
      .evaluateAll((rows) => rows.map((r) => (r.children[0].textContent ?? "").trim()));
    expect(dateTexts, "日期降序与 API 原值一致").toEqual(dates.slice(0, 5));
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("A6 查看更多长值同列：卡片增高、无卡内纵滚、末页按钮消失", async ({ page }) => {
    await openAppAndAuth(page);
    const { dates } = await mockNavHistoryStress(page);
    await page.goto(DETAIL_PATH);
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
    await page.goto(DETAIL_PATH);
    const history = page.getByTestId("product-history-card");
    await expect(history).toBeVisible({ timeout: 10_000 });

    for (const width of [700, 1024, 1280]) {
      await page.setViewportSize({ width, height: 720 });
      await expect(async () => {
        const m = await measureNavDetailLayout(history, `视口${width}`);
        expectNavDetailColumns(m, `视口${width}`);
      }).toPass({ timeout: 5_000 });
    }

    // 精确容器边界：在同一 <lg（main p-4）档内按容器宽反推视口增量，避免跨 lg 改 padding。
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

    // 1024 视口：顶部导航不占横向宽度，main 内容宽仍应让历史卡保持双栏。
    await page.setViewportSize({ width: 1024, height: 720 });
    await expectTopNavDetailLayout(page, history, "1024 顶部导航");
  });

  test("A5 局部横滚：首末列可达、表头同步、键盘可滚、标题不动", async ({ page }, testInfo) => {
    test.setTimeout(60_000);
    const isMobile = testInfo.project.name === "mobile";
    await openAppAndAuth(page);
    await mockNavHistoryStress(page);
    await page.goto(DETAIL_PATH);
    const history = page.getByTestId("product-history-card");
    await expect(history.getByTestId("product-history-row")).toHaveCount(5, { timeout: 10_000 });

    // 标题与查看更多在滚动区外（结构保证不随数据横移）
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
 * #935 全文身份与吸顶（产品管理-产品详情）。长名/空名/停用夹具只替换单产品读端点
 * GET /products/{code}/{market}（#856 同手法），nav 系与维度字典照常 hit 真实种子。
 */
const LONG_NAME_CN_935 = "嘉实沪深300ETF联接基金A类人民币份额长名称样例";
const LONG_NAME_EN_935 = "BlackRock Global Allocation Fund Class C USD Hedged Accumulation";

function mockDetailName935(page: Page, name: string, isActive = true) {
  return page.route(DETAIL_ENDPOINT, (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ status: 200, json: { ...PRODUCT_DETAIL_RESPONSE, name, is_active: isActive } })
      : route.continue()
  );
}

test.describe("全文身份与吸顶（#935）", () => {
  test("A1/A2/A7 长中文名与连续英文多档宽度全文可读，末尾类别/币种不丢", async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const isMobile = testInfo.project.name === "mobile";
    const height = page.viewportSize()?.height ?? 720;
    const widths = isMobile ? [320, 375, 390, 430] : [1024, 1280];
    for (const longName of [LONG_NAME_CN_935, LONG_NAME_EN_935]) {
      await mockDetailName935(page, longName);
      await page.goto(DETAIL_PATH);
      await expect(page.getByTestId("product-full-name")).toHaveText(longName, { timeout: 10_000 });
      for (const width of widths) {
        await page.setViewportSize({ width, height });
        const m = await expectFullNameReadable(page, `${longName.slice(0, 12)}… 视口${width}`);
        if (isMobile && width === 320) {
          expect(m.lines, "320px 下长名应自然换行（≥2 行），不限行不缩字号").toBeGreaterThanOrEqual(2);
        }
      }
      // A8 目检证据：长名全文身份区，浅/暗双主题整页（合成夹具名，非真实产品数据）
      if (longName === LONG_NAME_CN_935) {
        await page.setViewportSize({ width: isMobile ? 390 : 1280, height });
        await scrollWindowTo(page, 0);
        await page.screenshot({ path: testInfo.outputPath("identity-long-name-light.png"), fullPage: true });
        await page.emulateMedia({ colorScheme: "dark" });
        await page.screenshot({ path: testInfo.outputPath("identity-long-name-dark.png"), fullPage: true });
        await page.emulateMedia({ colorScheme: "light" });
      }
    }
  });

  test("A1 空名称回退显示码，身份区不空屏", async ({ page }) => {
    await mockDetailName935(page, "");
    await page.goto(DETAIL_PATH);
    await expectProductDetailIdentity(page, { name: "510300", meta: "510300 · A股场内" });
  });

  test("A3 页面放大 200%：名称/身份说明/管理动作仍可读可达（chromium）", async ({ page }, testInfo) => {
    // 方法：根元素 CSS zoom=2（Blink 页面缩放同机制），放大以渲染几何为证
    // （computed font-size 报告值不随根 zoom 变化）；不是缩小视口冒充文字放大。
    // mobile WebKit 无等价自动化入口（平台限制如实记录，
    // 真机字体放大复核并入依赖现场条件的闭环项，不在此冒充已验证）。
    test.skip(
      testInfo.project.name !== "chromium",
      "页面放大探针经 CSS zoom 实现，mobile WebKit 无等价自动化入口（功能确实缺，平台限制留痕）"
    );
    await mockDetailName935(page, LONG_NAME_CN_935);
    await page.goto(DETAIL_PATH);
    await expect(page.getByTestId("product-full-name")).toHaveText(LONG_NAME_CN_935, { timeout: 10_000 });
    const h1 = page.getByRole("heading", { level: 1, name: "产品详情" });
    const before = await h1.boundingBox();
    if (!before) throw new Error("页标题没有可见盒");
    await page.evaluate(() => {
      document.documentElement.style.zoom = "2";
    });
    // 放大以渲染几何为证：根元素 zoom 不改变 computed font-size 报告值，
    // 单行标题盒高应约翻倍（不是缩小视口冒充文字放大）
    await expect
      .poll(async () => (await h1.boundingBox())?.height ?? 0)
      .toBeGreaterThan(before.height * 1.9);
    await expectFullNameReadable(page, "200% 放大");
    // 管理动作可达：编辑打开表单对话框（放大不藏内容、不靠缩字）
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(dialogByTitle(page, "编辑产品")).toBeVisible();
  });

  test("A4 分区标题归 16/600/24：基本信息/分类维度/数据同步卡", async ({ page }) => {
    await page.goto(DETAIL_PATH);
    for (const [testid, title] of [
      ["product-basic-info-card", "基本信息"],
      ["product-dimension-card", "分类维度"],
      ["product-sync-card", "数据同步"],
    ] as const) {
      const heading = page.getByTestId(testid).getByRole("heading", { name: title });
      await expect(heading).toBeVisible({ timeout: 10_000 });
      await expect(heading).toHaveCSS("font-size", "16px");
      await expect(heading).toHaveCSS("font-weight", "600");
      await expect(heading).toHaveCSS("line-height", "24px");
    }
  });

  test("A5 吸顶折叠只剩返回+短标题：停用badge与管理动作随正文滚走", async ({ page }, testInfo) => {
    await mockDetailName935(page, PRODUCT_DETAIL_RESPONSE.name, false);
    await page.goto(DETAIL_PATH);
    await expectProductDetailIdentity(page, { name: "沪深300ETF", meta: "510300 · A股场内" });

    // 展开态：停用 badge 在身份区、管理动作行在身份区下方（正文，非页眉）
    const identity = page.getByTestId("product-identity");
    await expect(identity.getByTestId("product-inactive-badge")).toBeVisible();
    const actions = page.getByTestId("product-manage-actions");
    await expect(actions).toBeVisible();
    await expect(page.getByTestId("page-header").getByTestId("product-manage-actions")).toHaveCount(0);
    const identityBox = await identity.boundingBox();
    const actionsBox = await actions.boundingBox();
    if (!identityBox || !actionsBox) throw new Error("身份区/管理动作行没有可见盒");
    expect(actionsBox.y, "管理动作行应在身份区下方").toBeGreaterThanOrEqual(identityBox.y + identityBox.height - 1);
    await expect(identity).toHaveCSS("position", "static");

    // 阈值两侧：32px 不折叠、33px 折叠
    await scrollWindowTo(page, 32);
    await expect(page.getByTestId("page-header").getByRole("navigation", { name: "面包屑" })).toBeVisible();
    await scrollWindowTo(page, 33);
    await expectProductHeaderCollapsed(page, {
      backAria: "返回产品管理",
      absentTexts: ["沪深300ETF", "510300", "已停用"],
    });
    // A5/A8 目检证据：折叠页眉只剩返回+短标题（视口截图，非整页）
    await page.screenshot({ path: testInfo.outputPath("header-collapsed-light.png") });

    // 持续下滑：身份区与管理动作行滚出视口，页眉仍只有返回+短标题
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(32);
    const identityTop = await identity.evaluate((el) => el.getBoundingClientRect().top);
    expect(identityTop, "身份区应随正文滚走（顶缘 < 0）").toBeLessThan(0);
    await expectProductHeaderCollapsed(page, {
      backAria: "返回产品管理",
      absentTexts: ["沪深300ETF", "510300", "已停用"],
    });

    // 回顶：展开态恢复，身份区可再次完整阅读
    await scrollWindowTo(page, 0);
    await expectProductDetailIdentity(page, { name: "沪深300ETF", meta: "510300 · A股场内" });
    await expect(identity.getByTestId("product-inactive-badge")).toBeVisible();
    await expectFullNameReadable(page, "回顶后");
  });
});
