/**
 * 前端 E2E 测试：平台选择框搜索（防 #177 回归）
 *
 * 守护 SearchablePlatformSelect 的核心行为契约：
 *   - 客户端按 name/code 过滤（大小写不敏感、name 与 code 分支各自覆盖）、
 *     无匹配空态、清空恢复全量、点选/关闭后重开恢复全量；
 *   - 前置特殊项（全部平台/同交易平台）置顶、不参与过滤、点选回传空值语义；
 *   - 点选平台回显 name (code)，筛选请求参数 platform_code 含/不含；
 *   - 现金平台默认「同交易平台」时，提交请求 body 不含 cash_platform_code；
 *   - 现金转移互斥项可见但禁用；
 *   - 申赎/调仓/事件表单的平台原生 <select required> 被自定义组件替换后，
 *     空平台提交须被前端手动校验拦截（#209/#216）；
 *   - 接入点冒烟（#217）：share-change-events 条件渲染、申赎筛选栏、
 *     PC 持仓「更新非净值资产」。
 *
 * 定位器契约（#217）：组件侧 data-testid（platform-trigger / platform-option /
 * platform-special-option / platform-empty / cash-update-trigger），平台 code 从
 * 选项行 data-code 属性读取，不依赖 Tailwind 工具类与 lucide 图标类名；
 * 触发按钮回显文本断言保留——那是用户可见契约，不是实现耦合。选择框弹层的触发器/
 * 弹层/选项行定位自 #372 起由 e2e/helpers.ts 单点持有；本文件原先 3 处按 Tailwind
 * `div.cursor-pointer` 定位产品选项行（与上述契约相悖）已随该收敛一并消除。
 *
 * 数据说明：组合经 helpers 按 code 直达种子 draft 组合 E2E_PORT（#354），缺组合
 * 即硬失败、不 skip；搜索词不写死——打开弹层读取第一个平台选项推导；平台数 < 2、
 * 无平台数据等条件性数据仍优雅 skip，不在 CI 造数据。
 */
import { test, expect, type Page } from '@playwright/test';
import {
  E2E_PORT,
  collectPageErrors,
  dialogByTitle,
  expectFilteredPlatformOptions,
  firstPlatformOption,
  gotoPortfolioSubpage,
  optionName,
  pickFirstProduct,
  pickPlatformOption,
  platformOptions,
  platformPopover,
  platformTrigger,
  portfolioPath,
} from './helpers';

/** 进入 E2E_PORT 调仓交易页（桌面渲染信号：提交交易按钮） */
async function gotoTradesPage(page: Page): Promise<void> {
  await gotoPortfolioSubpage(page, E2E_PORT, 'trades');
}

/** 进入 E2E_PORT 申赎页（draft 组合触发按钮文案为「首次申购激活」） */
async function gotoSubscriptionsPage(page: Page): Promise<void> {
  await gotoPortfolioSubpage(page, E2E_PORT, 'subscriptions');
}

/** 进入 E2E_PORT 持仓页（桌面端；移动端无「更新非净值资产」入口，调用方需 skip mobile） */
async function gotoPositionsPage(page: Page): Promise<void> {
  await gotoPortfolioSubpage(page, E2E_PORT, 'positions');
}

/** 进入 E2E_PORT 份额变动事件页（渲染信号：新建事件按钮） */
async function gotoShareChangeEventsPage(page: Page): Promise<void> {
  await gotoPortfolioSubpage(page, E2E_PORT, 'share-change-events');
}

