/**
 * 目检截图（视觉验证脚手架）
 *
 * 用法：node scripts/visual-shot.mjs [--path P]... [--device desktop|mobile]...
 *                                     [--theme light|dark|both] [--scenario NAME]
 *                                     [--out DIR] [--base URL]
 *   --path    要截的页面路径，可重复；默认三个流水列表（见 DEFAULT_PATHS）
 *   --device  可重复；默认双端都截
 *   --theme   light|dark|both，默认 light（#872 PR2）；dark 经 colorScheme +
 *             addInitScript 写 localStorage.theme 注入偏好，文件名加 -dark 后缀
 *   --scenario  打开态浮层取证（#872 PR2，SPEC §9）：
 *             snapshot-delete-confirm = 快照删除确认 AlertDialog；
 *             trade-date-range = 交易日期区间 Popover（移动端先展开折叠筛选面板）；
 *             investor-reset-password = 投资人管理页重置密码 Dialog（#893，只开不提交，
 *             填短密码截校验 Alert 态）；
 *             snapshot-catchup-alert = 快照停更提示态（#906，API 造隔离组合把最新快照
 *             停在 4 个交易日前，详情页截 trailing 缺口 Alert；非浮层，close 为 no-op）；
 *             portfolio-breadcrumb-long-name = 组合子页长名称面包屑（#943 S1，仅改写
 *             当前页面的组合查询响应，不写库、不生成/重算快照）；
 *             info-tip = 说明浮层取证（#943 S2，未传 --path 时默认组合详情，也可指定
 *             产品/平台产品/快照/绩效说明所在页面）
 *   --out     输出目录，默认创建独立临时目录
 *   --base    前端地址，默认 http://localhost:3000（调用方须先启动服务）
 * 每个 --path 每张图出两张：整页 fullPage 与 table 元素裁剪（-table.png 后缀）；
 * --scenario 时改出打开态整页图（overlay-<scenario>-<device>[-dark].png）。
 * /login 特例：无认证 context 截（其余页复用 auth.json）。
 *
 * 与 playwright.config.ts 同口径：desktop = Desktop Chrome(1280x720)，
 * mobile = iPhone 13(webkit)，UA 驱动 src/proxy.ts 的 /m 重定向，
 * 故同一个 --path 双端通用。认证状态仅保存在本次输出目录。
 *
 * 通常由 scripts/visual-verify.sh 调用（那个脚本负责起后端与 standalone 前端）。
 * 直跑要求 Node ≥ 24（动态 import e2e/helpers.ts 依赖原生类型剥离，启动期有版本守卫）。
 * 造数与红线见 frontend/AGENTS.md §5「目检」。
 */
