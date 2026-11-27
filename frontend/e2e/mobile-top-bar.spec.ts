/**
 * #944 PR4.2：移动站顶栏与底部导航避让。
 * 两个 browser project 均以 iPhone 13 UA/视口直接走 /m 布局，避免 desktop UA 被 proxy
 * 映射回桌面站。只消费种子与只读页面，不推进/重算 E2E_ACTIVE。
 */
import { test, expect, devices } from '@playwright/test';
import {
  E2E_ACTIVE,
  collectPageErrors,
  dialogByTitle,
  openPopover,
  portfolioPath,
  settlePopovers,
} from './helpers';
import { mobileExitPaths, mobileTabPaths } from '../src/components/shared/navItems';

test.describe('移动站顶栏与底部导航（#944 PR4.2）', () => {
  const iphone = devices['iPhone 13'];
  test.use({
    userAgent: iphone.userAgent,
    viewport: iphone.viewport,
    deviceScaleFactor: iphone.deviceScaleFactor,
    isMobile: iphone.isMobile,
    hasTouch: iphone.hasTouch,
  });

  test('首页顶栏复用品牌/通知/账户组件，账户卡退役且菜单可用', async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.route('**/api/system/notifications?*', async (route) => {
      await route.fulfill({
        json: {
          items: [
            {
              id: 1,
              type: 'test',
              level: 'info',
              title: '移动顶栏测试通知',
              channel: 'in_app',
              status: 'pending',
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        },
      });
    });
    await page.goto('/m/dashboard');

    const topBar = page.getByTestId('mobile-top-bar');
    const brand = topBar.getByRole('link', { name: 'InvestRing 首页' });
    const account = topBar.getByRole('button', { name: '用户菜单', exact: true });
    const bell = topBar.getByRole('button', { name: '通知', exact: true });
    await expect(topBar).toBeVisible();
    await expect(brand).toHaveAttribute('href', '/m/dashboard');
    await expect(bell).toBeVisible();
    await expect(bell).toHaveAccessibleDescription('1 条未读通知');
    // 按钮可见不等于铃铛可见：默认 size 的 px-4 曾把 32px 按钮内容宽压成 0。
    await expect(bell.locator('svg')).toHaveCSS('width', '16px');
    await expect(bell.locator('svg')).toHaveCSS('height', '16px');
    await expect(account).toBeVisible();
    await expect(page.getByRole('button', { name: '用户菜单', exact: true })).toHaveCount(1);
    await expect(page.getByRole('heading', { name: '账户', exact: true })).toHaveCount(0);

    const menu = page.getByRole('menu');
    await openPopover(page, account, menu);
    await expect(menu.getByRole('menuitem', { name: '退出登录' })).toBeVisible();
    await menu.getByRole('menuitem', { name: '修改密码' }).click();
    const passwordDialog = dialogByTitle(page, '修改密码');
    await expect(passwordDialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(passwordDialog).toBeHidden();

    await openPopover(page, bell, menu);
    await expect(menu.getByText('移动顶栏测试通知', { exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await settlePopovers(page);
    await expect(menu).toHaveCount(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toEqual([]);
  });

  test('一级 Tab 页均有顶栏，底部导航路径仍由 mobileTabPaths 单源派生', async ({ page }) => {
    const tabPaths = mobileTabPaths('/m');
    for (const path of tabPaths) {
      await page.goto(path);
      const topBar = page.getByTestId('mobile-top-bar');
      const bottomNav = page.getByTestId('bottom-nav');
      await expect(topBar, `${path} 应有移动顶栏`).toBeVisible();
      await expect(topBar.getByRole('link', { name: 'InvestRing 首页' })).toHaveAttribute(
        'href',
        '/m/dashboard',
      );
      await expect(topBar.getByRole('button', { name: '通知', exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: '用户菜单', exact: true })).toHaveCount(1);
      await expect(bottomNav, `${path} 应显示底部导航`).toBeVisible();
      await expect(bottomNav.getByRole('link')).toHaveCount(tabPaths.length);
      for (const tabPath of tabPaths) {
        await expect(bottomNav.locator(`a[href="${tabPath}"]`)).toHaveCount(1);
      }
    }
  });

  test('管理页与组合钻取页也有顶栏，但非一级 Tab 不显示底部导航', async ({ page }) => {
    const paths = mobileExitPaths('/m').map((exit) => exit.path);
    paths.push(`/m${portfolioPath(E2E_ACTIVE, 'trades')}`, '/m/settings/appearance');

    for (const path of paths) {
      await page.goto(path);
      await expect(page.getByTestId('mobile-top-bar'), `${path} 应有移动顶栏`).toBeVisible();
      await expect(page.getByRole('button', { name: '用户菜单', exact: true })).toHaveCount(1);
      await expect(page.getByTestId('bottom-nav'), `${path} 不应显示底部导航`).toHaveCount(0);
    }
  });

  test('main 预留 64px 底栏高度，最后一项操作滚到底仍未被固定底栏遮挡', async ({ page }) => {
    await page.goto('/m/portfolio');
    const details = page.getByRole('button', { name: '详情', exact: true });
    await expect(details.first()).toBeVisible();

    const main = page.locator('main');
    const bottomNav = page.getByTestId('bottom-nav');
    await expect(main).toHaveCSS('padding-bottom', '64px');
    await expect(bottomNav).toHaveCSS('position', 'fixed');
    await expect(bottomNav).toHaveCSS('z-index', '50');
    await expect(bottomNav.locator(':scope > div')).toHaveCSS('height', '64px');

    // 取 DOM 中最后一张组合卡的操作行末项，不假设列表排序；只做几何与命中测试，不点击破坏性动作。
    const lastActions = details.last().locator('xpath=..');
    const lastAction = lastActions.getByRole('button').last();
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
    await expect(lastAction).toBeInViewport();

    const actionBox = await lastAction.boundingBox();
    const navBox = await bottomNav.boundingBox();
    expect(actionBox, '末项操作应有可见盒').not.toBeNull();
    expect(navBox, '底部导航应有可见盒').not.toBeNull();
    expect(actionBox!.y + actionBox!.height, '末项操作下缘不得进入底部导航').toBeLessThanOrEqual(
      navBox!.y,
    );
    const centerHitsAction = await lastAction.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      return hit === element || (hit !== null && element.contains(hit));
    });
    expect(centerHitsAction, '末项操作中心不得被固定底栏覆盖').toBe(true);
  });
});
