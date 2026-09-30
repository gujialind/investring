/**
 * 前端 E2E 测试：回归守卫用例
 *
 * 本文件针对排查报告中已修复的 P0 问题设立回归防线，
 * 每个用例注明其防止复发的具体问题。
 */
import { test, expect } from '@playwright/test';
import { E2E_ACTIVE, E2E_PORT, collectPageErrors, gotoPortfolioDetail, gotoPortfolioSubpage } from './helpers';

test.describe('页面渲染回归（防 P0 复发）', () => {
  // 防 P0-2：taskApi.list 返回分页对象却按数组处理，导致 tasks.map is not a function 白屏
  test('任务管理页应正常渲染，不出现客户端崩溃', async ({ page }) => {
    const errors = collectPageErrors(page);

    await page.goto('/settings/tasks');

    await expect(page.getByRole('heading', { name: '任务管理' })).toBeVisible();
    // 页面内「定时任务」文本存在多处（标题/描述），用 heading 角色精确定位
    await expect(page.getByRole('heading', { name: '定时任务' })).toBeVisible();
    // 执行历史区必须渲染（可以是空态，但不能是崩溃）
    await expect(page.getByText('执行历史')).toBeVisible();
    await expect(page.getByText(/Application error/i)).toHaveCount(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // 防 P0-7：侧边栏曾有指向不存在页面的「日志」死链
  test('侧边栏不应包含指向未实现页面的死链', async ({ page }) => {
    await page.goto('/dashboard');
    await expect(page.locator('aside a[href="/settings/logs"]')).toHaveCount(0);
  });

  // 防 P0-5：前端曾提供后端不存在的 DELETE /portfolios/{code}（405）
  test('组合详情页不应出现删除组合入口', async ({ page }) => {
    await gotoPortfolioDetail(page, E2E_PORT);
    await expect(page.getByRole('button', { name: '删除组合' })).toHaveCount(0);
  });
});

test.describe('移动端渲染回归（防 P0 复发）', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  // 防 P0-1：/m/login 曾被 m/layout 鉴权守卫拦截，渲染为空白导致移动端完全无法登录
  test('移动端登录页应渲染登录表单', async ({ page }) => {
    await page.goto('/m/login');
    await expect(page.getByLabel('用户名')).toBeVisible();
    await expect(page.getByLabel('密码')).toBeVisible();
    await expect(page.getByRole('button', { name: '登录' })).toBeVisible();
  });
});

test.describe('移动端布局回归（防 P0-8 复发）', () => {
  // 防 P0-8：/m 页面曾直接复用含 MainLayout 的 PC 页面，导致 PC 侧栏 + 底部 Tab 双导航
  test('移动端管理页不应出现 PC 侧边栏', async ({ page }, testInfo) => {
    // 仅 mobile 项目有意义：桌面 UA 访问 /m/products 会被 middleware 重定向
    // 回 /products（含 PC 侧边栏），断言必挂（此前编译慢时幸免）
    test.skip(testInfo.project.name !== 'mobile', '移动端布局断言仅针对移动项目');

    await page.goto('/m/products');
    // PC 侧边栏是 <aside>，移动端布局只应有 BottomNav（<nav>）
    await expect(page.locator('aside')).toHaveCount(0);
  });
});

test.describe('DateRangePicker 矮视口回归（防 #161 复发）', () => {
  // 防 #161：矮视口下日期区间弹层曾溢出视口、「确定」按钮不可达；
  // #161 以 max-h-[min(calc(100dvh_-_2rem),44rem)] + sticky footer 修复，
  // PR #164 修正 calc 任意值空格语法后高度兜底才真正生效——本用例以修正后行为为准。
  // 仅桌面项目有意义（mobile 项目 numberOfMonths=1、视口语义不同）
  test.use({ viewport: { width: 1280, height: 600 } });

  test('600px 视口下日期区间弹层「确定」按钮应在视口内可达', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '矮视口断言仅针对桌面项目');

    // 数据无关：任意组合的 trades 子页筛选栏都有 DateRangePicker，用 E2E_PORT 直达
    await gotoPortfolioSubpage(page, E2E_PORT, 'trades');

    // DateRangePicker 触发器：默认区间「近1年」，按钮文案为 yyyy-MM-dd ~ yyyy-MM-dd
    await page.getByRole('button').filter({ hasText: '~' }).first().click();

    const confirmBtn = page.getByRole('button', { name: '确定' });
    await expect(confirmBtn).toBeVisible();
    const box = await confirmBtn.boundingBox();
    expect(box, '「确定」按钮未渲染出 boundingBox').not.toBeNull();
    if (box) {
      expect(box.y).toBeGreaterThanOrEqual(0);
      expect(box.y + box.height).toBeLessThanOrEqual(600);
    }
  });
});