import { existsSync, mkdirSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { chromium, webkit, devices } from '@playwright/test';

// helpers.ts 为 TypeScript：依赖 Node 原生类型剥离（22.18+/23.6+ 默认开启，本仓
// .nvmrc=24）。守卫必须先于 helpers 加载生效，故用动态 import（静态 import 提升会
// 让旧 Node 在守卫前就抛 ERR_UNKNOWN_FILE_EXTENSION）；提前给可操作诊断（ocr r4）。
if (Number(process.versions.node.split('.')[0]) < 24) {
  console.error('visual-shot 需 Node ≥ 24（原生类型剥离加载 e2e/helpers.ts）；请先 nvm use');
  process.exit(2);
}
// 停更夹具造数与认证头与 E2E 共用 helpers 单点（ocr r3/r4 防漂移）
const { authHeaders, staleBaselineDates, seedStaleBaseline } = await import('../e2e/helpers.ts');

const DEVICES = {
  desktop: { descriptor: devices['Desktop Chrome'], launch: () => chromium.launch() },
  mobile: { descriptor: devices['iPhone 13'], launch: () => webkit.launch() },
};
// 种子契约组合（见 frontend/e2e/AGENTS.md §1）：E2E_ACTIVE 有已确认交易/申赎，表格里有行
const DEFAULT_PATHS = [
  '/portfolio/E2E_ACTIVE/trades',
  '/portfolio/E2E_ACTIVE/subscriptions',
  '/portfolio/E2E_ACTIVE/share-change-events',
];
const SCENARIOS = ['snapshot-delete-confirm', 'trade-date-range', 'investor-reset-password', 'snapshot-catchup-alert', 'portfolio-breadcrumb-long-name', 'info-tip'];
// 停更态目检夹具组合 code（scenario 造数与默认路径单点持有，防漂移，L2 r2）
const CATCHUP_FIXTURE_CODE = 'VIS906STALE';
const BREADCRUMB_SCENARIO_PATH = '/portfolio/E2E_ACTIVE/trades';
const BREADCRUMB_FIXTURE_CODE = BREADCRUMB_SCENARIO_PATH.split('/')[2];
const BREADCRUMB_LONG_NAME = '这是一个用于验证组合子页面包屑长名称换行与完整可访问语义的家庭投资组合';
const INFO_TIP_DEFAULT_PATH = '/portfolio/E2E_ACTIVE';

function infoTipLabelForPath(path) {
  const pathname = new URL(path, 'http://localhost').pathname;
  if (pathname.endsWith('/snapshots')) return '查看自动生成快照说明';
  if (pathname.includes('/platforms/') && pathname.includes('/products/')) return '查看累计收益说明';
  if (pathname.includes('/product/')) return '查看累计收益说明';
  if (pathname === INFO_TIP_DEFAULT_PATH || pathname === `/m${INFO_TIP_DEFAULT_PATH}`) {
    return '查看MWR 年化说明';
  }
  return null;
}

const opts = {
  base: 'http://localhost:3000',
  out: null,
  paths: [],
  devices: [],
  theme: 'light',
  scenario: null,
};
const argv = process.argv.slice(2);
let pathsExplicit = false;
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  const next = () => {
    const v = argv[++i];
    if (!v) {
      console.error(`${a} 缺少取值`);
      process.exit(2);
    }
    return v;
  };
  if (a === '--base') opts.base = next();
  else if (a === '--out') opts.out = next();
  else if (a === '--path') { opts.paths.push(next()); pathsExplicit = true; }
  else if (a === '--device') opts.devices.push(next());
  else if (a === '--theme') opts.theme = next();
  else if (a === '--scenario') opts.scenario = next();
  else {
    console.error(`未知参数：${a}\n可用：--path（可重复） --device desktop|mobile（可重复） --theme light|dark|both --scenario ${SCENARIOS.join('|')} --out --base`);
    process.exit(2);
  }
}
if (!['light', 'dark', 'both'].includes(opts.theme)) {
  console.error(`未知 theme：${opts.theme}（可用 light|dark|both）`);
  process.exit(2);
}
if (opts.scenario && !SCENARIOS.includes(opts.scenario)) {
  console.error(`未知 scenario：${opts.scenario}（可用 ${SCENARIOS.join('|')}）`);
  process.exit(2);
}
if (opts.paths.length === 0) opts.paths = DEFAULT_PATHS;
// 页面态 scenario 自行导航到夹具组合、忽略调用方 path：无条件收敛为单一路径，
// 避免多 path 产出同名异slug 的重复截图（L2 r1/r2）；显式 --path 给警告不静默
if (opts.scenario === 'snapshot-catchup-alert') {
  if (pathsExplicit) {
    console.warn(`⚠ scenario snapshot-catchup-alert 忽略 --path，统一截 /portfolio/${CATCHUP_FIXTURE_CODE}`);
  }
  opts.paths = [`/portfolio/${CATCHUP_FIXTURE_CODE}`];
}
if (opts.scenario === 'portfolio-breadcrumb-long-name') {
  if (pathsExplicit) {
    console.warn(`⚠ scenario portfolio-breadcrumb-long-name 忽略 --path，统一截 ${BREADCRUMB_SCENARIO_PATH}`);
  }
  opts.paths = [BREADCRUMB_SCENARIO_PATH];
}
if (opts.scenario === 'info-tip') {
  if (!pathsExplicit) opts.paths = [INFO_TIP_DEFAULT_PATH];
  const shotSlugs = new Set();
  for (const path of opts.paths) {
    if (!infoTipLabelForPath(path)) {
      console.error(`info-tip 场景不支持路径: ${path}；可用：种子组合详情（${INFO_TIP_DEFAULT_PATH}）、产品/平台产品详情、快照页`);
      process.exit(2);
    }
    const slug = shotSlug(path);
    if (shotSlugs.has(slug)) {
      console.error(`info-tip 场景截图文件名冲突: ${path}（slug ${slug}）；请改用可区分的路径`);
      process.exit(2);
    }
    shotSlugs.add(slug);
  }
}
if (opts.devices.length === 0) opts.devices = Object.keys(DEVICES);
for (const d of opts.devices) {
  if (!(d in DEVICES)) {
    console.error(`未知 device：${d}（可用 ${Object.keys(DEVICES).join(' | ')}）`);
    process.exit(2);
  }
}
const THEMES = opts.theme === 'both' ? ['light', 'dark'] : [opts.theme];
opts.out ??= mkdtempSync(join(tmpdir(), 'investring-visual-'));
mkdirSync(opts.out, { recursive: true });
const AUTH_FILE = resolve(opts.out, 'auth.json');

