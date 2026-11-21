import { test, expect, type Page } from "@playwright/test";
import { collectPageErrors, dialogByTitle } from "./helpers";

/**
 * #902：资产分类管理页读侧失败态门禁（双 project；桌面/移动共享组件单点，形态同断言）。
 *
 * 数据说明：字典 GET 造数（500 / 200 + items=[]）；A2 用「编辑 ASSET_STOCK 不改字段原值回写
 * → PUT 成功 invalidate 同键字典」触发后台重取（isRefetchError 保留旧 data），PUT 走真实隔离
 * 后端、仅 GET 被拦截，不改共享种子的规则/排序/适用关系。A4 的真·空字典在生产不可达（种子恒有
 * 大类），属 mock 守卫。
 *
 * 判序口径：错误优先**外层臂**（#683A / #900 同机理），但本页落点是**整卡区替换**——失败落地后
 * 五卡让位失败块、旧卡区不在屏；投资人页是「行集按 isMobile 无条件渲染 + 下方失败/空态槽位」，
 * 失败块与旧行集同屏（#903 L2 S3）。两页结构不同，断言时序与注释不可互抄。
 */

const DICT_RE = /\/api\/asset-classifications(\?|$)/;
const FAIL_JSON = { detail: { error: "INTERNAL", message: "维度字典服务暂不可用" } };
/** 五卡标题（大类 + 四个规则维度）；exact 避开 CardDescription/meta 里的同名片段 */
const CARD_TITLES = ["大类", "地区", "风格", "规模", "细分"];

/** 大类行锚点：桌面表格行 / 移动卡片（组件侧 data-testid）双形态联合，同 #900 investor-card 手法 */
function classRow(page: Page, text: string) {
  return page.locator('tr, [data-testid="asset-class-card"]').filter({ hasText: text });
}

test("字典读取失败 → 整卡区让位失败态，重试与全局 toast 可见（#902 A1）", async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.route(DICT_RE, (route) => route.fulfill({ status: 500, json: FAIL_JSON }));
  await page.goto("/asset-classifications");
  await expect(page.getByText("加载失败：维度字典服务暂不可用")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
  // 落点=整卡区单块（SPEC 决策②A）：五卡全部让位，不是「五卡保留 + 下方追加失败块」
  for (const title of CARD_TITLES) {
    await expect(page.getByText(title, { exact: true })).toHaveCount(0);
  }
  // hook 层 #214 toast 车道保留（半收敛 → 全收敛双车道）；DOM 口径，modal 开着时亦稳（#640）
  await expect(
    page.getByTestId("toast-card").filter({ hasText: "维度字典加载失败" }),
  ).toBeVisible();
  expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
});

test("同键后台重取失败且保留旧 data 时失败块仍可达（#902 A2 判序，外层臂）", async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.goto("/asset-classifications");
  await expect(classRow(page, "ASSET_STOCK")).toBeVisible({ timeout: 10_000 });

  // 首取成功后再拦截 GET：同键重取失败时 react-query 保留旧 data（isRefetchError）。
  // 用 gate 把重取**保持在途**直到前置态断完（#683 D-1 手法），不用定时延迟——本页失败落地即
  // 整区替换，延迟窗口下前置态断言会与失败落地竞态；gate 形不依赖时序。
  let releaseFailure!: () => void;
  const failureGate = new Promise<void>((resolve) => {
    releaseFailure = resolve;
  });
  await page.route(DICT_RE, async (route) => {
    await failureGate;
    await route.fulfill({ status: 500, json: FAIL_JSON });
  });

  await classRow(page, "ASSET_STOCK").getByRole("button", { name: "编辑" }).click();
  const dlg = dialogByTitle(page, "编辑 ASSET_STOCK");
  await expect(dlg).toBeVisible();
  // 不改任何字段：原值回写（asset_class 分支下发全量 dimension_rules），PUT 走真实隔离后端
  await dlg.getByRole("button", { name: "保存修改" }).click();
  await expect(dlg).toBeHidden();

  // 前置态钉住：重取在途窗口内旧卡区仍在屏（保留旧 data 的可观测代理）。
  // 守卫边界如实登记：本断言只钉「重取在途保留」，不钉「错误落地仍保留」——整区替换下后者
  // 两态 UI 同形、非 UI 可判别（不引入缓存内省，#900 同口径）。
  await expect(classRow(page, "ASSET_STOCK")).toBeVisible();
  releaseFailure();

  // PUT 成功 invalidate 同键字典 → 后台重取 500：外层臂失败块必须可见且整卡区让位。
  // 嵌套/AND 形（失败臂塞进 classes.length === 0 前件）在该态失败块不可达 → 本断言红。
  await expect(page.getByText("加载失败：维度字典服务暂不可用")).toBeVisible({ timeout: 10_000 });
  for (const title of CARD_TITLES) {
    await expect(page.getByText(title, { exact: true })).toHaveCount(0);
  }
  expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
});

test("点重试且接口恢复后正常渲染大类/维度卡片（#902 A3）", async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.route(DICT_RE, (route) => route.fulfill({ status: 500, json: FAIL_JSON }));
  await page.goto("/asset-classifications");
  await expect(page.getByText("加载失败：维度字典服务暂不可用")).toBeVisible({ timeout: 10_000 });

  await page.unroute(DICT_RE);
  // 默认 retry:1 的自动重试会提前满足谓词，故 waitForRequest 必须在失败块可见后注册（#683 A1 先例）
  const refetched = page.waitForRequest(
    (r) => r.method() === "GET" && DICT_RE.test(r.url()),
    { timeout: 10_000 },
  );
  await page.getByRole("button", { name: "重试" }).click();
  await refetched;
  await expect(classRow(page, "ASSET_STOCK")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("加载失败：")).toHaveCount(0);
  expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
});

test("真·空字典仍渲染现有静默空形态，不误报失败（#902 A4）", async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.route(DICT_RE, (route) =>
    route.fulfill({ json: { items: [], dimension_rules: {}, total: 0 } }),
  );
  await page.goto("/asset-classifications");
  // 本页无空态文案：真·空 = 五卡壳仍在、行集为空（决策③保持现状，不新增文案）
  for (const title of CARD_TITLES) {
    await expect(page.getByText(title, { exact: true })).toBeVisible({ timeout: 10_000 });
  }
  await expect(classRow(page, "ASSET_STOCK")).toHaveCount(0);
  await expect(page.getByText("加载失败：")).toHaveCount(0);
  await expect(
    page.getByTestId("toast-card").filter({ hasText: "维度字典加载失败" }),
  ).toHaveCount(0);
  expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
});
