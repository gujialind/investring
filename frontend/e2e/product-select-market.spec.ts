/**
 * 前端 E2E 测试：提交交易产品选择器市场标识（防 #259 回归）+ 市场筛选（#324）
 *
 * 守护 SearchableProductSelect 的市场标识契约（LOF 一码双市场分行）：
 *   - 搜 161017 → 161017.SZ / 161017.OF 两条选项分行；名称 / (code) / 市场 Badge
 *     三段各自独立元素，长名称截断不挤掉 (code) 与市场标识；两行市场文案不同
 *     （A股场内 / 内地场外），场内场外可分辨；
 *   - 选项行 title 悬停给出完整「名称 (code) · 市场」；
 *   - 选中场外项 → 弹层关闭，触发按钮回显「名称 (code) · 市场名」并挂 title 全文本。
 *
 * 市场筛选契约（#324，弹层内条件行）：
 *   - 搜索框与列表之间一行市场 Select，默认「全部市场」，选项四项
 *     （全部市场/A股场内/内地场外/香港互认）；
 *   - 选市场 → 请求带 market 参数（waitForResponse 拦截验证）+ 列表只含该市场；
 *     选「内地场外」时虚拟产品（CASH/IN_TRANSIT*，market 为空）自然排除；
 *   - LOF 161017 + 「内地场外」→ 仅 161017.OF 一条（数据层消除一码多市场歧义）；
 *   - 嵌套交互防线：弹层内 Select（modal）点选后外层 Popover 不误关
 *     （onInteractOutside preventDefault，Radix 公认方案）；
 *   - 筛选不持久：点选即弃、重开复位「全部市场」（列表恢复双市场两条）；
 *   - 提交载荷不变量：经筛选浏览路径点选后 POST /api/trades 的 market
 *     恒等于点选项 market（筛选状态不渗入选中值）。
 *
 * 定位器契约（#217 惯例；选择框弹层交互自 #372 起由 e2e/helpers.ts 单点持有）：
 * 选项行 data-testid="product-option" + data-code/data-market 属性定位（一码多市场时
 * 单靠 code 不唯一，须 code+market 双属性），不解析文案取 code、不依赖 Tailwind
 * 工具类与 Badge/lucide 内部结构；触发按钮 aria-haspopup="dialog" + 文本双条件；
 * 市场筛选 Select 为弹层内唯一 role=combobox（搜索框是 textbox），选项经
 * page 级 role=listbox 定位（Select portal 到 body）。市场 Badge / 回显文本
 * 断言保留——那是用户可见契约，不是实现耦合。
 *
 * 数据说明：组合经 helpers 按 code 直达种子 draft 组合 E2E_PORT（#354），缺组合
 * 即硬失败、不 skip；产品断言依赖 seed_base.py 的 161017.SZ/161017.OF LOF 双市场
 * 种子（#259），无该种子时优雅 skip（条件性数据），不在 CI 造数据。双端复跑：
 * TradesContent 为共享组件（「提交交易」Dialog 双端同构），mobile project 自动覆盖。
 */
import { test, expect, type Page, type Locator } from '@playwright/test';
import {
  E2E_PORT,
  collectPageErrors,
  openSubmitTradeDialog,
  pickFirstPlatformOption,
  platformPopover,
  platformTrigger,
  productOption,
  productOptions,
  productPopover,
  productTrigger,
} from './helpers';

/** LOF 双市场种子常量（与 backend/tests/seed_base.py 的 #259 种子一致） */
const LOF_NAME = '富国中证500指数增强(LOF)A';
const LOF_SZ = { code: '161017.SZ', market: 'CN_EXCHANGE', marketName: 'A股场内' };
const LOF_OTC = { code: '161017.OF', market: 'CN_OTC', marketName: '内地场外' };

/**
 * 打开产品弹层并搜索 161017，返回弹层 Locator。
 * 关键词防抖 300ms + 服务端搜索，waitFor 覆盖等待；无 LOF 双市场种子时优雅 skip。
 */
async function searchLofOptions(page: Page, dlg: Locator): Promise<Locator> {
  await productTrigger(dlg, '请选择产品').click();
  const popover = productPopover(page);
  await popover.getByPlaceholder('搜索产品代码/名称').fill('161017');
  try {
    await productOption(popover, LOF_SZ.code, LOF_SZ.market).waitFor({ timeout: 10_000 });
  } catch {
    test.skip(true, '环境中没有 161017 LOF 双市场种子数据');
  }
  return popover;
}

