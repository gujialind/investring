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
  openPopover,
} from './helpers';

/**
 * #915 硬前置：DisplayConfigDialog 双端操作用例（改多选之前先补防线，SPEC §7.3）。
 * 本文件先以「现状单选型态」落 characterization 用例（打开 / 改选与回退 / 保存显式
 * 覆盖项 / 全默认提交 null / 回填既有配置 / 字典晚到不静默清空），多选改造随 #915
 * 主实施同 PR 更新本文件——git 历史上「先补后改」顺序可证。
 *
 * 测试层口径（frontend/AGENTS.md §3）：vitest 只测 lib 纯逻辑、不引 jsdom/RTL，
 * 组件交互与运行时行为归 Playwright 双端 E2E，故本弹窗用例全部落 E2E、双端同跑。
 *
 * 数据说明：
 * - 只读用例（打开/改选/回填断言）用种子 E2E_ACTIVE，不写入、不污染共享种子。
 * - 写入用例（保存/null 清空/字典晚到）自建隔离 active 组合（首购确认激活，
 *   现金-only 也渲染持仓区与「分组维度」入口），code 经 isolatedPortfolioCode
 *   区分 project/worker/retry；残留由每轮重建的 E2E 库吸收。
 * - 可配置大类与选项来自种子规则矩阵（backend/app/constants/asset_dimensions.py
 *   ::DIMENSION_RULES）：股票 region/style/size/segment、债券 region/segment、
 *   商品 segment；现金无规则行 → 不出现。内置默认（lib/dimensions.ts
 *   ::SUB_DIM_BY_CLASS）：股票 region、债券/商品 segment。
 * - 「字典晚到」的构造边界：双端详情页 loading 闸门含字典查询（page.tsx
 *   isLoading 含 dictLoading），弹窗与页面同 query key，冷加载下「弹窗已开而
 *   字典未就绪」经 UI 不可达（组件注释明写）。本用例钉住组合保障：字典未就绪
 *   时不存在可触发清空的保存入口；就绪后初始化 effect 回填完整、保存不清空。
 *   若未来去掉闸门且初始化 effect 依赖退化（如只依赖 open），本用例必红。
 */

/** 大类行：行 div 的直接子 span 文本恰为大类名（结构锚点，不按 Tailwind 类定位） */
function classRow(dlg: Locator, className: string): Locator {
  return dlg.locator(`div:has(> span:text-is("${className}"))`);
}