test.describe('持仓明细维度二级分组（防 #109 / #114 复发，#128 维度化）', () => {
  // 防 #109：同分组产品曾各自独立成卡、无分组级合计；
  // 关系式断言（子分组头合计 = 名下各卡市值之和），不硬绑定生产快照数字
  // V4 定稿 + #114 修正：分组 chip 始终位于产品名之上（与大类同名除外），
  // chip 行合计恒显示（无论名下 1 卡还是多卡）；
  // data-testid="asset-group-header" 挂在所有 chip 行上
  // #128：分组数据源从 asset_name 换成维度 name（股票→region、债券/商品→segment）
  // #595：卡片改为跨平台聚合卡（holding-product-card），分组契约不变
  test('子分组头合计金额应等于名下各卡市值之和（含单卡分组）', async ({ page }) => {
    // E2E_ACTIVE 种子契约：2 日快照 + 510300.SH 持仓 → 持仓明细区必渲染（不再优雅 skip）。
    // 移动端经 middleware 重定向到 /m 详情页，PortfolioHoldings 为双端共享组件，故本
    // 用例在 mobile project 同样真跑（旧 `href^="/portfolio/"` 定位曾使其在移动端恒 skip）。
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByText('持仓明细')).toBeVisible({ timeout: 10_000 });

    const headers = page.locator('[data-testid="asset-group-header"]');
    // #595 起持仓聚合数据独立于首屏门加载：标题先渲染、数据后到——
    // count() 无重试，先等首个 chip 可见再计数（防数据未回时的 0 计数竞态）
    await expect(headers.first()).toBeVisible({ timeout: 10_000 });
    const headerCount = await headers.count();
    // 510300.SH=ASSET_STOCK 按 region 分组、组名与大类「股票」不同名 → 子分组 chip 必渲染
    expect(headerCount, 'E2E_ACTIVE 持仓应渲染出至少一个子分组头').toBeGreaterThanOrEqual(1);

    // 卡内首个两位小数数字 = 市值大数字（行 1）；占比一位小数不匹配
    const firstAmount = (text: string) =>
      Number(text.replace(/\s+/g, ' ').match(/[\d,]+\.\d{2}/)?.[0].replace(/,/g, ''));

    for (let i = 0; i < headerCount; i++) {
      const header = headers.nth(i);
      const group = header.locator('xpath=ancestor::div[@data-testid="asset-group"]');
      const cards = group.locator('[data-testid="holding-product-card"]');
      expect(await cards.count()).toBeGreaterThanOrEqual(1);

      const headerTotal = firstAmount(await header.innerText());
      let cardSum = 0;
      const cardCount = await cards.count();
      for (let j = 0; j < cardCount; j++) {
        cardSum += firstAmount(await cards.nth(j).innerText());
      }
      expect(headerTotal).toBeCloseTo(cardSum, 1);
    }
  });
});

