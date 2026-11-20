import { test, expect, type Page } from "@playwright/test";
import {
  E2E_PORT,
  collectPageErrors,
  dialogByTitle,
  dialogTestViewports,
  expectDialogSingleScroll,
  openSubmitTradeDialog,
} from "./helpers";

/**
 * #880 产品数据源切换（SPEC v1.1 Block 1+2）。
 *
 * 用 route mock 替换单产品读/写端点与列表端点（与 product-manage-detail.spec.ts 同手法），
 * 不向 E2E 库注入、不跑真实上游探测。断言：净值型表单含数据源下拉与后果提示、港互认选项
 * 收窄为 akshare、切换保存请求体含 data_source 且详情回读新源/待验证、探测失败透传中文
 * 消息且对话框不关、虚拟产品不显示控件。两端共用组件，mobile project 同跑。
 *
 * #910 追加：产品表单 DialogContent 外层限高/overflow 移除后的单滚动容器回归
 * （四档视口，共享 expectDialogSingleScroll）与调仓就地新增产品的真实 UI 提交
 * （精确 POST /api/products mock，不跑上游探测、不落库、不提交交易）。
 */

const ETF_DETAIL = {
  code: "510300.SH", market: "CN_EXCHANGE", name: "沪深300ETF", product_type: "ETF",
  display_code: "510300", hk_sale_code: null, confirm_days: 0, nav_lag_days: 0,
  is_qdii: false, is_active: true, data_source: "tushare", data_source_status: "success",
  last_sync_at: null, created_at: "2025-01-01T00:00:00",
  asset_class_code: "ASSET_STOCK", region_code: "REGION_CN", style_code: "STYLE_BALANCED",
  size_code: "SIZE_LARGE", segment_code: "SEG_COMPOSITE",
};
const HK_DETAIL = {
  ...ETF_DETAIL, code: "1001767344", market: "HK_MUTUAL", name: "摩根国际债券",
  product_type: "OEF", display_code: "968050", hk_sale_code: "968050",
  data_source: "akshare", nav_lag_days: 1, confirm_days: 1,
};
const OTC_DETAIL = {
  ...ETF_DETAIL, code: "000001.OF", market: "CN_OTC", name: "华夏成长混合",
  product_type: "OEF", display_code: "000001", data_source: "tushare", confirm_days: 1,
};
const HK_TUSHARE_DETAIL = { ...HK_DETAIL, data_source: "tushare" };

const ETF_ENDPOINT = /\/api\/products\/510300\.SH\/CN_EXCHANGE(\?|$)/;
const HK_ENDPOINT = /\/api\/products\/1001767344\/HK_MUTUAL(\?|$)/;
const ETF_PATH = "/products/510300.SH?market=CN_EXCHANGE";
const HK_PATH = "/products/1001767344?market=HK_MUTUAL";

