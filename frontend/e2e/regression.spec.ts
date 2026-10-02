/**
 * 前端 E2E 测试：回归守卫用例
 *
 * 本文件针对排查报告中已修复的 P0 问题设立回归防线，
 * 每个用例注明其防止复发的具体问题。
 */
import { test, expect } from '@playwright/test';
import { E2E_ACTIVE, E2E_PORT, collectPageErrors, gotoPortfolioDetail, gotoPortfolioSubpage } from './helpers';
// 运行时导入导航单源：断言清单与返回出口由同一份数据派生，不在 spec 里抄第三份（#650）
import { NAV_ITEMS, mobileExitPaths } from '../src/components/shared/navItems';

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

test.describe('桌面窄屏底部导航（#658 L2 Blocker 1 回归网）', () => {
  // 防 Blocker 1：basePath 写成 "/" 时 href 拼成 "//dashboard" 这种协议相对 URL（指向名为
  // dashboard 的 host），桌面窄屏五个入口整体离开本站、激活高亮恒假。此前三层门禁结构上
  // 碰不到这一面：chromium project 固定 1280 宽（本导航被 lg:hidden 隐藏）、mobile project
  // 走 /m 前缀（拼接恰好正确）、vitest 分母不含 components。
  test.use({ viewport: { width: 900, height: 800 } });

  test('窄屏底部导航 href 同源、当前项带 aria-current、点击到达同站一级页', async ({ page }, testInfo) => {
    test.skip(
      testInfo.project.name !== 'chromium',
      '桌面窄视口断言仅针对桌面项目（mobile 项目 390px 视口走 /m 端，另一条用例覆盖）',
    );

    const nav = page.getByTestId('bottom-nav');
    await page.goto('/dashboard');
    await expect(nav).toBeVisible();
    const origin = new URL(page.url()).origin;

    // 协议相对形态是本次回归的唯一特征：钉住它比钉住链接条数更判得出修复前后
    await expect(nav.locator('a[href^="//"]')).toHaveCount(0);
    for (const item of NAV_ITEMS.filter((i) => i.mobileTab)) {
      await expect(nav.locator(`a[href="${item.href}"]`)).toHaveCount(1);
    }
    // 高亮本身是类名（定位器契约禁止按它定位），aria-current 是同一判定的可测面
    await expect(nav.locator('a[href="/dashboard"][aria-current="page"]')).toHaveCount(1);

    await nav.locator('a[href="/portfolio"]').click();
    await page.waitForURL('**/portfolio');
    const after = new URL(page.url());
    expect(
      `${after.origin}${after.pathname}`,
      `点击应留在本站一级页，实际落到 ${page.url()}`,
    ).toBe(`${origin}/portfolio`);
    await expect(nav.locator('a[href="/portfolio"][aria-current="page"]')).toHaveCount(1);
  });
});

test.describe('移动端非 Tab 管理页的站内出口（#658 L2 Blocker 2 回归网）', () => {
  test('经页内入口进入后可返回宿主页，不再「可达即被困」', async ({ page }, testInfo) => {
    test.skip(
      testInfo.project.name !== 'mobile',
      '移动端出口断言仅针对移动项目（桌面端这些页走侧边栏，无此形态）',
    );

    // 先走一遍真实入口：钉住组件里的 /m/* 字面量与 navItems 的 mobileEntryHost 仍对齐
    await page.goto('/m/settings');
    await page.getByRole('link', { name: '平台管理' }).click();
    await page.waitForURL('**/m/platforms');

    for (const exit of mobileExitPaths('/m')) {
      await page.goto(exit.path);
      const back = page.getByRole('link', { name: `返回${exit.label}` });
      await expect(back, `${exit.path} 应有站内出口`).toBeVisible();
      // 这些页不渲染底部导航，返回入口是页内唯一出路
      await expect(page.getByTestId('bottom-nav')).toHaveCount(0);
      await back.click();
      await page.waitForURL(`**${exit.href}`);
      expect(new URL(page.url()).pathname, `返回应落在 ${exit.href}`).toBe(exit.href);
    }
  });
});
