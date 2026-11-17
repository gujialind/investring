import { test, expect } from '@playwright/test';
import { dialogByTitle } from './helpers';

/**
 * #900：投资人管理页读侧失败态门禁（双 project；桌面/移动共享组件单点，形态同断言）。
 * 数据说明：消费种子 ADMIN/VIEWER；A2 用「编辑提交 PUT 成功 → invalidate 同键列表」触发
 * 后台重取（isRefetchError 保留旧 data），PUT 走真实隔离后端、仅 GET 被拦截 500；
 * 写操作仅把 VIEWER 原值回写，隔离库按运行独占。A4 用 route 200 + items=[] 造真·空。
 * 判序口径：错误优先**外层三元**（#683A 同形）；嵌套形反例下 A2 应红（判别力经本地反例实测）。
 */

const LIST_RE = /\/api\/investors(\?|$)/;
const FAIL_JSON = { detail: { error: 'INTERNAL', message: '投资人目录服务暂不可用' } };

test('投资人列表读取失败 → 失败态优先于「暂无投资人」，重试与全局 toast 可见（#900 A1）', async ({ page }) => {
  await page.route(LIST_RE, (route) => route.fulfill({ status: 500, json: FAIL_JSON }));
  await page.goto('/investors');
  await expect(page.getByText('加载失败：投资人目录服务暂不可用')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('暂无投资人')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '重试' })).toBeVisible();
  // #214 toast 车道对齐（usePlatformList 同型）；无 modal 开着，role 口径可用，DOM 口径更稳
  await expect(page.getByTestId('toast-card').filter({ hasText: '投资人列表加载失败' })).toBeVisible();
});

test('同键后台重取失败且保留旧 data 时失败块仍可达（#900 A2 判序，外层三元）', async ({ page }) => {
  await page.goto('/investors');
  const container = (text: string) =>
    page.locator('tr, [data-testid="investor-card"]').filter({ hasText: text });
  await expect(container('ADMIN')).toBeVisible({ timeout: 10_000 });

  // 首取成功后再拦截 GET：后续同键重取失败时 react-query 保留旧 data（isRefetchError）
  await page.route(LIST_RE, (route) => route.fulfill({ status: 500, json: FAIL_JSON }));
  await container('VIEWER').getByRole('button', { name: '编辑' }).click();
  const dlg = dialogByTitle(page, '编辑投资人');
  await expect(dlg).toBeVisible();
  await dlg.getByRole('button', { name: '保存修改' }).click();
  await expect(dlg).toBeHidden();

  // PUT 成功 invalidate 同键列表 → 后台重取 500：外层三元失败块必须可见；
  // 嵌套形（失败臂塞进 length===0 前件）在该态既无失败块也无空态 → 本断言红，判别力成立
  await expect(page.getByText('加载失败：投资人目录服务暂不可用')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('暂无投资人')).toHaveCount(0);
});

test('点重试且接口恢复后正常渲染行集，失败态撤下（#900 A3）', async ({ page }) => {
  await page.route(LIST_RE, (route) => route.fulfill({ status: 500, json: FAIL_JSON }));
  await page.goto('/investors');
  await expect(page.getByText('加载失败：投资人目录服务暂不可用')).toBeVisible({ timeout: 10_000 });

  await page.unroute(LIST_RE);
  const refetched = page.waitForRequest(
    (r) => r.method() === 'GET' && LIST_RE.test(r.url()),
    { timeout: 10_000 },
  );
  await page.getByRole('button', { name: '重试' }).click();
  await refetched;
  await expect(page.locator('tr, [data-testid="investor-card"]').filter({ hasText: 'ADMIN' })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('加载失败：')).toHaveCount(0);
});

test('真·空列表仍渲染「暂无投资人」，不误报失败（#900 A4）', async ({ page }) => {
  await page.route(LIST_RE, (route) =>
    route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 100 } }),
  );
  await page.goto('/investors');
  await expect(page.getByText('暂无投资人')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('加载失败：')).toHaveCount(0);
  await expect(page.getByTestId('toast-card').filter({ hasText: '投资人列表加载失败' })).toHaveCount(0);
});