test.describe('组合净值走势卡双端一致（#649）', () => {
  // 抽取前：区间切换只在桌面落地（#99），移动端恒全量、无 chips、空数据整卡不渲染；
  // 桌面页级分支不论区间恒写「该区间暂无净值数据」。移动端那句「暂无净值数据」来自
  // NavCurve 内置空态，而抽取前该页只在有数据时才渲染曲线，故内置句在组合页是不可达
  // 分支（只在产品/平台页曲线卡生效）——两套文案是潜在漂移，不是两端各自呈现过的观感。
  // 抽取后组件自持 navRange 与 start_date，双端同能力、空态文案单点。
  test('区间 chips 双端同款：默认成立以来不带 start_date，切区间后按区间起点重新取数', async ({ page }) => {
    // 「成立以来 = 不带 start_date」是移动端默认态与抽取前恒全量等价的锚点，注册必须在 goto 前
    const fullRange = page.waitForRequest(
      (r) => r.url().includes('/nav-history') && !r.url().includes('start_date='),
      { timeout: 10_000 },
    );
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await fullRange;

    const card = page.getByTestId('portfolio-nav-trend-card');
    await expect(card).toBeVisible();
    // 默认「成立以来」= 移动端抽取前的恒全量行为，双端同一初值（同一缓存键）
    await expect(page.getByTestId('nav-range-all')).toHaveAttribute('aria-pressed', 'true');

    // 归属与取值都钉住：只断 URL 里「有 start_date=」时，6m/1y/3y 的分支写反照样全绿。
    // 容差 30 天远小于相邻窗口间隔（182/365/1096），能吸收 setMonth 月末进位与闰年，
    // 又不至于放过整档窗口取错。
    const expectedDaysBack: Record<string, number> = { '6m': 182, '1y': 365, '3y': 1096 };
    for (const [key, days] of Object.entries(expectedDaysBack)) {
      const ranged = page.waitForRequest(
        (r) => r.url().includes('/nav-history') && r.url().includes('start_date='),
        { timeout: 10_000 },
      );
      await page.getByTestId(`nav-range-${key}`).click();
      const sent = new URL((await ranged).url()).searchParams.get('start_date') ?? '';
      expect(sent, `${key} 应带 yyyy-mm-dd 形式的 start_date`).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      const [year, month, day] = sent.split('-').map(Number);
      // 按本地时区解析（不做 UTC 切片，#640 U-4），与组件侧 toDateOnly 同一时钟
      const daysBack = Math.round(
        (Date.now() - new Date(year, month - 1, day).getTime()) / 86_400_000,
      );
      expect(
        Math.abs(daysBack - days),
        `${key} 起点 ${sent} 偏离 ${days} 天过多`,
      ).toBeLessThanOrEqual(30);
      await expect(page.getByTestId(`nav-range-${key}`)).toHaveAttribute('aria-pressed', 'true');
    }
    await expect(page.getByTestId('nav-range-all')).toHaveAttribute('aria-pressed', 'false');
  });

  test('空数据：卡片仍渲染 + 卡内空态，全量态与区间态措辞由组件单点决定', async ({ page }) => {
    // 延迟 3s  fulfill，制造一个可断言的「加载中」窗口（#649 L2 S2：加载期不得写成空态）
    await page.route('**/nav-history**', async (route) => {
      await new Promise((r) => setTimeout(r, 3_000));
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    const card = page.getByTestId('portfolio-nav-trend-card');
    // 移动端抽取前是「整卡不渲染」，现与桌面一致：卡恒在、加载期 spinner、空数据才落空态。
    await expect(card).toBeVisible();
    // 取数在途时先出 spinner：修复前此处会渲染空态文案，本断言即红
    await expect(card.getByText('暂无净值数据', { exact: true })).toHaveCount(0);
    // 措辞用 exact 断言——「该区间暂无净值数据」本身包含「暂无净值数据」，
    // 非 exact 会让全量态断言在区间态文案下也假绿
    await expect(card.getByText('暂无净值数据', { exact: true })).toBeVisible();
    await page.getByTestId('nav-range-1y').click();
    await expect(card.getByText('该区间暂无净值数据', { exact: true })).toBeVisible();
  });
});
