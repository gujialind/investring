import { test, expect, type Locator, type Page, type TestInfo } from '@playwright/test';
import {
  E2E_ACTIVE,
  activatePortfolioViaApi,
  authHeaders,
  collectPageErrors,
  createPortfolioViaApi,
  dialogByTitle,
  gotoPortfolioDetail,
  isolatedPortfolioCode,
} from './helpers';

/**
 * #915 DisplayConfigDialog 双端操作用例（SPEC §7.3 六场景＋L2 r1 增补规则收紧场景）。
 * git 历史上本文件先以「现状单选型态」落 characterization 用例并实跑绿
 * （硬前置 commit），再随多选改造更新为本形态——「先补后改」顺序可证。
 *
 * 测试层口径（frontend/AGENTS.md §3）：vitest 只测 lib 纯逻辑、不引 jsdom/RTL，
 * 组件交互与运行时行为归 Playwright 双端 E2E，故本弹窗用例全部落 E2E、双端同跑。
 *
 * 数据说明：
 * - 只读用例（打开/勾选/回填断言）用种子 E2E_ACTIVE，不写入、不污染共享种子。
 * - 写入用例（保存/null 清空/字典晚到）自建隔离 active 组合（首购确认激活，
 *   现金-only 也渲染持仓区与「分组维度」入口），code 经 isolatedPortfolioCode
 *   区分 project/worker/retry；残留由每轮重建的 E2E 库吸收。
 * - 可配置大类与勾选项来自种子规则矩阵（backend/app/constants/asset_dimensions.py
 *   ::DIMENSION_RULES）：股票 region/style/size/segment、债券 region/segment、
 *   商品 segment；现金无规则行 → 不出现。默认子集（lib/dimensions.ts
 *   ::DEFAULT_SUB_DIMS_BY_CLASS，D3）：股票/债券 region,segment、商品 segment。
 * - 「字典晚到」的构造边界：双端详情页 loading 闸门含字典查询（page.tsx
 *   isLoading 含 dictLoading），弹窗与页面同 query key，冷加载下「弹窗已开而
 *   字典未就绪」经 UI 不可达（组件注释明写）。本用例钉住组合保障：字典未就绪
 *   时不存在可触发清空的保存入口；就绪后初始化 effect 回填完整、保存不清空。
 *   若未来去掉闸门且初始化 effect 依赖退化（如只依赖 open），本用例必红。
 * - 「规则事后收紧」（L2 r1 增补）的构造边界：写侧无规则收紧入口（规则矩阵种子），
 *   故经 page.route 摘读侧字典响应的 style 登记模拟；后端写侧保持真规则，存量预置
 *   合法可落库。mock 须装于首次导航前（字典 staleTime 5min，缓存命中后 route 不生效）。
 */

/** 大类配置块（组件侧 data-testid 锚点，#382 口径） */
function classBlock(dlg: Locator, classCode: string): Locator {
  return dlg.getByTestId(`display-config-class-${classCode}`);
}

function dimCheckbox(dlg: Locator, classCode: string, dimName: string): Locator {
  return classBlock(dlg, classCode).getByRole('checkbox', { name: dimName, exact: true });
}

/** 打开弹窗（入口仅管理员可见，E2E 登录 ADMIN） */
async function openDisplayConfigDialog(page: Page): Promise<Locator> {
  const entry = page.getByRole('button', { name: '分组维度' });
  await expect(entry).toBeVisible();
  // 移动端有 sticky 页头（top-0 z-30）与固定底部导航（bottom-0 z-40）：
  // 点击前自动滚动可能把按钮停在视口边缘被浮层盖住（实测拦截物在两者间振荡），
  // 显式滚到视口中央再点，确定性避开上下两条浮层
  await entry.evaluate((el) => el.scrollIntoView({ block: 'center' }));
  await entry.click();
  const dlg = dialogByTitle(page, '分组维度');
  await expect(dlg).toBeVisible();
  return dlg;
}

