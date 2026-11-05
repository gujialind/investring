/**
 * 前端 E2E 测试：空壳组合的删除闭环（issue #820，扩围口径）
 *
 * 覆盖 SPEC v1.1 §6.3-2：
 * 1. 列表卡片删除入口 → 二次确认弹窗点名 name + code → 确认 → 成功 toast → 卡片消失
 *    → 同一 code 可立即重建（issue 的核心痛点「误建 code 永久占用」）
 * 2. 取消确认弹窗零副作用
 * 3. 详情页删除成功后跳回列表（详情页数据已 removeQueries，停留会打到 404）
 * 4. viewer 会话双处都无删除入口（前端 isAdmin 门控；后端 403 由
 *    backend/tests/integration/test_portfolio_delete.py 守）
 * 5. closed 卡片有删除入口（扩围：入口按状态出现、不按「是否真空壳」预判），
 *    closed 且有账时后端 409 拦截、数据不动（closed 空壳经 API 不可构造，
 *    其成功删除路径由后端集成测试 TestDeleteEmptyClosed 承担）
 *
 * 数据说明：形态一律自建（`helpers.isolatedPortfolioCode` 区分 project/worker/retry），
 * 不复用种子 E2E_PORT——它的草稿态会被 trade-buy-amount-linkage 激活（#636），
 * 拿它断 draft 会随执行顺序漂移。残留由每轮重建的 E2E 库吸收（清理策略不变），
 * 只有「重建同码」用例显式把自己刚建的那条删回去。
 */
import { test, expect, type Page } from '@playwright/test';
import {
  activatePortfolioViaApi,
  closePortfolioViaApi,
  createPortfolioViaApi,
  isolatedPortfolioCode,
  openAppAndAuth,
} from './helpers';

const DELETE_BUTTON = '删除组合';

/**
 * 删除确认弹窗（Radix AlertDialog → role="alertdialog"，不是 dialog；
 * 口径同 task-run-async / trade-buy-amount-linkage 的确认框定位）。
 */
function deleteDialog(page: Page) {
  return page.getByRole('alertdialog').filter({ hasText: DELETE_BUTTON });
}

/** 卡片内删除入口的 E2E 锚点（组件侧 data-testid，见 PortfolioListContent 注释） */
function cardDeleteButton(page: Page, code: string) {
  return page.getByTestId(`portfolio-delete-${code}`);
}

/** 换 viewer 会话（沿用 cash-flows.spec.ts #788 的既有形态：清凭证后走真实登录流） */
async function loginAsViewer(page: Page): Promise<void> {
  await page.context().clearCookies();
  await page.evaluate(() => window.localStorage.clear());
  await page.goto('/login');
  await page.getByLabel('用户名').fill('VIEWER');
  await page.getByLabel('密码').fill('viewer123');
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForURL(/\/(m\/)?dashboard/, { waitUntil: 'domcontentloaded' });
}

/** 详情页标题渲染完成（两端共用组件，mobile 由 proxy 重定向到 /m） */
async function gotoDetailAndWait(page: Page, code: string) {
  await page.goto(`/portfolio/${code}`);
  await page
    .getByRole('heading', { level: 1 })
    .first()
    .waitFor({ state: 'visible', timeout: 15_000 });
}

