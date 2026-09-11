/**
 * 前端 E2E 测试：移动端份额变动事件页（#276）
 *
 * 数据说明：组合经 helpers 按 code 直达种子组合（#354），缺组合即硬失败、不 skip；
 * 份额变动事件数据种子不保证存在——列表断言兼容空态（「暂无份额变动事件」），
 * 用例 4/6 经 REST 造一条 pending 事件后删除（无产品/平台数据时优雅 skip）。
 * 用例 6（#424 确认预览）落在 active 组合 `E2E_ACTIVE`：预览要求「权益登记日有持仓
 * 快照」，draft 组合 `E2E_PORT` 零快照是其契约、不改；两个日期按 API 现状动态推导。
 * **不对 `E2E_ACTIVE` 跑 recalculate/catch-up/generate-next**（种子契约红线）。
 * 仅 mobile project 有意义：/m 路由由 middleware 按 UA 重定向。
 */
import { test, expect, type Page } from '@playwright/test';
import {
  E2E_ACTIVE,
  E2E_PORT,
  authHeaders,
  collectPageErrors,
  dialogByTitle,
  gotoPortfolioDetail,
} from './helpers';

/** 进入 E2E_PORT 移动端详情页（经 middleware 重定向到 /m），返回组合 code */
async function gotoMobilePortfolioDetail(page: Page): Promise<string> {
  await gotoPortfolioDetail(page, E2E_PORT);
  await expect(page.getByRole('heading', { name: '管理' })).toBeVisible({ timeout: 15_000 });
  return E2E_PORT;
}

