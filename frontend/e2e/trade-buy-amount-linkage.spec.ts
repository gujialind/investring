/**
 * 前端 E2E 测试：买入金额双字段联动（#193）
 *
 * 守护调仓交易表单「实际支付金额（含费，元）」/「净投入金额（扣费后，元）」
 * 双向联动契约，消除交割单录入口径错位（双重扣费陷阱）：
 *   - 正向联动：实付 + 手续费 → 净投入 = 实付 − 手续费；
 *   - 反向联动：净投入 + 手续费 → 实付 = 净投入 + 手续费；
 *   - 锚点跟随：手续费变化时按最后手改字段重算另一字段；
 *   - 手续费空/0 → 两字段相等；
 *   - 净额 ≤ 0 → 错误提示 + 提交禁用；
 *   - 提交契约：请求体仅含一个金额值且等于实付（含费）字段值；
 *   - 落库口径：创建后列表「金额」列显示净额；编辑 Dialog 预填双维度、差恰为手续费；
 *   - 卖出方向金额为纯派生量（#190）：只读展示毛额/实际到账，未填价格不展示。
 *
 * 数据说明：组合经 helpers 按 code 直达种子 draft 组合 E2E_PORT（#354），缺组合
 * 即硬失败、不 skip；平台/产品数据缺失时仍优雅 skip。本 spec 结构性绑定 E2E_PORT
 * 而非 E2E_ACTIVE：落库用例（用例 7）依赖零快照组合的 1.0000 首购窗口经 API
 * 「申购+确认」注入现金，在已有快照的 E2E_ACTIVE 上会 NAV_NOT_AVAILABLE。
 * 种子零现金而买入创建按扣款平台校验可用现金，落库用例先经 API
 * 「申购+确认」向所选平台注入现金（申购确认日 T+1，申请日取交易日前一工作日，
 * 种子日历工作日即交易日；首购按净值 1.0 确认，残留申购不清理、重复注入无害）。
 * 落库用例真实创建一笔买入并在用例内删除清理；创建前另经 API 清除同自然键
 * 残留（上轮中途失败或 CI 重试的遗留），防双 project 时间交叠/重试撞
 * DUPLICATE_TRADE。提交契约用例经 page.route 拦截 + abort，
 * 不落任何数据。双端复跑：TradesContent 为共享组件（「提交交易」Dialog 双端
 * 同构），mobile project 自动覆盖，无需单独用例。
 */
import { test, expect, type Page, type Locator } from '@playwright/test';
import {
  E2E_PORT,
  authHeaders,
  collectPageErrors,
  dialogByTitle,
  openSubmitTradeDialog,
  pickFirstPlatformOption,
  pickFirstProduct,
  platformPopover,
} from './helpers';

/** 选中首个交易平台（无平台数据优雅 skip），返回所选平台 code。
 *  注意：提交交易 Dialog 内有两个 SearchablePlatformSelect（交易平台 + 现金平台），
 *  触发按钮均挂 data-testid="platform-trigger"，须按 id 消歧 */
async function pickFirstPlatform(page: Page, dlg: Locator): Promise<string> {
  await dlg.locator('button#platform_code').click();
  return pickFirstPlatformOption(platformPopover(page));
}

/** 买入金额两输入框 */
const actualInput = (dlg: Locator) => dlg.getByLabel('实际支付金额（含费，元）');
const netInput = (dlg: Locator) => dlg.getByLabel('净投入金额（扣费后，元）');
const feeInput = (dlg: Locator) => dlg.getByLabel('手续费（元）');

