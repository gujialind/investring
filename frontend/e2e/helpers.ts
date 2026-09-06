/**
 * 前端 E2E 共享导航 / 断言 / 选择框交互 helper（#354 导航；#372 选择框弹层）。
 *
 * 设计原则：spec 按组合 code 直达，不再经组合列表 `.first()`——list_portfolios
 * 无 ORDER BY，种子新增 E2E_ACTIVE 后「首个组合」不确定（曾使全部业务 spec 命中
 * 漂移）。两个组合是种子契约（backend/tests/seed_base.py），缺组合或形态退化即
 * 硬失败，helper 不做优雅 skip；`test.skip` 只留给真正条件性数据（平台 < 2、
 * 端专属用例、LOF 双市场种子缺失等）。
 *
 * 单一路径：portfolioPath 恒返回桌面路径 /portfolio/{code}[/sub]，mobile project
 * 经 src/proxy.ts 按 UA 重定向到 /m 前缀（Next.js 16 middleware 更名 proxy，#332）
 * ——结构性消除 `href^="/portfolio/"` 类只在桌面成立的定位（regression.spec.ts
 * 旧 mobile bug：移动端锚点 href 为 /m/portfolio/...，`^=` 永不匹配 → 恒 skip）。
 */
import { expect, test, type Page, type Locator } from '@playwright/test';

/** 种子契约组合（backend/tests/seed_base.py，勿删勿改形态） */
export const E2E_PORT = 'E2E_PORT'; // draft、零交易/申赎/快照
export const E2E_ACTIVE = 'E2E_ACTIVE'; // active、首购 + 已确认场内交易 + 2 日快照 + 1 pending 交易

/** 组合 code 限定为两个种子契约值：typo 即编译期报错，不再退化为运行期 15s 超时（#354 消除静默失败） */
export type PortfolioCode = typeof E2E_PORT | typeof E2E_ACTIVE;

/** 组合子页，与 src/app/portfolio/[code]/ 下路由一一对应 */
export type PortfolioSub =
  | 'trades'
  | 'snapshots'
  | 'subscriptions'
  | 'positions'
  | 'share-change-events';

/** 桌面组合路径；mobile project 靠 middleware 按 UA 重定向到 /m/portfolio/... */
export function portfolioPath(code: PortfolioCode, sub?: PortfolioSub): string {
  return sub ? `/portfolio/${code}/${sub}` : `/portfolio/${code}`;
}

/**
 * 各子页客户端渲染信号：等到其可见再返回，避免首帧空判（列表/表单为客户端 fetch）。
 * positions 信号「更新非净值资产」为桌面专属——移动端 positions 是独立实现
 * （m/positions/page.tsx，触发器为纯图标 cash-update-trigger），移动端调用方须用
 * portfolioPath 自行 goto 并等 cash-update-trigger（见 platform-select-search
 * 「移动端：更新非净值资产与筛选面板的平台选择框可搜索」用例）。
 */
const SUBPAGE_READY: Record<PortfolioSub, (page: Page) => Locator> = {
  trades: (page) => page.getByRole('button', { name: '提交交易' }).first(),
  snapshots: (page) => page.getByRole('button', { name: '追平至日期' }),
  subscriptions: (page) => page.getByRole('button', { name: /提交申请|首次申购激活/ }).first(),
  positions: (page) => page.getByRole('button', { name: '更新非净值资产' }),
  'share-change-events': (page) => page.getByRole('button', { name: '新建事件' }),
};

/** 进入组合详情页，等待页头 h1（组合名，draft/active 与双端均渲染）可见 */
export async function gotoPortfolioDetail(page: Page, code: PortfolioCode): Promise<void> {
  await page.goto(portfolioPath(code));
  await page
    .getByRole('heading', { level: 1 })
    .first()
    .waitFor({ state: 'visible', timeout: 15_000 });
}

