/**
 * 主题启用回归守卫（#872 PR2，SPEC §10.1 八用例 + 本轮新增载体 computed-style 两用例）。
 * 断 computed style 而非像素；双 project（chromium 1280 / iPhone 13 webkit）。
 * 偏好注入用 addInitScript 写 localStorage.theme（next-themes 存裸字符串），
 * 系统档用 emulateMedia({ colorScheme })；不新建取色脚本、不加依赖。
 */
import { test, expect, type Page, type TestInfo } from '@playwright/test';
import {
  E2E_ACTIVE,
  E2E_PORT,
  activatePortfolioViaApi,
  closePortfolioViaApi,
  createPortfolioViaApi,
  gotoPortfolioSubpage,
  isolatedPortfolioCode,
  nearestTradingDay,
  openAppAndAuth,
  openFilterPanelIfMobile,
  settlePopovers,
} from './helpers';

// B3 golden：基线 :root 33 个色彩 token 逐字节不变 + PR1 新增 destructive-solid（同声明）。
// 来源 frontend/src/app/globals.css :root（#872 PR1 落地态）；改 light 值必须同批改这里并走口径决策。
const LIGHT_TOKENS: Record<string, string> = {
  background: '220 23% 97%',
  foreground: '218 32% 15%',
  card: '0 0% 100%',
  'card-foreground': '218 32% 15%',
  popover: '0 0% 100%',
  'popover-foreground': '218 32% 15%',
  primary: '223 68% 51%',
  'primary-foreground': '210 40% 98%',
  secondary: '223 28% 95%',
  'secondary-foreground': '218 32% 15%',
  muted: '223 28% 95%',
  'muted-foreground': '221 16% 47%',
  accent: '223 28% 95%',
  'accent-foreground': '218 32% 15%',
  destructive: '348 67% 46%',
  'destructive-soft': '347 64% 95%',
  'destructive-foreground': '347 69% 33%',
  'destructive-solid': '348 67% 46%',
  border: '220 28% 91.5%',
  input: '220 28% 91.5%',
  ring: '223 68% 51%',
  gain: '5 69% 48%',
  'gain-soft': '9 70% 95%',
  'gain-foreground': '7 74% 36%',
  loss: '150 66% 27%',
  'loss-soft': '125 32% 93%',
  'loss-foreground': '150 72% 21%',
  success: '223 68% 51%',
  'success-soft': '220 64% 95%',
  'success-foreground': '223 72% 40%',
  warning: '38 88% 31%',
  'warning-soft': '41 76% 94%',
  'warning-foreground': '35 95% 25%',
  'foreground-secondary': '217 14% 41%',
};

const DARK_BG = 'rgb(15, 20, 32)'; // #0F1420
const DARK_CARD = 'rgb(23, 30, 44)'; // #171E2C
const DARK_FG = 'rgb(233, 237, 245)'; // #E9EDF5
const SOLID_LIGHT = 'rgb(196, 39, 70)'; // #C42746 渲染值
const SOLID_DARK = 'rgb(197, 43, 74)'; // #C52B4A

async function initTheme(page: Page, value: string) {
  await page.addInitScript((v) => localStorage.setItem('theme', v), value);
}

