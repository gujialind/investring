import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { E2E_ACTIVE, authHeaders, gotoPortfolioDetail, toISODate } from './helpers';

/**
 * #693 组合投资人列表（?tab=investors）：表格化暴露 investor_holding 已落库的
 * 派生列（市值/总成本/成本价/持仓收益/收益率/冻结份额）+ 占比经最大余数法。
 *
 * 数据说明：不用共享种子——E2E_ACTIVE 仅 ADMIN 一个投资人（无多占比/清仓形态），
 * 且禁止对共享种子推进快照。经 API 自建隔离组合 E693INV*：纯现金（不买入
 * 产品，首窗计价 1.0000 无需行情，unit_price 恒 1.0），三投资人
 * （ADMIN 10 万 / VIEWER 6 万 / 自建 4 万）apply=d1 → confirm（confirm_date
 * 恒 T+1 = d2）→ d2 快照。期望形态：ratio 0.5/0.3/0.2 → 占比 50.0/30.0/20.0，
 * profit 全 0（纯现金无净值波动，「真 0」显示 ¥0.00 而非 --）。
 * withRedeems（#694）：d3 造 VIEWER 全额赎回 + 第三人部分赎回（apply=d3、
 * confirm_date=d4）→ d3 快照 → confirm → d4 快照；终态 = 2 持仓行 + 1 清仓行，
 * 纯现金口径下三者累计收益均 0（真 0 显示 ¥0.00）。
 * 数值精算断言留给后端集成测试（test_portfolios.py），本 spec 钉结构与展示口径。
 * code 区分 tag/project/retry（组合无 DELETE 端点、投资人表全局唯一，同 spec
 * 内每个自建用例须各自 code；残留由每轮重建的 E2E 库吸收）。
 */

async function setupInvestorsPortfolio(
  page: Page, testInfo: TestInfo, tag: string, withRedeems = false,
): Promise<string> {
  const code = `E693INV${tag}_${testInfo.project.name}_${testInfo.retry}`;
  const third = `E693I3${tag}_${testInfo.project.name}_${testInfo.retry}`;
  await gotoPortfolioDetail(page, E2E_ACTIVE);
  const headers = await authHeaders(page);

  // 交易日锚定经 /api/trading-calendar（#468），本地口径 toISODate（#640 S-2，
  // 禁 toISOString().slice(0,10) 的 UTC 切日）；手法照 portfolio-holdings-view
  const today = toISODate(new Date());
  const year = Number(today.slice(0, 4));
  const calendars = await Promise.all(
    [year - 1, year, year + 1].map(async (y) => {
      const resp = await page.request.get(`/api/trading-calendar?year=${y}`, { headers });
      await expect(resp).toBeOK();
      return (await resp.json()) as { calendar_date: string; is_open: boolean }[];
    }),
  );
  const days = calendars
    .flat()
    .filter((d) => d.is_open)
    .map((d) => d.calendar_date)
    .sort();
  const uptoToday = days.filter((d) => d <= today);
  // 赎回形态需 d1..d4 四个交易日；不足时 slice(-4) 会解出 undefined，
  // 以快照接口 422 等糊涂形态失败，故下界随形态提到 4
  const minDays = withRedeems ? 4 : 2;
  expect(uptoToday.length, `种子日历须覆盖今天之前的至少 ${minDays} 个交易日`).toBeGreaterThanOrEqual(minDays);
  const [d1, d2, d3, d4] = withRedeems
    ? uptoToday.slice(-4)
    : [...uptoToday.slice(-2), '', ''];

  const post = async <T = unknown>(path: string, data?: unknown): Promise<T> => {
    const resp = await page.request.post(path, { headers, data });
    await expect(resp, `${path} 返回 ${resp.status()} ${await resp.text()}`).toBeOK();
    return resp.json();
  };

  await post('/api/portfolios', { code, name: `投资人视图 ${code}` });
  await post('/api/investors', { code: third, name: '第三人', password: 'e693inv@2026' });
  // 三笔申购同 apply=d1：确认日均为 d2（恒 T+1）；逐笔确认均走首窗判定
  // （申请日无快照且无 confirm_date <= apply 的已确认申购）→ 1.0000 计价
  for (const [investor, amount] of [
    ['ADMIN', 100000],
    ['VIEWER', 60000],
    [third, 40000],
  ] as const) {
    const sub = await post<{ id: number }>('/api/subscriptions', {
      portfolio_code: code,
      investor_code: investor,
      platform_code: 'HBZQ',
      sub_type: 'subscribe',
      amount,
      apply_date: d1,
    });
    await post(`/api/subscriptions/${sub.id}/confirm`);
  }
  // 首快照日 == 最早 confirm_date（#180）
  await post('/api/snapshots/generate', { portfolio_code: code, target_date: d2 });

  if (withRedeems) {
    // #694：d3 录入赎回（confirm_date = d4，创建期闸门要求晚于最新快照 d2）→
    // d3 快照（赎回仍 pending，仅冻结份额）→ confirm → d4 快照（VIEWER 清仓无行）
    const redeemFull = await post<{ id: number }>('/api/subscriptions', {
      portfolio_code: code,
      investor_code: 'VIEWER',
      platform_code: 'HBZQ',
      sub_type: 'redeem',
      shares: 60000,
      apply_date: d3,
    });
    const redeemPart = await post<{ id: number }>('/api/subscriptions', {
      portfolio_code: code,
      investor_code: third,
      platform_code: 'HBZQ',
      sub_type: 'redeem',
      shares: 10000,
      apply_date: d3,
    });
    await post('/api/snapshots/generate', { portfolio_code: code, target_date: d3 });
    await post(`/api/subscriptions/${redeemFull.id}/confirm`);
    await post(`/api/subscriptions/${redeemPart.id}/confirm`);
    await post('/api/snapshots/generate', { portfolio_code: code, target_date: d4 });
  }
  return code;
}

