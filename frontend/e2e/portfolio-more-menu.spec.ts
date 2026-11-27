import { test, expect, type Page } from '@playwright/test';
import {
  E2E_ACTIVE,
  activatePortfolioViaApi,
  closePortfolioViaApi,
  createPortfolioViaApi,
  gotoPortfolioDetail,
  isolatedPortfolioCode,
  openAppAndAuth,
  openPortfolioMoreMenu,
} from './helpers';

async function loginAsViewer(page: Page): Promise<void> {
  await page.context().clearCookies();
  await page.evaluate(() => window.localStorage.clear());
  await page.goto('/login');
  await page.getByLabel('用户名').fill('VIEWER');
  await page.getByLabel('密码').fill('viewer123');
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForURL(/\/(m\/)?dashboard/, { waitUntil: 'domcontentloaded' });
}

async function gotoDetail(page: Page, code: string): Promise<void> {
  await page.goto(`/portfolio/${code}`);
  await page.getByTestId('page-header').waitFor({ state: 'visible', timeout: 15_000 });
}

test.describe('组合详情统一更多菜单（#943 S4）', () => {
  test('桌面悬停可移入阅读、离开关闭，点击与键盘均可打开并 Escape 归还焦点', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'chromium', '移动项目无物理键盘输入语义；鼠标悬停由桌面用例覆盖');
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    const trigger = page.getByTestId('portfolio-more-trigger');
    const menu = page.getByTestId('portfolio-more-menu');
    const tradeLink = page.getByRole('link', { name: '调仓', exact: true });

    await tradeLink.focus();
    await expect(tradeLink).toBeFocused();
    await page.mouse.move(0, 0);
    await trigger.hover();
    await expect(menu).toBeVisible();
    await expect(tradeLink).toBeFocused();
    await expect(menu.locator('a')).toHaveText(['份额变动事件', '快照管理', '现金流水']);
    await expect(page.getByRole('link', { name: '持仓管理' })).toHaveCount(0);
    await trigger.click();
    await expect(menu).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('portfolio-more-desktop-light.png') });
    await page.emulateMedia({ colorScheme: 'dark' });
    await expect(page.locator('html')).toHaveClass(/dark/);
    await page.screenshot({ path: testInfo.outputPath('portfolio-more-desktop-dark.png') });
    await page.emulateMedia({ colorScheme: 'light' });
    await trigger.click();
    await expect(menu).toBeHidden();
    await page.mouse.move(0, 0);
    await tradeLink.focus();
    await trigger.hover();
    await expect(menu).toBeVisible();
    const menuBox = await menu.boundingBox();
    if (!menuBox) throw new Error('更多菜单没有可测量边界');
    await page.mouse.move(menuBox.x + 20, menuBox.y + 20);
    await page.waitForTimeout(150);
    await expect(menu).toBeVisible();
    await page.mouse.move(0, 0);
    await expect(menu).toBeHidden();
    await expect(tradeLink).toBeFocused();

    await trigger.focus();
    await page.keyboard.press('Enter');
    await expect(menu).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(menu).toBeHidden();
    await expect(trigger).toBeFocused();

    await trigger.dispatchEvent('click', { detail: 0 });
    await expect(menu).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(menu).toBeHidden();
    await expect(trigger).toBeFocused();

    await page.mouse.move(0, 0);
    await trigger.hover();
    await expect(menu).toBeVisible();
    const triggerBox = await trigger.boundingBox();
    if (!triggerBox) throw new Error('更多按钮没有可测量边界');
    await page.keyboard.press('ArrowDown');
    await expect(menu.getByRole('menuitem', { name: '份额变动事件', exact: true })).toBeFocused();
    await page.mouse.move(triggerBox.x + triggerBox.width / 2, triggerBox.y + triggerBox.height / 2);
    await page.mouse.move(0, triggerBox.y + triggerBox.height / 2);
    await page.waitForTimeout(180);
    await expect(menu).toBeVisible();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(`/portfolio/${E2E_ACTIVE}/share-change-events`);
  });

  test('移动 Sheet 行满足 44px 触控高度并可从菜单导航', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '仅移动项目验证 Sheet');
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await openPortfolioMoreMenu(page, testInfo);
    const menu = page.getByTestId('portfolio-more-menu');
    const heights = await menu.locator('a').evaluateAll((links) =>
      links.map((link) => link.getBoundingClientRect().height),
    );
    expect(heights).toHaveLength(3);
    for (const height of heights) expect(height).toBeGreaterThanOrEqual(44);
    await page.screenshot({ path: testInfo.outputPath('portfolio-more-mobile-light.png') });
    await page.emulateMedia({ colorScheme: 'dark' });
    await expect(page.locator('html')).toHaveClass(/dark/);
    await page.screenshot({ path: testInfo.outputPath('portfolio-more-mobile-dark.png') });
    await page.emulateMedia({ colorScheme: 'light' });

    await menu.getByRole('link', { name: '份额变动事件' }).click();
    await expect(page).toHaveURL(`/m/portfolio/${E2E_ACTIVE}/share-change-events`);
    await expect(menu).toBeHidden();
  });

  test('viewer 普通详情三态只显示只读更多菜单，不暴露管理或危险操作', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const draftCode = isolatedPortfolioCode('E943V', testInfo, 'D');
    await createPortfolioViaApi(page, headers, draftCode);
    const closedCode = isolatedPortfolioCode('E943V', testInfo, 'C');
    await createPortfolioViaApi(page, headers, closedCode);
    await activatePortfolioViaApi(page, headers, closedCode);
    await closePortfolioViaApi(page, headers, closedCode);
    await loginAsViewer(page);

    for (const code of [E2E_ACTIVE, draftCode, closedCode]) {
      await gotoDetail(page, code);
      await expect(page.getByTestId('portfolio-more-trigger')).toBeVisible();
      await expect(page.getByRole('link', { name: '申购赎回', exact: true })).toHaveCount(0);
      await expect(page.getByRole('button', { name: '首次申购激活' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: '重新激活' })).toHaveCount(0);
      await openPortfolioMoreMenu(page, testInfo);
      const menu = page.getByTestId('portfolio-more-menu');
      await expect(menu.locator('a')).toHaveText([
        '申购赎回记录',
        '调仓交易记录',
        '份额变动事件',
        '现金流水',
      ]);
      await expect(
        menu.getByRole('menuitem', { name: '快照管理' }).or(menu.getByRole('link', { name: '快照管理' })),
      ).toHaveCount(0);
      await expect(page.getByTestId(`portfolio-delete-${code}`)).toHaveCount(0);
      await page.keyboard.press('Escape');
      await expect(menu).toBeHidden();
    }
  });

  test('台账不接收净值管理菜单，投资人页不渲染组合操作组', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E943L', testInfo);
    const response = await page.request.post('/api/portfolios', {
      headers,
      data: { code, name: `菜单边界 ${code}`, kind: 'ledger', owner_investor_code: 'ADMIN' },
    });
    expect(response.ok(), `${response.status()} ${await response.text()}`).toBeTruthy();
    await gotoDetail(page, code);
    await expect(page.getByTestId('ledger-activate')).toBeVisible();

    if (testInfo.project.name === 'mobile') {
      await openPortfolioMoreMenu(page, testInfo);
      await expect(page.getByTestId('portfolio-more-menu').getByRole('link')).toHaveCount(0);
      await expect(page.getByTestId(`portfolio-delete-${code}`)).toBeVisible();
      await page.keyboard.press('Escape');
    } else {
      await expect(page.getByTestId('portfolio-more-trigger')).toHaveCount(0);
      await expect(page.getByTestId(`portfolio-delete-${code}`)).toBeVisible();
    }

    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await page.goto(`/portfolio/${E2E_ACTIVE}?tab=investors`);
    await expect(page).toHaveURL(/[?&]tab=investors/);
    await expect(
      page.getByTestId('portfolio-investors-table').or(page.getByTestId('portfolio-investors-cards')),
    ).toBeVisible();
    await expect(page.getByTestId('portfolio-more-trigger')).toHaveCount(0);
  });
});