async function openEditFromDetail(page: Page, path: string) {
  await page.goto(path);
  await expect(page.getByTestId("product-basic-info-card")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  return dialogByTitle(page, "编辑产品");
}

test.describe("产品数据源切换表单（#880）", () => {
  test("净值型编辑表单含数据源下拉（两源）与后果提示", async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.route(ETF_ENDPOINT, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, json: ETF_DETAIL })
        : route.continue()
    );
    const dlg = await openEditFromDetail(page, ETF_PATH);
    const ds = dlg.locator("select#data_source");
    await expect(ds).toBeVisible();
    await expect(ds.locator("option")).toHaveCount(2);
    await expect(ds.locator("option").nth(0)).toHaveText("tushare");
    await expect(ds.locator("option").nth(1)).toHaveText("akshare");
    // 后果提示：场内（CN_EXCHANGE）不显示日涨跌口径差（Nit 按市场限定），仅通用提示
    await expect(dlg.getByText("切换后新净值来自新数据源")).toBeVisible();
    await expect(dlg.getByText("日涨跌列口径随源变化")).toHaveCount(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("场外（CN_OTC）编辑显示日涨跌口径与分红后果提示", async ({ page }) => {
    await page.route(/\/api\/products\/000001\.OF\/CN_OTC(\?|$)/, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, json: OTC_DETAIL })
        : route.continue()
    );
    const dlg = await openEditFromDetail(page, "/products/000001.OF?market=CN_OTC");
    await expect(dlg.getByText("日涨跌列口径随源变化")).toBeVisible();
    await expect(dlg.getByText("仍会自动检测分红")).toBeVisible();
  });

  test("存量 HK+tushare 行只改名：不下发 data_source、保存成功（F1）", async ({ page }) => {
    let putBody: Record<string, unknown> | null = null;
    await page.route(/\/api\/products\/1001767344\/HK_MUTUAL(\?|$)/, (route) => {
      const method = route.request().method();
      if (method === "GET") return route.fulfill({ status: 200, json: HK_TUSHARE_DETAIL });
      if (method === "PUT") {
        putBody = route.request().postDataJSON();
        return route.fulfill({ status: 200, json: { ...HK_TUSHARE_DETAIL, name: putBody!.name } });
      }
      return route.continue();
    });
    const dlg = await openEditFromDetail(page, "/products/1001767344?market=HK_MUTUAL");
    // 当前值 tushare 不在可选列表 → 真实显示「tushare（当前值）」，不误导成 akshare
    await expect(dlg.locator("select#data_source")).toHaveValue("tushare");
    await dlg.locator("#name").fill("改名不改源");
    await dlg.getByRole("button", { name: "保存修改" }).click();
    await expect(dlg).toHaveCount(0);
    expect(putBody).not.toBeNull();
    expect(putBody!.name).toBe("改名不改源");
    expect("data_source" in putBody!).toBe(false);
  });

  test("港互认产品数据源下拉只 akshare（tushare 不支持）", async ({ page }) => {
    await page.route(HK_ENDPOINT, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, json: HK_DETAIL })
        : route.continue()
    );
    const dlg = await openEditFromDetail(page, HK_PATH);
    const ds = dlg.locator("select#data_source");
    await expect(ds).toBeVisible();
    await expect(ds.locator("option")).toHaveCount(1);
    await expect(ds.locator("option").nth(0)).toHaveText("akshare");
  });

  test("切换数据源保存：PUT 含 data_source，详情回读新源与待验证", async ({ page }) => {
    let current = { ...ETF_DETAIL };
    await page.route(ETF_ENDPOINT, (route) => {
      const method = route.request().method();
      if (method === "GET") return route.fulfill({ status: 200, json: current });
      if (method === "PUT") {
        const body = route.request().postDataJSON();
        current = { ...current, data_source: body.data_source, data_source_status: "pending" };
        return route.fulfill({ status: 200, json: current });
      }
      return route.continue();
    });
    const dlg = await openEditFromDetail(page, ETF_PATH);
    await dlg.locator("select#data_source").selectOption("akshare");
    const put = page.waitForRequest(
      (r) => r.method() === "PUT" && ETF_ENDPOINT.test(r.url())
    );
    await dlg.getByRole("button", { name: "保存修改" }).click();
    const body = (await put).postDataJSON();
    expect(body.data_source).toBe("akshare");
    // 对话框关闭，详情回读新源 + 状态待验证（V5）
    await expect(dlg).toHaveCount(0);
    const basic = page.getByTestId("product-basic-info-card");
    await expect(basic).toContainText("akshare", { timeout: 10_000 });
    await expect(basic).toContainText("待验证");
  });

  test("探测失败：错误 toast 透传中文消息，对话框不关", async ({ page }) => {
    await page.route(ETF_ENDPOINT, (route) => {
      const method = route.request().method();
      if (method === "GET") return route.fulfill({ status: 200, json: ETF_DETAIL });
      if (method === "PUT") {
        return route.fulfill({
          status: 422,
          json: { detail: { error: "DATA_SOURCE_PROBE_FAILED", message: "数据源暂时不可达，请稍后重试" } },
        });
      }
      return route.continue();
    });
    const dlg = await openEditFromDetail(page, ETF_PATH);
    await dlg.locator("select#data_source").selectOption("akshare");
    await dlg.getByRole("button", { name: "保存修改" }).click();
    // modal Dialog 开着时 toast 走 DOM testid（#640 口径）
    const toast = page.getByTestId("toast-card").filter({ hasText: "数据源暂时不可达" });
    await expect(toast).toBeVisible({ timeout: 10_000 });
    await expect(dlg).toBeVisible();
  });

  test("虚拟产品编辑表单不显示数据源控件", async ({ page }) => {
    const cashRow = {
      code: "CASH", market: "", name: "现金类资产", product_type: "CASH",
      hk_sale_code: null, display_code: "CASH", confirm_days: 0, nav_lag_days: 0,
      is_qdii: false, is_active: true, data_source_status: null,
    };
    await page.route(/\/api\/products(\?|$)/, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, json: { items: [cashRow], total: 1, page: 1, page_size: 20 } })
        : route.continue()
    );
    await page.goto("/products");
    const card = page.getByTestId("products-list-card");
    await expect(card).toBeVisible({ timeout: 15_000 });
    // #904 P1d：移动卡化后行=卡片，桌面仍表格行；.or 双形态单定位
    const row = card
      .getByRole("row")
      .filter({ hasText: "现金类资产" })
      .first()
      .or(card.getByTestId("product-card").filter({ hasText: "现金类资产" }).first());
    await row.getByRole("button", { name: "更多操作" }).click();
    await page.getByRole("menuitem", { name: "编辑" }).click();
    const dlg = dialogByTitle(page, "编辑产品");
    await expect(dlg).toBeVisible();
    await expect(dlg.locator("select#data_source")).toHaveCount(0);
  });
});

