/**
 * 前端 E2E：快照停更可见提示（issue #906 SPEC §4）
 *
 * 修复 A 把悬空产品码挡在 MISSING_NAV 后，读侧停在最后一条成功快照——
 * 「数字停更」必须被动可见。status 端点新增 trailing 缺口字段
 * catchup_pending_dates（最新快照日之后至业务昨日应生成而未生成的 SSE 交易日），
 * 详情页双端共享 SnapshotCatchupAlert 消费：非空渲染、空不渲染。
 *
 * 数据说明（与 helpers.ts 种子契约互补）：
 * - 停更/追平用例自建隔离组合（isolatedPortfolioCode），现金注入走「申购+确认」
 *   （首窗净值 1.0000 无需行情），快照经 API generate/catch-up 真实生成——
 *   trailing 缺口由后端真算，不用 page.route  mock 读侧响应。
 * - 业务昨日按 helpers.businessDateISO(-1) 独立锚定，再经 /api/trading-calendar 取
 *   最近开市日；不使用运行器本地日期，避免 CI 的 UTC/上海错日窗口。
 * - 台账型/draft 用例断言恒不渲染（SPEC §4 断言 3 与无基线口径）。
 * - E2E_ACTIVE 禁 catch-up/generate-next（#354 红线），本 spec 不触碰种子组合形态。
 */
import { test, expect, type Page } from '@playwright/test';
import {
  businessDateISO,
  collectPageErrors,
  createPortfolioViaApi,
  gotoPortfolioDetail,
  isolatedPortfolioCode,
  latestTradingDayOnOrBefore,
  openAppAndAuth,
  staleBaselineDates,
  seedStaleBaseline,
  E2E_PORT,
} from './helpers';

type Headers = { Authorization: string };

/**
 * 建隔离组合并把最新快照停在「today 前第 4 个开市日」（日期约定单点
 * staleBaselineDates，造数链 seedStaleBaseline，均 helpers 共用，ocr r3/r5）。
 */
async function setupStalePortfolio(
  page: Page,
  headers: Headers,
  code: string,
): Promise<{ latest: string }> {
  await createPortfolioViaApi(page, headers, code);
  const { applyDate, latest } = await staleBaselineDates(page, headers);
  await seedStaleBaseline(page, headers, code, applyDate, latest);
  return { latest };
}

async function statusOf(page: Page, headers: Headers, code: string) {
  const resp = await page.request.get(`/api/snapshots/portfolios/${code}/status`, { headers });
  expect(resp.ok(), `status 查询失败 ${resp.status()}`).toBeTruthy();
  return (await resp.json()) as {
    latest_snapshot_date?: string;
    missing_dates: string[];
    catchup_pending_dates?: string[];
  };
}