async function cssVar(page: Page, name: string): Promise<string> {
  return page.evaluate(
    (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim(),
    name,
  );
}

/** 打开一个危险确认弹窗，断载体 computed style，取消收尾（零业务写入） */
async function expectCarrier(
  page: Page,
  dialogTitle: string,
  confirmName: string,
  bg: string,
) {
  const dlg = page.getByRole('alertdialog').filter({ hasText: dialogTitle });
  await dlg.waitFor();
  const btn = dlg.getByRole('button', { name: confirmName });
  const styles = await btn.evaluate((el) => {
    const s = getComputedStyle(el);
    return { bg: s.backgroundColor, color: s.color };
  });
  expect(styles.bg).toBe(bg);
  expect(styles.color).toBe('rgb(255, 255, 255)');
  await dlg.getByRole('button', { name: '取消' }).click();
  await expect(dlg).toHaveCount(0);
}

/** #874 四处 solid 载体逐个打开断言（删除组合/删除交易/删除申赎/重新激活）。
 *  tag 区分浅/暗两轮夹具 code，避免同 worker/retry 撞 ALREADY_EXISTS。 */
async function expectFourCarriers(page: Page, testInfo: TestInfo, bg: string, tag: string) {
  await page.goto('/portfolio');
  await page.locator('button[title="删除组合"]').first().click();
  await expectCarrier(page, '删除组合', '确认删除', bg);

  await gotoPortfolioSubpage(page, E2E_ACTIVE, 'trades');
  await page.locator('button[title="删除"]').first().click();
  await expectCarrier(page, '删除交易', '确认', bg);

  const headers = await openAppAndAuth(page);
  const subCode = isolatedPortfolioCode('T872S', testInfo, `${tag}S`);
  await createPortfolioViaApi(page, headers, subCode);
  const applyDate = await nearestTradingDay(page, headers);
  const sub = await page.request.post('/api/subscriptions', {
    headers,
    data: {
      portfolio_code: subCode,
      investor_code: 'ADMIN',
      platform_code: 'HBZQ',
      sub_type: 'subscribe',
      amount: 10000,
      apply_date: applyDate,
    },
  });
  expect(sub.status(), await sub.text()).toBe(200);
  const { id } = (await sub.json()) as { id: number };
  try {
    await gotoPortfolioSubpage(page, subCode as typeof E2E_PORT, 'subscriptions');
    await page.locator('button[title="删除"]').first().click();
    await expectCarrier(page, '删除申请', '确认', bg);
  } finally {
    await page.request.delete(`/api/subscriptions/${id}`, { headers });
    await page.request.delete(`/api/portfolios/${subCode}`, { headers });
  }

  const reactCode = isolatedPortfolioCode('T872R', testInfo, `${tag}R`);
  await createPortfolioViaApi(page, headers, reactCode);
  await activatePortfolioViaApi(page, headers, reactCode);
  await closePortfolioViaApi(page, headers, reactCode);
  await page.goto('/portfolio');
  await page.locator('button[title="重新激活"]').first().click();
  await expectCarrier(page, '重新激活组合', '确认', bg);
}

test('默认浅色：html 无 .dark，33+1 个 light token 逐字节等于 golden（B3/D1）', async ({ page }) => {
  // 显式注入 light 偏好，解除对运行器默认 colorScheme 的隐式耦合（playwright.config use 未设 colorScheme）
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/dashboard');
  await expect(page.locator('html')).not.toHaveClass(/dark/);
  for (const [name, value] of Object.entries(LIGHT_TOKENS)) {
    expect(await cssVar(page, `--${name}`), `--${name}`).toBe(value);
  }
  const bodyBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  // hsl(220,23%,97%) 实际渲染 #F6F7F9（规范 hex #F7F8FA 为近似标注，±1/255）；以渲染值为准
  expect(bodyBg).toBe('rgb(246, 247, 249)');
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe('light');
});

test('深色生效：html.dark + 页面底/卡底/辅助文字/涨跌数值计算色（B1/D1）', async ({ page }) => {
  await initTheme(page, 'dark');
  await page.goto('/dashboard');
  await expect(page.locator('html')).toHaveClass(/dark/);
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe('dark');
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe(DARK_BG);
  expect(await cssVar(page, '--card')).toBe('220 31.34% 13.14%');
  const muted = page.locator('p.text-muted-foreground, span.text-muted-foreground').first();
  await expect(muted).toHaveCSS('color', 'rgb(138, 148, 171)');
  // 涨跌数值色：组合详情双端同源（positions 子页就绪信号桌面专属，不用作移动导航）
  await page.goto(`/portfolio/${E2E_ACTIVE}`);
  const gain = page.locator('.text-gain').first();
  await expect(gain).toHaveCSS('color', 'rgb(240, 115, 95)');
});

test('持久化：设置页选深色 → 刷新仍暗 → 新 context 复用 storageState 仍暗（B1）', async ({ page, context, browser }) => {
  await page.goto('/settings');
  await page.getByRole('tab', { name: '外观' }).click();
  await page.getByTestId('theme-select').click();
  await page.getByRole('option', { name: '深色' }).click();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await page.reload();
  await expect(page.locator('html')).toHaveClass(/dark/);
  const state = await context.storageState();
  const fresh = await browser.newContext({ storageState: state });
  const p2 = await fresh.newPage();
  await p2.goto('/dashboard');
  await expect(p2.locator('html')).toHaveClass(/dark/);
  await fresh.close();
});

test('跟随系统：OS 偏好中途改变即时切换，无需刷新（B2）', async ({ page }) => {
  await initTheme(page, 'system');
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/dashboard');
  await expect(page.locator('html')).toHaveClass(/dark/);
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(page.locator('html')).not.toHaveClass(/dark/);
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveClass(/dark/);
});

test('登录页双端暗色生效（B1/B5，未认证 context）', async ({ browser }, testInfo) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await initTheme(page, 'dark');
  const path = testInfo.project.name === 'mobile' ? '/m/login' : '/login';
  await page.goto(path);
  await expect(page.locator('html')).toHaveClass(/dark/);
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe(DARK_BG);
  await expect(page.getByRole('heading', { name: 'InvestRing' })).toHaveCSS('color', DARK_FG);
  await context.close();
});