/** 进入组合子页并等待该页渲染信号（positions 信号桌面专属，见 SUBPAGE_READY 注） */
export async function gotoPortfolioSubpage(
  page: Page,
  code: PortfolioCode,
  sub: PortfolioSub,
): Promise<void> {
  await page.goto(portfolioPath(code, sub));
  await SUBPAGE_READY[sub](page).waitFor({ state: 'visible', timeout: 15_000 });
}

/** 按弹窗标题定位业务 Dialog（Popover 弹层同样带 role=dialog，需用文案区分） */
export function dialogByTitle(page: Page, title: string | RegExp): Locator {
  return page.locator('[role="dialog"]').filter({ hasText: title }).first();
}

/**
 * 收集页面未捕获异常（客户端崩溃防线），用例末尾断言为空。
 * 豁免 Next.js standalone/mobile 的 RSC `_rsc` prefetch 被重定向层拦下的框架级
 * `access control` 噪音（`/m/...?_rsc=... due to access control checks`）：移动端
 * 导航至组合页时必然出现，页面功能与渲染均正常，非产品 bug；其余错误照常严格断言。
 */
export function collectPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => {
    const msg = e.message;
    if (/access control checks/.test(msg) && /_rsc=/.test(msg)) return;
    errors.push(msg);
  });
  return errors;
}

/**
 * 后端仅认 Authorization: Bearer 头（token 存 localStorage，无 cookie 回退），
 * page.request 不会自动附带，须从页面 localStorage 显式读取后传递。
 */
export async function authHeaders(page: Page): Promise<{ Authorization: string }> {
  const token = await page.evaluate(() => window.localStorage.getItem('token'));
  expect(token, '页面 localStorage 中缺少登录 token，无法调用后端 API').toBeTruthy();
  return { Authorization: `Bearer ${token}` };
}

// ===========================================================================
// 平台 / 产品选择框共享交互（issue #372）
//
// 抽自 platform-select-search / product-select-market / trade-buy-amount-linkage
// 三个 spec 的重复定位器。抽离前同一批 DOM 节点已有两种写法并各自漂移：产品选项行
// 一处按 Tailwind `div.cursor-pointer`、两处按 `data-testid="product-option"`；
// 「无数据优雅 skip」的 10s 等待 + 触发条件 + 文案散在 7 处。此处单点持有。
//
// 定位策略（勿在 spec 内另起一套）：
//   - 弹层 = 搜索 Input 最近的 role=dialog 祖先。#191 后 Dialog 内打开时
//     PopoverContent 被 Portal 注入 DialogContent，该祖先即弹层本身，天然排除外层
//     业务 Dialog；不在 Dialog 内时 Portal 到 body，该祖先同样是弹层本身。故弹层
//     查找一律 page 级，不要 scope 到 dlg。
//   - 触发器 = testid/aria + 回显文本双条件。不用 getByRole('button', { name })：
//     placeholder 态按钮实测 accessible name 匹配为 0。
//   - 选项行 = data-testid + data-code/data-market，不解析文案、不依赖 Tailwind
//     工具类与 lucide 图标类名（#217 定位器契约）。
//
// 命名约定（新增 helper 须遵守，消除 click/locate 混淆）：
//   xPopover / xTrigger / xOption → 纯 Locator 工厂（同步、无副作用）
//   xOptions                      → 只读批量提取 data 属性
//   firstXOption                  → 只读单行（含等待与优雅 skip），不点选
//   pickXxx                       → 会点选
//   expectXxx                     → 断言共享组件契约（业务断言留在 spec）
//   openXxx                       → 导航 + 开弹窗
// ===========================================================================

/** 首行等待 + 优雅 skip 的选项类别（决定 testid 与 skip 文案） */
type OptionKind = 'platform' | 'product';

/** 平台选项行：code 取自 data-code，text 为「名称 (CODE)」整串显示文本 */
export interface PlatformOptionRow {
  code: string;
  text: string;
}