test.describe('产品选择器市场标识（防 #259 回归）', () => {
  // ---- 用例 1：LOF 双市场选项分行，市场 Badge 独立可见且文案不同，行 title 完整 ----
  test('搜 161017 出现双市场选项，市场标识独立可见且行 title 完整', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);
    const popover = await searchLofOptions(page, dlg);

    const szOption = productOption(popover, LOF_SZ.code, LOF_SZ.market);
    const otcOption = productOption(popover, LOF_OTC.code, LOF_OTC.market);
    await expect(szOption).toBeVisible();
    await expect(otcOption).toBeVisible();
    // 一码双市场恰分行两条（种子环境确定性数据）
    await expect(popover.getByTestId('product-option')).toHaveCount(2);

    // 三段式契约：名称 / (code) / 市场 Badge 各自独立元素——exact 文本匹配仅当
    // 该文本独占一个元素时命中，长名称截断时 (code) 与市场标识仍是独立可见元素
    await expect(szOption.getByText(LOF_NAME, { exact: true })).toBeVisible();
    await expect(szOption.getByText(`(${LOF_SZ.code})`, { exact: true })).toBeVisible();
    await expect(szOption.getByText(LOF_SZ.marketName, { exact: true })).toBeVisible();
    await expect(otcOption.getByText(`(${LOF_OTC.code})`, { exact: true })).toBeVisible();
    await expect(otcOption.getByText(LOF_OTC.marketName, { exact: true })).toBeVisible();

    // 两行市场标识文案不同（场内/场外可分辨，不出现同一文案或缺失）
    await expect(szOption.getByText(LOF_OTC.marketName, { exact: true })).toHaveCount(0);
    await expect(otcOption.getByText(LOF_SZ.marketName, { exact: true })).toHaveCount(0);

    // 行 title 悬停给出完整「名称 (code) · 市场」（截断部分的全文出口）
    await expect(szOption).toHaveAttribute(
      'title',
      `${LOF_NAME} (${LOF_SZ.code}) · ${LOF_SZ.marketName}`
    );
    await expect(otcOption).toHaveAttribute(
      'title',
      `${LOF_NAME} (${LOF_OTC.code}) · ${LOF_OTC.marketName}`
    );

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 2：选中场外项 → 触发按钮回显「名称 (code) · 市场名」并挂 title 全文本 ----
  test('选中场外项回显含「内地场外」且触发按钮挂完整 title', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);
    const popover = await searchLofOptions(page, dlg);

    await productOption(popover, LOF_OTC.code, LOF_OTC.market).click();

    // 点选后弹层关闭（Radix Popover 关闭即卸载内容）
    await expect(popover.getByPlaceholder('搜索产品代码/名称')).toHaveCount(0);

    // 回显「名称 (code) · 市场名」：DOM 文本为全文（视觉截断不影响断言），
    // 市场后缀使场外项与场内项回显可分辨
    const trigger = productTrigger(dlg, LOF_OTC.marketName);
    await expect(trigger).toBeVisible();
    await expect(trigger).toContainText(`${LOF_NAME} (${LOF_OTC.code})`);
    // 触发按钮挂 title 全文本（名称截断时的悬停全文出口）
    await expect(trigger.locator('[title]')).toHaveAttribute(
      'title',
      `${LOF_NAME} (${LOF_OTC.code}) · ${LOF_OTC.marketName}`
    );

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});

// ======================== 市场筛选（#324） ========================

/** 弹层内市场筛选 Select：弹层唯一 role=combobox（搜索框为 textbox，选项行为普通 div） */
function marketFilterTrigger(popover: Locator): Locator {
  return popover.getByRole('combobox');
}

/**
 * 点选市场筛选项：Select listbox portal 到 body，须 page 级定位；
 * 点选后外层 Popover 应保持打开（onInteractOutside preventDefault，#324 关键交互防线）。
 */
async function pickMarketFilter(page: Page, popover: Locator, label: string): Promise<void> {
  await marketFilterTrigger(popover).click();
  await page.getByRole('listbox').getByRole('option', { name: label }).click();
}

/** 产品列表响应体 items 的最小类型（只取本 spec 关心的字段） */
interface ProductListItem {
  code: string;
  market?: string | null;
}