function classSelect(dlg: Locator, className: string): Locator {
  return classRow(dlg, className).getByRole('combobox');
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

/** 在某大类的单选 Select 里选项，并断言触发器回显 */
async function pickOption(
  page: Page,
  dlg: Locator,
  className: string,
  optionName: string,
): Promise<void> {
  const trigger = classSelect(dlg, className);
  await openPopover(page, trigger, page.getByRole('listbox'));
  await page.getByRole('option', { name: optionName, exact: true }).click();
  await expect(trigger).toHaveText(optionName);
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

test.describe('#915 硬前置 DisplayConfigDialog（现状单选型态，双端）', () => {
  test('打开：可配置大类按字典序枚举、默认回显、现金不出现；取消关闭', async ({ page }) => {
    const errors = collectPageErrors(page);
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByTestId('holdings-view-tabs')).toBeVisible({ timeout: 10_000 });

    const dlg = await openDisplayConfigDialog(page);
    // 枚举规则矩阵有行的三大类（不限当前持仓，支持预配置）；现金无规则行不出现
    for (const name of ['股票', '债券', '商品']) {
      await expect(classRow(dlg, name)).toBeVisible();
    }
    await expect(classRow(dlg, '现金')).toHaveCount(0);
    // sort_order 排序：股票(1) → 债券(2) → 商品(3)
    const text = await dlg.innerText();
    expect(text.indexOf('股票')).toBeLessThan(text.indexOf('债券'));
    expect(text.indexOf('债券')).toBeLessThan(text.indexOf('商品'));
    // 未配置组合：各大类回显内置默认（股票 region、债券/商品 segment）
    await expect(classSelect(dlg, '股票')).toHaveText('默认（地区）');
    await expect(classSelect(dlg, '债券')).toHaveText('默认（细分）');
    await expect(classSelect(dlg, '商品')).toHaveText('默认（细分）');

    await dlg.getByRole('button', { name: '取消' }).click();
    await expect(dlg).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('改选与回退（单选形态的勾选/取消）：选项集=默认+规则维度；取消零写入', async ({ page }, testInfo) => {
    // 本用例浮层开合最多（7 轮），mobile webkit stable 等待按帧率计费（#763），
    // 90s 实测仍耗尽在点击中途，按 trade-in-transit 先例放宽到 180s
    mobileTimeout(testInfo, 180_000);
    const errors = collectPageErrors(page);
    const puts: string[] = [];
    page.on('request', (r) => {
      if (r.method() === 'PUT' && r.url().includes('/api/portfolios/')) puts.push(r.url());
    });
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByTestId('holdings-view-tabs')).toBeVisible({ timeout: 10_000 });
    const dlg = await openDisplayConfigDialog(page);

    // 股票选项集 = 默认 + 4 个非禁止维（required/optional 均可选）
    await openPopover(page, classSelect(dlg, '股票'), page.getByRole('listbox'));
    const listbox = page.getByRole('listbox');
    await expect(listbox.getByRole('option')).toHaveCount(5);
    for (const name of ['默认（地区）', '地区', '风格', '规模', '细分']) {
      await expect(listbox.getByRole('option', { name, exact: true })).toBeVisible();
    }
    // 点当前值收起弹层（不改值）
    await listbox.getByRole('option', { name: '默认（地区）', exact: true }).click();

    // 债券/商品选项集 = 默认 + 各自规则矩阵维度
    await openPopover(page, classSelect(dlg, '债券'), page.getByRole('listbox'));
    await expect(page.getByRole('listbox').getByRole('option')).toHaveCount(3);
    await page.getByRole('option', { name: '默认（细分）', exact: true }).click();
    await openPopover(page, classSelect(dlg, '商品'), page.getByRole('listbox'));
    await expect(page.getByRole('listbox').getByRole('option')).toHaveCount(2);
    await page.getByRole('option', { name: '默认（细分）', exact: true }).click();

    // 改选 → 回显；回退默认 → 回显默认；显式选等于内置默认的维度也有匹配选项
    await pickOption(page, dlg, '股票', '风格');
    await pickOption(page, dlg, '股票', '默认（地区）');
    await pickOption(page, dlg, '股票', '地区');
    await pickOption(page, dlg, '股票', '默认（地区）');

    // 取消：不产生任何组合写请求
    await dlg.getByRole('button', { name: '取消' }).click();
    await expect(dlg).toHaveCount(0);
    expect(puts).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('保存显式覆盖项：仅提交非默认键，弹窗关闭+成功提示+后端落库', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 60_000);
    const errors = collectPageErrors(page);
    const { code, headers } = await setupIsolatedActive(page, testInfo, 'S');
    await gotoOwnPortfolio(page, code);
    const dlg = await openDisplayConfigDialog(page);

    await pickOption(page, dlg, '股票', '风格');
    await pickOption(page, dlg, '债券', '地区');
    // 商品保持默认 → 提交体不含该键（仅显式覆盖项）
    const payload = await saveAndCapturePayload(page, dlg, code);
    expect(payload).toEqual({
      display_config: { ASSET_STOCK: 'style', ASSET_BOND: 'region' },
    });
    await expect(dlg).toHaveCount(0);
    await expect(page.getByTestId('toast-card').filter({ hasText: '更新成功' })).toBeVisible();
    expect(await getConfigViaApi(page, headers, code)).toEqual({
      ASSET_STOCK: 'style',
      ASSET_BOND: 'region',
    });
    expect(errors).toEqual([]);
  });

  test('全默认提交 null：既有配置全部回退默认 → 清空落库', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 90_000);
    const errors = collectPageErrors(page);
    const { code, headers } = await setupIsolatedActive(page, testInfo, 'N');
    await setConfigViaApi(page, headers, code, {
      ASSET_STOCK: 'style',
      ASSET_BOND: 'region',
      ASSET_COMMODITY: 'segment',
    });
    await gotoOwnPortfolio(page, code);
    const dlg = await openDisplayConfigDialog(page);

    await pickOption(page, dlg, '股票', '默认（地区）');
    await pickOption(page, dlg, '债券', '默认（细分）');
    await pickOption(page, dlg, '商品', '默认（细分）');
    const payload = await saveAndCapturePayload(page, dlg, code);
    expect(payload).toEqual({ display_config: null });
    await expect(dlg).toHaveCount(0);
    expect(await getConfigViaApi(page, headers, code)).toBeNull();
    expect(errors).toEqual([]);
  });

  test('回填既有配置：显式值回显，等于内置默认的显式值不渲染空白', async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    const { code, headers } = await setupIsolatedActive(page, testInfo, 'B');
    await setConfigViaApi(page, headers, code, {
      ASSET_STOCK: 'style',
      ASSET_BOND: 'region',
      // 显式覆盖值 == 商品内置默认（segment）：CLI/API 可合法落库，
      // 触发器须渲染匹配选项「细分」，不得空白、也不得误显示「默认（细分）」
      ASSET_COMMODITY: 'segment',
    });
    await gotoOwnPortfolio(page, code);
    const dlg = await openDisplayConfigDialog(page);

    await expect(classSelect(dlg, '股票')).toHaveText('风格');
    await expect(classSelect(dlg, '债券')).toHaveText('地区');
    await expect(classSelect(dlg, '商品')).toHaveText('细分');

    await dlg.getByRole('button', { name: '取消' }).click();
    await expect(dlg).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('字典晚到不静默清空：未就绪无保存入口，就绪后回填完整、保存不清空', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 60_000);
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
    await expect(classSelect(dlg, '股票')).toHaveText('风格');

    // 不做任何改选直接保存：提交既有显式配置，绝不因初始化竞态误提交 null
    const payload = await saveAndCapturePayload(page, dlg, code);
    expect(payload).toEqual({ display_config: { ASSET_STOCK: 'style' } });
    await expect(dlg).toHaveCount(0);
    expect(await getConfigViaApi(page, headers, code)).toEqual({ ASSET_STOCK: 'style' });
    expect(errors).toEqual([]);
  });
});