test('图表暗色：饼图扇区/净值主线走暗色板，Tooltip 无白底（A③/D1）', async ({ page }) => {
  await initTheme(page, 'dark');
  await page.goto(`/portfolio/${E2E_ACTIVE}`);
  const sector = page.locator('.recharts-pie-sector path').first();
  await expect(sector).toHaveAttribute('fill', '#5C82E0');
  const line = page.locator('.recharts-line-curve').first();
  await expect(line).toHaveAttribute('stroke', '#5C82E0');
  // recharts 扇区入场动画/重渲染会使 path handle 失效（boundingBox null）；用稳定容器坐标触发 tooltip
  const wrap = page.locator('.recharts-responsive-container').first();
  const wb = await wrap.boundingBox();
  expect(wb).not.toBeNull();
  await page.mouse.move(wb!.x + wb!.width / 2, wb!.y + wb!.height * 0.15);
  const tooltip = page.locator('.recharts-tooltip-wrapper div').first();
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toHaveCSS('background-color', DARK_CARD);
});

test('首屏不闪白代理事实 + 无水合报错 + SSR 含主题内联脚本（B4）', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text());
  });
  page.on('pageerror', (e) => errors.push(e.message));
  await initTheme(page, 'dark');
  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
  // 首绘代理：domcontentloaded 时 .dark 已就位（内联脚本早于应用内容）
  expect(await page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(true);
  // 首绘无内联取色圆点常驻护栏①（守 visual-spec §1.1 第 5 条 / §17 已验证结论）：
  // 内联取色圆点（style={{ background }}）只应在客户端取数后渲染；若服务端预取
  // （dehydrate/HydrationBoundary）使它们回到首绘 DOM，本断言变红。
  const firstPaintInlineBg = await page.evaluate(() =>
    Array.from(document.querySelectorAll('[style]'))
      .filter((el) => /background(-color)?\s*:/i.test(el.getAttribute('style') ?? ''))
      .map((el) => el.outerHTML.slice(0, 120)),
  );
  expect(firstPaintInlineBg, 'domcontentloaded 不得存在内联 background 取色元素（§1.1/§17）').toHaveLength(0);
  await page.waitForLoadState('load');
  const hydration = errors.filter((e) => /hydrat|Minified React error/i.test(e));
  expect(hydration, hydration.join(' | ')).toHaveLength(0);
  const ssr = await page.request.get('/dashboard');
  const html = await ssr.text();
  // 首绘无内联取色圆点常驻护栏②（同 §1.1/§17）：SSR HTML 不得含内联 background/background-color
  // 取色 style——圆点实际序列化形态，属性锚定（不命中 background-image/background-size 等非取色属性）。
  // 覆盖 /dashboard 与圆点实际所在的三个页面（组合详情含饼图图例 / trades / cash-flows 含在途卡）；
  // 裸 HTTP 取 HTML、不经浏览器渲染，直接守服务端预取回归。
  for (const path of [
    '/dashboard',
    `/portfolio/${E2E_ACTIVE}`,
    `/portfolio/${E2E_ACTIVE}/trades`,
    `/portfolio/${E2E_ACTIVE}/cash-flows`,
  ]) {
    const body = path === '/dashboard' ? html : await (await page.request.get(path)).text();
    const inlineBg = body.match(/style="[^"]*background(-color)?\s*:[^"]*"/i);
    expect(inlineBg, `SSR ${path} 不得含内联 background 取色 style（§1.1/§17）: ${inlineBg?.[0] ?? ''}`).toBeNull();
  }
  // 可证伪：提取 SSR 内联脚本并断言其确为 next-themes 首绘机制（documentElement + classList +
  // localStorage + storageKey），而非任意含词串的 HTML。移除 ThemeProvider 时该脚本消失→本断言红。
  const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  const themeScript = scripts.find(
    (s) =>
      s.includes('document.documentElement') &&
      s.includes('classList') &&
      s.includes('localStorage.getItem') &&
      s.includes('"theme"'),
  );
  expect(themeScript, 'SSR 必须含 next-themes 首绘内联脚本（移除 ThemeProvider 应使本断言红）').toBeTruthy();
  // 功能证明：执行提取出的内联脚本（先移除 .dark），断言它真的给 documentElement 加回 .dark
  await page.evaluate((src) => {
    document.documentElement.classList.remove('dark');
    (0, eval)(src);
  }, themeScript!);
  expect(await page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(true);
  // colorScheme 属性传播（next-themes enableColorScheme 注入 style.colorScheme）；原生控件实际渲染仍列未覆盖
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe('dark');
});