/** 自建隔离 active 组合（现金-only；首购确认激活，无需行情/快照） */
async function setupIsolatedActive(
  page: Page,
  testInfo: TestInfo,
  suffix: string,
): Promise<{ code: string; headers: { Authorization: string } }> {
  // 先落同源页取 localStorage token（about:blank 会 SecurityError）
  await gotoPortfolioDetail(page, E2E_ACTIVE);
  const headers = await authHeaders(page);
  const code = isolatedPortfolioCode('E915', testInfo, suffix);
  await createPortfolioViaApi(page, headers, code);
  await activatePortfolioViaApi(page, headers, code);
  return { code, headers };
}

/** 自建组合直导航 + 同款就绪信号（gotoPortfolioDetail 收窄为种子字面量） */
async function gotoOwnPortfolio(page: Page, code: string): Promise<void> {
  await page.goto(`/portfolio/${code}`);
  await page
    .getByRole('heading', { level: 1 })
    .first()
    .waitFor({ state: 'visible', timeout: 15_000 });
}

async function setConfigViaApi(
  page: Page,
  headers: { Authorization: string },
  code: string,
  config: Record<string, string> | null,
): Promise<void> {
  const resp = await page.request.put(`/api/portfolios/${code}`, {
    headers,
    data: { display_config: config },
  });
  await expect(resp, `预置 display_config 失败 ${resp.status()}`).toBeOK();
}

async function getConfigViaApi(
  page: Page,
  headers: { Authorization: string },
  code: string,
): Promise<Record<string, string> | null> {
  const resp = await page.request.get(`/api/portfolios/${code}`, { headers });
  await expect(resp).toBeOK();
  return ((await resp.json()) as { display_config: Record<string, string> | null }).display_config;
}

/** 点保存并捕获 PUT /api/portfolios/{code} 的请求体 */
async function saveAndCapturePayload(
  page: Page,
  dlg: Locator,
  code: string,
): Promise<unknown> {
  const put = page.waitForRequest(
    (r) => r.method() === 'PUT' && r.url().endsWith(`/api/portfolios/${code}`),
    { timeout: 10_000 },
  );
  await dlg.getByRole('button', { name: '保存' }).click();
  return (await put).postDataJSON();
}

/** mobile webkit 点击 stable 等待按帧率计费（#763），长链路按 project 放宽预算 */
function mobileTimeout(testInfo: TestInfo, ms: number): void {
  if (testInfo.project.name === 'mobile') test.setTimeout(ms);
}