/** 深链带 query：直导航 + 页头 h1 就绪信号（gotoPortfolioDetail 不带 tab，手法照 portfolio-holdings-view） */
async function gotoInvestorsTab(page: Page, code: string): Promise<void> {
  await page.goto(`/portfolio/${code}?tab=investors`);
  await page.getByRole('heading', { level: 1 }).first().waitFor({ state: 'visible', timeout: 15_000 });
  await expect(page).toHaveURL(/[?&]tab=investors/);
}

test.describe('#693 组合投资人列表（?tab=investors）', () => {
  test('深链进入：三行表格、表头齐全、占比加总 100.0、纯现金真零收益显示 ¥0.00', async ({ page }, testInfo) => {
    const code = await setupInvestorsPortfolio(page, testInfo, 'a');
    await gotoInvestorsTab(page, code);

    const table = page.getByTestId('portfolio-investors-table');
    await expect(table).toBeVisible();
    await expect(page.getByTestId('investor-row')).toHaveCount(3);
    for (const header of ['投资人', '份额', '占比', '市值', '总成本', '成本价', '持仓收益', '冻结']) {
      await expect(table.getByRole('columnheader', { name: header })).toBeVisible();
    }

    // 行级占比加总 = 100.0（§4 最大余数法；手法照 portfolio-holdings-view 的占比加总断言）
    const ratioTexts = await page.getByTestId('investor-ratio').allInnerTexts();
    expect(ratioTexts).toHaveLength(3);
    const sum = ratioTexts.reduce((s, t) => s + Number(t.replace('%', '')), 0);
    expect(Math.abs(sum - 100.0), `占比加总应恒为 100.0，实际 ${ratioTexts.join(' + ')}`).toBeLessThanOrEqual(0.05);

    // 纯现金无净值波动：profit=0 是「真 0」，显示 ¥0.00 而非 --（§12 边界）
    await expect(table).toContainText('¥0.00');
  });

  test('历史快照派生列 NULL → 单元格 --，不伪装成 ¥0.00', async ({ page }, testInfo) => {
    const code = await setupInvestorsPortfolio(page, testInfo, 'b');
    // mock 派生列全 null（#693 验收：NULL → --；route 手法照 #663 失败态用例，
    // 此处 fulfill 200 而非 500——钉的是空值兜底不是失败态）
    await page.route(/\/api\/portfolios\/[^/]+\/investors(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        json: [
          {
            investor_code: 'ADMIN',
            name: '管理员',
            shares: 100000,
            frozen_shares: null,
            cost_per_share: null,
            market_value: null,
            total_cost: null,
            profit: null,
            profit_percent: null,
            ratio: null,
          },
        ],
      }),
    );
    await gotoInvestorsTab(page, code);

    const table = page.getByTestId('portfolio-investors-table');
    await expect(page.getByTestId('investor-row')).toHaveCount(1);
    // 未计算列一律 --（份额列恒有值：100,000.00 份）
    await expect(page.getByTestId('investor-ratio')).toHaveText('--');
    await expect(table).toContainText('--');
    await expect(table).not.toContainText('¥0.00');
  });

  test('#694 清仓行合并：2 持仓 + 1 清仓（Badge 与 testid）、占比加总含 0.0% 仍 100.0', async ({ page }, testInfo) => {
    const code = await setupInvestorsPortfolio(page, testInfo, 'c', true);
    await gotoInvestorsTab(page, code);

    await expect(page.getByTestId('investor-row')).toHaveCount(2);
    const liquidated = page.getByTestId('investor-row-liquidated');
    await expect(liquidated).toHaveCount(1);
    await expect(liquidated).toContainText('已清仓');

    // 占比加总含清仓行 0.0% 仍恒 100.0（§4 最大余数法）
    const ratioTexts = await page.getByTestId('investor-ratio').allInnerTexts();
    expect(ratioTexts).toHaveLength(3);
    const sum = ratioTexts.reduce((s, t) => s + Number(t.replace('%', '')), 0);
    expect(Math.abs(sum - 100.0), `占比加总应恒为 100.0，实际 ${ratioTexts.join(' + ')}`).toBeLessThanOrEqual(0.05);
  });

  test('#694 累计收益*列与 text-warning 口径注记可见', async ({ page }, testInfo) => {
    const code = await setupInvestorsPortfolio(page, testInfo, 'd', true);
    await gotoInvestorsTab(page, code);

    const table = page.getByTestId('portfolio-investors-table');
    await expect(table.getByRole('columnheader', { name: '累计收益*' })).toBeVisible();
    const note = page.getByTestId('investor-cumulative-note');
    await expect(note).toBeVisible();
    await expect(note).toContainText('与「持仓收益」');
  });

  test('#694 清仓行累计收益真零显示 ¥0.00（非 --）', async ({ page }, testInfo) => {
    const code = await setupInvestorsPortfolio(page, testInfo, 'e', true);
    await gotoInvestorsTab(page, code);

    // 纯现金口径：VIEWER 赎回 6 万 − 申购 6 万 = 0，真零显示 ¥0.00（§12 边界）
    const liquidated = page.getByTestId('investor-row-liquidated');
    await expect(liquidated.getByTestId('investor-cumulative')).toHaveText('¥0.00');
    // 清仓行份额/市值为真 0，派生列（成本价等 NULL）为 --
    await expect(liquidated).toContainText('0.00 份');
    await expect(liquidated).toContainText('--');
  });
});
