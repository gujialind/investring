/**
 * 目检截图（视觉验证脚手架）
 *
 * 用法：node scripts/visual-shot.mjs [--path P]... [--device desktop|mobile]...
 *                                     [--out DIR] [--base URL]
 *   --path    要截的页面路径，可重复；默认三个流水列表（见 DEFAULT_PATHS）
 *   --device  可重复；默认双端都截
 *   --out     输出目录，默认 /tmp/visual-verify
 *   --base    前端地址，默认 http://localhost:3000（须已由 visual-verify.sh 起好）
 * 每个 --path 每张图出两张：整页 fullPage 与 table 元素裁剪（-table.png 后缀）。
 *
 * 与 playwright.config.ts 同口径：desktop = Desktop Chrome(1280x720)，
 * mobile = iPhone 13(webkit)，UA 驱动 src/proxy.ts 的 /m 重定向，
 * 故同一个 --path 双端通用。认证复用 e2e/.auth/admin.json 的 storageState，
 * 失效/不存在时走 auth.setup.ts 的登录流程并回写。
 *
 * 通常由 scripts/visual-verify.sh 调用（那个脚本负责起后端与 standalone 前端）。
 * 造数与红线见 frontend/AGENTS.md §4「目检」。
 */
import { existsSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { chromium, webkit, devices } from '@playwright/test';

const AUTH_FILE = 'e2e/.auth/admin.json';
const DEVICES = {
  desktop: { descriptor: devices['Desktop Chrome'], launch: () => chromium.launch() },
  mobile: { descriptor: devices['iPhone 13'], launch: () => webkit.launch() },
};
// 种子契约组合（见 frontend/AGENTS.md §4）：E2E_ACTIVE 有已确认交易/申赎，表格里有行
const DEFAULT_PATHS = [
  '/portfolio/E2E_ACTIVE/trades',
  '/portfolio/E2E_ACTIVE/subscriptions',
  '/portfolio/E2E_ACTIVE/share-change-events',
];

const opts = {
  base: 'http://localhost:3000',
  out: '/tmp/visual-verify',
  paths: [],
  devices: [],
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
  else {
    console.error(`未知参数：${a}\n可用：--path（可重复） --device desktop|mobile（可重复） --out --base`);
    process.exit(2);
  }
}
if (opts.paths.length === 0) opts.paths = DEFAULT_PATHS;
if (opts.devices.length === 0) opts.devices = Object.keys(DEVICES);
for (const d of opts.devices) {
  if (!(d in DEVICES)) {
    console.error(`未知 device：${d}（可用 ${Object.keys(DEVICES).join(' | ')}）`);
    process.exit(2);
  }
}
mkdirSync(opts.out, { recursive: true });

/**
 * storageState 可能不存在（e2e/.auth/ 被 gitignore）或 token 已过期：
 * 先访问 /dashboard 验，被弹回登录页就现场登录并回写，不要求先跑 playwright setup
 */
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

function shotName(path, device) {
  const slug = path.split('/').filter(Boolean).slice(-2).join('-') || 'root';
  return `${slug}-${device}.png`;
}

const written = [];
for (const device of opts.devices) {
  const { descriptor, launch } = DEVICES[device];
  const browser = await launch();
  const context = await browser.newContext({
    ...descriptor,
    baseURL: opts.base,
    storageState: existsSync(AUTH_FILE) ? AUTH_FILE : undefined,
  });
  const probe = await context.newPage();
  await ensureLoggedIn(probe, context);
  await probe.close();

  for (const path of opts.paths) {
    const page = await context.newPage();
    await page.goto(path, { waitUntil: 'domcontentloaded' });
    const table = page.locator('table').first();
    const hasTable = await table
      .waitFor({ state: 'visible', timeout: 15_000 })
      .then(() => true, () => false);
    if (!hasTable) console.log(`⚠ ${path} 未出现表格（页面无 table 或数据未加载），仅出整页图`);
    await page.waitForTimeout(500); // 流式渲染 / 过渡动画收尾

    const full = resolve(opts.out, shotName(path, device));
    await page.screenshot({ path: full, fullPage: true });
    written.push(full);
    if (hasTable) {
      const clipped = resolve(opts.out, shotName(path, device).replace(/\.png$/, '-table.png'));
      await table.screenshot({ path: clipped });
      written.push(clipped);
    }
    console.log(`✔ [${device}] ${path}`);
    await page.close();
  }
  await browser.close();
}

console.log(`\n共 ${written.length} 张截图：`);
for (const f of written) console.log(`  ${f}`);
