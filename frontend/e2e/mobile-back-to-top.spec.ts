import { test, expect, devices, type Page } from '@playwright/test';
import {
  E2E_ACTIVE,
  E2E_PORT,
  activatePortfolioViaApi,
  closePortfolioViaApi,
  createPortfolioViaApi,
  isolatedPortfolioCode,
  openAppAndAuth,
  portfolioPath,
  scrollWindowTo,
} from './helpers';
import { BACK_TO_TOP_VISIBILITY_MULTIPLIER } from '../src/components/mobile/backToTopConfig';

// #937 A1/A10：两个项目都覆用移动 UA 与触控语义，分别以 Chromium/WebKit 验证真实 /m 路由。
const MOBILE_DEVICE = {
  userAgent: devices['iPhone 13'].userAgent,
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
};

test.describe('移动组合详情页内操作区与回到顶部（#937）', () => {
  test.use(MOBILE_DEVICE);

  async function gotoMobileDetail(page: Page, code = E2E_ACTIVE, query = ''): Promise<void> {
    const seedCode = code === E2E_ACTIVE || code === E2E_PORT ? code : null;
    const path = seedCode ? portfolioPath(seedCode) : `/portfolio/${code}`;
    await page.goto(`${path}${query}`);
    await expect(page.getByTestId('page-header')).toBeVisible();
    expect(new URL(page.url()).pathname).toBe(`/m/portfolio/${code}`);
    if (code === E2E_ACTIVE && !query.includes('tab=investors')) {
      await expect(page.getByTestId('portfolio-holdings')).toBeVisible();
      await expect(
        page.getByTestId('portfolio-nav-trend-card').locator('svg.recharts-surface').first(),
      ).toBeVisible();
    }
  }

  async function collapsePageHeader(page: Page): Promise<void> {
    // 页头折叠会改变文档高度；先越过 32px 并等折叠提交，再做精确 scrollY/文档坐标断言。
    const maxY = await page.evaluate(
      () => document.documentElement.scrollHeight - document.documentElement.clientHeight,
    );
    if (maxY <= 32) return;
    await page.evaluate((top) => window.scrollTo(0, top), Math.min(100, maxY));
    await expect(page.getByTestId('page-header').getByRole('navigation', { name: '面包屑' })).toHaveCount(0);
  }

  async function makeLongPage(page: Page): Promise<void> {
    // 只延长本次测试文档，不造业务数据、不推进共享种子；真实 scroll/resize 与组件仍照常运行。
    await page.addStyleTag({ content: 'main::after { content: ""; display: block; height: 4200px; }' });
    await collapsePageHeader(page);
  }

  async function scrollToThreshold(page: Page, offset = 0): Promise<number> {
    const threshold = await page.evaluate(
      (multiplier) => multiplier * window.innerHeight,
      BACK_TO_TOP_VISIBILITY_MULTIPLIER,
    );
    await scrollWindowTo(page, threshold + offset);
    return threshold;
  }

  async function expectActionButtons(page: Page, labels: string[]): Promise<void> {
    const action = page.getByTestId('portfolio-action-buttons');
    await expect(action).toHaveCount(labels.length === 0 ? 0 : 1);
    if (labels.length === 0) return;
    const controls = action.getByRole('button');
    await expect(controls).toHaveCount(labels.length);
    for (const label of labels) {
      const role = ['更多操作', '重新激活', '激活组合'].includes(label) ? 'button' : 'link';
      await expect(action.getByRole(role, { name: label, exact: true })).toBeVisible();
    }
    for (let i = 0; i < labels.length; i++) {
      const box = await controls.nth(i).boundingBox();
      expect(box, `${labels[i]} 应有可测量边界`).not.toBeNull();
      expect(box!.height, `${labels[i]} 高度`).toBe(44);
    }
    const more = action.getByRole('button', { name: '更多操作', exact: true });
    if (labels.includes('更多操作')) {
      const box = await more.boundingBox();
      expect(box!.width, '更多按钮宽度').toBe(44);
    }
  }

  async function expectActionPlacement(page: Page, placement: 'nav' | 'draft' | 'ledger' | 'none'): Promise<void> {
    if (placement === 'none') return;
    const geometry = await page.getByTestId('portfolio-action-buttons').evaluate((element, kind) => {
      const action = element.getBoundingClientRect();
      const byHeading = (text: string) => Array.from(document.querySelectorAll('h3')).find(
        (item) => item.textContent === text,
      )!.getBoundingClientRect().top;
      const stats = kind === 'nav'
        ? document.querySelector('[data-testid="portfolio-stat-grid"]')!.getBoundingClientRect()
        : null;
      const alert = Array.from(document.querySelectorAll('[role="alert"]')).find(
        (item) => item.textContent?.includes('组合尚未激活'),
      )?.getBoundingClientRect();
      return {
        position: getComputedStyle(element).position,
        statsBottom: stats?.bottom ?? 0,
        actionTop: action.top,
        actionBottom: action.bottom,
        allocationTop: kind === 'nav' ? byHeading('资产分布') : 0,
        draftAlertBottom: alert?.bottom ?? 0,
        ledgerTop: kind === 'ledger' ? byHeading('台账汇总') : 0,
      };
    }, placement);
    expect(geometry.position).toBe('static');
    if (placement === 'nav') {
      expect(geometry.statsBottom).toBeLessThanOrEqual(geometry.actionTop);
      expect(geometry.actionBottom).toBeLessThan(geometry.allocationTop);
    } else if (placement === 'draft') {
      expect(geometry.draftAlertBottom).toBeGreaterThan(0);
      expect(geometry.draftAlertBottom).toBeLessThanOrEqual(geometry.actionTop);
    } else {
      expect(geometry.actionBottom).toBeLessThan(geometry.ledgerTop);
    }
  }

  test('A1-A3：390/360 视口下操作区位于统计与资产分布之间、单组三列 44px 并随文档滚走', async ({ page }, testInfo) => {
    for (const viewport of [{ width: 390, height: 844 }, { width: 360, height: 800 }]) {
      await page.setViewportSize(viewport);
      await gotoMobileDetail(page);
      const action = page.getByTestId('portfolio-action-buttons');
      await expect(action).toHaveCount(1);
      await expectActionButtons(page, ['调仓', '申购赎回', '更多操作']);

      const geometry = await action.evaluate((element) => {
        const stats = document.querySelector('[data-testid="portfolio-stat-grid"]')!;
        const allocationHeading = Array.from(document.querySelectorAll('h3')).find(
          (heading) => heading.textContent === '资产分布',
        )!;
        const grid = element.firstElementChild as HTMLElement;
        return {
          position: getComputedStyle(element).position,
          gridPosition: getComputedStyle(grid).position,
          columns: getComputedStyle(grid).gridTemplateColumns.split(' ').length,
          statsBottom: stats.getBoundingClientRect().bottom,
          actionTop: element.getBoundingClientRect().top,
          actionBottom: element.getBoundingClientRect().bottom,
          allocationTop: allocationHeading.getBoundingClientRect().top,
          parentPaddingBottom: getComputedStyle(element.parentElement!).paddingBottom,
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
        };
      });
      expect(geometry.position).toBe('static');
      expect(geometry.gridPosition).toBe('static');
      expect(geometry.columns).toBe(3);
      expect(geometry.statsBottom).toBeLessThanOrEqual(geometry.actionTop);
      expect(geometry.actionBottom).toBeLessThan(geometry.allocationTop);
      expect(geometry.parentPaddingBottom, '保留回顶按钮所需普通底部留白，而非 pb-24').toBe('64px');
      expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);

      await collapsePageHeader(page);
      const actionDocumentTop = await action.evaluate((element) => element.getBoundingClientRect().top + window.scrollY);
      await scrollWindowTo(page, Math.ceil(actionDocumentTop + 60));
      const bottom = await action.evaluate((element) => element.getBoundingClientRect().bottom);
      expect(bottom, '操作区应随文档滚出视口').toBeLessThan(0);
      await expect(page.getByTestId('back-to-top')).toHaveCount(0);
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await gotoMobileDetail(page);
    for (const theme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: theme });
      await expect(page.locator('html')).toHaveClass(theme === 'dark' ? /dark/ : /^(?!.*dark).*$/);
      await page.screenshot({ path: testInfo.outputPath(`portfolio-inflow-${theme}.png`) });
    }
  });

  test('A4：三屏阈值精确两侧显隐、向上回越隐藏，短页始终不出现', async ({ page }) => {
    await gotoMobileDetail(page);
    await makeLongPage(page);
    const threshold = await scrollToThreshold(page, -1);
    await expect(page.getByTestId('back-to-top')).toHaveCount(0);
    await scrollWindowTo(page, threshold);
    await page.evaluate(() => window.dispatchEvent(new Event('scroll')));
    await expect(page.getByTestId('back-to-top')).toHaveCount(1);
    await expect(page.getByRole('button', { name: '回到顶部', exact: true })).toBeVisible();
    await scrollWindowTo(page, threshold - 1);
    await page.evaluate(() => window.dispatchEvent(new Event('scroll')));
    await expect(page.getByTestId('back-to-top')).toHaveCount(0);

    await gotoMobileDetail(page, E2E_PORT);
    await collapsePageHeader(page);
    const maxY = await page.evaluate(() => document.documentElement.scrollHeight - document.documentElement.clientHeight);
    expect(maxY, '草稿短页应不足三屏').toBeLessThan(BACK_TO_TOP_VISIBILITY_MULTIPLIER * 844);
    await scrollWindowTo(page, maxY);
    await expect(page.getByTestId('back-to-top')).toHaveCount(0);
  });

  test('A4/A6：挂载时已处于深处也会立即计算，无需等待下一次 scroll', async ({ page }) => {
    let releaseRequest!: () => void;
    const requestGate = new Promise<void>((resolve) => { releaseRequest = resolve; });
    await page.route(`**/api/portfolios/${E2E_ACTIVE}`, async (route) => {
      await requestGate;
      await route.continue();
    });
    const portfolioRequest = page.waitForRequest(
      (request) => new URL(request.url()).pathname === `/api/portfolios/${E2E_ACTIVE}`,
    );
    await page.goto(portfolioPath(E2E_ACTIVE));
    await portfolioRequest;
    await makeLongPage(page);
    await page.evaluate(
      (multiplier) => window.scrollTo(0, multiplier * window.innerHeight),
      BACK_TO_TOP_VISIBILITY_MULTIPLIER,
    );
    releaseRequest();
    await expect(page.getByTestId('page-header')).toBeVisible();
    await expect(page.getByTestId('back-to-top')).toBeVisible();
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThanOrEqual(BACK_TO_TOP_VISIBILITY_MULTIPLIER * 844);
  });

  test('A5：点击与键盘瞬时回顶，保留 view/sub/tab 与数据、不发业务请求且焦点交还页头', async ({ page }, testInfo) => {
    const query = '?view=platform&sub=ASSET_STOCK:region';
    await gotoMobileDetail(page, E2E_ACTIVE, query);
    await makeLongPage(page);
    await scrollToThreshold(page, 100);
    const urlBefore = page.url();
    const headingBefore = await page.getByRole('heading', { level: 1 }).innerText();
    const apiRequests: string[] = [];
    const onRequest = (request: import('@playwright/test').Request) => {
      if (new URL(request.url()).pathname.startsWith('/api/')) apiRequests.push(request.url());
    };
    page.on('request', onRequest);

    await page.evaluate(() => {
      const audited = window as typeof window & { __scrollCalls: unknown[] };
      audited.__scrollCalls = [];
      const original = window.scrollTo.bind(window);
      window.scrollTo = ((...args: unknown[]) => {
        audited.__scrollCalls.push(args);
        return original(...args as [number, number]);
      }) as typeof window.scrollTo;
    });
    await page.getByRole('button', { name: '回到顶部', exact: true }).click();
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    await expect(page.getByTestId('back-to-top')).toHaveCount(0);
    await expect(page.getByTestId('page-header').getByRole('navigation', { name: '面包屑' })).toBeVisible();
    expect(page.url()).toBe(urlBefore);
    expect(await page.getByRole('heading', { level: 1 }).innerText()).toBe(headingBefore);
    expect(await page.evaluate(() => (window as typeof window & { __scrollCalls: unknown[] }).__scrollCalls)).toEqual([[0, 0]]);
    expect(apiRequests, '回顶不得额外触发业务请求').toEqual([]);
    page.off('request', onRequest);

    await collapsePageHeader(page);
    await scrollToThreshold(page, 100);
    await page.getByRole('button', { name: '回到顶部', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    await expect(page.getByTestId('back-to-top')).toHaveCount(0);
    await expect(page.getByTestId('page-header').getByRole('link', { name: '返回组合列表' })).toBeFocused();
    await page.screenshot({ path: testInfo.outputPath('portfolio-back-to-top-restored.png') });

    await gotoMobileDetail(page, E2E_ACTIVE, '?tab=investors');
    await makeLongPage(page);
    await scrollToThreshold(page, 100);
    await page.getByRole('button', { name: '回到顶部', exact: true }).click();
    await expect(page).toHaveURL(/\/m\/portfolio\/E2E_ACTIVE\?tab=investors$/);
    await expect(page.getByTestId('portfolio-investors-table').or(page.getByTestId('portfolio-investors-cards'))).toBeVisible();
  });

  test('A4/A6：resize 按新视口高度重算，安全区与 44px/z-40 几何正确', async ({ page }, testInfo) => {
    await gotoMobileDetail(page);
    await makeLongPage(page);
    await scrollWindowTo(page, 2500);
    await expect(page.getByTestId('back-to-top')).toHaveCount(0);
    await page.setViewportSize({ width: 390, height: 700 });
    await expect(page.getByTestId('back-to-top')).toBeVisible();
    await page.setViewportSize({ width: 390, height: 900 });
    await expect(page.getByTestId('back-to-top')).toHaveCount(0);
    await page.setViewportSize({ width: 390, height: 700 });

    const button = page.getByTestId('back-to-top');
    const baseGeometry = await button.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        width: element.getBoundingClientRect().width,
        height: element.getBoundingClientRect().height,
        right: style.right,
        bottom: style.bottom,
        zIndex: style.zIndex,
      };
    });
    expect(baseGeometry.width).toBe(44);
    expect(baseGeometry.height).toBe(44);
    expect(baseGeometry.right).toBe('16px');
    expect(baseGeometry.bottom).toBe('16px');
    expect(baseGeometry.zIndex).toBe('40');

    if (testInfo.project.name === 'chromium') {
      const session = await page.context().newCDPSession(page);
      await session.send('Emulation.setSafeAreaInsetsOverride', { insets: { top: 0, left: 0, right: 24, bottom: 34 } });
      await page.setViewportSize({ width: 390, height: 701 });
      await expect(button).toBeVisible();
      const insetGeometry = await button.evaluate((element) => {
        const style = getComputedStyle(element);
        return { right: style.right, bottom: style.bottom };
      });
      expect(insetGeometry).toEqual({ right: '40px', bottom: '50px' });

      await gotoMobileDetail(page);
      await page.getByTestId('portfolio-action-buttons').evaluate((action) => {
        const content = action.parentElement!;
        const spacer = document.createElement('div');
        spacer.style.height = '2200px';
        content.insertBefore(spacer, content.lastElementChild);
      });
      const maxY = await page.evaluate(
        () => document.documentElement.scrollHeight - document.documentElement.clientHeight,
      );
      await scrollWindowTo(page, maxY);
      await expect(button).toBeVisible();
      const tailGeometry = await page.evaluate(() => {
        const action = document.querySelector('[data-testid="portfolio-action-buttons"]')!;
        const content = action.parentElement!;
        const last = content.lastElementChild!.getBoundingClientRect();
        const back = document.querySelector('[data-testid="back-to-top"]')!.getBoundingClientRect();
        return {
          paddingBottom: getComputedStyle(content).paddingBottom,
          lastBottom: last.bottom,
          backTop: back.top,
          backBottom: back.bottom,
          viewportBottom: window.innerHeight,
        };
      });
      expect(tailGeometry.paddingBottom).toBe('98px');
      expect(tailGeometry.lastBottom, '页尾正文不得被回顶按钮覆盖').toBeLessThanOrEqual(tailGeometry.backTop - 4);
      expect(tailGeometry.backBottom).toBeLessThanOrEqual(tailGeometry.viewportBottom);
      await session.send('Emulation.setSafeAreaInsetsOverride', { insets: { top: 0, left: 0, right: 0, bottom: 0 } });
    }
  });

  test('A4/A6：按钮因滚动隐藏时焦点不滞留，反复进出不累积监听且其他页无残留', async ({ page }) => {
    await page.addInitScript(() => {
      const audited = window as typeof window & {
        __listenerAudit: { scrollAdds: number; scrollRemoves: number; resizeAdds: number; resizeRemoves: number };
      };
      audited.__listenerAudit = { scrollAdds: 0, scrollRemoves: 0, resizeAdds: 0, resizeRemoves: 0 };
      const add = window.addEventListener.bind(window);
      const remove = window.removeEventListener.bind(window);
      window.addEventListener = ((type: string, listener: EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions) => {
        if (type === 'scroll') audited.__listenerAudit.scrollAdds++;
        if (type === 'resize') audited.__listenerAudit.resizeAdds++;
        return add(type, listener, options);
      }) as typeof window.addEventListener;
      window.removeEventListener = ((type: string, listener: EventListenerOrEventListenerObject, options?: boolean | EventListenerOptions) => {
        if (type === 'scroll') audited.__listenerAudit.scrollRemoves++;
        if (type === 'resize') audited.__listenerAudit.resizeRemoves++;
        return remove(type, listener, options);
      }) as typeof window.removeEventListener;
    });

    await gotoMobileDetail(page);
    await makeLongPage(page);
    const threshold = await scrollToThreshold(page, 100);
    await page.getByTestId('back-to-top').focus();
    await scrollWindowTo(page, threshold - 1);
    await expect(page.getByTestId('back-to-top')).toHaveCount(0);
    await expect(page.getByTestId('page-header').getByRole('link', { name: '返回组合列表' })).toBeFocused();
    const listenerBalance = () => page.evaluate(() => {
      const audit = (window as typeof window & {
        __listenerAudit: { scrollAdds: number; scrollRemoves: number; resizeAdds: number; resizeRemoves: number };
      }).__listenerAudit;
      return {
        scroll: audit.scrollAdds - audit.scrollRemoves,
        resize: audit.resizeAdds - audit.resizeRemoves,
      };
    });
    const firstAudit = await listenerBalance();

    await page.getByTestId('page-header').getByRole('link', { name: '返回组合列表' }).click();
    await expect(page).toHaveURL(/\/m\/portfolio$/);
    await expect(page.getByTestId('back-to-top')).toHaveCount(0);
    await page.locator(`a[href="/m/portfolio/${E2E_ACTIVE}"]`).click();
    await expect(page.getByTestId('page-header')).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/m/portfolio/${E2E_ACTIVE}$`));
    await makeLongPage(page);
    await scrollToThreshold(page, 100);
    await expect(page.getByTestId('back-to-top')).toHaveCount(1);
    const secondAudit = await listenerBalance();
    expect(secondAudit).toEqual(firstAudit);
  });

  test('A7：admin 状态/台账/投资人分支保留原操作矩阵，回顶不受管理权限门控', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const headers = await openAppAndAuth(page);
    const draftCode = isolatedPortfolioCode('E937D', testInfo);
    await createPortfolioViaApi(page, headers, draftCode);
    const closedCode = isolatedPortfolioCode('E937C', testInfo);
    await createPortfolioViaApi(page, headers, closedCode);
    await activatePortfolioViaApi(page, headers, closedCode);
    await closePortfolioViaApi(page, headers, closedCode);

    const ledgerCodes = {
      draft: isolatedPortfolioCode('E937LD', testInfo),
      active: isolatedPortfolioCode('E937LA', testInfo),
      closed: isolatedPortfolioCode('E937LC', testInfo),
    };
    for (const [status, code] of Object.entries(ledgerCodes)) {
      const response = await page.request.post('/api/portfolios', {
        headers,
        data: { code, name: `回顶台账 ${code}`, kind: 'ledger', owner_investor_code: 'ADMIN' },
      });
      expect(response.ok(), `${status} 台账创建失败：${response.status()} ${await response.text()}`).toBeTruthy();
      if (status !== 'draft') {
        const activation = await page.request.post(`/api/portfolios/${code}/activate`, { headers });
        expect(activation.ok(), `${status} 台账激活失败：${activation.status()} ${await activation.text()}`).toBeTruthy();
      }
      if (status === 'closed') await closePortfolioViaApi(page, headers, code);
    }

    const cases = [
      { code: E2E_ACTIVE, query: '', labels: ['调仓', '申购赎回', '更多操作'], placement: 'nav' as const },
      { code: draftCode, query: '', labels: ['首次申购激活', '更多操作'], placement: 'draft' as const },
      { code: closedCode, query: '', labels: ['重新激活', '更多操作'], placement: 'nav' as const },
      { code: ledgerCodes.draft, query: '?tab=investors', labels: ['激活组合', '更多操作'], placement: 'ledger' as const },
      { code: ledgerCodes.active, query: '', labels: [], placement: 'none' as const },
      { code: ledgerCodes.closed, query: '', labels: ['重新激活', '更多操作'], placement: 'ledger' as const },
      { code: E2E_ACTIVE, query: '?tab=investors', labels: [], placement: 'none' as const },
    ];
    for (const item of cases) {
      await gotoMobileDetail(page, item.code, item.query);
      await expectActionButtons(page, item.labels);
      await expectActionPlacement(page, item.placement);
      if (item.code === draftCode) {
        await page.screenshot({ path: testInfo.outputPath('portfolio-inflow-draft-light.png') });
        await page.emulateMedia({ colorScheme: 'dark' });
        await expect(page.locator('html')).toHaveClass(/dark/);
        await page.screenshot({ path: testInfo.outputPath('portfolio-inflow-draft-dark.png') });
        await page.emulateMedia({ colorScheme: 'light' });
      }
      await makeLongPage(page);
      await scrollToThreshold(page, 100);
      await expect(page.getByTestId('back-to-top')).toHaveCount(1);
    }
  });

  test('A7：viewer 不获得写入口，#943 只读更多与回顶阅读导航并存', async ({ page }) => {
    await page.context().clearCookies();
    await page.goto('/m/login');
    await page.evaluate(() => window.localStorage.clear());
    await page.reload();
    await page.getByLabel('用户名').fill('VIEWER');
    await page.getByLabel('密码').fill('viewer123');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.waitForURL(/\/m\/dashboard/, { waitUntil: 'domcontentloaded' });
    await gotoMobileDetail(page);
    await expect(page.getByRole('link', { name: '调仓', exact: true })).toHaveCount(0);
    await expect(page.getByRole('link', { name: '申购赎回', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '更多操作', exact: true })).toBeVisible();
    await makeLongPage(page);
    await scrollToThreshold(page, 100);
    await expect(page.getByTestId('back-to-top')).toBeVisible();
  });

  test('A7：loading 与不存在早退均不渲染操作区或回顶', async ({ page }, testInfo) => {
    let releaseRequest!: () => void;
    const requestGate = new Promise<void>((resolve) => { releaseRequest = resolve; });
    await page.route(`**/api/portfolios/${E2E_ACTIVE}`, async (route) => {
      await requestGate;
      await route.continue();
    });
    const portfolioRequest = page.waitForRequest(
      (request) => new URL(request.url()).pathname === `/api/portfolios/${E2E_ACTIVE}`,
    );
    await page.goto(portfolioPath(E2E_ACTIVE));
    await portfolioRequest;
    await expect(page.getByTestId('page-header')).toHaveCount(0);
    await expect(page.getByTestId('portfolio-action-buttons')).toHaveCount(0);
    await expect(page.getByTestId('back-to-top')).toHaveCount(0);
    releaseRequest();
    await expect(page.getByTestId('page-header')).toBeVisible();

    const missing = isolatedPortfolioCode('E937X', testInfo);
    await page.goto(`/portfolio/${missing}`);
    await expect(page.getByText('组合不存在', { exact: true })).toBeVisible();
    await expect(page.getByTestId('portfolio-action-buttons')).toHaveCount(0);
    await expect(page.getByTestId('back-to-top')).toHaveCount(0);
  });

  test('A8：Sheet/AlertDialog 的 z-50 遮罩覆盖且不穿透回顶，关闭后滚动与焦点正常', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const draftCode = isolatedPortfolioCode('E937A8', testInfo);
    await createPortfolioViaApi(page, headers, draftCode);
    await gotoMobileDetail(page, draftCode);
    await makeLongPage(page);
    const back = page.getByTestId('back-to-top');
    const captureBackGeometry = async () => {
      // WebKit 关闭 AlertDialog 后可能恢复展开页头；先折叠再量 maxY，避免 41px 页头收缩
      // 在 scrollTo 后把 2632px 目标锚定成 2591px。保留精确阈值断言，不引入容差或固定等待。
      await collapsePageHeader(page);
      await scrollToThreshold(page, 100);
      await expect(back).toBeVisible();
      const box = await back.boundingBox();
      if (!box) throw new Error('回顶按钮没有可测量边界');
      return {
        x: box.x + box.width / 2,
        y: box.y + box.height / 2,
        zIndex: await back.evaluate((element) => Number(getComputedStyle(element).zIndex)),
      };
    };
    const assertCovered = async (modal: ReturnType<Page['getByTestId']>, geometry: Awaited<ReturnType<typeof captureBackGeometry>>) => {
      await expect(modal).toBeVisible();
      const modalBox = await modal.boundingBox();
      if (!modalBox) throw new Error('模态没有可测量边界');
      const modalZIndex = await modal.evaluate((element) => Number(getComputedStyle(element).zIndex));
      expect(geometry.zIndex).toBe(40);
      expect(modalZIndex).toBe(50);
      const topElementIsBack = await page.evaluate(({ x, y }) =>
        document.elementFromPoint(x, y)?.getAttribute('data-testid') === 'back-to-top',
        { x: geometry.x, y: geometry.y },
      );
      expect(topElementIsBack, '模态遮罩应覆盖回顶按钮原位置').toBe(false);
      const overlayPoint = {
        x: Math.min(geometry.x, modalBox.x + modalBox.width - 1),
        y: modalBox.y - 8,
      };
      expect(overlayPoint.y, '遮挡点击点应位于模态内容上方的真实遮罩').toBeGreaterThan(0);
      return overlayPoint;
    };

    const sheetGeometry = await captureBackGeometry();
    const trigger = page.getByTestId('portfolio-more-trigger');
    await trigger.dispatchEvent('click');
    const sheet = page.getByTestId('portfolio-more-menu');
    const overlayPoint = await assertCovered(sheet, sheetGeometry);
    const urlBefore = page.url();
    await page.mouse.click(overlayPoint.x, overlayPoint.y);
    await expect(sheet).toBeHidden();
    await expect(trigger).toBeFocused();
    expect(page.url()).toBe(urlBefore);
    await expect.poll(() => page.evaluate(() => getComputedStyle(document.body).overflowY)).toBe('visible');

    const dialogGeometry = await captureBackGeometry();
    await trigger.dispatchEvent('click');
    await sheet.getByTestId(`portfolio-delete-${draftCode}`).click();
    const dialog = page.getByRole('alertdialog');
    await assertCovered(dialog, dialogGeometry);
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
    await scrollToThreshold(page, 100);
    await expect(back).toBeVisible();

    const scrolling = await page.evaluate(() => ({
      root: document.scrollingElement === document.documentElement,
      bodyOverflow: getComputedStyle(document.body).overflowY,
      mainOverflow: getComputedStyle(document.querySelector('main')!).overflowY,
    }));
    expect(scrolling).toEqual({ root: true, bodyOverflow: 'visible', mainOverflow: 'visible' });
  });
});