/** 本地日期 → ISO 字符串（避免 toISOString 的 UTC 时移） */
function toISODate(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

/**
 * 清除同自然键的残留 pending 买入（组合/产品/市场/平台/方向/交易日）。
 * 用例中途失败或 CI 重试（retries: 2）会残留上一轮创建的交易，不清除则本轮
 * 创建撞 DUPLICATE_TRADE（fullyParallel 双 project 时间交叠同样命中）。
 */
async function purgePendingSameBuys(
  page: Page,
  headers: { Authorization: string },
  portfolioCode: string,
  productCode: string,
  market: string,
  platformCode: string,
  tradeDateISO: string,
): Promise<void> {
  const qs = new URLSearchParams({
    portfolio_code: portfolioCode,
    status: 'pending',
    trade_type: 'buy',
    product_code: productCode,
    market,
    platform_code: platformCode,
    trade_date_start: tradeDateISO,
    trade_date_end: tradeDateISO,
    page_size: '50',
  });
  const listResp = await page.request.get(`/api/trades?${qs.toString()}`, { headers });
  expect(listResp.ok(), `残留清理：查询失败 ${listResp.status()}`).toBeTruthy();
  const { items } = (await listResp.json()) as { items: { id: number }[] };
  for (const t of items) {
    const delResp = await page.request.delete(`/api/trades/${t.id}`, { headers });
    expect(delResp.ok(), `残留清理：删除 #${t.id} 失败 ${delResp.status()}`).toBeTruthy();
  }
}

/**
 * 经 API「申购+确认」给指定平台注入可用现金（种子零现金，买入创建按扣款平台
 * 校验可用现金）。申购确认日为申请日下一交易日（T+1），故申请日取交易日前一
 * 工作日，使 CASH 腿 confirm_date 恰落交易日当天计入可用现金；种子日历工作日
 * 即交易日，倒推跳周末即可。残留申购不清理：E2E 库每次运行重建，双 project
 * 重复注入只是现金叠加，无防重约束冲突。
 */
async function injectCashViaSubscription(
  page: Page,
  headers: { Authorization: string },
  portfolioCode: string,
  platformCode: string,
  tradeDateISO: string,
): Promise<void> {
  const apply = new Date(`${tradeDateISO}T00:00:00`);
  do {
    apply.setDate(apply.getDate() - 1);
  } while (apply.getDay() === 0 || apply.getDay() === 6);

  const createResp = await page.request.post('/api/subscriptions', {
    data: {
      portfolio_code: portfolioCode,
      investor_code: 'ADMIN',
      platform_code: platformCode,
      sub_type: 'subscribe',
      amount: 20000,
      apply_date: toISODate(apply),
    },
    headers,
  });
  expect(
    createResp.ok(),
    `注入现金：创建申购失败 ${createResp.status()} ${await createResp.text()}`
  ).toBeTruthy();
  const { id } = (await createResp.json()) as { id: number };
  const confirmResp = await page.request.post(`/api/subscriptions/${id}/confirm`, { headers });
  expect(
    confirmResp.ok(),
    `注入现金：确认申购失败 ${confirmResp.status()} ${await confirmResp.text()}`
  ).toBeTruthy();
}

test.describe('买入金额双字段联动（#193）', () => {
  // ---- 用例 1：正向联动——实付 1000 + fee 5 → 净投入 995.00 ----
  test('填实付与手续费后净投入自动联动', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);

    await actualInput(dlg).fill('1000');
    // fee 未填时净额 = 实付（缺省 0 手续费）
    await expect(netInput(dlg)).toHaveValue('1000.00');
    await feeInput(dlg).fill('5');
    await expect(netInput(dlg)).toHaveValue('995.00');

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 2：反向联动——净投入 995 + fee 5 → 实付 1000.00 ----
  test('填净投入与手续费后实付自动联动', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);

    await netInput(dlg).fill('995');
    await expect(actualInput(dlg)).toHaveValue('995.00');
    await feeInput(dlg).fill('5');
    await expect(actualInput(dlg)).toHaveValue('1000.00');
    await expect(netInput(dlg)).toHaveValue('995');

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 3：锚点跟随——改手续费按最后手改字段重算另一字段 ----
  test('手续费变化按锚点字段重算（锚在净投入/实付两种）', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);

    // 锚在净投入：净额不变、实付重算
    await netInput(dlg).fill('995');
    await feeInput(dlg).fill('10');
    await expect(netInput(dlg)).toHaveValue('995');
    await expect(actualInput(dlg)).toHaveValue('1005.00');

    // 锚切实付：实付不变、净额重算
    await actualInput(dlg).fill('1000');
    await expect(netInput(dlg)).toHaveValue('990.00');
    await feeInput(dlg).fill('20');
    await expect(actualInput(dlg)).toHaveValue('1000');
    await expect(netInput(dlg)).toHaveValue('980.00');

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 4：手续费空或 0 → 两字段相等 ----
  test('手续费为 0 时两字段数值相等', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);

    await actualInput(dlg).fill('1000');
    await feeInput(dlg).fill('0');
    await expect(netInput(dlg)).toHaveValue('1000.00');

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 5：净额 ≤ 0 → 错误提示 + 提交禁用 ----
  test('手续费不小于实付时提示错误且无法提交', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);
    await pickFirstProduct(page, dlg);
    await pickFirstPlatform(page, dlg);

    await actualInput(dlg).fill('3');
    await feeInput(dlg).fill('5');
    await expect(netInput(dlg)).toHaveValue('-2.00');

    await expect(dlg.getByRole('alert').filter({ hasText: '净投入金额需大于 0' })).toBeVisible();
    await expect(dlg.getByRole('button', { name: '提交交易' })).toBeDisabled();

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 6：提交契约——请求体仅含费值一个金额（route 拦截不落数据）----
  test('提交请求体金额等于实付字段值且无冗余金额字段', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);
    await pickFirstProduct(page, dlg);
    await pickFirstPlatform(page, dlg);

    await actualInput(dlg).fill('1000');
    await feeInput(dlg).fill('5');
    await expect(netInput(dlg)).toHaveValue('995.00');

    let postBody: string | null = null;
    await page.route(/\/api\/trades(\?|$)/, async (route) => {
      const req = route.request();
      if (req.method() === 'POST') {
        postBody = req.postData();
        await route.abort();
        return;
      }
      await route.continue();
    });
    await dlg.getByRole('button', { name: '提交交易' }).click();
    await expect.poll(() => postBody, { timeout: 10_000 }).not.toBeNull();

    const body = JSON.parse(postBody!) as Record<string, unknown>;
    expect(body.trade_type).toBe('buy');
    expect(body.amount).toBe(1000);
    expect(body.fee).toBe(5);
    // 净投入仅前端展示维度，不进请求体；含费值以 amount 单字段传达
    expect('net_amount' in body).toBe(false);
    expect('actual_amount' in body).toBe(false);

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 7：落库口径 + 编辑预填——列表金额为净额，编辑双维度差恰为手续费 ----
  test('创建买入后列表显示净额，编辑弹窗预填双维度', async ({ page }) => {
    const errors = collectPageErrors(page);
    const now = new Date();
    if (now.getDay() === 0 || now.getDay() === 6) {
      test.skip(true, '今天非交易日（周末），表单默认交易日期会被后端拒绝');
    }
    const tradeDate = toISODate(now);
    const { dlg, portfolioCode } = await openSubmitTradeDialog(page, E2E_PORT);
    const product = await pickFirstProduct(page, dlg);
    const platformCode = await pickFirstPlatform(page, dlg);
    const headers = await authHeaders(page);
    await purgePendingSameBuys(page, headers, portfolioCode, product.code, product.market, platformCode, tradeDate);
    await injectCashViaSubscription(page, headers, portfolioCode, platformCode, tradeDate);

    await actualInput(dlg).fill('1234.56');
    await feeInput(dlg).fill('5');
    await expect(netInput(dlg)).toHaveValue('1229.56');
    await dlg.getByRole('button', { name: '提交交易' }).click();
    // 创建成功 Dialog 关闭（重复交易等异常路径会保留 Dialog）
    await expect(dlg).toBeHidden({ timeout: 15_000 });

    // 列表「金额」列为净额（#173 口径）：1234.56 − 5 = 1229.56
    const row = page.getByRole('row').filter({ hasText: '¥1,229.56' }).first();
    await row.waitFor({ timeout: 15_000 });

    // 编辑预填双维度：实付 = actual_amount、净投入 = amount，差恰为手续费
    await row.locator('button[title="编辑"]').click();
    const editDlg = dialogByTitle(page, '编辑交易');
    await editDlg.waitFor();
    await expect(editDlg.getByLabel('实际支付金额（含费，元）')).toHaveValue('1234.56');
    await expect(editDlg.getByLabel('净投入金额（扣费后，元）')).toHaveValue('1229.56');
    await page.keyboard.press('Escape');
    await expect(editDlg).toBeHidden();

    // 清理：删除该交易（防双 project 重复运行撞 DUPLICATE_TRADE）
    await row.locator('button[title="删除"]').click();
    const deleteDlg = page.getByRole('alertdialog').filter({ hasText: '删除交易' });
    await deleteDlg.waitFor();
    await deleteDlg.getByRole('button', { name: '确认' }).click();
    await expect(row).toBeHidden({ timeout: 15_000 });

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  // ---- 用例 8：卖出方向金额为纯派生量——只读展示毛额/到手，无价格不展示 ----
  test('卖出表单只读展示毛额与实际到账，未填价格不展示', async ({ page }) => {
    const errors = collectPageErrors(page);
    const { dlg } = await openSubmitTradeDialog(page, E2E_PORT);

    await dlg.getByRole('button', { name: '卖出' }).click();
    await dlg.getByLabel('份额').fill('100');
    await dlg.getByLabel('价格').fill('10');
    await feeInput(dlg).fill('5');

    await expect(dlg.getByText('毛额（份额×价格）')).toBeVisible();
    await expect(dlg.getByText('¥1,000.00')).toBeVisible();
    await expect(dlg.getByText('实际到账（毛额−手续费）')).toBeVisible();
    await expect(dlg.getByText('¥995.00')).toBeVisible();

    // 清空价格（场外未传价）→ 派生展示区消失
    await dlg.getByLabel('价格').fill('');
    await expect(dlg.getByText('毛额（份额×价格）')).toHaveCount(0);

    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