/** 产品选项行：一码多市场分行，code 单独不唯一，须与 market 联用 */
export interface ProductOptionRow {
  code: string;
  market: string;
}

/**
 * 等首行选项出现（10s），超时即按类别优雅 skip。
 *
 * ⚠️ `test.skip()` 与 `expect` 一样靠 Playwright 的 async-context 解析当前 TestInfo，
 * 故本函数及其调用者（firstXOption / pickXxx）**只能在测试体直接 await 的调用链里
 * 使用**——放进游离 promise、Promise.race、setTimeout 或 `page.on` 回调都会失败。
 *
 * 只覆盖「首行 waitFor 失败 → 无数据」这一种触发条件。另一种形似而不同的是「读全量
 * 选项后按条数 skip」（platform-select-search 用例 6 的 `options.length < 2`、用例 10
 * 的 `options.length === 0`），触发条件与文案都不同，**不要互相改写**。
 */
async function firstOptionOrSkip(popover: Locator, kind: OptionKind): Promise<Locator> {
  const firstRow = popover
    .getByTestId(kind === 'platform' ? 'platform-option' : 'product-option')
    .first();
  try {
    await firstRow.waitFor({ timeout: 10_000 });
  } catch {
    test.skip(true, kind === 'platform' ? '环境中没有平台数据' : '环境中没有产品数据');
  }
  return firstRow;
}

/** 平台搜索弹层（SearchablePlatformSelect） */
export function platformPopover(page: Page): Locator {
  return page
    .getByPlaceholder('搜索平台名称/代码')
    .locator('xpath=ancestor::div[@role="dialog"][1]');
}

/** 产品搜索弹层（SearchableProductSelect） */
export function productPopover(page: Page): Locator {
  return page
    .getByPlaceholder('搜索产品代码/名称')
    .locator('xpath=ancestor::div[@role="dialog"][1]');
}

/**
 * SearchablePlatformSelect 触发按钮（testid + 回显/占位文本双条件；同页多实例由 scope
 * 限定）。文本可区分时用本函数——交易平台「请选择平台」与现金平台「同交易平台」互不
 * 为子串。文本无法区分时改按组件 id prop 定位（见 trade-buy-amount-linkage 的
 * `dlg.locator('button#platform_code')`）：id 是组件显式 API、不受回显变化影响，
 * 不要把它改写成文本形式。
 */
export function platformTrigger(scope: Page | Locator, label: string): Locator {
  return scope.getByTestId('platform-trigger').filter({ hasText: label }).first();
}

/**
 * SearchableProductSelect 触发按钮。产品组件的 testid 注解随其键盘化重写（#215）一并
 * 落地，此处暂用 aria-haspopup="dialog"（Radix Popover 触发器运行时注入）+ 文本双条件；
 * 同 Dialog 内平台选择框同为 aria-haspopup 按钮，靠 label 文本区分。
 */
export function productTrigger(scope: Page | Locator, label: string): Locator {
  return scope.locator('button[aria-haspopup="dialog"]', { hasText: label }).first();
}

/** 弹层内指定 code 的平台选项（按 data-code 属性定位，不依赖选项文案） */
export function platformOption(popover: Locator, code: string): Locator {
  return popover.locator(`[data-testid="platform-option"][data-code="${code}"]`);
}

/** 弹层内指定 code + market 的产品选项（一码多市场分行，单靠 code 不唯一） */
export function productOption(popover: Locator, code: string, market: string): Locator {
  return popover.locator(
    `[data-testid="product-option"][data-code="${code}"][data-market="${market}"]`
  );
}

/** 读取弹层内全部平台选项（不含特殊项） */
export async function platformOptions(popover: Locator): Promise<PlatformOptionRow[]> {
  return popover.getByTestId('platform-option').evaluateAll((els) =>
    els.map((el) => ({
      code: el.getAttribute('data-code') ?? '',
      text: ((el as HTMLElement).innerText || '').trim(),
    }))
  );
}