test.describe("产品表单滚动契约与就地新增回归（#910）", () => {
  test("添加产品：默认与股票长表单仅内层滚动，创建/关闭可达（四档视口）", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await page.goto("/products");
    await expect(page.getByTestId("products-list-card")).toBeVisible({ timeout: 15_000 });
    for (const vp of dialogTestViewports(testInfo, page)) {
      await page.setViewportSize(vp);
      const label = `添加产品 ${vp.width}×${vp.height}`;
      await page.getByRole("button", { name: "添加产品" }).click();
      const dlg = dialogByTitle(page, "添加产品");
      // 默认表单：外层不裁剪不滚动（须在自动滚动动作前断言）
      await expectDialogSingleScroll(dlg, `${label} 默认表单`);
      // 股票大类展开四维字段 → 长表单：整体滚动只归内层，创建按钮滚后可达
      await dlg.locator("select#asset_class_code").selectOption("ASSET_STOCK");
      await expect(dlg.locator("select#region_code")).toBeVisible();
      await expectDialogSingleScroll(dlg, `${label} 股票长表单`, {
        bottomAction: dlg.getByRole("button", { name: "创建产品" }),
      });
      // Close 在滚动容器外，始终可达
      await expect(dlg.getByRole("button", { name: "Close" })).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(dlg).toHaveCount(0);
    }
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("编辑产品：列表入口股票长表单滚动回归，不可改字段保持禁用（四档视口）", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await page.goto("/products");
    const card = page.getByTestId("products-list-card");
    await expect(card).toBeVisible({ timeout: 15_000 });
    for (const vp of dialogTestViewports(testInfo, page)) {
      await page.setViewportSize(vp);
      // 种子 510300.SH 五维齐全，编辑态即长表单；桌面表格行/移动卡片双形态
      const row = card
        .getByRole("row")
        .filter({ hasText: "沪深300ETF" })
        .first()
        .or(card.getByTestId("product-card").filter({ hasText: "沪深300ETF" }).first());
      await row.getByRole("button", { name: "更多操作" }).click();
      await page.getByRole("menuitem", { name: "编辑" }).click();
      const dlg = dialogByTitle(page, "编辑产品");
      await expect(dlg.locator("#code")).toBeDisabled();
      await expect(dlg.locator("select#market")).toBeDisabled();
      await expectDialogSingleScroll(dlg, `编辑产品 ${vp.width}×${vp.height}`, {
        bottomAction: dlg.getByRole("button", { name: "保存修改" }),
      });
      await page.keyboard.press("Escape");
      await expect(dlg).toHaveCount(0);
    }
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("调仓就地新增产品：创建成功回填 code/market，原调仓输入不丢，不提交交易", async ({ page }) => {
    const errors = collectPageErrors(page);
    // 真实 UI + 精确 POST mock：产品创建会触发上游数据源探测（product_service），
    // 本用例只验 UI 链路（请求体/关闭/回填），不是后端落库验收
    const NEW_PRODUCT = {
      code: "E2E910.OF", market: "CN_OTC", name: "E2E910测试基金", product_type: "OEF",
      display_code: "E2E910", hk_sale_code: null, confirm_days: 1, nav_lag_days: 0,
      is_qdii: false, is_active: true, data_source: "tushare", data_source_status: "pending",
    };
    let postBody: Record<string, unknown> | null = null;
    const tradePosts: string[] = [];
    page.on("request", (r) => {
      if (r.method() === "POST" && /\/api\/trades(\?|$)/.test(r.url())) tradePosts.push(r.url());
    });
    await page.route(/\/api\/products(\?|$)/, (route) => {
      if (route.request().method() === "POST") {
        postBody = route.request().postDataJSON();
        return route.fulfill({ status: 200, json: NEW_PRODUCT });
      }
      return route.continue();
    });

    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);
    await dlg.locator("#amount").fill("123.45");
    await dlg.getByRole("button", { name: "新增产品" }).click();
    const form = dialogByTitle(page, "添加产品");
    await expectDialogSingleScroll(form, "调仓就地新增产品", {
      bottomAction: form.getByRole("button", { name: "创建产品" }),
    });
    await form.locator("#code").fill("E2E910.OF");
    await form.locator("#name").fill("E2E910测试基金");
    await form.locator("select#market").selectOption("CN_OTC");
    const posted = page.waitForRequest(
      (r) => r.method() === "POST" && /\/api\/products(\?|$)/.test(r.url())
    );
    await form.getByRole("button", { name: "创建产品" }).click();
    await posted;
    await expect(form).toHaveCount(0);

    // 既存行为（修前/修后探针一致，与本单三处样式无关，见 #910 正文任务外发现）：
    // 调仓 Dialog 为 modal={false}，产品 modal 打开夺焦瞬间调仓弹窗即经 focusOutside
    // 关闭；formData 是 TradesContent 组件态、不随关闭重置。故 A4「原调仓输入不丢 +
    // 回填」以重开调仓弹窗验证：金额与回填产品仍在。
    await expect(dlg).toHaveCount(0);
    await page.getByRole("button", { name: "提交交易" }).first().click();
    const reopenedTrade = dialogByTitle(page, "提交交易");
    await expect(reopenedTrade.locator("#amount")).toHaveValue("123.45");
    await expect(reopenedTrade.locator("button#product_code")).toContainText("E2E910");
    expect(postBody).not.toBeNull();
    expect(postBody!.code).toBe("E2E910.OF");
    expect(postBody!.market).toBe("CN_OTC");

    // 关闭产品表单（X）不重置调仓表单状态：再开产品表单直接关闭，重开调仓输入仍在
    await reopenedTrade.getByRole("button", { name: "新增产品" }).click();
    const reopenedForm = dialogByTitle(page, "添加产品");
    await reopenedForm.getByRole("button", { name: "Close" }).click();
    await expect(reopenedForm).toHaveCount(0);
    await page.getByRole("button", { name: "提交交易" }).first().click();
    const thirdTrade = dialogByTitle(page, "提交交易");
    await expect(thirdTrade.locator("#amount")).toHaveValue("123.45");
    await expect(thirdTrade.locator("button#product_code")).toContainText("E2E910");
    expect(tradePosts, `就地新增产品不得提交交易: ${tradePosts.join(" | ")}`).toHaveLength(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });
});
