/**
 * 前端 E2E 共享导航 / 断言 / 选择框交互 helper（#354 导航；#372 选择框弹层；#853 单行布局判据）。
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
 *
 * **Node 直载契约（#906 ocr r6）**：本文件被 scripts/visual-shot.mjs 经 Node 原生
 * 类型剥离直接 import（非 Playwright TS loader）。故仅可用**可擦除** TS 语法
 * （no enum/namespace/参数属性），且相对导入必须带 `.ts` 扩展名（类型剥离不解析
 * 无扩展名 `./foo`→`./foo.ts`）。违反会使 visual-shot 启动期 ERR_MODULE_NOT_FOUND /
 * 语法报错，而 Playwright 运行仍绿（visual-shot 在 e2e CI job 之外）。当前仅
 * import '@playwright/test'，满足契约；新增相对导入须带 .ts。
 */
import { expect, test, type Page, type Locator, type TestInfo } from '@playwright/test';

/** 种子契约组合（backend/tests/seed_base.py，勿删勿改形态） */
export const E2E_PORT = 'E2E_PORT'; // draft、零交易/申赎/快照
export const E2E_ACTIVE = 'E2E_ACTIVE'; // active、首购 + 已确认场内交易 + 2 日快照 + 1 pending 交易

/** 面包屑长名夹具：同时覆盖 CJK 换行与无空格 Latin token，防止撑破窄栏。 */
export const BREADCRUMB_LONG_NAME = `这是一个用于验证组合子页面包屑长名称换行与完整可访问语义的家庭投资组合-${'A'.repeat(55)}`;

/** 组合 code 限定为两个种子契约值：typo 即编译期报错，不再退化为运行期 15s 超时（#354 消除静默失败） */
export type PortfolioCode = typeof E2E_PORT | typeof E2E_ACTIVE;

/** 组合子页，与 src/app/portfolio/[code]/ 下路由一一对应 */
export type PortfolioSub =
  | 'trades'
  | 'snapshots'
  | 'subscriptions'
  | 'positions'
  | 'share-change-events'
  | 'cash-flows';

/** 桌面组合路径；mobile project 靠 middleware 按 UA 重定向到 /m/portfolio/... */
export function portfolioPath(code: PortfolioCode, sub?: PortfolioSub): string {
  return sub ? `/portfolio/${code}/${sub}` : `/portfolio/${code}`;
}

/**
 * 各子页客户端渲染信号：等到其可见再返回，避免首帧空判（列表/表单为客户端 fetch）。
 * positions 信号「更新现金市值」为桌面专属——移动端 positions 是独立实现
 * （m/positions/page.tsx，触发器为纯图标 cash-update-trigger），移动端调用方须用
 * portfolioPath 自行 goto 并等 cash-update-trigger（见 platform-select-search
 * 「移动端：更新现金市值与筛选面板的平台选择框可搜索」用例）。
 */