test.describe('平台选择框搜索（防 #177 回归）', () => {
  // ---- 用例 1：调仓页筛选平台可搜索 + 特殊项「全部平台」+ 请求参数断言 ----
  test('调仓页筛选平台可搜索，保留「全部平台」且请求参数正确', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '桌面筛选栏断言仅针对桌面项目');
    const errors = collectPageErrors(page);

    // 「不含」侧：默认（全部平台）进页面，列表请求不带 platform_code。
    // useTradeList staleTime=30s——选平台再切回「全部平台」会命中 fresh 缓存不发请求，
    // 故「不含」断言锚定进页面的初始请求，而非切回后的请求
    const initialResp = page.waitForResponse(
      (r) =>
        r.request().method() === 'GET' &&
        r.url().includes('/api/trades') &&
        !r.url().includes('platform_code='),
      { timeout: 15_000 }
    );
    await page.goto(portfolioPath(E2E_PORT, 'trades'));
    await page.getByRole('button', { name: '提交交易' }).first().waitFor({ timeout: 15_000 });
    await initialResp;

    // 打开筛选栏平台弹层，动态取第一个平台 code 片段作为搜索词
    await platformTrigger(page, '全部平台').click();
    const popover = platformPopover(page);
    const { keyword } = await firstPlatformOption(popover);
    const totalOptions = await popover.getByTestId('platform-option').count();

    // 输入搜索词 → 仅剩命中项；特殊项「全部平台」不参与过滤恒显示
    await popover.getByPlaceholder('搜索平台名称/代码').fill(keyword);
    const matched = await expectFilteredPlatformOptions(popover, keyword);
    await expect(popover.getByTestId('platform-special-option')).toHaveText('全部平台');

    // 点选 → 弹层关闭、触发按钮回显 name (code)；列表请求带 platform_code（「含」侧）
    const picked = matched[0];
    const respWith = page.waitForResponse(
      (r) =>
        r.request().method() === 'GET' &&
        r.url().includes('/api/trades') &&
        r.url().includes(`platform_code=${picked.code}`),
      { timeout: 10_000 }
    );
    await pickPlatformOption(popover, picked.code);
    await respWith;
    const selectedTrigger = platformTrigger(page, picked.text);
    await expect(selectedTrigger).toBeVisible();

    // 带过滤词点选关闭后重开 → 搜索词已重置、选项恢复全量（点选路径不经过 onOpenChange）
    await selectedTrigger.click();
    const popover2 = platformPopover(page);
    await expect(popover2.getByPlaceholder('搜索平台名称/代码')).toHaveValue('');
    await expect(popover2.getByTestId('platform-option')).toHaveCount(totalOptions);
    await expect(popover2.getByTestId('platform-special-option')).toHaveCount(1);

    // 点「全部平台」→ 触发按钮回显恢复
    await popover2.getByTestId('platform-special-option').click();
    await expect(platformTrigger(page, '全部平台')).toBeVisible();

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 2：提交交易表单「交易平台」可搜索、「现金平台」默认「同交易平台」置顶 ----
  test('提交交易表单：交易平台可搜索，现金平台默认「同交易平台」', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '桌面表单断言仅针对桌面项目');
    const errors = collectPageErrors(page);
    await gotoTradesPage(page);
    await page.getByRole('button', { name: '提交交易' }).first().click();
    const dlg = dialogByTitle(page, '提交交易');
    await dlg.waitFor();

    // 交易平台：搜索 → 过滤 → 点选 → 触发按钮回显 name (code)
    await platformTrigger(dlg, '请选择平台').click();
    const popover = platformPopover(page);
    const { keyword } = await firstPlatformOption(popover);
    await popover.getByPlaceholder('搜索平台名称/代码').fill(keyword);
    const matched = await expectFilteredPlatformOptions(popover, keyword);
    await pickPlatformOption(popover, matched[0].code);
    // 回显断言：按钮 accessible name 是 Label「交易平台」而非文本，用 hasText 定位回显串
    await expect(platformTrigger(dlg, matched[0].text)).toBeVisible();

    // 现金平台：未操作时回显特殊项「同交易平台」；打开后特殊项置顶
    const cashTrigger = platformTrigger(dlg, '同交易平台');
    await expect(cashTrigger).toBeVisible();
    await cashTrigger.click();
    const popover2 = platformPopover(page);
    await expect(popover2.getByTestId('platform-special-option')).toHaveText('同交易平台');

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 3：申赎表单原生 required 被自定义组件替换后，空平台提交须被前端手动校验拦截 ----
  test('申赎表单未选平台提交被前端拦截', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '桌面表单断言仅针对桌面项目');
    const errors = collectPageErrors(page);
    await gotoSubscriptionsPage(page);
    await page.getByRole('button', { name: /提交申请|首次申购激活/ }).first().click();
    const dlg = dialogByTitle(page, /提交申请|首次申购激活/);
    await dlg.waitFor();

    // 选投资人（原生 select 保留）、填金额（申购模式默认）；平台刻意不选
    const investorSelect = dlg.locator('select#investor_code');
    const investorCount = await investorSelect.locator('option').count();
    test.skip(investorCount < 2, '环境中没有投资人数据');
    await investorSelect.selectOption({ index: 1 });
    await dlg.getByLabel('金额（元）').fill('1000');

    // 提交 → 前端拦截：校验 toast 出现、Dialog 保持打开、未发出创建请求
    let createRequested = false;
    page.on('request', (req) => {
      if (req.method() === 'POST' && req.url().includes('/api/subscriptions')) {
        createRequested = true;
      }
    });
    await dlg.getByRole('button', { name: '提交申请' }).click();

    await expect(page.getByRole('heading', { name: '表单校验失败' })).toBeVisible();
    // toast 卡片内断言 message（页面另有同文案的平台占位符，不能全局 getByText）
    const toast = page
      .getByRole('heading', { name: '表单校验失败' })
      .locator('xpath=ancestor::div[contains(@class,"rounded-lg")][1]');
    await expect(toast.getByText('请选择平台', { exact: true })).toBeVisible();
    await expect(dlg).toBeVisible();
    expect(createRequested, '未选平台时不应发出创建请求').toBe(false);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 4：调仓表单空平台提交被前端手动校验拦截（#209，镜像用例 3 申赎拦截形态）----
  test('提交交易表单未选平台提交被前端拦截', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '桌面表单断言仅针对桌面项目');
    const errors = collectPageErrors(page);
    await gotoTradesPage(page);
    await page.getByRole('button', { name: '提交交易' }).first().click();
    const dlg = dialogByTitle(page, '提交交易');
    await dlg.waitFor();

    // 选产品（可搜索下拉取第一项）、填金额（买入模式默认）；平台刻意不选
    await pickFirstProduct(page, dlg);
    await dlg.getByLabel('实际支付金额（含费，元）').fill('1000');

    // 提交 → 前端拦截：校验 toast 出现、Dialog 保持打开、未发出创建请求
    let createRequested = false;
    page.on('request', (req) => {
      if (req.method() === 'POST' && req.url().includes('/api/trades')) {
        createRequested = true;
      }
    });
    await dlg.getByRole('button', { name: '提交交易' }).click();

    await expect(page.getByRole('heading', { name: '表单校验失败' })).toBeVisible();
    // toast 卡片内断言 message（页面另有同文案的平台占位符，不能全局 getByText）
    const toast = page
      .getByRole('heading', { name: '表单校验失败' })
      .locator('xpath=ancestor::div[contains(@class,"rounded-lg")][1]');
    await expect(toast.getByText('请选择平台', { exact: true })).toBeVisible();
    await expect(dlg).toBeVisible();
    expect(createRequested, '未选平台时不应发出创建请求').toBe(false);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 5：新建事件表单平台级事件空平台提交被前端手动校验拦截（#216，镜像用例 3/4）----
  test('新建事件表单：平台级事件未选平台提交被前端拦截', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '桌面表单断言仅针对桌面项目');
    const errors = collectPageErrors(page);
    await gotoShareChangeEventsPage(page);
    await page.getByRole('button', { name: '新建事件' }).click();
    const dlg = dialogByTitle(page, '新建份额变动事件');
    await dlg.waitFor();

    // 事件类型选平台级「现金分红」→ 平台选择框出现；选产品（可搜索下拉取第一项）、平台刻意不选
    await dlg.getByRole('combobox').click();
    await page.getByRole('option', { name: '现金分红' }).click();
    await expect(platformTrigger(dlg, '选择平台')).toBeVisible();
    await pickFirstProduct(page, dlg);

    // 提交 → 前端拦截：校验 toast 出现、Dialog 保持打开、未发出创建请求
    let createRequested = false;
    page.on('request', (req) => {
      if (req.method() === 'POST' && req.url().includes('/api/share-change-events')) {
        createRequested = true;
      }
    });
    await dlg.getByRole('button', { name: '创建' }).click();

    await expect(page.getByRole('heading', { name: '表单校验失败' })).toBeVisible();
    // toast 卡片内断言 message（页面另有同文案的平台占位符，不能全局 getByText）
    const toast = page
      .getByRole('heading', { name: '表单校验失败' })
      .locator('xpath=ancestor::div[contains(@class,"rounded-lg")][1]');
    await expect(toast.getByText('请选择平台', { exact: true })).toBeVisible();
    await expect(dlg).toBeVisible();
    expect(createRequested, '未选平台时不应发出创建请求').toBe(false);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 6：现金转移互斥——对方已选平台在列表中可见但禁用 ----
  test('现金转移：对方已选平台可见但禁用，点击不生效', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '移动端无现金转移功能');
    const errors = collectPageErrors(page);
    await gotoPositionsPage(page);
    await page.getByRole('button', { name: '现金转移' }).click();
    const dlg = dialogByTitle(page, '平台间现金转移');
    await dlg.waitFor();

    // 转出平台选第一项
    await platformTrigger(dlg, '选择转出平台').click();
    const popover = platformPopover(page);
    await popover.getByTestId('platform-option').first().waitFor();
    const options = await platformOptions(popover);
    test.skip(options.length < 2, '环境中平台数 < 2，无法验证互斥');
    const picked = options[0];
    await pickPlatformOption(popover, picked.code);
    await expect(platformTrigger(dlg, picked.text)).toBeVisible();

    // 打开转入平台：已选平台行存在且 aria-disabled；点击不生效（弹层不关闭、值不变）
    await platformTrigger(dlg, '选择转入平台').click();
    const popover2 = platformPopover(page);
    const disabledRow = popover2.locator(
      '[data-testid="platform-option"][aria-disabled="true"]',
      { hasText: picked.text }
    );
    await expect(disabledRow).toHaveCount(1);
    // aria-disabled 元素须 force 点击（绕过 Playwright actionability 的 enabled 等待）
    await disabledRow.click({ force: true });
    await expect(popover2.getByPlaceholder('搜索平台名称/代码')).toBeVisible();
    await expect(platformTrigger(dlg, '选择转入平台')).toBeVisible();

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 7：移动端平台选择框可搜索（m-positions 弹窗 + trades 移动筛选面板）----
  test('移动端：更新非净值资产与筛选面板的平台选择框可搜索', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '仅移动端项目');
    const errors = collectPageErrors(page);

    // /m/portfolio/{code}/positions → 「更新非净值资产」（纯图标触发器）→ 平台搜索点选
    await page.goto(portfolioPath(E2E_PORT, 'positions'));
    await expect(page).toHaveURL(/\/m\/portfolio\//);
    const refreshTrigger = page.getByTestId('cash-update-trigger');
    await refreshTrigger.waitFor({ timeout: 15_000 });
    await refreshTrigger.click();
    const dlg = dialogByTitle(page, '更新非净值资产');
    await dlg.waitFor();
    await platformTrigger(dlg, '请选择平台').click();
    const popover = platformPopover(page);
    const { keyword } = await firstPlatformOption(popover);
    await popover.getByPlaceholder('搜索平台名称/代码').fill(keyword);
    const matched = await expectFilteredPlatformOptions(popover, keyword);
    await pickPlatformOption(popover, matched[0].code);
    await expect(platformTrigger(dlg, matched[0].text)).toBeVisible();
    await dlg.getByRole('button', { name: '取消' }).click();

    // trades 移动页筛选面板（覆盖 shared 组件 mobile variant）：展开「筛选」→ 平台控件可搜索
    await page.goto(portfolioPath(E2E_PORT, 'trades'));
    await page.getByRole('button', { name: '提交交易' }).first().waitFor({ timeout: 15_000 });
    await page.getByRole('button', { name: '筛选' }).click();
    await platformTrigger(page, '全部平台').click();
    const popover2 = platformPopover(page);
    const opt2 = await firstPlatformOption(popover2);
    await popover2.getByPlaceholder('搜索平台名称/代码').fill(opt2.keyword);
    const matched2 = await expectFilteredPlatformOptions(popover2, opt2.keyword);
    await pickPlatformOption(popover2, matched2[0].code);
    await expect(platformTrigger(page, matched2[0].text)).toBeVisible();

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 8：清空搜索词恢复全量 + 无匹配空态 ----
  test('清空搜索词恢复全量，无匹配显示空态提示', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '桌面筛选栏断言仅针对桌面项目');
    const errors = collectPageErrors(page);
    await gotoTradesPage(page);
    await platformTrigger(page, '全部平台').click();
    const popover = platformPopover(page);
    const { keyword } = await firstPlatformOption(popover);

    const optionRows = popover.getByTestId('platform-option');
    const totalOptions = await optionRows.count();
    const input = popover.getByPlaceholder('搜索平台名称/代码');

    // 输入搜索词再清空 → 选项恢复全量
    await input.fill(keyword);
    await input.fill('');
    await expect(optionRows).toHaveCount(totalOptions);

    // 不可能命中的词 → 空态提示；平台行清零，特殊项仍置顶
    await input.fill('zzz-none');
    await expect(popover.getByTestId('platform-empty')).toBeVisible();
    await expect(async () => {
      expect(await platformOptions(popover)).toHaveLength(0);
    }).toPass();
    await expect(popover.getByTestId('platform-special-option')).toHaveText('全部平台');

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 9：按平台名称片段搜索（覆盖过滤的 name 分支，而非仅 code 分支）----
  test('按平台名称片段搜索可过滤', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '桌面筛选栏断言仅针对桌面项目');
    const errors = collectPageErrors(page);
    await gotoTradesPage(page);
    await platformTrigger(page, '全部平台').click();
    const popover = platformPopover(page);
    const { text, code } = await firstPlatformOption(popover);
    const name = optionName(text, code);
    test.skip(name.length === 0 || name === text, '平台名称为空或无 code 后缀，无法推导名称搜索词');
    const nameKw = name.slice(0, 2).toLowerCase();

    await popover.getByPlaceholder('搜索平台名称/代码').fill(nameKw);
    const matched = await expectFilteredPlatformOptions(popover, nameKw);
    // 通用断言经 code 巧合也可通过，须显式确认至少一项按 name 命中（抽样平台自身必命中）
    expect(
      matched.some((o) => optionName(o.text, o.code).toLowerCase().includes(nameKw)),
      `过滤结果应含按名称命中的平台: ${matched.map((o) => o.text).join(' | ')}`
    ).toBe(true);

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 10：现金平台默认「同交易平台」时，提交请求 body 不含 cash_platform_code ----
  test('现金平台默认「同交易平台」时提交 body 不含 cash_platform_code', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '桌面表单断言仅针对桌面项目');
    const errors = collectPageErrors(page);
    await gotoTradesPage(page);
    await page.getByRole('button', { name: '提交交易' }).first().click();
    const dlg = dialogByTitle(page, '提交交易');
    await dlg.waitFor();

    // 产品：可搜索下拉取第一项（无产品数据优雅 skip）
    await pickFirstProduct(page, dlg);

    // 交易平台选第一项；现金平台刻意不碰，保持默认「同交易平台」
    await platformTrigger(dlg, '请选择平台').click();
    const popover = platformPopover(page);
    const options = await platformOptions(popover);
    test.skip(options.length === 0, '环境中没有平台数据');
    await pickPlatformOption(popover, options[0].code);

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
    expect(body.platform_code).toBe(options[0].code);
    expect('cash_platform_code' in body, '「同交易平台」默认时 cash_platform_code 应被省略').toBe(false);

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 11（#217 冒烟）：share-change-events 新建事件 Dialog 平台选择框（条件渲染接入点）----
  test('新建事件表单：事件类型切换的平台选择框条件渲染与搜索点选', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '移动端无份额变动事件页');
    const errors = collectPageErrors(page);
    await gotoShareChangeEventsPage(page);
    await page.getByRole('button', { name: '新建事件' }).click();
    const dlg = dialogByTitle(page, '新建份额变动事件');
    await dlg.waitFor();

    // 条件渲染接线：默认「现金分红」（平台级）平台框可见 → 切基金级「份额拆分」消失 → 切回重现
    await expect(platformTrigger(dlg, '选择平台')).toBeVisible();
    await dlg.getByRole('combobox').click();
    await page.getByRole('option', { name: '份额拆分' }).click();
    await expect(platformTrigger(dlg, '选择平台')).toHaveCount(0);
    await dlg.getByRole('combobox').click();
    await page.getByRole('option', { name: '现金分红' }).click();
    const trigger = platformTrigger(dlg, '选择平台');
    await expect(trigger).toBeVisible();

    // 搜索 → 点选 → 触发按钮回显 name (code)（无平台数据时 firstPlatformOption 内优雅 skip）
    await trigger.click();
    const popover = platformPopover(page);
    const { keyword } = await firstPlatformOption(popover);
    await popover.getByPlaceholder('搜索平台名称/代码').fill(keyword);
    const matched = await expectFilteredPlatformOptions(popover, keyword);
    await pickPlatformOption(popover, matched[0].code);
    await expect(platformTrigger(dlg, matched[0].text)).toBeVisible();

    await dlg.getByRole('button', { name: '取消' }).click();
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 12（#217 冒烟）：申赎筛选栏平台选择框（特殊项「全部平台」+ 请求参数断言）----
  test('申赎页筛选平台可搜索，保留「全部平台」且请求参数正确', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '桌面筛选栏断言仅针对桌面项目');
    const errors = collectPageErrors(page);
    await gotoSubscriptionsPage(page);

    // 打开筛选栏平台弹层，动态取第一个平台 code 片段作为搜索词
    await platformTrigger(page, '全部平台').click();
    const popover = platformPopover(page);
    const { keyword } = await firstPlatformOption(popover);

    // 输入搜索词 → 仅剩命中项；特殊项「全部平台」不参与过滤恒显示
    await popover.getByPlaceholder('搜索平台名称/代码').fill(keyword);
    const matched = await expectFilteredPlatformOptions(popover, keyword);
    await expect(popover.getByTestId('platform-special-option')).toHaveText('全部平台');

    // 点选 → 触发按钮回显 name (code)；列表请求带 platform_code（镜像用例 1 的请求断言形态）
    const picked = matched[0];
    const respWith = page.waitForResponse(
      (r) =>
        r.request().method() === 'GET' &&
        r.url().includes('/api/subscriptions') &&
        r.url().includes(`platform_code=${picked.code}`),
      { timeout: 10_000 }
    );
    await pickPlatformOption(popover, picked.code);
    await respWith;
    await expect(platformTrigger(page, picked.text)).toBeVisible();

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 13（#217 冒烟）：PC 持仓页「更新非净值资产」平台选择框（移动端同控件由用例 7 覆盖）----
  test('持仓页「更新非净值资产」平台选择框可搜索点选', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '移动端同控件已由移动端用例覆盖');
    const errors = collectPageErrors(page);
    await gotoPositionsPage(page);
    await page.getByRole('button', { name: '更新非净值资产' }).click();
    const dlg = dialogByTitle(page, '更新非净值资产');
    await dlg.waitFor();

    // 平台选择框：搜索 → 过滤 → 点选 → 触发按钮回显 name (code)（不提交，取消关闭）
    await platformTrigger(dlg, '请选择平台').click();
    const popover = platformPopover(page);
    const { keyword } = await firstPlatformOption(popover);
    await popover.getByPlaceholder('搜索平台名称/代码').fill(keyword);
    const matched = await expectFilteredPlatformOptions(popover, keyword);
    await pickPlatformOption(popover, matched[0].code);
    await expect(platformTrigger(dlg, matched[0].text)).toBeVisible();

    await dlg.getByRole('button', { name: '取消' }).click();
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