test.describe('移动端份额变动事件页（#276）', () => {
  test('管理列表入口跳转并渲染列表/筛选', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '移动端入口断言仅针对移动项目');
    const errors = collectPageErrors(page);

    const code = await gotoMobilePortfolioDetail(page);
    // 入口：页尾「管理」列表第 5 项
    await page.getByRole('link', { name: '份额变动事件' }).click();
    await expect(page).toHaveURL(`/m/portfolio/${code}/share-change-events`);

    // 页面骨架：标题 + 新建按钮 + 筛选折叠按钮（移动端形态）
    await expect(page.getByRole('heading', { name: '份额变动事件' })).toBeVisible();
    await expect(page.getByRole('button', { name: '新建事件' })).toBeVisible();
    await expect(page.getByRole('button', { name: '筛选' })).toBeVisible();
    // 列表渲染：事件行或空态二者必居其一
    await expect(
      page.getByText('暂无份额变动事件').or(page.locator('table tbody tr')).first()
    ).toBeVisible({ timeout: 15_000 });
    expect(errors, `页面抛出未捕获异常: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('筛选折叠面板展开显示控件', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '仅移动端项目');
    await gotoMobilePortfolioDetail(page);
    await page.getByRole('link', { name: '份额变动事件' }).click();
    await page.getByRole('button', { name: '筛选' }).waitFor({ timeout: 15_000 });

    // 折叠态：筛选控件不可见
    await expect(page.getByRole('combobox', { name: /全部状态|状态/ })).toHaveCount(0);
    await page.getByRole('button', { name: '筛选' }).click();
    // 展开态：状态/类型下拉与除息日区间出现
    await expect(page.getByText('全部状态')).toBeVisible();
    await expect(page.getByText('全部类型')).toBeVisible();
  });

  test('新建事件弹窗可打开（移动端单列表单）', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '仅移动端项目');
    await gotoMobilePortfolioDetail(page);
    await page.getByRole('link', { name: '份额变动事件' }).click();
    await page.getByRole('button', { name: '新建事件' }).waitFor({ timeout: 15_000 });

    await page.getByRole('button', { name: '新建事件' }).click();
    const dlg = dialogByTitle(page, '新建份额变动事件');
    await expect(dlg).toBeVisible();
    // 现金分红默认类型：每份分红金额字段存在
    await expect(dlg.getByText('每份分红金额（元）')).toBeVisible();
    await page.keyboard.press('Escape');
  });

  test('待确认事件编辑入口：列表名称展示与就地修改（#342，联动 #344 刷新）', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '仅移动端项目');
    const code = await gotoMobilePortfolioDetail(page);

    // API 造数：种子不保证有事件数据，经 REST 造一条 pending 现金分红事件；
    // 双日期取种子日历内固定交易日（2025-2026 工作日，见 seed_base）
    const headers = await authHeaders(page);

    const products = await (await page.request.get('/api/products?page_size=1', { headers })).json();
    const platforms = await (await page.request.get('/api/platforms?page_size=1', { headers })).json();
    const product = products.items?.[0];
    const platform = platforms.items?.[0];
    if (!product || !platform) test.skip(true, '环境中没有产品/平台数据');

    const createResp = await page.request.post('/api/share-change-events', {
      headers,
      data: {
        portfolio_code: code,
        product_code: product.code,
        market: product.market,
        event_type: 'cash_dividend',
        ex_date: '2026-09-02',
        entitlement_date: '2026-09-01',
        platform_code: platform.code,
        div_cash: 0.5,
      },
    });
    if (!createResp.ok()) test.skip(true, `造数失败: ${createResp.status()} ${await createResp.text()}`);
    const created = await createResp.json();

    try {
      await page.getByRole('link', { name: '份额变动事件' }).click();

      // 列表展示（#342 → #355 双行）：产品列主行名称、次行 `代码 · 市场名`（虚拟产品 market 为空则无后缀）
      const row = page.locator('table tbody tr').filter({ hasText: created.product_code }).first();
      await expect(row).toBeVisible({ timeout: 15_000 });
      if (product.name) {
        await expect(row.getByText(product.name)).toBeVisible();
      }
      await expect(row.getByText(new RegExp(`^${created.product_code}( · .+)?$`))).toBeVisible();

      // pending 父记录有编辑入口
      await row.locator('button[title="编辑"]').click();
      const dlg = dialogByTitle(page, '编辑份额变动事件');
      await expect(dlg).toBeVisible();
      // 只读摘要含事件类型
      await expect(dlg.getByText('现金分红').first()).toBeVisible();

      // 修改每份分红金额并保存
      await dlg.locator('#edit_div_cash').fill('0.8');
      await dlg.getByRole('button', { name: '保存' }).click();
      await expect(dlg).toBeHidden();

      // 保存成功提示 + 行仍在（列表经 byPortfolio 失效即时刷新，无需手动刷新页面）
      await expect(page.getByText('更新成功')).toBeVisible();
      await expect(row).toBeVisible();
    } finally {
      await page.request.delete(`/api/share-change-events/${created.id}`, { headers });
    }
  });

  test('确认弹窗展示后端预览值，pending 行两列为「--」（#424）', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '仅移动端项目');

    // 预览要求「权益登记日有持仓快照」+「除息日晚于最新快照日」，故本用例落在
    // active 组合 E2E_ACTIVE（draft 组合 E2E_PORT 零快照是该组合的契约、不改）。
    // 两个日期按 API 现状动态推导，避免写死日期在某些运行日失效（日历止 2026-12-31）：
    // entitlement = 现有最早快照日（保证有持仓基数）、ex_date = 最新快照日之后的第一个交易日
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByRole('heading', { name: '管理' })).toBeVisible({ timeout: 15_000 });
    const headers = await authHeaders(page);

    const navHistory = await (
      await page.request.get(`/api/portfolios/${E2E_ACTIVE}/nav-history`, { headers })
    ).json();
    const snapshotDays: string[] = (navHistory as { snapshot_date: string }[])
      .map((r) => r.snapshot_date)
      .sort();
    if (snapshotDays.length === 0) test.skip(true, 'E2E_ACTIVE 无快照，无法构造有基数的预览场景');
    const entitlementDate = snapshotDays[0];
    const latestSnapshot = snapshotDays[snapshotDays.length - 1];

    const calendar = await (
      await page.request.get('/api/trading-calendar?year=2026', { headers })
    ).json();
    const openDays: string[] = (calendar as { calendar_date: string; is_open: boolean }[])
      .filter((r) => r.is_open)
      .map((r) => r.calendar_date)
      .sort();
    const exDate = openDays.find((d) => d > latestSnapshot);
    if (!exDate) test.skip(true, '交易日历内找不到晚于最新快照日的交易日');

    // 权益份额取快照日的基金持仓；平台必须与持仓一致（平台级事件必填 platform_code）
    const positions = await (
      await page.request.get(`/api/positions?portfolio_code=${E2E_ACTIVE}&page_size=50`, { headers })
    ).json();
    const held = (
      positions.items as { product_code: string; market: string; platform_code: string }[]
    ).find((p) => p.product_code !== 'CASH');
    if (!held) test.skip(true, 'E2E_ACTIVE 无基金持仓，无法构造平台级事件');
    const heldProduct = held!.product_code;
    const heldMarket = held!.market;
    const heldPlatform = held!.platform_code;

    // 造一条 pending 强制调整（用户直填值）：修复前弹窗对任何事件都渲染列表行的
    // NULL 字段（兜底成 0.00），故直填值能精确暴露该缺陷。取非零且非整的值，
    // 避免「恰好为 0」或格式歧义被当成结论
    const createResp = await page.request.post('/api/share-change-events', {
      headers,
      data: {
        portfolio_code: E2E_ACTIVE,
        product_code: heldProduct,
        market: heldMarket,
        event_type: 'forced_adjustment',
        ex_date: exDate,
        entitlement_date: entitlementDate,
        platform_code: heldPlatform,
        shares_change: 7.77,
        cash_change: -12.34,
      },
    });
    if (!createResp.ok()) test.skip(true, `造数失败: ${createResp.status()} ${await createResp.text()}`);
    const created = await createResp.json();

    try {
      await page.getByRole('link', { name: '份额变动事件' }).click();
      const row = page.locator('table tbody tr').filter({ hasText: created.product_code }).first();
      await expect(row).toBeVisible({ timeout: 15_000 });

      // pending 行未计算：两列显示「--」而非误导性的 0.00（#424 附带项）
      await expect(row.getByText('--')).toHaveCount(2);

      // 确认弹窗：拉取 /preview 展示预期变动量（修复前这两个位置恒为 0.00 / ¥0.00）
      await row.locator('button[title="确认"]').click();
      const dlg = dialogByTitle(page, '确认份额变动事件');
      await expect(dlg).toBeVisible();
      await expect(dlg.getByText('7.77 份')).toBeVisible({ timeout: 15_000 });
      await expect(dlg.getByText('¥-12.34')).toBeVisible();
    } finally {
      await page.request.delete(`/api/share-change-events/${created.id}`, { headers });
    }
  });
});