const SUBPAGE_READY: Record<PortfolioSub, (page: Page) => Locator> = {
  trades: (page) => page.getByRole('button', { name: '提交交易' }).first(),
  snapshots: (page) => page.getByRole('button', { name: '追平至日期' }),
  subscriptions: (page) => page.getByRole('button', { name: /提交申请|首次申购激活/ }).first(),
  positions: (page) => page.getByRole('button', { name: '更新现金市值' }),
  'share-change-events': (page) => page.getByRole('button', { name: '新建事件' }),
  // 现金流水页首屏 isLoading 期只渲染 LoadingState，「按平台构成」按钮出现即首帧数据落地（#725）
  'cash-flows': (page) => page.getByTestId('open-breakdown'),
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

/**
 * 移动端展开折叠筛选面板；桌面端筛选栏恒展开，本函数 no-op（#383）。
 *
 * `components/shared/*Content.tsx` 的 `variant === "mobile"` 分支把 filterControls 包在
 * `{filterOpen && …}` 里、`filterOpen` 初值 false，故未展开时 platformTrigger 定位到
 * 未渲染节点、click 直接超时。这是端差异里**唯一**需要适配的一处：面板展开后控件与
 * 桌面完全同构（同一 filterControls 节点），断言本身双端通用。
 *
 * 按钮名必须用锚定正则——激活计数 Badge 会把 accessible name 变成「筛选1」，而空态在
 * `hasNonDefaultFilter` 下另有「重置筛选」按钮（EmptyState 的 resetFilters action），
 * Playwright 字符串 name 默认子串匹配，两者会 strict-mode 撞车。
 */
export async function openFilterPanelIfMobile(page: Page, testInfo: TestInfo): Promise<void> {
  if (testInfo.project.name !== 'mobile') return;
  const trigger = page.getByRole('button', { name: /^筛选/ });
  await trigger.waitFor({ state: 'visible', timeout: 15_000 });
  await trigger.click();
}

/**
 * 关闭移动端筛选 sheet（#904 P1b）：sheet 是 modal Dialog，开着时表格外 aria-hidden，
 * 行集 role 定位/点击不可达；设完筛选须回列表（点「完成」）再断言行集，与真实用户流一致。
 * 桌面筛选栏恒展开、无 sheet，本函数 no-op。
 */
export async function closeFilterPanelIfMobile(page: Page, testInfo: TestInfo): Promise<void> {
  if (testInfo.project.name !== 'mobile') return;
  await page.getByRole('button', { name: '完成', exact: true }).click();
}

/**
 * 打开组合详情「更多」菜单（#943）：已打开时直接复用，桌面先移开再 hover 以触发 pointerenter、取消延时关闭；
 * 未打开时同样清理旧 hover；移动 Sheet 不是 popper，残留则 Escape、等卸载并清理浮层后点击。
 */
export async function openPortfolioMoreMenu(page: Page, testInfo: TestInfo): Promise<void> {
  const trigger = page.getByTestId('portfolio-more-trigger');
  const menu = page.getByTestId('portfolio-more-menu');
  if (await menu.isVisible()) {
    if (testInfo.project.name === 'desktop') {
      await page.mouse.move(0, 0);
      await trigger.hover();
    }
    return;
  }
  if (testInfo.project.name === 'mobile') {
    if ((await menu.count()) > 0) await page.keyboard.press('Escape');
    await expect(menu).toHaveCount(0);
    await settlePopovers(page);
    await trigger.click();
  } else {
    await page.mouse.move(0, 0);
    await settlePopovers(page);
    await trigger.hover();
  }
  await expect(menu).toBeVisible();
}

/** 从组合详情「更多」菜单（双端同一入口）进入历史导航页。 */
export async function clickPortfolioMoreMenuItem(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  await openPortfolioMoreMenu(page, testInfo);
  const menu = page.getByTestId('portfolio-more-menu');
  await menu
    .getByRole('menuitem', { name, exact: true })
    .or(menu.getByRole('link', { name, exact: true }))
    .click();
}

/**
 * 打开设置页某个分区（#878 双端形态分流）：桌面是三 Tab（点 tab），移动是分组列表 +
 * 行级钻取子页（点行 Link）。`goto('/settings')` 在 mobile project 经 proxy 落到
 * /m/settings，故按落地 pathname 是否带 /m/ 前缀分流，不依赖 testInfo（调用方未必持有）。
 * desktopTab / mobileRow 分别给两端可见名：日志管理/交易日历同步两端同名；外观桌面在
 * 「通用」Tab（默认激活）、移动是「外观与主题」行。
 */
export async function openSettingsSection(
  page: Page,
  desktopTab: string,
  mobileRow: string,
): Promise<void> {
  await page.goto('/settings');
  if (new URL(page.url()).pathname.startsWith('/m/')) {
    await page.getByRole('link', { name: mobileRow }).click();
  } else {
    await page.getByRole('tab', { name: desktopTab }).click();
  }
}

/** 按弹窗标题定位业务 Dialog（Popover 弹层同样带 role=dialog，需用文案区分） */
export function dialogByTitle(page: Page, title: string | RegExp): Locator {
  return page.locator('[role="dialog"]').filter({ hasText: title }).first();
}

/**
 * 按标题定位 toast 卡片（#382）。ToastContainer 卡片根挂 data-testid="toast-card"；
 * toast 默认存活 3s、可堆叠出多张，故用 filter({ has: heading }) 按标题收窄。
 * 不用 hasText：把标题钉在 <h4> 上，避免误命中消息 <p>。
 *
 * ⚠️ **只能用于「没有 modal Dialog 开着」的时机**（#640 第三轮实测）：Radix modal Dialog
 * 打开期间把 portal 之外的其余子树置 `aria-hidden`，而 `getByRole` 走**无障碍树**——
 * 挂在 app root 的 toast 此刻对 `getByRole('heading')` 完全不可见，本函数恒返回 0 命中，
 * 而 DOM 口径的 `getByTestId('toast-card')` 照常命中。所以在 Dialog 内提交/撤销后断 toast
 * （典型如「更新现金市值」），写 `page.getByTestId('toast-card').filter({ hasText: 标题 })`，
 * 不要改用本函数——它会以「element(s) not found」红在一个其实正确的产品行为上。
 */
export function toastByTitle(page: Page, title: string | RegExp): Locator {
  return page
    .getByTestId('toast-card')
    .filter({ has: page.getByRole('heading', { name: title }) })
    .first();
}

/**
 * 收集页面未捕获异常（客户端崩溃防线），用例末尾断言为空。
 * 豁免两类 webkit/Next.js 框架级 `access control` 噪音（均非产品 bug，页面功能与
 * 渲染正常，其余错误照常严格断言）：
 * 1. RSC `_rsc` prefetch 被重定向层拦下：`/m/...?_rsc=... due to access control checks`，
 *    移动端导航至组合页时必然出现。
 * 2. `tolerateLoginRaceNoise`（仅显式传入时生效）：webkit 在**UI 登录→dashboard 硬导航
 *    窗口期**丢弃指向本机 E2E 栈的同源 `/api/` XHR（#789 实测：mobile=webkit 6 次
 *    viewer 登录压测 6/6 触发；同一消息有两种形态——
 *    `XMLHttpRequest cannot load http://127.0.0.1:<port>/api/...` 与
 *    `/127.0.0.1:<port>/api/... due to access control checks`；被拦的恰是 dashboard
 *    聚合查询 useDashboardStats 的 4 个 GET）。请求未达后端、react-query 下一次重试即
 *    恢复，数据与 UI 断言照常通过——是引擎在导航竞态里抛的 rejection，不是应用崩溃。
 *    只有真的做 UI 登录的用例才该声明它；同源 API 真接错时，该用例的数据断言会先红。
 */
export function collectPageErrors(page: Page, options?: { tolerateLoginRaceNoise?: boolean }): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => {
    const msg = e.message;
    if (!/access control checks/.test(msg)) {
      errors.push(msg);
      return;
    }
    if (/_rsc=/.test(msg)) return;
    if (
      options?.tolerateLoginRaceNoise &&
      /(?:^|[\s/])(?:https?:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?|127\.0\.0\.1(?::\d+)?|localhost(?::\d+)?)\/api\/\S+ due to access control checks/.test(msg)
    ) {
      return;
    }
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
// 日历选日共享交互（#640 U-4/S-2）
//
// 抽自 platform-detail 的 pickDialogDay 与 trade-in-transit 的 pickDay——两份逐行同形，
// 漂移出来的正是多套一层 getByRole('dialog') 的那份（#640 T-1 因此从未真正执行过）；
// 第四轮再消掉 trade-buy-amount-linkage 的 selectTradeDate（同形、只少那条跳过守卫）。
// 刻意未并的两处见 frontend/e2e/AGENTS.md §1 的台账：share-change-cash-pay-date 的 pickDay
// 同为 ISO 口径、仅多一条点击前 toBeEnabled 断言；datepicker-in-dialog
// 按「当月某日」（data-day$="-18"）定位，不属本函数口径。
// ===========================================================================

/** 本地日期 → yyyy-MM-dd（**不用 toISOString**：它按 UTC 切日，与组件侧 `toDateOnly()`
 *  的本地口径错开，本机 TZ=UTC+8 的开发者在 00:00–07:59 跑必红，#640 U-2/S-2。
 *  e2e 侧的同形本地拷贝已全部改为导入本函数，CI 容器是 UTC、测不到这类错开，
 *  新增 spec 别再复制一份「看起来一样」的本地实现） */
export function toISODate(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

/** 默认业务时区 Asia/Shanghai（settings.timezone）；改配置须同步此锚点。 */
export function businessDateISO(dayOffset = 0): string {
  const shifted = new Date(Date.now() + (8 + dayOffset * 24) * 60 * 60 * 1000);
  const m = String(shifted.getUTCMonth() + 1).padStart(2, '0');
  const d = String(shifted.getUTCDate()).padStart(2, '0');
  return `${shifted.getUTCFullYear()}-${m}-${d}`;
}

/**
 * 在日历弹层点选 `targetISO`（yyyy-MM-dd），断言 trigger 回显后收干净弹层。
 *
 * `trigger` 由**调用方**给出，且其作用域必须是 `dialogByTitle` 的结果：Radix
 * `PopoverContent` 自带 `role="dialog"`，在已取得的 dialog 上再套一层
 * `getByRole('dialog')` 会命中 0（弹层内没有第二个 dialog）。
 *
 * 「目标日 == 控件当前值」时直接跳过、不开弹层：少一次浮层开合即少一处 #524 竞态面。
 * #542 之前这一跳过是**正确性前提**——react-day-picker@10 单选未传 `required`，把「点已
 * 选日」当成取消选择（`useSingle`: `!required && isSameDay → onSelect(undefined)`），而当
 * 时 `date-picker.tsx` 只在 newDate 非空时才关弹层，结果值被清空、弹层还开着（CI
 * 2026-09-16 实踩：目标日恰为表单默认值 today）。#542 已改为「点任何日都关弹层、toggle-off
 * 不回传调用方」，清空唯一入口是 X 按钮，故这里的跳过只剩效率意义。
 *
 * 翻页基准取**控件当前值**（未选值时取 today），不能按 today：`DatePicker` 打开时把
 * month 重置为 `date ?? new Date()`（ui/date-picker.tsx），跨月连续选两日时后一目标
 * 可能仍 ≤ today 却晚于已选日（#660 的 d3=上月末、d4=本月首个交易日），按 today 判会
 * 反向翻页并超时（CI 2026-11-02 实踩）。各调用点目标与基准最多跨一个自然月；更远会
 * 点不到目标日而显式超时，不会静默点错月。
 *
 * 隐含前提：trigger 所在 DialogContent **不得**自带 `overflow-y-auto`——#191 方案 C 把
 * 弹层 Portal 注入 DialogContent 自身，父级 overflow 会裁掉月历底行，那一点落在遮罩上、
 * 连人带表单一起关闭（#640 U-1）。
 */
export async function pickCalendarDay(
  page: Page,
  trigger: Locator,
  targetISO: string,
): Promise<void> {
  const currentISO = (await trigger.textContent())?.trim();
  if (currentISO === targetISO) return;
  await openPopover(page, trigger, page.locator('button.rdp-day_button').first());
  const day = page.locator(`button.rdp-day_button[data-day="${targetISO}"]`);
  // 显式上界，与全局 use.actionTimeout（#551 §2）同值；留字面值防预算随配置漂移
  if ((await day.count()) === 0) {
    const fromISO = currentISO && /^\d{4}-\d{2}-\d{2}$/.test(currentISO)
      ? currentISO
      : toISODate(new Date());
    const dir = targetISO > fromISO ? 'next' : 'previous';
    await page.locator(`button.rdp-button_${dir}`).click({ timeout: 10_000 });
  }
  await day.click({ timeout: 10_000 });
  await expect(trigger).toHaveText(targetISO);
  await settlePopovers(page); // 选日即关弹层（#542），收干净再让调用方操作下一控件
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
//   xByTitle                      → 纯 Locator 工厂，按标题文案定位容器（dialog / toast）
//   xOptions                      → 只读批量提取 data 属性
//   firstXOption                  → 只读单行（含等待与优雅 skip），不点选
//   pickXxx                       → 会点选（点选即关弹层，内部已 settlePopovers）
//   expectXxx                     → 断言共享组件契约（业务断言留在 spec）
//   openXxx                       → 导航 + 开弹窗
//   openXxxIfMobile               → 端差异适配（另一端 no-op），不含导航
//   openPopover                   → 开浮层并确认内容可见（#524 吞点击同步，非纯工厂）
//   settlePopovers                → 等 Radix 浮层容器全部卸载（#524 同步点，非断言）
//                                  （#551 起不再有 settlePreview：预览 refetch 的重挂载缺陷
//                                   已在 ConfirmInfoDialog 侧根治，见其 inputSlot 注释）
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

/**
 * Radix 浮层挂载容器（Popover / Select / Dropdown 的 Popper 出口）。
 *
 * ⚠️ 这是 Radix **实现层**注入的容器属性，不在其公开 API 清单内（与 `rdp-day_button`
 * 那类库自己承诺的锚点不同性质，本仓能用它纯属实现细节稳定）。升级
 * `@radix-ui/react-popover` 时须复核属性名是否仍在：一旦改名，下面的谓词会**立即满足**
 * （页面上永远查不到该属性）、`settlePopovers` 静默退化为 no-op、#524 竞态无声回归，
 * 而所有用例仍是绿的。非 Tailwind 工具类，#382 定位器门禁不拦。
 */
const POPPER_WRAPPER = '[data-radix-popper-content-wrapper]';

/**
 * 交互动作的显式上界。全局 `use.actionTimeout` 已设成同一个 10_000（#551 §2），这里仍逐个
 * 显式钉：① helper 的预算不该依赖调用方是否记得配置文件里有一行；② `openPopover` 的 3_000
 * 分支探针必须严格小于本值，这道大小关系要在代码里看得见，不能一头写死、一头靠全局。
 */
const CLICK_TIMEOUT = 10_000;

/**
 * 等页面上已无挂载中的 Radix 浮层容器——同步点是**弹层整棵卸载**，不是「等动画走完」。
 *
 * #524：Radix `DismissableLayer` 在关闭过程中仍挂在 document 上监听 `pointerdown`，会把
 * 紧接着的下一次点击（开另一个浮层、或点 Dialog 里的按钮）整口吞掉；层清理完成的可见
 * 标志就是 popper 容器从 DOM 消失。浮层开合 helper 一律「开前等、收后等」，从源头挡掉
 * 这一竞态。等不到不在此处判红——真卡住时后面的可见性/状态断言会以更清楚的方式失败，
 * 这里只是同步点而非断言。
 *
 * 归因订正（#551）：本函数旧文档与调用点注释里的「退出动画」不实。本仓是
 * `tailwindcss ^4` 且未装 `tailwindcss-animate`，`ui/popover.tsx` 上的
 * `animate-in` / `animate-out` / `fade-*` / `zoom-*` / `slide-in-from-*` 全是**无定义的
 * 工具类**（注意 `animate-spin` 是 v4 核心类、有定义，别把两者混为一谈），Radix Presence
 * 在 `animationName === 'none'` 时直接卸载，**没有动画可等**。也别顺手把动画补回来——
 * 那只会把这段竞态窗口拉长。
 */
export async function settlePopovers(page: Page): Promise<void> {
  await page
    .waitForFunction(
      (selector) => document.querySelectorAll(selector).length === 0,
      POPPER_WRAPPER,
      { timeout: 5_000 },
    )
    .catch(() => undefined);
}

/**
 * 点 trigger 打开浮层，并确认 `content`（该浮层内的锚点元素）真的可见。
 *
 * #524：Radix `DismissableLayer` 在关闭过程中仍在 document 上监听 `pointerdown`，会把紧
 * 跟着那次「打开」整口吞掉——点击送达 trigger，新浮层随即被 dismiss。此后调用方的
 * `waitFor` 只能等到超时（裸 `waitFor()` 现由全局 `actionTimeout` 兜在 10s，但被吞一次仍
 * 白烧 10s，故浮层开合仍须走本 helper）。CI 形态对比
 * （capture `--retries=0`，无 retry 保护）连红 5 次，失败点都落在「收一个浮层 → 立刻开
 * 下一个」的相邻链路上。
 *
 * 两道同步，都是「等一等、看一眼」而非放宽断言：
 * 1. 点前先等旧浮层容器彻底卸载（根因）；
 * 2. 点后再看一眼：**只有**「内容不可见 且 页面上没有任何浮层挂载」才判定被吞、重点一次
 *    （浮层已挂载却等不到内容属定位器/业务问题，重点只会把开着的浮层关掉，故直接抛超时）。
 * 兜底重试仍失败即按正常超时红，不掩盖真失败。
 *
 * `content` 应选**与数据量无关**的锚点（弹层自身或其中的搜索框），不要拿选项行当锚点——
 * 否则「无数据」这一合法形态会被本函数判成「没开成」而重点一次，把 spec 里
 * `firstOptionOrSkip` 的优雅 skip 语义改写成硬失败。
 *
 * ⚠️ 预算（#551 登记，暂不改行为）：三条分支的内部上界分别是 18s（5 settle + 10 click +
 * 3 探针，首见即返）/ 28s（再 +10，已挂载但不可见）/ 43s（判定被吞、走了重点一次），而
 * `playwright.config.ts` 全局 `timeout: 30_000`。后两条在慢环境里会先撞全局 timeout，
 * 届时失败信息是裸的用例超时而非这里的分支超时——排查时先看这一条，别怀疑竞态又回来了。
 * （#551 §2 设的全局 `actionTimeout` 与本段无关：这里每个等待都写死了显式值。）
 */
export async function openPopover(
  page: Page,
  trigger: Locator,
  content: Locator,
): Promise<void> {
  await settlePopovers(page);
  await trigger.click({ timeout: CLICK_TIMEOUT });
  const appeared = await content
    .waitFor({ state: 'visible', timeout: 3_000 })
    .then(() => true)
    .catch(() => false);
  if (appeared) return;
  if ((await page.locator(POPPER_WRAPPER).count()) > 0) {
    await content.waitFor({ state: 'visible', timeout: 10_000 });
    return;
  }
  await settlePopovers(page);
  await trigger.click({ timeout: CLICK_TIMEOUT });
  await content.waitFor({ state: 'visible', timeout: 10_000 });
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

/**
 * 点选弹层内指定 code 的平台选项。
 *
 * 点选即关弹层，故收完必须 settle——否则调用方「紧接着点下一个控件」就落在 #524 的
 * 吞点击窗口里。本文件早期版本只点不收，是该竞态在 helper 侧的残留面（#551 §1）。
 */
export async function pickPlatformOption(popover: Locator, code: string): Promise<void> {
  await platformOption(popover, code).click({ timeout: CLICK_TIMEOUT });
  await settlePopovers(popover.page());
}

/** 点选第一个平台选项（无数据优雅 skip），返回所选 code；settle 语义同 pickPlatformOption */
export async function pickFirstPlatformOption(popover: Locator): Promise<string> {
  const firstRow = await firstOptionOrSkip(popover, 'platform');
  const code = (await firstRow.getAttribute('data-code')) ?? '';
  await firstRow.click({ timeout: CLICK_TIMEOUT });
  await settlePopovers(popover.page());
  return code;
}

/**
 * 在 scope 内打开产品选择框并点选第一项（无数据优雅 skip），返回所选 code/market。
 * data 属性必须在 click **之前**读——点选即卸载弹层。
 *
 * 「弹层开没开成」（openPopover，锚点用与数据量无关的搜索框容器）与「有没有数据」
 * （firstOptionOrSkip 的优雅 skip）是两种不同触发条件，刻意分两步、不合并——把数据
 * 缺失判成吞点击，会把这些 spec 的优雅 skip 改写成硬失败。
 */
export async function pickFirstProduct(
  page: Page,
  scope: Page | Locator
): Promise<ProductOptionRow> {
  await openPopover(page, productTrigger(scope, '请选择产品'), productPopover(page));
  const popover = productPopover(page);
  const firstRow = await firstOptionOrSkip(popover, 'product');
  const code = (await firstRow.getAttribute('data-code')) ?? '';
  const market = (await firstRow.getAttribute('data-market')) ?? '';
  await firstRow.click({ timeout: CLICK_TIMEOUT });
  await settlePopovers(page);
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

/** 单行布局的测量结果；调用方用 scrollWidth/clientWidth 追加各自判据，避免把 B/C 锁进公共 helper */
export type SingleLineMetrics = {
  height: number;
  lineHeight: number;
  scrollWidth: number;
  clientWidth: number;
};

/**
 * 断言元素按单行渲染（判据 A，#829 / #733）：可见盒 + line-height 为正 + 盒高 ≤ 1.5×行高。
 *
 * 取**盒高 vs 计算行高**而不是 `getClientRects().length`：行盒数随引擎与文本节点数变化
 * （同一行里 `{label}{platformName}` 两个文本节点在 chromium 下可能回 2 个 rect），
 * 只有高度是双端一致的事实；折行时高度是行高的整数倍（实测 2 行 35px、3 行 55px）。
 *
 * `nowrap` 是调用方口径而非公共判据：`trade-cash-pairing` 的子行标签明确要求 nowrap（#733），
 * 产品列表次行只断单行/不溢出、不锁 `white-space`，故默认不查 CSS（#829 取舍）。
 * 返回测量值，B（不横向溢出）与 C（首列不窄于内容宽）由调用方继续断言。
 */
export async function expectSingleLine(
  locator: Locator,
  label: string,
  options: { nowrap?: boolean } = {}
): Promise<SingleLineMetrics> {
  await expect(locator).toBeVisible();
  if (options.nowrap) {
    await expect(locator).toHaveCSS('white-space', 'nowrap');
  }

  const box = await locator.boundingBox();
  if (!box) throw new Error(`${label} 没有可见盒`);

  const metrics = await locator.evaluate((el) => {
    const style = getComputedStyle(el);
    return {
      lineHeight: parseFloat(style.lineHeight),
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
    };
  });
  expect(metrics.lineHeight, `${label} line-height 应为正数`).toBeGreaterThan(0);
  expect(
    box.height,
    `${label} 应单行（高 ${box.height}px，行高 ${metrics.lineHeight}px）`
  ).toBeLessThanOrEqual(metrics.lineHeight * 1.5);

  return { height: box.height, ...metrics };
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
  await dlg.waitFor({ timeout: 10_000 });
  return { dlg, portfolioCode: code };
}

/**
 * 取认证头前**必须先落到应用同源页面**：`authHeaders` 经 `page.evaluate` 读
 * `window.localStorage`，而新开的 page 停在 `about:blank`（不透明源）会抛 SecurityError。
 * 仓库其余 spec 同样遵循「先导航 → 取头」的顺序（如 `gotoPortfolioDetail` + 可见性等待
 * 之后再 `authHeaders`）。
 */
export async function openAppAndAuth(page: Page): Promise<{ Authorization: string }> {
  await page.goto('/');
  await expect(page.locator('body')).toBeVisible();
  return authHeaders(page);
}

/**
 * 单年开市日取数单点（#906 ocr r6）：ok 守卫 + 行形状 cast + is_open/截止过滤
 * 集中一处，nearestTradingDay 与 prevOpenDays 共用，防日历 API 契约改动两路漂移。
 * inclusive=true 含截止日（<=），false 不含（<）。
 */
async function fetchYearOpenDays(
  page: Page,
  headers: { Authorization: string },
  year: number,
  cutoffISO: string,
  inclusive: boolean,
): Promise<string[]> {
  const resp = await page.request.get(`/api/trading-calendar?year=${year}`, { headers });
  expect(resp.ok(), `交易日历查询失败 ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const rows = (await resp.json()) as { calendar_date: string; is_open: boolean }[];
  return rows
    .filter((r) => r.is_open && (inclusive ? r.calendar_date <= cutoffISO : r.calendar_date < cutoffISO))
    .map((r) => r.calendar_date)
    .sort();
}

/** 取 cutoffISO 及之前最近一个开市日；跨年时回看上一年。 */
export async function latestTradingDayOnOrBefore(
  page: Page,
  headers: { Authorization: string },
  cutoffISO: string,
): Promise<string> {
  const year = Number(cutoffISO.slice(0, 4));
  for (const candidateYear of [year, year - 1]) {
    const open = await fetchYearOpenDays(page, headers, candidateYear, cutoffISO, true);
    if (open.length > 0) return open[open.length - 1];
  }
  throw new Error(`交易日历中找不到 ${cutoffISO} 及之前的开市日`);
}

/** 取「today 起最近一个交易日」（含 today）；当年尚无开市日则回看上一年末（#468 口径） */
export async function nearestTradingDay(
  page: Page,
  headers: { Authorization: string },
): Promise<string> {
  const today = toISODate(new Date());
  for (const year of [Number(today.slice(0, 4)), Number(today.slice(0, 4)) - 1]) {
    const open = await fetchYearOpenDays(page, headers, year, today, true);
    if (open.length > 0) return open[open.length - 1];
  }
  throw new Error(`交易日历中找不到 ${today} 起最近一个交易日`);
}

/** from 之后的第 n 个交易日（后端 /trading-calendar/next 为唯一事实来源） */
export async function nextTradingDays(
  page: Page,
  headers: { Authorization: string },
  from: string,
  n: number,
): Promise<string[]> {
  const out: string[] = [];
  let cursor = from;
  for (let i = 0; i < n; i++) {
    const resp = await page.request.get(
      `/api/trading-calendar/next?from_date=${cursor}&days=1`,
      { headers },
    );
    expect(resp.ok(), `取 ${cursor} 的下一交易日失败 ${resp.status()}`).toBeTruthy();
    cursor = ((await resp.json()) as { trading_day: string }).trading_day;
    out.push(cursor);
  }
  return out;
}

/**
 * 本 worker 独占的隔离组合 code（≤20 字符，`portfolio.code` 是 String(20)）。
 * `prefix` 是各 spec 的隔离契约（`E2E493` / `E2E725` / `E2E734`），必填、不给默认值；
 * 用例后缀两种用法都保留：经 `suffix` 入参，或调用点字符串追加。
 *
 * 必须同时区分 **project / worker / retry**，否则必然撞车：
 * - `chromium` 与 `mobile` 两个 project 都会跑同一 spec，而 `workerIndex`（与
 *   `parallelIndex`）都是**每个 project 内**从 0 重新计数 → 只用 worker 槽位会让
 *   mobile 复用 chromium 已建的同名组合，`POST /api/portfolios` 撞主键直接失败。
 * - CI 主 e2e job 带 `retries: 2`：失败重试若沿用同一 code，也会撞上上次残留的组合。
 *
 * 长度：prefix(6) + 日期(8) + project(1) + worker(1) + retry(1) = 17，加用例后缀 1 位 = 18。
 * ⚠️ 这个 18 只在 workerIndex/retry **均为个位数**时成立：两者按十进制原样插值，而本地
 * `workers` 默认取 CPU 数（CI 才钉 2），任一进两位就撑到 19（16 核本地即如此）、两位齐全
 * 就到 20 上限。**但腾位不是 §4 的决定性理由**——见下。
 *
 * **per-run nonce：#551 §4 评估后决定不加**，这是定案不是欠账。两条理由按严重度排：
 * ① 数据前提一破就会**响亮失败**：`POST /api/portfolios` 撞 `ALREADY_EXISTS` 400，第一次
 *   就红并指名道出撞的是哪个 code。#820 虽补了 `DELETE /api/portfolios/{code}`（扩围后放行
 *   「draft/closed 且无任何关联记录」的空壳），但 `trade-in-transit` 这类被激活、生成过
 *   快照的组合仍有依赖行、删不掉，加 nonce 只会把一次响亮失败换成同一后端生命周期内的
 *   静默残留堆积，更难发现。
 *   （`regression.spec.ts` 现在守的是 #820 扩围后的契约：draft/closed 详情**必须**有删除
 *   入口、active 必须没有；「有账不可删」由 `portfolio-delete.spec.ts` 断后端 409。）
 * ② 受益面只有本地手跑——CI 每个 job 新建栈（`.github/workflows/e2e-stack.yml`），本就
 *   不存在跨 run 复用；而本地正该走「重启后端重灌种子」这条既定菜谱（见
 *   `frontend/e2e/AGENTS.md` §1「E2E（Playwright）」的「净值夹具」条）。
 *
 * 若日后推翻本定案，腾位首选压日期段（`yyyyMMdd` → `yyMMdd` 省 2 位），**不要**删 project
 * 标记或 retry 段：那两段正是跨 project / 跨 retry 唯一性所系，删一个就重新引入上面要防的撞车。
 */
export function isolatedPortfolioCode(
  prefix: string,
  testInfo: { project: { name: string }; workerIndex: number; retry: number },
  suffix = '',
): string {
  const day = toISODate(new Date()).replace(/-/g, '');
  const proj = testInfo.project.name === 'mobile' ? 'M' : 'D';
  return `${prefix}${day}${proj}${testInfo.workerIndex}${testInfo.retry}${suffix}`;
}

// ===========================================================================
// 组合形态的 API 夹具（issue #820）
//
// 删除/关闭类用例需要「可控的组合形态」，而两个种子契约组合都不能拿来改形态
// （E2E_PORT 是零交易契约、且会被其他 spec 激活漂移 #636；E2E_ACTIVE 禁 recalculate/
// close 类破坏性操作）。这里单点持有「自建 → 激活 → 关闭」三步，供 ≥2 个 spec 共用，
// 不在各 spec 里复制同形代码（#478/#479 的提取口径）。
// ===========================================================================

async function apiPost(page: Page, headers: { Authorization: string }, path: string, data?: unknown) {
  const resp = await page.request.post(path, { headers, data });
  expect(resp, `POST ${path} 返回 ${resp.status()} ${await resp.text()}`).toBeOK();
  return resp.json();
}

/** 经 API 自建 draft 组合（code 由 isolatedPortfolioCode 保证 project/worker/retry 唯一） */
export async function createPortfolioViaApi(
  page: Page,
  headers: { Authorization: string },
  code: string,
  name = `E2E 夹具组合 ${code}`,
): Promise<void> {
  await apiPost(page, headers, '/api/portfolios', { code, name });
}

/**
 * 申购 + 确认共用单点（#906 ocr r4）：种子契约值 ADMIN/HBZQ/100000 单点持有，
 * 激活夹具与停更基线共用，防未来夹具改动在两路漂移。
 */
async function subscribeAndConfirmViaApi(
  page: Page,
  headers: { Authorization: string },
  code: string,
  applyDate: string,
): Promise<void> {
  const sub = (await apiPost(page, headers, '/api/subscriptions', {
    portfolio_code: code,
    investor_code: 'ADMIN',
    platform_code: 'HBZQ',
    sub_type: 'subscribe',
    amount: 100000,
    apply_date: applyDate,
  })) as { id: number };
  await apiPost(page, headers, `/api/subscriptions/${sub.id}/confirm`);
}

/**
 * 首购确认把 draft 激活为 active（零快照组合首窗净值 1.0000，无需行情，同
 * `trade-in-transit` 的现金注入形态）。
 *
 * #820 之后这步是「造 closed 夹具」的**必经前置**：close 收紧为仅 active，
 * 直接对 draft 调 close 会 422 `PORTFOLIO_NOT_ACTIVE`。
 * 申请日取「today 起最近一个交易日」，经日历端点锚定，不写死年份（#468）。
 */
export async function activatePortfolioViaApi(
  page: Page,
  headers: { Authorization: string },
  code: string,
): Promise<void> {
  const applyDate = await nearestTradingDay(page, headers);
  await subscribeAndConfirmViaApi(page, headers, code, applyDate);
}

/** 关闭组合（仅 active 可关，#820 收紧） */
export async function closePortfolioViaApi(
  page: Page,
  headers: { Authorization: string },
  code: string,
): Promise<void> {
  await apiPost(page, headers, `/api/portfolios/${code}/close`);
}

/**
 * today 之前（不含）最近 n 个 SSE 开市日，升序；跨年回看上一年（同 nearestTradingDay 口径，#468）。
 * #906 停更夹具与 E2E / visual-shot 共用单点，防造数漂移（ocr r3）。
 */
export async function prevOpenDays(
  page: Page,
  headers: { Authorization: string },
  n: number,
): Promise<string[]> {
  expect(Number.isInteger(n) && n > 0, `prevOpenDays 需要正整数 n，收到 ${n}`).toBeTruthy();
  const today = toISODate(new Date());
  const days: string[] = [];
  for (const year of [Number(today.slice(0, 4)), Number(today.slice(0, 4)) - 1]) {
    days.push(...await fetchYearOpenDays(page, headers, year, today, false));
    if (days.length >= n) break;
  }
  days.sort();
  expect(days.length, `交易日历不足 ${n} 个历史开市日`).toBeGreaterThanOrEqual(n);
  return days.slice(-n);
}

/**
 * 停更基线日期约定单点（#906 ocr r5）：最新快照停在「today 前第 4 个开市日」——
 * 申购申请日 = 前第 5 个开市日、确认日(T+1) = 前第 4 个 = latest。其后至今至少
 * 2 个开市日缺口（周末形态亦成立），trailing 缺口确定性非空。E2E 与 visual-shot
 * 共用，防「5/[0]/[1]」偏移约定在两路漂移。
 */
export async function staleBaselineDates(
  page: Page,
  headers: { Authorization: string },
): Promise<{ applyDate: string; latest: string }> {
  const prev5 = await prevOpenDays(page, headers, 5);
  return { applyDate: prev5[0], latest: prev5[1] };
}

/**
 * 停更基线造数（#906）：申购（apply）+ 确认（T+1=latest）+ generate latest 日快照。
 * 纯 CASH 持仓首窗净值 1.0000 无需行情；组合须已存在且 draft（调用方负责创建/复用）。
 * E2E 与 visual-shot 共用单点（ocr r3）。
 */
export async function seedStaleBaseline(
  page: Page,
  headers: { Authorization: string },
  code: string,
  applyDate: string,
  latest: string,
): Promise<void> {
  await subscribeAndConfirmViaApi(page, headers, code, applyDate);
  await apiPost(page, headers, '/api/snapshots/generate', {
    portfolio_code: code,
    target_date: latest,
  });
}

// ===========================================================================
// Dialog 单滚动容器契约（#910，延伸 #640 U-1）
//
// 业务 DialogContent 本体不得自带限高/overflow：整体内容滚动只归 ui/dialog.tsx
// 的唯一直接子内容 div（max-h-[min(100dvh-2rem,44rem)] overflow-y-auto）。
// 本 helper 供产品表单 / 资产分类 / 产品高级筛选三个业务 spec 复用：
// 先校验节点归属与外层契约，再（可选）滚内层到底验证底部动作可达。
// 必须在 scrollIntoViewIfNeeded / 提交点击等自动滚动动作**之前**调用——
// Chromium 的自动滚动会把外层缺陷救回、掩盖双滚动条（#640 第四轮口径）。
// ===========================================================================

/** 滚动测量结果；调用方可追加各自判据（如长列表 innerOverflow > 0） */
export type DialogScrollMetrics = {
  outerOverflow: number;
  innerOverflow: number;
};

/**
 * 断言 Dialog 只由内层容器负责整体滚动（#910 A1）。
 *
 * `dlg` 必须是 dialogByTitle 的返回（DialogContent 本体，role=dialog）。
 * 节点归属校验：当前结构下 DialogContent 的直接子 div 有且只有一个（内容容器；
 * Close 是 button）。Radix Popover 打开时会把 popper div 注入 DialogContent，
 * 故调用前须无浮层挂载（本函数先 settlePopovers）；Select 的浮层默认 Portal 到
 * body、外部 Sheet 不在本节点内，均不计数。不按 Tailwind 类名定位。
 *
 * 判据（几何允许 ≤1 CSS px 的边框/取整误差；不比较滚动条宽度、不强制常驻滚动条）：
 * - 外层 computed overflow-x/y 为 visible、max-height 为 none（不限高、不裁剪）；
 * - 外层 scrollHeight ≤ clientHeight + 1 且 scrollTop ≈ 0（不独立滚动）；
 * - 外层盒完整落在视口内（居中定位不被自身滚动/裁剪破坏）；
 * - 内层 computed overflow-y 为 auto（短内容 innerOverflow 可为 0，不算失败）。
 *
 * 传 `bottomAction` 时：断言完外层后把内层滚到底，验证该按钮可见且盒在视口内
 * （真实可达，而非仅 DOM 存在），并复查外层仍未滚动。
 */
export async function expectDialogSingleScroll(
  dlg: Locator,
  label: string,
  options: { bottomAction?: Locator } = {},
): Promise<DialogScrollMetrics> {
  await settlePopovers(dlg.page());
  await expect(dlg, `${label}: Dialog 应可见`).toBeVisible();

  const measure = (el: Element) => {
    const outer = el as HTMLElement;
    const divChildren = Array.from(outer.children).filter((c) => c.tagName === 'DIV');
    if (divChildren.length !== 1) {
      throw new Error(
        `DialogContent 直接子 div 数=${divChildren.length}，预期唯一内容容器（浮层未收干净或结构已变）`,
      );
    }
    const inner = divChildren[0] as HTMLElement;
    const os = getComputedStyle(outer);
    const is = getComputedStyle(inner);
    const rect = outer.getBoundingClientRect();
    return {
      outerOverflowY: os.overflowY,
      outerOverflowX: os.overflowX,
      outerMaxHeight: os.maxHeight,
      outerScrollTop: outer.scrollTop,
      outerOverflow: outer.scrollHeight - outer.clientHeight,
      innerOverflowY: is.overflowY,
      innerOverflow: inner.scrollHeight - inner.clientHeight,
      top: rect.top,
      bottom: rect.bottom,
      viewportH: window.innerHeight,
    };
  };

  const m = await dlg.evaluate(measure);
  expect(m.outerOverflowY, `${label}: 外层 overflow-y 应为 visible，实际 ${m.outerOverflowY}`).toBe('visible');
  expect(m.outerOverflowX, `${label}: 外层 overflow-x 应为 visible，实际 ${m.outerOverflowX}`).toBe('visible');
  expect(m.outerMaxHeight, `${label}: 外层不得限高，实际 max-height=${m.outerMaxHeight}`).toBe('none');
  expect(m.outerScrollTop, `${label}: 外层不得独立滚动（scrollTop=${m.outerScrollTop}）`).toBeLessThanOrEqual(1);
  expect(m.outerOverflow, `${label}: 外层可滚动距离 ${m.outerOverflow}px 应 ≤1（双滚动条缺陷）`).toBeLessThanOrEqual(1);
  expect(m.top, `${label}: 外层盒顶 ${m.top}px 应在视口内`).toBeGreaterThanOrEqual(-1);
  expect(m.bottom, `${label}: 外层盒底 ${m.bottom}px 应 ≤ 视口高 ${m.viewportH}px`).toBeLessThanOrEqual(m.viewportH + 1);
  expect(m.innerOverflowY, `${label}: 内层应承担滚动（overflow-y=${m.innerOverflowY}）`).toBe('auto');

  if (options.bottomAction) {
    await dlg.evaluate((outer) => {
      const inner = Array.from(outer.children).find((c) => c.tagName === 'DIV') as HTMLElement;
      inner.scrollTop = inner.scrollHeight;
    });
    await expect(options.bottomAction, `${label}: 底部动作应可见`).toBeVisible();
    const box = await options.bottomAction.boundingBox();
    if (!box) throw new Error(`${label}: 底部动作没有可见盒`);
    expect(box.y, `${label}: 底部动作 y=${box.y} 应在视口内`).toBeGreaterThanOrEqual(-1);
    expect(box.y + box.height, `${label}: 底部动作底边应 ≤ 视口高 ${m.viewportH}`).toBeLessThanOrEqual(m.viewportH + 1);
    const after = await dlg.evaluate(measure);
    expect(after.outerOverflow, `${label}: 内层滚动后外层仍不得溢出`).toBeLessThanOrEqual(1);
    expect(after.outerScrollTop, `${label}: 内层滚动后外层仍不得滚动`).toBeLessThanOrEqual(1);
  }

  return { outerOverflow: m.outerOverflow, innerOverflow: m.innerOverflow };
}

/**
 * #910 弹窗滚动回归的视口档：chromium 跑三档桌面（1280×720 / 1280×600 / 1440×900，
 * 前两档是 issue 已复现双滚动条的矮视口），mobile project 用自身原生视口
 * （iPhone 13 webkit 390×664），不改设备形态。调用方逐档 setViewportSize。
 */
export function dialogTestViewports(
  testInfo: TestInfo,
  page: Page,
): { width: number; height: number }[] {
  if (testInfo.project.name === 'mobile') {
    return [page.viewportSize() ?? { width: 390, height: 664 }];
  }
  return [
    { width: 1280, height: 720 },
    { width: 1280, height: 600 },
    { width: 1440, height: 900 },
  ];
}

/**
 * 清除交易列表的默认「近1年」日期区间（trade_date_end = today）。
 *
 * 在途 / 分页类 spec 的被测腿在**未来**交易日下单（D 是 ≤ today 的最近交易日 ⇒ D+1 起
 * 均晚于 today），默认区间会把这些行滤出列表；筛选状态只存于组件 state，
 * 每次 `gotoPortfolioSubpage` 重新挂载即复位，故每个 trades 页生命周期都要
 * 重新清一次（移动端先展开折叠筛选面板）。
 */
export async function clearTradeDateRange(page: Page, testInfo: TestInfo): Promise<void> {
  await openFilterPanelIfMobile(page, testInfo);
  await page.getByRole('button', { name: '清除日期区间' }).click();
  // 清完回列表再断言行集（modal sheet 开着时表格外 aria-hidden，#904 P1c-1）
  await closeFilterPanelIfMobile(page, testInfo);
}

// ===========================================================================
// 历史净值卡共享列宽与桌面降栏契约（#934）
//
// 三个入口（产品管理详情 / 组合内产品详情 / 平台内产品详情）共用 ProductNavCards：
// 表头与全部行经 subgrid 继承同一四列轨道，宽度不足时仅数据区横滚。
// 本节供三个 spec 复用：合成两页压力数据 + 文字级几何断言 + 桌面父布局量测。
// 量测口径（issue #934 验收断言）：相邻文字间距用文本节点 Range 矩形（不是单元格
// 盒子），期望 ≥8px、浮点容差 0.5px；列边界用单元格盒子，容差 1px。
// ===========================================================================

/** 合成历史净值压力样本：7 个交易日降序（page1 前 5 + page2 后 2），日期经交易日历动态取得 */
export type NavHistoryStressFixture = { dates: string[] };

/**
 * 仅拦截目标 nav-history GET 的 page-aware 合成响应（不直写数据库、不连生产）。
 * page1 含 0.4700 / 1.2143 / 累计净值有值与 null / 正负零缺失涨跌（首行后即现场同形
 * 样本「完整日期 + 0.4700 + --」）；page2 加入 12,345.6789 与 -100.00% 布局压力值。
 * 调用前须已落到应用同源页面（authHeaders 契约），注册后再 goto 目标详情页。
 */
export async function mockNavHistoryStress(page: Page): Promise<NavHistoryStressFixture> {
  const headers = await authHeaders(page);
  const dates = (await prevOpenDays(page, headers, 7)).reverse();
  const page1 = [
    { price_date: dates[0], unit_price: 1.2143, accumulated_nav: 1.3143, pct_change: -0.74 },
    { price_date: dates[1], unit_price: 0.47, accumulated_nav: null, pct_change: null },
    { price_date: dates[2], unit_price: 1.0, accumulated_nav: 1.0, pct_change: 0 },
    { price_date: dates[3], unit_price: 1.1, accumulated_nav: 1.21, pct_change: 1.25 },
    { price_date: dates[4], unit_price: 0.98, accumulated_nav: null, pct_change: -2 },
  ];
  const page2 = [
    { price_date: dates[5], unit_price: 12345.6789, accumulated_nav: 12345.6789, pct_change: -100 },
    { price_date: dates[6], unit_price: 12345.6789, accumulated_nav: null, pct_change: 100 },
  ];
  await page.route(/\/nav-history(\?|$)/, (route) => {
    const url = new URL(route.request().url());
    const p = Number(url.searchParams.get('page') ?? '1');
    return route.fulfill({
      status: 200,
      json: { items: p === 1 ? page1 : page2, total: dates.length, page: p, page_size: 5 },
    });
  });
  return { dates };
}

/** 历史卡几何量测结果；调用方可追加各自判据（如窄档 scrollOverflowX > 0） */
export type NavHistoryGeometry = {
  rowCount: number;
  minTextGap: number;
  scrollOverflowX: number;
  scrollOverflowY: number;
  pageOverflowX: number;
  dateTexts: string[];
};

/**
 * 断言历史卡列对齐 / 文字间距 / 完整单行（#934 A1）。
 * `history` 是历史卡 section 定位器，`prefix` 是 testid 前缀（product / platform-product）。
 * 判据：
 * - 表头与每行 4 格；同行相邻文字（Range 矩形）间距 ≥ 8px − 0.5px 容差；
 * - 各行与表头同列左右边界一致（单元格盒子，容差 1px）——subgrid 共享轨道的承重点；
 * - 每格单行（盒高 ≤ 1.5×行高）且不截断（scrollWidth ≤ clientWidth + 1）；
 * - 日期完整 YYYY-MM-DD 十字符；表头非 sticky；
 * - 数据区无卡内纵滚（scrollHeight ≤ clientHeight + 1）、整页无横向溢出（≤ 1px）。
 */
export async function expectNavHistoryGeometry(
  history: Locator,
  prefix: string,
  label: string,
): Promise<NavHistoryGeometry> {
  await expect(history, `${label}: 历史卡应可见`).toBeVisible();
  const m = await history.evaluate((card, p) => {
    const scroll = card.querySelector(`[data-testid="${p}-history-scroll"]`) as HTMLElement | null;
    const header = card.querySelector(`[data-testid="${p}-history-header"]`);
    const rows = Array.from(card.querySelectorAll(`[data-testid="${p}-history-row"]`));
    if (!scroll || !header || rows.length === 0) {
      throw new Error(
        `历史卡结构缺失（scroll=${!!scroll} header=${!!header} rows=${rows.length}），无法量测`,
      );
    }
    const readLine = (row: Element) =>
      Array.from(row.children).map((cell) => {
        const box = cell.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(cell);
        const text = range.getBoundingClientRect();
        return {
          left: box.left,
          right: box.right,
          height: box.height,
          lineHeight: parseFloat(getComputedStyle(cell).lineHeight),
          scrollWidth: cell.scrollWidth,
          clientWidth: cell.clientWidth,
          textLeft: text.left,
          textRight: text.right,
        };
      });
    const lines = [readLine(header), ...rows.map(readLine)];
    let minTextGap = Infinity;
    for (const line of lines) {
      for (let i = 0; i + 1 < line.length; i++) {
        minTextGap = Math.min(minTextGap, line[i + 1].textLeft - line[i].textRight);
      }
    }
    return {
      lines,
      minTextGap,
      headerPosition: getComputedStyle(header).position,
      scrollOverflowX: scroll.scrollWidth - scroll.clientWidth,
      scrollOverflowY: scroll.scrollHeight - scroll.clientHeight,
      pageOverflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      dateTexts: rows.map((r) => (r.children[0]?.textContent ?? '').trim()),
    };
  }, prefix);

  const headerLine = m.lines[0];
  for (const [li, line] of m.lines.entries()) {
    const where = li === 0 ? '表头' : `第${li}行`;
    expect(line.length, `${label}: ${where}应为 4 格`).toBe(4);
    for (const [ci, cell] of line.entries()) {
      expect(
        cell.height,
        `${label}: ${where}第${ci + 1}格应单行（高 ${cell.height}px，行高 ${cell.lineHeight}px）`,
      ).toBeLessThanOrEqual(cell.lineHeight * 1.5);
      expect(
        cell.scrollWidth,
        `${label}: ${where}第${ci + 1}格文字不得截断（scrollWidth=${cell.scrollWidth} clientWidth=${cell.clientWidth}）`,
      ).toBeLessThanOrEqual(cell.clientWidth + 1);
      if (li > 0) {
        expect(
          Math.abs(cell.left - headerLine[ci].left),
          `${label}: ${where}第${ci + 1}列左边界应与表头一致`,
        ).toBeLessThanOrEqual(1);
        expect(
          Math.abs(cell.right - headerLine[ci].right),
          `${label}: ${where}第${ci + 1}列右边界应与表头一致`,
        ).toBeLessThanOrEqual(1);
      }
    }
  }
  expect(
    m.minTextGap,
    `${label}: 相邻文字最小间距 ${m.minTextGap}px 应 ≥8px（容差 0.5px，负值即叠印）`,
  ).toBeGreaterThanOrEqual(7.5);
  for (const d of m.dateTexts) {
    expect(d, `${label}: 日期应完整 YYYY-MM-DD，实际「${d}」`).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  }
  expect(m.headerPosition, `${label}: 表头不得 sticky`).not.toBe('sticky');
  expect(m.scrollOverflowY, `${label}: 数据区不得出现卡内纵滚`).toBeLessThanOrEqual(1);
  expect(m.pageOverflowX, `${label}: 整页横向溢出 ${m.pageOverflowX}px 应 ≤1px`).toBeLessThanOrEqual(1);

  return {
    rowCount: m.dateTexts.length,
    minTextGap: m.minTextGap,
    scrollOverflowX: m.scrollOverflowX,
    scrollOverflowY: m.scrollOverflowY,
    pageOverflowX: m.pageOverflowX,
    dateTexts: m.dateTexts,
  };
}

/**
 * 断言宽度不足时仅历史数据区横滚且首/末列完整可达（#934 A5）。
 * 调用前须已构造出溢出形态（窄视口 + 长值行）。判据：
 * - 数据区 scrollWidth > clientWidth，整页不横向溢出；
 * - 滚到最右：表头与全部行的末列文字右缘 ≤ 滚动视口右缘；滚回 0：首列日期左缘 ≥ 左缘；
 * - 横滚后列边界/文字间距契约不变（表头与行同步横移）；
 * - 卡片标题不随数据横移；
 * - keyboard=true 时聚焦数据区按方向键可横滚（移动端无物理键盘，属合法端专属不传）。
 * 触屏横滑由 overflow-x:auto 原生行为承接，自动化以 scrollLeft 验证内容可达，
 * 不合成原生 touch 手势（WebKit 无 CDP，合成 TouchEvent 不驱动原生滚动）。
 */
export async function expectNavHistoryHScroll(
  history: Locator,
  prefix: string,
  label: string,
  options: { keyboard?: boolean } = {},
): Promise<void> {
  const scroll = history.getByTestId(`${prefix}-history-scroll`);
  await expect(scroll, `${label}: 数据区应可见`).toBeVisible();
  const overflow = await scroll.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(overflow, `${label}: 窄档长值应使数据区横滚（溢出 ${overflow}px）`).toBeGreaterThan(1);

  const title = history.locator('h3');
  const titleBox = await title.boundingBox();
  if (!titleBox) throw new Error(`${label}: 标题没有可见盒`);

  const end = await scroll.evaluate(
    (el, arg) => {
      el.scrollLeft = el.scrollWidth;
      const rect = el.getBoundingClientRect();
      const lines = [
        el.querySelector(`[data-testid="${arg.p}-history-header"]`),
        ...Array.from(el.querySelectorAll(`[data-testid="${arg.p}-history-row"]`)),
      ].filter((row): row is Element => row !== null);
      const texts = lines.map((row) => {
        const cells = row.children;
        const cell = cells[cells.length - 1];
        const range = document.createRange();
        range.selectNodeContents(cell);
        return range.getBoundingClientRect().right;
      });
      return {
        scrollLeft: el.scrollLeft,
        maxScrollLeft: el.scrollWidth - el.clientWidth,
        viewportRight: rect.right,
        texts,
      };
    },
    { p: prefix },
  );
  expect(end.scrollLeft, `${label}: 应可滚到最右`).toBeGreaterThanOrEqual(end.maxScrollLeft - 1);
  for (const right of end.texts) {
    expect(right, `${label}: 最右时末列文字应完整可读`).toBeLessThanOrEqual(end.viewportRight + 0.5);
  }

  const start = await scroll.evaluate(
    (el, arg) => {
      el.scrollLeft = 0;
      const rect = el.getBoundingClientRect();
      const lines = [
        el.querySelector(`[data-testid="${arg.p}-history-header"]`),
        ...Array.from(el.querySelectorAll(`[data-testid="${arg.p}-history-row"]`)),
      ].filter((row): row is Element => row !== null);
      const texts = lines.map((row) => {
        const range = document.createRange();
        range.selectNodeContents(row.children[0]);
        return range.getBoundingClientRect().left;
      });
      return { viewportLeft: rect.left, texts };
    },
    { p: prefix },
  );
  for (const left of start.texts) {
    expect(left, `${label}: 滚回最左时首列日期应完整可读`).toBeGreaterThanOrEqual(start.viewportLeft - 0.5);
  }

  // 横滚后共享轨道契约不变（表头与行同步横移，不各自漂移）
  await expectNavHistoryGeometry(history, prefix, `${label} 横滚后`);

  const titleBoxAfter = await title.boundingBox();
  if (!titleBoxAfter) throw new Error(`${label}: 横滚后标题没有可见盒`);
  expect(
    Math.abs(titleBoxAfter.x - titleBox.x),
    `${label}: 标题不得随数据横移`,
  ).toBeLessThanOrEqual(1);

  if (options.keyboard) {
    // 聚焦滚动容器后方向键横滚（headless Chromium 对聚焦 div 的 End/PageDown 落在
    // document 上、不滚容器，ArrowRight 才是容器级键盘滚动，探针实测 40px/次）。
    // 键盘滚动是平滑动画：等 scrollLeft 两次采样一致（动画结束）再断言与归零，
    // 否则立即读会读到动画中间值、归零也会被动画余量追滚。
    await scroll.focus();
    await scroll.page().keyboard.press('ArrowRight');
    await expect
      .poll(
        async () => {
          const first = await scroll.evaluate((el) => el.scrollLeft);
          if (first <= 1) return false;
          await scroll.page().waitForTimeout(200);
          const second = await scroll.evaluate((el) => el.scrollLeft);
          return first === second;
        },
        { message: `${label}: 键盘方向键应能横滚数据区并停稳`, timeout: 5_000 },
      )
      .toBe(true);
    await scroll.evaluate((el) => {
      el.scrollLeft = 0;
    });
  }
}

/** 桌面父布局量测结果（#934 S4/A2） */
export type NavDetailLayout = {
  containerWidth: number;
  tracks: number[];
  columnGap: number;
};

/**
 * 量测历史卡所在桌面双栏栅格：历史卡 section → 左栏 div → 栅格 → @container 父层。
 * 结构归属校验：栅格必须是 grid、恰两个组、左组在前且 min-w-0，父层必须是
 * inline-size 容器（断点看内容容器不看 viewport）。不按 Tailwind 类名定位。
 */
export async function measureNavDetailLayout(history: Locator, label: string): Promise<NavDetailLayout> {
  const m = await history.evaluate((card) => {
    const left = card.parentElement;
    const grid = left?.parentElement;
    const wrapper = grid?.parentElement;
    if (!left || !grid || !wrapper || !grid.contains(card)) {
      throw new Error('历史卡不在预期的 左栏→栅格→容器 层级内');
    }
    const gs = getComputedStyle(grid);
    if (gs.display !== 'grid') {
      throw new Error(`父布局应为 grid，实际 display=${gs.display}`);
    }
    const leftRect = left.getBoundingClientRect();
    const rightRect = grid.children[1]?.getBoundingClientRect();
    return {
      containerWidth: wrapper.clientWidth,
      containerType: getComputedStyle(wrapper).containerType,
      tracks: gs.gridTemplateColumns.split(' ').map((v) => parseFloat(v)),
      columnGap: parseFloat(gs.columnGap),
      groupCount: grid.children.length,
      leftFirst: grid.children[0] === left,
      leftMinWidth: getComputedStyle(left).minWidth,
      leftBottom: leftRect.bottom,
      rightTop: rightRect ? rightRect.top : null,
      leftTop: leftRect.top,
    };
  });
  expect(m.containerType, `${label}: 父层应为 inline-size 容器`).toBe('inline-size');
  expect(m.groupCount, `${label}: 栅格应恰有左右两组`).toBe(2);
  expect(m.leftFirst, `${label}: 左组（净值卡）应在 DOM 前位`).toBeTruthy();
  expect(m.leftMinWidth, `${label}: 左栏应 min-w-0`).toBe('0px');
  if (m.tracks.length === 1 && m.rightTop !== null) {
    expect(m.rightTop, `${label}: 单栏时右组应排在左组下方`).toBeGreaterThanOrEqual(m.leftBottom - 1);
  }
  if (m.tracks.length === 2 && m.rightTop !== null) {
    expect(Math.abs(m.rightTop - m.leftTop), `${label}: 双栏时左右组应同顶`).toBeLessThanOrEqual(1);
  }
  return { containerWidth: m.containerWidth, tracks: m.tracks, columnGap: m.columnGap };
}

/** 按容器宽断言栏数（#934 S4：≥800px 双栏 360px 右栏 + 16px 栏距，否则单栏） */
export function expectNavDetailColumns(m: NavDetailLayout, label: string): void {
  if (m.containerWidth >= 800) {
    expect(m.tracks.length, `${label}: 容器 ${m.containerWidth}px ≥800 应双栏`).toBe(2);
    expect(Math.abs(m.tracks[1] - 360), `${label}: 右栏应 360px，实际 ${m.tracks[1]}px`).toBeLessThanOrEqual(1);
    expect(m.columnGap, `${label}: 栏间距应 16px`).toBe(16);
    expect(
      Math.abs(m.tracks[0] - (m.containerWidth - 360 - 16)),
      `${label}: 左栏应占剩余宽`,
    ).toBeLessThanOrEqual(1);
  } else {
    expect(m.tracks.length, `${label}: 容器 ${m.containerWidth}px <800 应单栏`).toBe(1);
    expect(Math.abs(m.tracks[0] - m.containerWidth), `${label}: 单栏应铺满容器`).toBeLessThanOrEqual(1);
  }
}

/** #935 页标题 h1 字号单点（展开/折叠共用，防两处字面量漂移；ocr r2） */
async function expectPageTitleTypography(h1: Locator): Promise<void> {
  await expect(h1).toHaveCSS('font-size', '20px');
  await expect(h1).toHaveCSS('font-weight', '600');
  await expect(h1).toHaveCSS('line-height', '28px');
}

/** #935 次要文字 14/22 单点（身份说明/平台上下文/面包屑共用；ocr r3） */
async function expectSecondaryTypography(locator: Locator): Promise<void> {
  await expect(locator).toHaveCSS('font-size', '14px');
  await expect(locator).toHaveCSS('line-height', '22px');
}

/**
 * #935 A1/A4：三类产品详情的短页眉 + 正文全文身份区（单点断言，防三页漂移）。
 * 页标题 h1「产品详情」20/600/28；全名 h2 16/600/24；身份说明 14/22；
 * 平台上下文（仅平台-产品页）独立一行。展开态面包屑导航 14/22 一并钉住。
 */
export async function expectProductDetailIdentity(
  page: Page,
  opts: { name: string; meta: string; platformContext?: string },
): Promise<void> {
  // 展开态前置：scrollY>32 会折叠面包屑，先响亮报前置而非字号错（ocr r2）
  const scrollY = await page.evaluate(() => window.scrollY);
  expect(
    scrollY,
    `展开态身份断言前置：scrollY=${scrollY} 应 ≤ 32（请先 scrollWindowTo(page, 0)）`,
  ).toBeLessThanOrEqual(32);
  const h1 = page.getByRole('heading', { level: 1, name: '产品详情' });
  await expect(h1).toBeVisible();
  await expectPageTitleTypography(h1);

  const identity = page.getByTestId('product-identity');
  await expect(identity).toBeVisible();
  const h2 = identity.getByTestId('product-full-name');
  // 语义层级一并钉住：同 testid 渲染成 p/div 不得静默通过（ocr r2）
  await expect(h2).toHaveJSProperty('tagName', 'H2');
  await expect(h2).toHaveText(opts.name);
  await expect(h2).toHaveCSS('font-size', '16px');
  await expect(h2).toHaveCSS('font-weight', '600');
  await expect(h2).toHaveCSS('line-height', '24px');
  const meta = identity.getByTestId('product-identity-meta');
  await expect(meta).toHaveText(opts.meta);
  await expectSecondaryTypography(meta);
  const platform = identity.getByTestId('product-platform-context');
  if (opts.platformContext) {
    await expect(platform).toHaveText(opts.platformContext);
    await expectSecondaryTypography(platform);
  } else {
    await expect(platform).toHaveCount(0);
  }

  // 展开态面包屑/返回文字 14/22（#935 S1 关键导航档）
  const crumb = page.getByTestId('page-header').getByRole('navigation', { name: '面包屑' }).getByRole('link').first();
  await expect(crumb).toBeVisible();
  await expectSecondaryTypography(crumb);
}

/** #935 A1/A7 全名可读性量测：非裁剪样式 + 文本几何（末尾行盒在容器内）+ 整页无横溢 */
export interface FullNameMetrics {
  lines: number;
  lineHeight: number;
  scrollWidth: number;
  clientWidth: number;
  lastRight: number;
  boxRight: number;
  docScrollWidth: number;
  docClientWidth: number;
}

export async function expectFullNameReadable(page: Page, label: string): Promise<FullNameMetrics> {
  const h2 = page.getByTestId('product-full-name');
  await expect(h2).toBeVisible();
  // 非裁剪样式：不允许 ellipsis/clamp/hidden 掩盖截字（A1/A7）
  await expect(h2).toHaveCSS('text-overflow', 'clip');
  await expect(h2).toHaveCSS('white-space', 'normal');
  await expect(h2).toHaveCSS('overflow', 'visible');
  await expect(h2).toHaveCSS('-webkit-line-clamp', 'none');

  const m = await h2.evaluate((el) => {
    const cs = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(el);
    const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0 && r.height > 0);
    const last = rects[rects.length - 1];
    const lineHeight = parseFloat(cs.lineHeight);
    return {
      lines: Math.round(box.height / lineHeight),
      lineHeight,
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
      // 无可见行盒时返回 NaN 让下游比较响亮失败，不得回退 0 空断言（ocr r1）
      lastRight: last ? last.right : Number.NaN,
      boxRight: box.right,
      docScrollWidth: document.documentElement.scrollWidth,
      // 与 scrollWidth 同坐标空间（根 zoom 下 innerWidth 是视觉视口、会空断言，ocr r1）
      docClientWidth: document.documentElement.clientWidth,
    };
  });
  expect(m.lineHeight, `${label}: 行高应为正`).toBeGreaterThan(0);
  expect(
    m.scrollWidth - m.clientWidth,
    `${label}: 全名 scrollWidth=${m.scrollWidth} 应 ≤ clientWidth=${m.clientWidth}（无横向裁剪）`,
  ).toBeLessThanOrEqual(1);
  expect(
    m.lastRight,
    `${label}: 末尾文本行盒右缘 ${m.lastRight}px 应 ≤ 容器右缘 ${m.boxRight}px（末尾完整可读）`,
  ).toBeLessThanOrEqual(m.boxRight + 0.5);
  expect(
    m.docScrollWidth,
    `${label}: 整页 scrollWidth=${m.docScrollWidth} 应 ≤ 布局视口 clientWidth=${m.docClientWidth}（无横向溢出，1px 取整容差）`,
  ).toBeLessThanOrEqual(m.docClientWidth + 1);
  return m;
}

/**
 * #935 A5：三类产品详情折叠页眉只含返回 + 短标题「产品详情」（S3 已确认取舍）。
 * 断言限定页眉容器：名称/代码/badge/管理动作不得在页眉内（正文中仍可读，由调用方另断）。
 */
export async function expectProductHeaderCollapsed(
  page: Page,
  opts: { backAria: string; absentTexts: string[] },
): Promise<void> {
  const header = page.getByTestId('page-header');
  const h1 = header.getByRole('heading', { level: 1 });
  await expect(h1).toHaveText('产品详情');
  await expectPageTitleTypography(h1);
  // 折叠态：面包屑隐去，首 crumb 收成行内图标返回（aria 不变）
  await expect(header.getByRole('navigation', { name: '面包屑' })).toHaveCount(0);
  const links = header.getByRole('link');
  await expect(links).toHaveCount(1);
  await expect(links.first()).toHaveAttribute('aria-label', opts.backAria);
  for (const text of opts.absentTexts) {
    await expect(header, `折叠页眉不应含「${text}」`).not.toContainText(text);
  }
  await expect(header.getByTestId('product-inactive-badge')).toHaveCount(0);
  await expect(header.getByTestId('product-manage-actions')).toHaveCount(0);
}

/** #935 A5：滚动到指定 scrollY 并等待到位（阈值两侧/回顶断言用）。
 *  限制（ocr r3）：maxY 按展开态布局量测；滚动触发页眉折叠后 scrollHeight 会缩小，
 *  贴近页底的目标可能被夹取而轮询超时——现有调用点（0/32/33）不受影响，
 *  新增页底目标调用方须自行预留折叠收缩余量。 */
export async function scrollWindowTo(page: Page, y: number): Promise<void> {
  // clientHeight 与 scrollHeight 同坐标空间（innerHeight 在移动仿真/根 zoom 下是视觉视口；ocr r2）
  const maxY = await page.evaluate(
    () => document.documentElement.scrollHeight - document.documentElement.clientHeight
  );
  expect(
    maxY,
    `页面最大可滚 ${maxY}px，不足以滚到目标 ${y}px（请先加长内容或降低目标）`
  ).toBeGreaterThanOrEqual(y);
  await page.evaluate((top) => window.scrollTo(0, top), y);
  // 精确相等：±1px 容差会在 32/33 边界目标跨越 >32 折叠阈值造成假失败（ocr r3），
  // 且 instant scrollTo 整数落定在 Chromium/WebKit 多轮实测均稳定；分数落定如出现再按实证处理
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(y);
}
