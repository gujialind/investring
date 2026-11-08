/**
 * 前端 E2E 测试：份额变动事件行级「取消」二次确认（#845）
 *
 * 数据说明：组合经 helpers 按 code 直达 draft 种子组合 E2E_PORT（零快照 ⇒ pending
 * 事件无「除息日晚于最新快照日」约束，缺组合即硬失败、不 skip）；种子不保证事件
 * 数据，每用例经 REST 自造一条 pending 现金分红、finally 删除（delete 无状态守卫，
 * pending/cancelled 均可清；无产品/平台数据时优雅 skip）。双日期经交易日历动态取
 * 相邻交易日（#468 口径，不写死）。两 project 并行各造一行，行按事件 id 经
 * `event-detail-open-${id}` testid 精确区分，不用 `.first()` 猜行。
 * 双端同断言：桌面/移动共用 ShareChangeEventsContent 与同一 AlertDialog，
 * 不写端专属 skip；mobile 经 proxy 按 UA 重定向到 /m。
 */
import { test, expect, type Page } from '@playwright/test';
import {
  E2E_PORT,
  gotoPortfolioSubpage,
  nearestTradingDay,
  nextTradingDays,
  openAppAndAuth,
} from './helpers';

// Radix AlertDialog 的 role 是 alertdialog 而非 dialog，dialogByTitle 恒 0 命中；
// 按标题定位口径先例：portfolio-delete.spec.ts / task-run-async.spec.ts
function cancelDialog(page: Page) {
  return page.getByRole('alertdialog').filter({ hasText: '取消事件' });
}

/**
 * 经 REST 造一条 pending 现金分红事件，返回认证头与事件 id。
 * 先做同源导航取认证头（openAppAndAuth），造数后再进列表页，行随首帧渲染。
 */
async function createPendingEvent(
  page: Page,
): Promise<{ headers: { Authorization: string }; id: number }> {
  const headers = await openAppAndAuth(page);

  const products = await (await page.request.get('/api/products?page_size=1', { headers })).json();
  const platforms = await (await page.request.get('/api/platforms?page_size=1', { headers })).json();
  const product = products.items?.[0];
  const platform = platforms.items?.[0];
  if (!product || !platform) test.skip(true, '环境中没有产品/平台数据');

  // 权益登记日 = today 起最近交易日，除息日 = 其后首个交易日
  // （创建校验：均为交易日且除息日不早于权益登记日；同日现金分红另要求 cash_pay_date，故取相邻两日）
  const entitlement = await nearestTradingDay(page, headers);
  const [exDate] = await nextTradingDays(page, headers, entitlement, 1);

  const createResp = await page.request.post('/api/share-change-events', {
    headers,
    data: {
      portfolio_code: E2E_PORT,
      product_code: product.code,
      market: product.market,
      event_type: 'cash_dividend',
      ex_date: exDate,
      entitlement_date: entitlement,
      platform_code: platform.code,
      div_cash: 0.5,
    },
  });
  if (!createResp.ok()) test.skip(true, `造数失败: ${createResp.status()} ${await createResp.text()}`);
  const created = await createResp.json();
  return { headers, id: created.id };
}

test.describe('份额变动事件行级取消二次确认（#845）', () => {
  test('放弃路径：开弹窗不发请求，点「取消」关闭后事件仍待确认', async ({ page }) => {
    const { headers, id } = await createPendingEvent(page);
    // 计数取消请求并放行：修复前点行级「取消」即发请求，本用例直接红
    let cancelCalls = 0;
    await page.route('**/api/share-change-events/*/cancel', (route) => {
      cancelCalls += 1;
      return route.continue();
    });

    try {
      await gotoPortfolioSubpage(page, E2E_PORT, 'share-change-events');
      const row = page
        .locator('table tbody tr')
        .filter({ has: page.getByTestId(`event-detail-open-${id}`) });
      await expect(row).toBeVisible({ timeout: 15_000 });

      await row.locator('button[title="取消"]').click();
      const dlg = cancelDialog(page);
      await expect(dlg).toBeVisible();
      // 弹窗打开但尚未确认：不发起取消请求
      expect(cancelCalls).toBe(0);

      await dlg.getByRole('button', { name: '取消' }).click();
      await expect(dlg).toBeHidden();
      // 放弃后仍不发起请求，事件仍 pending、行级「取消」入口仍在
      expect(cancelCalls).toBe(0);
      await expect(row.getByText('待确认')).toBeVisible();
      await expect(row.locator('button[title="取消"]')).toBeVisible();
    } finally {
      await page.request.delete(`/api/share-change-events/${id}`, { headers });
    }
  });

  test('确认路径：确认按钮 solid destructive，点「确认」才执行取消', async ({ page }) => {
    const { headers, id } = await createPendingEvent(page);
    let cancelCalls = 0;
    await page.route('**/api/share-change-events/*/cancel', (route) => {
      cancelCalls += 1;
      return route.continue();
    });

    try {
      await gotoPortfolioSubpage(page, E2E_PORT, 'share-change-events');
      const row = page
        .locator('table tbody tr')
        .filter({ has: page.getByTestId(`event-detail-open-${id}`) });
      await expect(row).toBeVisible({ timeout: 15_000 });

      await row.locator('button[title="取消"]').click();
      const dlg = cancelDialog(page);
      await expect(dlg).toBeVisible();
      expect(cancelCalls).toBe(0);
      // §14：危险操作确认按钮 solid destructive
      const confirmBtn = dlg.getByRole('button', { name: '确认' });
      await expect(confirmBtn).toHaveClass(/bg-destructive/);
      await expect(confirmBtn).toHaveClass(/text-white/);

      await confirmBtn.click();
      await expect(dlg).toBeHidden();
      // 成功提示（#640 DOM 口径：弹窗刚关闭，toast 经 testid 定位）
      await expect(page.getByTestId('toast-card').filter({ hasText: '取消成功' })).toBeVisible();
      // 恰好发起一次取消请求；行内状态徽标变「已取消」、行级「取消」入口消失
      expect(cancelCalls).toBe(1);
      await expect(row.getByText('已取消')).toBeVisible();
      await expect(row.locator('button[title="取消"]')).toHaveCount(0);
    } finally {
      await page.request.delete(`/api/share-change-events/${id}`, { headers });
    }
  });
});