test.describe('组合删除闭环（#820）', () => {
  test('列表卡片删除空壳草稿：确认弹窗点名 name+code，删除后同 code 可重建', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E820D', testInfo, 'L');
    const name = `删除闭环 ${code}`;
    await createPortfolioViaApi(page, headers, code, name);

    await page.goto('/portfolio');
    const button = cardDeleteButton(page, code);
    await expect(button, `列表未渲染自建草稿 ${code} 的删除入口`).toBeVisible();

    await button.click();
    const dialog = deleteDialog(page);
    await expect(dialog).toBeVisible();
    // Q4 口径：普通二次确认，文案点名 name + code 与不可恢复
    await expect(dialog.getByText(name, { exact: false })).toBeVisible();
    await expect(dialog.getByText('不可恢复', { exact: false })).toBeVisible();

    await dialog.getByRole('button', { name: '确认删除' }).click();
    // modal Dialog 开着时按 role 找 toast 恒 0 命中（Radix 把外部子树置 aria-hidden），
    // 故按 testid 定位（frontend/AGENTS.md §1.2 的实测口径）
    await expect(page.getByTestId('toast-card').filter({ hasText: '删除成功' })).toBeVisible();
    await expect(button).toHaveCount(0);

    // 主键释放：同 code 立即可重建（重建出来的这条再删回去，不给下一轮留残留）
    const recreate = await page.request.post('/api/portfolios', {
      data: { code, name: `重建 ${code}` },
      headers,
    });
    expect(recreate.status(), await recreate.text()).toBe(200);
    const cleanup = await page.request.delete(`/api/portfolios/${code}`, { headers });
    expect(cleanup.status(), await cleanup.text()).toBe(200);
  });

  test('取消确认弹窗不删任何东西', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E820D', testInfo, 'X');
    await createPortfolioViaApi(page, headers, code, `取消删除 ${code}`);

    await page.goto('/portfolio');
    await cardDeleteButton(page, code).click();
    const dialog = deleteDialog(page);
    await dialog.getByRole('button', { name: '取消' }).click();
    await expect(dialog).toHaveCount(0);

    // 入口仍在、后端仍能查到——取消动作不得有任何副作用
    await expect(cardDeleteButton(page, code)).toBeVisible();
    const got = await page.request.get(`/api/portfolios/${code}`, { headers });
    expect(got.status()).toBe(200);

    const cleanup = await page.request.delete(`/api/portfolios/${code}`, { headers });
    expect(cleanup.status(), await cleanup.text()).toBe(200);
  });

  test('详情页删除成功后跳回列表', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E820D', testInfo, 'P');
    await createPortfolioViaApi(page, headers, code, `详情页删除 ${code}`);

    await gotoDetailAndWait(page, code);
    await page.getByRole('button', { name: DELETE_BUTTON }).click();

    const dialog = deleteDialog(page);
    await expect(dialog.getByText(code, { exact: false })).toBeVisible();
    await dialog.getByRole('button', { name: '确认删除' }).click();

    // 成功后回组合列表（mobile project 由 proxy 重定向到 /m/portfolio）
    await page.waitForURL(/\/(m\/)?portfolio(\?|$)/, { timeout: 15_000 });
    await expect(cardDeleteButton(page, code)).toHaveCount(0);
  });

  test('viewer 会话：列表与详情都没有删除入口（#820 权限门）', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E820D', testInfo, 'V');
    await createPortfolioViaApi(page, headers, code, `viewer 可见 ${code}`);

    await loginAsViewer(page);

    await page.goto('/portfolio');
    // sanity：卡片确实渲染到了（有详情链接），排除「没数据所以没按钮」的假阳性
    await expect(
      page.locator(`a[href="/portfolio/${code}"], a[href="/m/portfolio/${code}"]`).first(),
    ).toBeVisible();
    await expect(cardDeleteButton(page, code)).toHaveCount(0);

    await gotoDetailAndWait(page, code);
    await expect(page.getByRole('button', { name: DELETE_BUTTON })).toHaveCount(0);
    // 夹具由 admin 会话创建，viewer 删不掉——留残留属既有清理定案（见文件头）
  });

  test('closed 卡片有删除入口；有账组合由后端 409 拦截且数据不动（#820 扩围）', async ({ page }, testInfo) => {
    const headers = await openAppAndAuth(page);
    const code = isolatedPortfolioCode('E820D', testInfo, 'C');
    await createPortfolioViaApi(page, headers, code, `已关闭有账 ${code}`);
    // closed 形态必须「先激活再关闭」（#820 收紧后 draft 直接 close 会 422）；
    // 激活必落 subscription 行，于是得到「closed 且有账」——入口照出，删除应被拒
    await activatePortfolioViaApi(page, headers, code);
    await closePortfolioViaApi(page, headers, code);

    await page.goto('/portfolio');
    const button = cardDeleteButton(page, code);
    await expect(button, `列表未渲染自建 closed ${code} 的删除入口（扩围门控）`).toBeVisible();

    await button.click();
    const dialog = deleteDialog(page);
    await dialog.getByRole('button', { name: '确认删除' }).click();

    await expect(
      page.getByTestId('toast-card').filter({ hasText: '删除失败' }),
    ).toBeVisible();
    await expect(
      page.getByTestId('toast-card').filter({ hasText: '组合仍存在关联数据' }),
    ).toBeVisible();
    // 拒绝不留痕：入口仍在、后端仍能查到（残留由每轮重建的 E2E 库吸收）
    await expect(button).toBeVisible();
    const got = await page.request.get(`/api/portfolios/${code}`, { headers });
    expect(got.status()).toBe(200);
  });
});