test.describe('#915 DisplayConfigDialog 维度子集多选（双端）', () => {
  test('打开：可配置大类按字典序枚举、默认态零勾选、现金不出现；取消关闭', async ({ page }) => {
    const errors = collectPageErrors(page);
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByTestId('holdings-view-tabs')).toBeVisible({ timeout: 10_000 });

    const dlg = await openDisplayConfigDialog(page);
    // 枚举规则矩阵有行的三大类（不限当前持仓，支持预配置）；现金无规则行不出现
    for (const code of ['ASSET_STOCK', 'ASSET_BOND', 'ASSET_COMMODITY']) {
      await expect(classBlock(dlg, code)).toBeVisible();
    }
    await expect(classBlock(dlg, 'ASSET_CASH')).toHaveCount(0);
    // sort_order 排序：股票(1) → 债券(2) → 商品(3)
    const text = await dlg.innerText();
    expect(text.indexOf('股票')).toBeLessThan(text.indexOf('债券'));
    expect(text.indexOf('债券')).toBeLessThan(text.indexOf('商品'));

    // 未配置组合：各大类零勾选、提示回显默认子集（D3）
    for (const code of ['ASSET_STOCK', 'ASSET_BOND', 'ASSET_COMMODITY']) {
      const checked = classBlock(dlg, code).getByRole('checkbox', { checked: true });
      await expect(checked).toHaveCount(0);
    }
    await expect(classBlock(dlg, 'ASSET_STOCK')).toContainText('当前：默认（地区·细分）');
    await expect(classBlock(dlg, 'ASSET_BOND')).toContainText('当前：默认（地区·细分）');
    await expect(classBlock(dlg, 'ASSET_COMMODITY')).toContainText('当前：默认（细分）');

    // 勾选项集 = 该大类全部非禁止维（required/optional 均可勾）
    await expect(classBlock(dlg, 'ASSET_STOCK').getByRole('checkbox')).toHaveCount(4);
    await expect(classBlock(dlg, 'ASSET_BOND').getByRole('checkbox')).toHaveCount(2);
    await expect(classBlock(dlg, 'ASSET_COMMODITY').getByRole('checkbox')).toHaveCount(1);
    for (const name of ['地区', '风格', '规模', '细分']) {
      await expect(dimCheckbox(dlg, 'ASSET_STOCK', name)).toBeVisible();
    }

    await dlg.getByRole('button', { name: '取消' }).click();
    await expect(dlg).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('勾选/取消：提示随勾选更新、全序拼接；取消零写入', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 120_000);
    const errors = collectPageErrors(page);
    const puts: string[] = [];
    page.on('request', (r) => {
      if (r.method() === 'PUT' && r.url().includes('/api/portfolios/')) puts.push(r.url());
    });
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByTestId('holdings-view-tabs')).toBeVisible({ timeout: 10_000 });
    const dlg = await openDisplayConfigDialog(page);
    const stock = classBlock(dlg, 'ASSET_STOCK');

    // 勾选 → 提示为已选子集；取消 → 回到默认提示
    await dimCheckbox(dlg, 'ASSET_STOCK', '风格').check();
    await expect(dimCheckbox(dlg, 'ASSET_STOCK', '风格')).toBeChecked();
    await expect(stock).toContainText('当前：风格');
    await dimCheckbox(dlg, 'ASSET_STOCK', '风格').uncheck();
    await expect(stock).toContainText('当前：默认（地区·细分）');

    // 乱序勾选（先细分后地区）→ 提示按固定全序拼接「地区·细分」
    await dimCheckbox(dlg, 'ASSET_STOCK', '细分').check();
    await dimCheckbox(dlg, 'ASSET_STOCK', '地区').check();
    await expect(stock).toContainText('当前：地区·细分');
    // 「恢复默认」= 清空该大类全部勾选（提交时省略该键 = 走默认子集，不是平铺）
    await stock.getByRole('button', { name: '恢复默认' }).click();
    await expect(stock.getByRole('checkbox', { checked: true })).toHaveCount(0);
    await expect(stock).toContainText('当前：默认（地区·细分）');

    // 取消：不产生任何组合写请求
    await dlg.getByRole('button', { name: '取消' }).click();
    await expect(dlg).toHaveCount(0);
    expect(puts).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('保存显式覆盖项：仅提交非默认键、子集全序串；弹窗关闭+成功提示+后端落库', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 90_000);
    const errors = collectPageErrors(page);
    const { code, headers } = await setupIsolatedActive(page, testInfo, 'S');
    await gotoOwnPortfolio(page, code);
    const dlg = await openDisplayConfigDialog(page);

    // 股票乱序勾两个维（先细分后地区）→ 提交归一为 "region,segment"（Q5）
    await dimCheckbox(dlg, 'ASSET_STOCK', '细分').check();
    await dimCheckbox(dlg, 'ASSET_STOCK', '地区').check();
    await dimCheckbox(dlg, 'ASSET_BOND', '地区').check();
    // 商品保持默认 → 提交体不含该键（仅显式覆盖项）
    const payload = await saveAndCapturePayload(page, dlg, code);
    expect(payload).toEqual({
      display_config: { ASSET_STOCK: 'region,segment', ASSET_BOND: 'region' },
    });
    await expect(dlg).toHaveCount(0);
    await expect(page.getByTestId('toast-card').filter({ hasText: '更新成功' })).toBeVisible();
    expect(await getConfigViaApi(page, headers, code)).toEqual({
      ASSET_STOCK: 'region,segment',
      ASSET_BOND: 'region',
    });
    expect(errors).toEqual([]);
  });

  test('全默认提交 null：既有配置全部恢复默认 → 清空落库', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 120_000);
    const errors = collectPageErrors(page);
    const { code, headers } = await setupIsolatedActive(page, testInfo, 'N');
    await setConfigViaApi(page, headers, code, {
      ASSET_STOCK: 'style',
      ASSET_BOND: 'region',
      ASSET_COMMODITY: 'segment',
    });
    await gotoOwnPortfolio(page, code);
    const dlg = await openDisplayConfigDialog(page);

    for (const classCode of ['ASSET_STOCK', 'ASSET_BOND', 'ASSET_COMMODITY']) {
      await classBlock(dlg, classCode).getByRole('button', { name: '恢复默认' }).click();
    }
    const payload = await saveAndCapturePayload(page, dlg, code);
    expect(payload).toEqual({ display_config: null });
    await expect(dlg).toHaveCount(0);
    expect(await getConfigViaApi(page, headers, code)).toBeNull();
    expect(errors).toEqual([]);
  });

  test('回填既有配置：多值子集按勾选回显；等于默认子集的显式配置仍为勾选态', async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    const { code, headers } = await setupIsolatedActive(page, testInfo, 'B');
    await setConfigViaApi(page, headers, code, {
      ASSET_STOCK: 'region,segment',
      ASSET_BOND: 'region',
      // 显式覆盖值 == 商品默认子集（segment）：须回显为勾选态而非默认态
      ASSET_COMMODITY: 'segment',
    });
    await gotoOwnPortfolio(page, code);
    const dlg = await openDisplayConfigDialog(page);

    await expect(dimCheckbox(dlg, 'ASSET_STOCK', '地区')).toBeChecked();
    await expect(dimCheckbox(dlg, 'ASSET_STOCK', '细分')).toBeChecked();
    await expect(dimCheckbox(dlg, 'ASSET_STOCK', '风格')).not.toBeChecked();
    await expect(dimCheckbox(dlg, 'ASSET_STOCK', '规模')).not.toBeChecked();
    await expect(dimCheckbox(dlg, 'ASSET_BOND', '地区')).toBeChecked();
    await expect(dimCheckbox(dlg, 'ASSET_BOND', '细分')).not.toBeChecked();
    await expect(dimCheckbox(dlg, 'ASSET_COMMODITY', '细分')).toBeChecked();
    await expect(classBlock(dlg, 'ASSET_COMMODITY')).toContainText('当前：细分');

    await dlg.getByRole('button', { name: '取消' }).click();
    await expect(dlg).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('字典晚到不静默清空：未就绪无保存入口，就绪后回填完整、保存不清空', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 90_000);
    const errors = collectPageErrors(page);
    const { code, headers } = await setupIsolatedActive(page, testInfo, 'L');
    await setConfigViaApi(page, headers, code, { ASSET_STOCK: 'style' });

    // 扣住字典响应，模拟晚到（缓存未命中形态）
    let release!: () => void;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    await page.route(/\/api\/asset-classifications(\?|$)/, async (route) => {
      await gate;
      await route.continue();
    });
    const dictRequested = page.waitForRequest(
      (r) => r.method() === 'GET' && /\/api\/asset-classifications(\?|$)/.test(r.url()),
      { timeout: 15_000 },
    );
    await page.goto(`/portfolio/${code}`);
    await dictRequested;

    // 字典未就绪：页面 loading 闸门持住，不存在可触发误清空的配置入口
    await expect(page.getByRole('button', { name: '分组维度' })).toHaveCount(0);
    release();

    // 就绪后入口出现；初始化 effect 依赖字典数据，回填须完整
    await page
      .getByRole('heading', { level: 1 })
      .first()
      .waitFor({ state: 'visible', timeout: 15_000 });
    const dlg = await openDisplayConfigDialog(page);
    await expect(dimCheckbox(dlg, 'ASSET_STOCK', '风格')).toBeChecked();

    // 不做任何改选直接保存：提交既有显式配置，绝不因初始化竞态误提交 null
    const payload = await saveAndCapturePayload(page, dlg, code);
    expect(payload).toEqual({ display_config: { ASSET_STOCK: 'style' } });
    await expect(dlg).toHaveCount(0);
    expect(await getConfigViaApi(page, headers, code)).toEqual({ ASSET_STOCK: 'style' });
    expect(errors).toEqual([]);
  });

  test('规则事后收紧：存量显式配置引用已失效维度——回填不持有该勾选、直接保存提交过滤后子集或 null', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 120_000);
    const errors = collectPageErrors(page);
    // 读侧 mock 须在首次导航前装好（字典 staleTime 5min，缓存命中后 route 不生效）：
    // 摘掉 ASSET_STOCK 的 style 登记模拟规则事后收紧；后端写侧仍真规则，
    // 故 region,style / style 的存量预置合法可落库
    await page.route(/\/api\/asset-classifications(\?|$)/, async (route) => {
      const resp = await route.fetch();
      const json = (await resp.json()) as {
        dimension_rules?: Record<string, Record<string, string>>;
      };
      if (json.dimension_rules?.ASSET_STOCK) delete json.dimension_rules.ASSET_STOCK.style;
      const headers = { ...resp.headers() };
      delete headers['content-length'];
      delete headers['content-encoding'];
      await route.fulfill({ status: resp.status(), headers, json });
    });
    const { code, headers } = await setupIsolatedActive(page, testInfo, 'T');
    await setConfigViaApi(page, headers, code, { ASSET_STOCK: 'region,style' });
    await gotoOwnPortfolio(page, code);
    const dlg = await openDisplayConfigDialog(page);
    const stock = classBlock(dlg, 'ASSET_STOCK');

    // 展示：style 勾选项不再枚举；回填：仅保留已登记 region（不持有失效勾选）
    await expect(dimCheckbox(dlg, 'ASSET_STOCK', '风格')).toHaveCount(0);
    await expect(stock.getByRole('checkbox')).toHaveCount(3);
    await expect(dimCheckbox(dlg, 'ASSET_STOCK', '地区')).toBeChecked();
    await expect(stock).toContainText('当前：地区');

    // 不改选直接保存：提交过滤后子集（不回提失效维度、不 422）
    const payload = await saveAndCapturePayload(page, dlg, code);
    expect(payload).toEqual({ display_config: { ASSET_STOCK: 'region' } });
    await expect(dlg).toHaveCount(0);
    expect(await getConfigViaApi(page, headers, code)).toEqual({ ASSET_STOCK: 'region' });

    // 第二段：存量维度全部失效 → 回填零勾选、提示回默认；直接保存提交 null
    await setConfigViaApi(page, headers, code, { ASSET_STOCK: 'style' });
    await page.reload();
    await page
      .getByRole('heading', { level: 1 })
      .first()
      .waitFor({ state: 'visible', timeout: 15_000 });
    const dlg2 = await openDisplayConfigDialog(page);
    const stock2 = classBlock(dlg2, 'ASSET_STOCK');
    await expect(stock2.getByRole('checkbox', { checked: true })).toHaveCount(0);
    await expect(stock2).toContainText('当前：默认（地区·细分）');
    const payload2 = await saveAndCapturePayload(page, dlg2, code);
    expect(payload2).toEqual({ display_config: null });
    await expect(dlg2).toHaveCount(0);
    expect(await getConfigViaApi(page, headers, code)).toBeNull();
    expect(errors).toEqual([]);
  });
});