test.describe('产品选择器市场筛选（#324）', () => {
  // ---- 用例 3：弹层内出现市场筛选行，默认「全部市场」，选项四项 ----
  test('弹层内出现市场筛选，默认「全部市场」且选项四项', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);
    await productTrigger(dlg, '请选择产品').click();
    const popover = productPopover(page);

    const trigger = marketFilterTrigger(popover);
    await expect(trigger).toBeVisible();
    await expect(trigger).toHaveText('全部市场');

    // 选项四项且顺序固定（静态项，无需产品数据）
    await trigger.click();
    const options = page.getByRole('listbox').getByRole('option');
    await expect(options).toHaveCount(4);
    await expect(options.nth(0)).toHaveText('全部市场');
    await expect(options.nth(1)).toHaveText('A股场内');
    await expect(options.nth(2)).toHaveText('内地场外');
    await expect(options.nth(3)).toHaveText('香港互认');

    // 点「全部市场」收起 listbox（值不变），弹层与筛选行保持可用
    await options.nth(0).click();
    await expect(popover.getByPlaceholder('搜索产品代码/名称')).toBeVisible();
    await expect(trigger).toHaveText('全部市场');

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 4：选「A股场内」→ 请求带 market=CN_EXCHANGE，列表只含场内产品 ----
  test('选「A股场内」请求带 market=CN_EXCHANGE，列表只含场内产品', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);
    await productTrigger(dlg, '请选择产品').click();
    const popover = productPopover(page);

    const respPromise = page.waitForResponse(
      (r) =>
        r.request().method() === 'GET' &&
        r.url().includes('/api/products') &&
        r.url().includes('market=CN_EXCHANGE'),
      { timeout: 15_000 }
    );
    await pickMarketFilter(page, popover, 'A股场内');
    const resp = await respPromise;
    // 嵌套交互防线：内部 Select（modal）点选后外层 Popover 不误关
    await expect(popover.getByPlaceholder('搜索产品代码/名称')).toBeVisible();

    // 响应体即服务端事实（无渲染时序竞争）：只返回场内产品
    const items = (((await resp.json()) as { items?: ProductListItem[] }).items ?? []);
    test.skip(items.length === 0, '环境中没有场内产品数据');
    for (const it of items) expect(it.market).toBe('CN_EXCHANGE');

    // DOM 渲染同步（keepPreviousData 旧数据占位，轮询至刷新完成）
    await expect(async () => {
      const rows = await productOptions(popover);
      expect(rows.length).toBeGreaterThan(0);
      for (const r of rows) expect(r.market).toBe('CN_EXCHANGE');
    }).toPass();

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 5：选「内地场外」→ 只含 CN_OTC，虚拟产品（CASH/IN_TRANSIT*）不出现 ----
  test('选「内地场外」只含 CN_OTC，虚拟产品不出现', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);
    await productTrigger(dlg, '请选择产品').click();
    const popover = productPopover(page);

    const respPromise = page.waitForResponse(
      (r) =>
        r.request().method() === 'GET' &&
        r.url().includes('/api/products') &&
        r.url().includes('market=CN_OTC'),
      { timeout: 15_000 }
    );
    await pickMarketFilter(page, popover, '内地场外');
    const resp = await respPromise;
    await expect(popover.getByPlaceholder('搜索产品代码/名称')).toBeVisible();

    // 响应体：只返回内地场外；虚拟产品（market 为空）被服务端 market 过滤自然排除
    const items = (((await resp.json()) as { items?: ProductListItem[] }).items ?? []);
    test.skip(items.length === 0, '环境中没有内地场外产品数据');
    for (const it of items) expect(it.market).toBe('CN_OTC');
    expect(
      items.some((it) => it.code === 'CASH' || it.code.startsWith('IN_TRANSIT')),
      '内地场外过滤结果不应含虚拟产品（CASH/IN_TRANSIT*）'
    ).toBe(false);

    // DOM：全部行 data-market=CN_OTC，虚拟产品行不出现
    await expect(async () => {
      const rows = await productOptions(popover);
      expect(rows.length).toBeGreaterThan(0);
      for (const r of rows) expect(r.market).toBe('CN_OTC');
    }).toPass();
    await expect(
      popover.locator('[data-testid="product-option"][data-code="CASH"]')
    ).toHaveCount(0);
    await expect(
      popover.locator('[data-testid="product-option"][data-code^="IN_TRANSIT"]')
    ).toHaveCount(0);

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 6：LOF 161017 选「内地场外」→ 仅 161017.OF 一条（数据层消除歧义） ----
  test('LOF 161017 选「内地场外」仅显示 161017.OF 一条', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);
    const popover = await searchLofOptions(page, dlg);

    const respPromise = page.waitForResponse(
      (r) =>
        r.request().method() === 'GET' &&
        r.url().includes('/api/products') &&
        r.url().includes('market=CN_OTC'),
      { timeout: 15_000 }
    );
    await pickMarketFilter(page, popover, '内地场外');
    await respPromise;

    // 双市场分行两条 → 过滤后仅剩场外一条（keepPreviousData 占位期间重试至刷新）
    await expect(popover.getByTestId('product-option')).toHaveCount(1);
    await expect(productOption(popover, LOF_OTC.code, LOF_OTC.market)).toBeVisible();

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 7（联动断言）：选中 161017.OF 重开 → 筛选复位「全部市场」、回显完整 ----
  test('选中 161017.OF 后重开：市场筛选复位「全部市场」，已选回显完整', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);
    const popover = await searchLofOptions(page, dlg);

    await pickMarketFilter(page, popover, '内地场外');
    await expect(popover.getByTestId('product-option')).toHaveCount(1);
    await productOption(popover, LOF_OTC.code, LOF_OTC.market).click();

    // 弹层关闭；触发按钮回显完整「名称 (code) · 市场名」，市场徽章为「内地场外」
    await expect(popover.getByPlaceholder('搜索产品代码/名称')).toHaveCount(0);
    const trigger = productTrigger(dlg, LOF_OTC.marketName);
    await expect(trigger).toBeVisible();
    await expect(trigger).toContainText(`${LOF_NAME} (${LOF_OTC.code})`);

    // 重开：筛选复位「全部市场」（非"内地场外"），列表恢复双市场两条（证明筛选已弃）
    await trigger.click();
    const popover2 = productPopover(page);
    await expect(marketFilterTrigger(popover2)).toHaveText('全部市场');
    await expect(popover2.getByTestId('product-option')).toHaveCount(2);
    await expect(productOption(popover2, LOF_SZ.code, LOF_SZ.market)).toBeVisible();
    await expect(productOption(popover2, LOF_OTC.code, LOF_OTC.market)).toBeVisible();

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 8（提交载荷不变量）：经筛选浏览路径点选后提交，POST market 恒为点选项 market ----
  test('经市场筛选点选后提交，POST 载荷 market 恒为点选项 market', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);

    // 经「内地场外」筛选路径点选 161017.OF（筛选是浏览态，不渗入选中值）
    await productTrigger(dlg, '请选择产品').click();
    const popover = productPopover(page);
    await popover.getByPlaceholder('搜索产品代码/名称').fill('161017');
    try {
      await productOption(popover, LOF_SZ.code, LOF_SZ.market).waitFor({ timeout: 10_000 });
    } catch {
      test.skip(true, '环境中没有 161017 LOF 双市场种子数据');
    }
    await pickMarketFilter(page, popover, '内地场外');
    await expect(popover.getByTestId('product-option')).toHaveCount(1);
    await productOption(popover, LOF_OTC.code, LOF_OTC.market).click();

    // 交易平台选第一项（无平台数据优雅 skip）；金额填 1000（买入默认，场外无需价格）
    await platformTrigger(dlg, '请选择平台').click();
    await pickFirstPlatformOption(platformPopover(page));
    await dlg.getByLabel('实际支付金额（含费，元）').fill('1000');

    // 拦截创建请求并 abort：只取 body 做断言，不落任何数据
    let postBody: string | null = null;
    await page.route(/\/api\/trades(\?|$)/, async (route) => {
      const req = route.request();
      if (req.method() === 'POST') {
        postBody = req.postData();
        await route.abort();
        return;
      }
      await route.continue();
    });
    await dlg.getByRole('button', { name: '提交交易' }).click();
    await expect.poll(() => postBody, { timeout: 10_000 }).not.toBeNull();

    const body = JSON.parse(postBody!) as Record<string, unknown>;
    expect(body.product_code).toBe(LOF_OTC.code);
    expect(body.market).toBe(LOF_OTC.market);

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