test.describe('快照停更可见提示（#906）', () => {
  test('停更组合：详情页 Alert 可见，含最新快照日与缺口数', async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E2E906', testInfo) + 'S';
    const { latest } = await setupStalePortfolio(page, headers, code);

    // 后端真算的 trailing 缺口非空（SPEC §4 断言 1 的读侧前提）
    const status = await statusOf(page, headers, code);
    const pending = status.catchup_pending_dates ?? [];
    expect(pending.length, '停更组合 trailing 缺口应非空').toBeGreaterThan(0);
    expect(status.latest_snapshot_date).toBe(latest);

    await page.goto(`/portfolio/${code}`);
    await page.getByRole('heading', { level: 1 }).first().waitFor({ state: 'visible', timeout: 15_000 });
    const alert = page.getByTestId('snapshot-catchup-alert');
    await expect(alert).toBeVisible({ timeout: 15_000 });
    await expect(alert).toContainText('快照停更');
    await expect(alert).toContainText(latest);
    await expect(alert).toContainText(`${pending.length} 个交易日未生成`);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('追平至业务昨日：Alert 不渲染', async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E2E906', testInfo) + 'C';
    await setupStalePortfolio(page, headers, code);

    const target = await latestTradingDayOnOrBefore(page, headers, businessDateISO(-1));
    const before = await statusOf(page, headers, code);
    const pending = before.catchup_pending_dates ?? [];
    expect(pending.length, '追平前应存在 trailing 缺口').toBeGreaterThan(0);
    expect(pending[pending.length - 1], 'trailing 缺口末日应为业务昨日或之前最近开市日').toBe(target);
    const catchUp = await page.request.post('/api/snapshots/catch-up', {
      headers,
      data: { portfolio_code: code, to_date: target },
    });
    expect(catchUp.ok(), `追平失败 ${catchUp.status()} ${await catchUp.text()}`).toBeTruthy();
    const catchUpResult = (await catchUp.json()) as {
      latest_snapshot_date?: string | null;
      failed_date?: string | null;
    };
    expect(catchUpResult.failed_date, `追平中断于 ${catchUpResult.failed_date}`).toBeNull();
    expect(catchUpResult.latest_snapshot_date).toBe(target);

    // SPEC §4 断言 2：最新快照日到达业务昨日 → trailing 缺口空
    const status = await statusOf(page, headers, code);
    expect(status.latest_snapshot_date).toBe(target);
    expect(status.catchup_pending_dates ?? []).toHaveLength(0);

    await page.goto(`/portfolio/${code}`);
    await page.getByRole('heading', { level: 1 }).first().waitFor({ state: 'visible', timeout: 15_000 });
    await expect(page.getByTestId('snapshot-catchup-alert')).toHaveCount(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('台账型组合：恒不渲染停更 Alert', async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E2E906', testInfo) + 'L';
    const createResp = await page.request.post('/api/portfolios', {
      headers,
      data: { code, name: `台账停更夹具 ${code}`, kind: 'ledger', owner_investor_code: 'ADMIN' },
    });
    expect(createResp.ok(), `台账组合创建失败 ${createResp.status()}`).toBeTruthy();
    const act = await page.request.post(`/api/portfolios/${code}/activate`, { headers });
    expect(act.ok(), `台账激活失败 ${act.status()} ${await act.text()}`).toBeTruthy();

    // SPEC §4 断言 3：台账型 catchup_pending_dates 恒空
    const status = await statusOf(page, headers, code);
    expect(status.catchup_pending_dates ?? []).toHaveLength(0);

    await page.goto(`/portfolio/${code}`);
    await page.getByRole('heading', { level: 1 }).first().waitFor({ state: 'visible', timeout: 15_000 });
    await expect(page.getByTestId('snapshot-catchup-alert')).toHaveCount(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('draft 无快照基线：不渲染停更 Alert', async ({ page }) => {
    // 登录后立即导航：dashboard 在途请求被导航打断的 access control 噪音属登录竞态
    // （helpers.collectPageErrors 的 tolerateLoginRaceNoise 先例同口径）
    const errors = collectPageErrors(page, { tolerateLoginRaceNoise: true });
    await openAppAndAuth(page);
    // E2E_PORT 是零快照 draft 契约组合（只读导航，不改形态）
    await gotoPortfolioDetail(page, E2E_PORT);
    await expect(page.getByTestId('snapshot-catchup-alert')).toHaveCount(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('closed 刻意停更：不渲染停更 Alert（L2 r1）', async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E2E906', testInfo) + 'X';
    await setupStalePortfolio(page, headers, code);
    const close = await page.request.post(`/api/portfolios/${code}/close`, { headers });
    expect(close.ok(), `关闭组合失败 ${close.status()} ${await close.text()}`).toBeTruthy();

    // 自动快照只推进 active：closed 的 trailing 缺口永久增长，属刻意停更非故障
    const status = await statusOf(page, headers, code);
    expect(status.catchup_pending_dates ?? []).toHaveLength(0);

    await page.goto(`/portfolio/${code}`);
    await page.getByRole('heading', { level: 1 }).first().waitFor({ state: 'visible', timeout: 15_000 });
    await expect(page.getByTestId('snapshot-catchup-alert')).toHaveCount(0);
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });
});