/** 读取弹层内全部产品选项的 {code, market}（data 属性，不解析文案） */
export function productOptions(popover: Locator): Promise<ProductOptionRow[]> {
  return popover.getByTestId('product-option').evaluateAll((els) =>
    els.map((el) => ({
      code: el.getAttribute('data-code') ?? '',
      market: el.getAttribute('data-market') ?? '',
    }))
  );
}

/** 选项文本 → name：剥离已知 ` (code)` 后缀（code 由 data-code 传入，非文案正则解析） */
export function optionName(text: string, code: string): string {
  const suffix = ` (${code})`;
  return text.endsWith(suffix) ? text.slice(0, -suffix.length) : text;
}

/** 只读弹层内第一个平台选项（无数据优雅 skip）；keyword = code 前 2 字符小写，供搜索断言 */
export async function firstPlatformOption(
  popover: Locator
): Promise<PlatformOptionRow & { keyword: string }> {
  const firstRow = await firstOptionOrSkip(popover, 'platform');
  const code = (await firstRow.getAttribute('data-code')) ?? '';
  const text = (await firstRow.innerText()).trim();
  return { code, text, keyword: code.slice(0, 2).toLowerCase() };
}

/** 点选弹层内指定 code 的平台选项 */
export async function pickPlatformOption(popover: Locator, code: string): Promise<void> {
  await platformOption(popover, code).click();
}

/** 点选第一个平台选项（无数据优雅 skip），返回所选 code */
export async function pickFirstPlatformOption(popover: Locator): Promise<string> {
  const firstRow = await firstOptionOrSkip(popover, 'platform');
  const code = (await firstRow.getAttribute('data-code')) ?? '';
  await firstRow.click();
  return code;
}

/**
 * 在 scope 内打开产品选择框并点选第一项（无数据优雅 skip），返回所选 code/market。
 * data 属性必须在 click **之前**读——点选即卸载弹层。
 */
export async function pickFirstProduct(
  page: Page,
  scope: Page | Locator
): Promise<ProductOptionRow> {
  await productTrigger(scope, '请选择产品').click();
  const firstRow = await firstOptionOrSkip(productPopover(page), 'product');
  const code = (await firstRow.getAttribute('data-code')) ?? '';
  const market = (await firstRow.getAttribute('data-market')) ?? '';
  await firstRow.click();
  return { code, market };
}

/**
 * 断言共享组件的客户端过滤契约：剩余平台选项均命中 keyword（小写比较）且至少 1 项。
 * 用 toPass() 轮询而非一次性读——搜索是受控输入，一次性读会撞上客户端重渲染竞态。
 * 返回过滤后选项，供调用方继续断言业务结果。
 */
export async function expectFilteredPlatformOptions(
  popover: Locator,
  keyword: string
): Promise<PlatformOptionRow[]> {
  let options: PlatformOptionRow[] = [];
  await expect(async () => {
    options = await platformOptions(popover);
    expect(options.length).toBeGreaterThan(0);
    for (const o of options) {
      expect(o.text.toLowerCase()).toContain(keyword);
    }
  }).toPass();
  return options;
}

/**
 * 进入组合调仓页并打开「提交交易」Dialog（TradesContent 双端共享，无需按 project 区分）。
 * 回传 portfolioCode 供调用方做 API 侧数据准备（如注入现金），免去再写一遍常量。
 */
export async function openSubmitTradeDialog(
  page: Page,
  code: PortfolioCode
): Promise<{ dlg: Locator; portfolioCode: PortfolioCode }> {
  await gotoPortfolioSubpage(page, code, 'trades');
  await page.getByRole('button', { name: '提交交易' }).first().click();
  const dlg = dialogByTitle(page, '提交交易');
  await dlg.waitFor();
  return { dlg, portfolioCode: code };
}
