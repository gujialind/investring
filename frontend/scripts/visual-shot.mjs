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
 *             trade-date-range = 交易日期区间 Popover（移动端先展开折叠筛选面板）
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
 * 造数与红线见 frontend/AGENTS.md §5「目检」。
 */
import { existsSync, mkdirSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { chromium, webkit, devices } from '@playwright/test';

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
const SCENARIOS = ['snapshot-delete-confirm', 'trade-date-range'];

const opts = {
  base: 'http://localhost:3000',
  out: null,
  paths: [],
  devices: [],
  theme: 'light',
  scenario: null,
};
const argv = process.argv.slice(2);
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
  else if (a === '--path') opts.paths.push(next());
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

function shotName(path, device, theme) {
  const slug = path.split('/').filter(Boolean).slice(-2).join('-') || 'root';
  const suffix = theme === 'dark' ? '-dark' : '';
  return `${slug}-${device}${suffix}.png`;
}

/** 打开态浮层取证：交互同步点沿用 e2e/helpers 的 popper 挂载/卸载语义（不裸 waitFor） */
async function runScenario(page, scenario, device) {
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
      const suffix = theme === 'dark' ? '-dark' : '';
      if (opts.scenario && !loginOnly) {
        const handle = await runScenario(page, opts.scenario, device);
        await page.waitForTimeout(300); // 浮层入场收尾
        const overlay = resolve(opts.out, `overlay-${opts.scenario}-${device}${suffix}.png`);
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