test('浮层暗色：确认弹窗与日期区间浮层表面走暗 popover，日历文字可读（C1/D1）', async ({ page }, testInfo) => {
  await initTheme(page, 'dark');
  await gotoPortfolioSubpage(page, E2E_ACTIVE, 'snapshots');
  await page.locator('button[title="删除"]').first().click();
  const dlg = page.getByRole('alertdialog').filter({ hasText: '删除快照' });
  await dlg.waitFor();
  // Dialog/AlertDialog 表面走 bg-background（与 light 侧同 token parity），暗色=页面底 #0F1420；
  // Popover 表面走 bg-popover=#171E2C。两者均无白底残留（C1）。
  await expect(dlg).toHaveCSS('background-color', DARK_BG);
  await dlg.getByRole('button', { name: '取消' }).click();
  await expect(dlg).toHaveCount(0);

  await gotoPortfolioSubpage(page, E2E_ACTIVE, 'trades');
  await openFilterPanelIfMobile(page, testInfo);
  // 交易日期默认已有值（近1年）→ trigger title 为区间串；确认日期无值 → title=placeholder 稳定
  await page.locator('button[title="确认日期"]').click();
  const popper = page.locator('[data-radix-popper-content-wrapper]');
  await popper.waitFor();
  const surface = popper.locator('div').first();
  await expect(surface).toHaveCSS('background-color', DARK_CARD);
  const day = popper.locator('button[data-day]').first();
  // 首格可能为月外灰字（muted-foreground）；月内为 foreground。两者对暗 popover 均 ≥4.5:1 可读；
  // 断言落在可读暗色 token 集合内，排除硬编码浅灰残留。
  const dayColor = await day.evaluate((el) => getComputedStyle(el).color);
  expect([DARK_FG, 'rgb(138, 148, 171)']).toContain(dayColor);
  await page.keyboard.press('Escape');
  await settlePopovers(page);
});

test('四处 solid 危险载体暗色 computed style（#874 常驻收口）', async ({ page }, testInfo) => {
  test.setTimeout(90_000); // 4 弹窗 + API 夹具，mobile webkit 帧率计费需放宽（#763 同族）
  await initTheme(page, 'dark');
  await expectFourCarriers(page, testInfo, SOLID_DARK, 'D');
});

test('四处 solid 危险载体浅色 computed style（#874 常驻收口）', async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  // 显式注入 light 偏好，解除对运行器默认 colorScheme 的隐式耦合
  await page.emulateMedia({ colorScheme: 'light' });
  await expectFourCarriers(page, testInfo, SOLID_LIGHT, 'L');
});
