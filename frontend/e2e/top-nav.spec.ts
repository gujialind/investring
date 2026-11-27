/** #944 V2-2：桌面横向顶部导航与窄屏回退。 */
import { test, expect, type Page } from '@playwright/test';
import { collectPageErrors, openPopover, settlePopovers } from './helpers';
import { sidebarItemsForRole } from '../src/components/shared/navItems';

/** 换 viewer 会话：清凭证后走真实登录流，复用现有种子账号 VIEWER/viewer123。 */
async function loginAsViewer(page: Page): Promise<void> {
  await page.goto('/');
  await page.context().clearCookies();
  await page.evaluate(() => window.localStorage.clear());
  await page.goto('/login');
  await page.waitForFunction(() => {
    const form = document.querySelector('form');
    return form && Object.keys(form).some((key) => key.startsWith('__reactProps'));
  });
  await page.getByLabel('用户名').fill('VIEWER');
  await page.getByLabel('密码').fill('viewer123');
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForURL(/\/(m\/)?dashboard/, { waitUntil: 'domcontentloaded' });
}

test.describe('桌面顶部导航（#944 V2-2）', () => {
  test.use({ viewport: { width: 1280, height: 720 } });

  test.beforeEach(async ({}, testInfo) => {
    test.skip(
      testInfo.project.name !== 'chromium',
      '桌面 TopNav 仅桌面项目（移动 /m 使用 MobileLayout 与底部 Tab，无此组件）',
    );
  });

  test('admin 导航按角色与 inNav 过滤，品牌在左、导航在中/左、账户与通知在右', async ({ page }) => {
    await page.goto('/dashboard');
    const topNav = page.getByTestId('top-nav');
    const nav = topNav.getByRole('navigation', { name: '主导航' });
    await expect(topNav).toBeVisible();
    await expect(page.locator('aside')).toHaveCount(0);

    const items = sidebarItemsForRole('admin');
    await expect(nav.getByRole('link')).toHaveCount(items.length);
    for (const item of items) {
      const link = nav.getByRole('link', { name: item.label, exact: true });
      await expect(link).toBeVisible();
      await expect(link).toHaveAttribute('href', item.href);
      await expect(link).toHaveAttribute('aria-label', item.label);
    }
    for (const label of ['投资人', '平台', '分类']) {
      await expect(nav.getByRole('link', { name: label, exact: true })).toHaveCount(0);
    }

    const brandBox = await topNav.getByRole('link', { name: 'InvestRing 首页' }).boundingBox();
    const navBox = await nav.boundingBox();
    const accountBox = await topNav.getByRole('button', { name: '用户菜单', exact: true }).boundingBox();
    const bellBox = await topNav.getByRole('button', { name: '通知', exact: true }).boundingBox();
    expect(brandBox, '品牌链接应有可见盒').not.toBeNull();
    expect(navBox, '主导航应有可见盒').not.toBeNull();
    expect(accountBox, '账户触发器应有可见盒').not.toBeNull();
    expect(bellBox, '通知触发器应有可见盒').not.toBeNull();
    expect(brandBox!.x + brandBox!.width).toBeLessThanOrEqual(navBox!.x + 1);
    expect(accountBox!.x).toBeGreaterThanOrEqual(navBox!.x + navBox!.width - 1);
    expect(accountBox!.x + accountBox!.width).toBeLessThanOrEqual(bellBox!.x + 1);
  });

  test('viewer 只见首页与组合，admin-only 项不进入 TopNav', async ({ page }) => {
    const errors = collectPageErrors(page, { tolerateLoginRaceNoise: true });
    await loginAsViewer(page);
    const nav = page.getByTestId('top-nav').getByRole('navigation', { name: '主导航' });
    const items = sidebarItemsForRole('viewer');
    await expect(nav.getByRole('link')).toHaveCount(items.length);
    for (const item of items) {
      await expect(nav.getByRole('link', { name: item.label, exact: true })).toBeVisible();
    }
    for (const label of ['产品', '设置', '投资人', '平台', '分类']) {
      await expect(nav.getByRole('link', { name: label, exact: true })).toHaveCount(0);
    }
    await expect(page.getByRole('button', { name: '用户菜单', exact: true })).toHaveCount(1);
    expect(errors).toEqual([]);
  });

  test('组合子页前缀激活，点击后激活态随一级路由迁移', async ({ page }) => {
    await page.goto('/portfolio/E2E_ACTIVE/trades');
    const nav = page.getByTestId('top-nav').getByRole('navigation', { name: '主导航' });
    const portfolio = nav.getByRole('link', { name: '组合', exact: true });
    const products = nav.getByRole('link', { name: '产品', exact: true });
    await expect(portfolio).toHaveAttribute('aria-current', 'page');
    await expect(products).not.toHaveAttribute('aria-current', 'page');

    await products.click();
    await page.waitForURL('**/products');
    await expect(products).toHaveAttribute('aria-current', 'page');
    await expect(portfolio).not.toHaveAttribute('aria-current', 'page');
  });

  test('账户触发器全宽屏唯一可见，菜单可打开且保留修改密码/退出登录', async ({ page }) => {
    await page.goto('/dashboard');
    const trigger = page.getByRole('button', { name: '用户菜单', exact: true });
    await expect(trigger).toHaveCount(1);
    await expect(trigger).toBeVisible();
    const menu = page.getByRole('menu');
    await openPopover(page, trigger, menu);
    await expect(menu.getByRole('menuitem', { name: '修改密码' })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: '退出登录' })).toBeVisible();
    await page.keyboard.press('Escape');
    await settlePopovers(page);
    await expect(menu).toHaveCount(0);
  });

  test('1024px 显示静态 TopNav，不占侧栏宽度且主导航不横向溢出', async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 900 });
    await page.goto('/dashboard');
    const topNav = page.getByTestId('top-nav');
    const nav = topNav.getByRole('navigation', { name: '主导航' });
    await expect(topNav).toBeVisible();
    await expect(topNav).toHaveCSS('position', 'static');
    await expect(page.getByTestId('compact-header')).toBeHidden();
    await expect(page.getByTestId('bottom-nav')).toBeHidden();
    await expect(page.locator('aside')).toHaveCount(0);

    const mainBox = await page.locator('main').boundingBox();
    const layoutWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(mainBox, 'main 应有可见盒').not.toBeNull();
    expect(Math.abs(mainBox!.x), 'main 左侧不应有侧栏占宽').toBeLessThanOrEqual(1);
    expect(Math.abs(mainBox!.width - layoutWidth), 'main 应铺满布局视口').toBeLessThanOrEqual(1);
    expect(
      await nav.evaluate((element) => element.scrollWidth <= element.clientWidth),
      '1024px 下 admin 主导航应完整显示，不产生内部横滚',
    ).toBe(true);
    const documentWidth = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(documentWidth.scrollWidth).toBeLessThanOrEqual(documentWidth.innerWidth);
  });

  test('900px 回退紧凑顶栏 + BottomNav，TopNav 隐藏且账户入口仍唯一', async ({ page }) => {
    await page.setViewportSize({ width: 900, height: 800 });
    await page.goto('/dashboard');
    const compactHeader = page.getByTestId('compact-header');
    const bottomNav = page.getByTestId('bottom-nav');
    await expect(page.getByTestId('top-nav')).toBeHidden();
    await expect(compactHeader).toBeVisible();
    await expect(bottomNav).toBeVisible();
    await expect(page.locator('aside')).toHaveCount(0);
    await expect(compactHeader.getByRole('link', { name: 'InvestRing 首页' })).toBeVisible();
    await expect(compactHeader.getByRole('button', { name: '通知', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '用户菜单', exact: true })).toHaveCount(1);
    await expect(page.getByRole('button', { name: '用户菜单', exact: true })).toBeVisible();

    const headerBox = await compactHeader.boundingBox();
    const bottomBox = await bottomNav.boundingBox();
    expect(headerBox, '紧凑顶栏应有可见盒').not.toBeNull();
    expect(bottomBox, '底部导航应有可见盒').not.toBeNull();
    expect(bottomBox!.y).toBeGreaterThanOrEqual(headerBox!.y + headerBox!.height);
  });
});