async function ensureLoggedIn(page, context) {
  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
  if (!new URL(page.url()).pathname.includes('login')) return;

  // hydration 前 fill 的值会被 controlled input 重渲染清空，导致静默登录失败
  await page.waitForFunction(() => {
    const form = document.querySelector('form');
    return form && Object.keys(form).some((k) => k.startsWith('__reactProps'));
  });
  await page.getByLabel('用户名').fill('ADMIN');
  await page.getByLabel('密码').fill('admin@2026');
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForURL(/\/(m\/)?dashboard/, { timeout: 15_000, waitUntil: 'domcontentloaded' });
  await context.storageState({ path: AUTH_FILE });
  console.log(`✔ 登录态已失效，重新登录并写入 ${AUTH_FILE}`);
}

function shotSlug(path) {
  return path.split('/').filter(Boolean).slice(-2).join('-') || 'root';
}

function shotName(path, device, theme) {
  const slug = shotSlug(path);
  const suffix = theme === 'dark' ? '-dark' : '';
  return `${slug}-${device}${suffix}.png`;
}

/** 打开态浮层取证：交互同步点沿用 e2e/helpers 的 popper 挂载/卸载语义（不裸 waitFor） */
async function runScenario(page, scenario, device) {
  if (scenario === 'info-tip') {
    const label = infoTipLabelForPath(page.url());
    if (!label) throw new Error(`info-tip 场景不支持路径: ${new URL(page.url()).pathname}`);
    const trigger = page.getByRole('button', { name: label });
    if (device === 'desktop') await trigger.hover();
    else await trigger.tap();
    const content = page.getByTestId('info-tip-content');
    await content.waitFor({ state: 'visible', timeout: 15_000 });
    return { close: async () => {
      await page.keyboard.press('Escape');
      await content.waitFor({ state: 'hidden', timeout: 15_000 });
    } };
  }
  if (scenario === 'portfolio-breadcrumb-long-name') {
    const portfolioApi = new RegExp(`/api/portfolios/${BREADCRUMB_FIXTURE_CODE}$`);
    const headers = await authHeaders(page);
    const response = await page.request.get(`/api/portfolios/${BREADCRUMB_FIXTURE_CODE}`, { headers });
    if (!response.ok()) {
      throw new Error(`目检夹具组合读取失败: ${response.status()} ${await response.text()}`);
    }
    const body = await response.json();
    await page.route(portfolioApi, async (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      await route.fulfill({ json: { ...body, name: BREADCRUMB_LONG_NAME } });
    });
    await page.goto(BREADCRUMB_SCENARIO_PATH, { waitUntil: 'domcontentloaded' });
    const crumb = page.getByRole('link', { name: '返回组合详情' });
    await crumb.waitFor({ state: 'visible', timeout: 15_000 });
    const text = await crumb.textContent();
    if (text !== BREADCRUMB_LONG_NAME) {
      throw new Error(`目检夹具面包屑名称不完整: ${text}`);
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (overflow > 1) throw new Error(`目检夹具长名称横向溢出: ${overflow}px`);
    return { close: async () => {} };
  }
  if (scenario === 'snapshot-catchup-alert') {
    // #906：API 造隔离组合（申购+确认+generate，纯 CASH 无需行情），最新快照停在
    // today 前第 4 个开市日 → trailing 缺口确定性非空（与 E2E setupStalePortfolio 同形）。
    // 目检库按运行独占新建，固定 code 不撞；同一运行内第二端/第二主题复用已造组合，
    // 复用与否都以 status 的 trailing 缺口非空为准（L2 r1：任何非 OK create 都当
    // 「已存在」会把真实后端错误掩成 15s 定位超时）。
    const headers = await authHeaders(page);
    const code = CATCHUP_FIXTURE_CODE;
    const okJson = async (resp, what) => {
      if (!resp.ok()) throw new Error(`目检夹具${what}失败: ${resp.status()} ${await resp.text()}`);
      return resp.json();
    };
    const create = await page.request.post('/api/portfolios', {
      headers, data: { code, name: '目检停更夹具' },
    });
    if (!create.ok()) {
      // 仅容忍结构化「已存在」（400 + detail.error=ALREADY_EXISTS，同运行第二端/
      // 第二主题复用）；其余带响应体响亮失败，不掩真实后端错误（L2 r2/r3）
      const body = await create.text();
      let errCode = null;
      try { errCode = JSON.parse(body)?.detail?.error ?? null; } catch { /* 非 JSON 一律响亮失败 */ }
      if (!(create.status() === 400 && errCode === 'ALREADY_EXISTS')) {
        throw new Error(`目检夹具建组合失败: ${create.status()} ${body}`);
      }
    } else {
      const { applyDate, latest } = await staleBaselineDates(page, headers);
      await seedStaleBaseline(page, headers, code, applyDate, latest);
    }
    const status = await okJson(
      await page.request.get(`/api/snapshots/portfolios/${code}/status`, { headers }),
      'status 查询',
    );
    if (!(status.catchup_pending_dates ?? []).length) {
      throw new Error(`目检夹具 ${code} trailing 缺口为空（create=${create.status()}），不可取证停更态`);
    }
    await page.goto(`/portfolio/${code}`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { level: 1 }).first().waitFor({ state: 'visible', timeout: 15_000 });
    await page.getByTestId('snapshot-catchup-alert').waitFor({ state: 'visible', timeout: 15_000 });
    return { close: async () => {} }; // 页面态而非浮层，无需收口
  }
  if (scenario === 'investor-reset-password') {
    // 本人（ADMIN）行无此按钮，first() 命中的是首个他人行；只开不提交，取消收口。
    // 填短密码触发校验 Alert 取证（#893 L2 S1：inline Alert 形态；校验拦截不发请求）
    await page.locator('button[title="重置密码"]').first().click();
    const dlg = page.getByRole('dialog').filter({ hasText: '重置密码' });
    await dlg.waitFor();
    await dlg.getByLabel('新密码', { exact: true }).fill('abc');
    await dlg.getByLabel('确认新密码').fill('abc');
    await dlg.getByRole('button', { name: '重置密码' }).click();
    await dlg.getByTestId('reset-password-validation-alert').waitFor();
    return { close: async () => {
      await dlg.getByRole('button', { name: '取消' }).click();
      await dlg.waitFor({ state: 'hidden' });
    } };
  }
  if (scenario === 'snapshot-delete-confirm') {
    await page.locator('button[title="删除"]').first().click();
    const dlg = page.getByRole('alertdialog').filter({ hasText: '删除快照' });
    await dlg.waitFor();
    return { close: async () => {
      await dlg.getByRole('button', { name: '取消' }).click();
      await dlg.waitFor({ state: 'hidden' });
    } };
  }
  // trade-date-range：移动端先展开折叠筛选面板（helpers.openFilterPanelIfMobile 同形态：
  // waitFor visible 再 click，避免 count() 竞态跳过展开）
  if (device === 'mobile') {
    const filterTrigger = page.getByRole('button', { name: /^筛选/ });
    await filterTrigger.waitFor({ state: 'visible', timeout: 15_000 });
    await filterTrigger.click();
  }
  await page.locator('button[title="确认日期"]').click();
  const popper = page.locator('[data-radix-popper-content-wrapper]');
  await popper.waitFor();
  return { close: async () => {
    await page.keyboard.press('Escape');
    await popper.waitFor({ state: 'hidden' });
  } };
}

const written = [];
for (const device of opts.devices) {
  const { descriptor, launch } = DEVICES[device];
  const browser = await launch();
  for (const theme of THEMES) {
    const context = await browser.newContext({
      ...descriptor,
      baseURL: opts.base,
      colorScheme: theme,
      storageState: existsSync(AUTH_FILE) ? AUTH_FILE : undefined,
    });
    // next-themes 存裸字符串；addInitScript 每次导航前注入，覆盖 storageState 携带的偏好
    await context.addInitScript((t) => localStorage.setItem('theme', t), theme);
    const probe = await context.newPage();
    await ensureLoggedIn(probe, context);
    await probe.close();

    for (const path of opts.paths) {
      // /login 特例：无认证 context（登录页本身不需要登录态）
      const loginOnly = path === '/login' || path === '/m/login';
      const ctx = loginOnly
        ? await browser.newContext({ ...descriptor, baseURL: opts.base, colorScheme: theme })
        : context;
      if (loginOnly) await ctx.addInitScript((t) => localStorage.setItem('theme', t), theme);
      const page = await ctx.newPage();
      await page.goto(path, { waitUntil: 'domcontentloaded' });
      if (opts.scenario && !loginOnly) {
        const handle = await runScenario(page, opts.scenario, device);
        await page.waitForTimeout(300); // 浮层入场收尾
        // 复用 shotName 带 path slug，避免多 --path 时场景图静默互相覆盖（#876 L2 S2）
        const overlay = resolve(opts.out, `overlay-${opts.scenario}-${shotName(path, device, theme)}`);
        await page.screenshot({ path: overlay, fullPage: true });
        written.push(overlay);
        await handle.close();
        console.log(`✔ [${device}/${theme}] overlay ${opts.scenario} ${path}`);
      } else {
        const table = page.locator('table').first();
        const hasTable = await table
          .waitFor({ state: 'visible', timeout: 15_000 })
          .then(() => true, () => false);
        if (!hasTable) console.log(`⚠ ${path} 未出现表格（页面无 table 或数据未加载），仅出整页图`);
        await page.waitForTimeout(500); // 流式渲染 / 过渡动画收尾

        const full = resolve(opts.out, shotName(path, device, theme));
        await page.screenshot({ path: full, fullPage: true });
        written.push(full);
        if (hasTable) {
          const clipped = resolve(opts.out, shotName(path, device, theme).replace(/\.png$/, '-table.png'));
          await table.screenshot({ path: clipped });
          written.push(clipped);
        }
        console.log(`✔ [${device}/${theme}] ${path}`);
      }
      await page.close();
      if (loginOnly) await ctx.close();
    }
    await context.close();
  }
  await browser.close();
}

console.log(`\n共 ${written.length} 张截图：`);
for (const f of written) console.log(`  ${f}`);
