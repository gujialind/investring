import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { authHeaders, dialogByTitle } from './helpers';

/**
 * #893 管理员重置他人密码（投资人管理页入口，双端共享 InvestorsContent）。
 *
 * 数据说明：**不改共享种子口令**——VIEWER/viewer123 被 cash-flows / portfolio-delete /
 * settings-redesign 等多个 spec 消费，重置它会污染并行用例（auth-lifecycle 同口径）。
 * 经 API 自建临时投资人（viewer、无份额），重置断言完成后 DELETE 清理；code 区分
 * project/retry（投资人表全局唯一），失败残留由每轮重建的 E2E 库吸收。
 * 锁定→重置→立即登录的后端语义归 tests/integration/test_auth.py::TestAdminResetPassword，
 * 本 spec 钉 UI 流程与会话撤销的端到端形态。
 */

const OLD_PASSWORD = 'e893old@2026';
const NEW_PASSWORD = 'e893new@2026';

function tempInvestorCode(testInfo: TestInfo, tag: string): string {
  return `E893R${tag}_${testInfo.project.name}_${testInfo.retry}`;
}

async function createTempInvestor(
  page: Page, headers: { Authorization: string }, code: string,
): Promise<void> {
  const resp = await page.request.post('/api/investors', {
    headers,
    data: { code, name: `重置目标 ${code}`, password: OLD_PASSWORD },
  });
  expect(resp, `创建临时投资人返回 ${resp.status()} ${await resp.text()}`).toBeOK();
}

async function loginViaApi(page: Page, code: string, password: string) {
  return page.request.post('/api/auth/login', { data: { code, password } });
}

test.describe('管理员重置他人密码', () => {
  test('重置后目标会话失效、新密码可登录，操作者留在投资人页', async ({ page }, testInfo) => {
    const code = tempInvestorCode(testInfo, 'A');
    await page.goto('/investors');
    await expect(page.getByRole('heading', { name: '投资人管理' })).toBeVisible();
    const headers = await authHeaders(page);
    await createTempInvestor(page, headers, code);

    // 重置前签发的目标会话 token，重置后应 401（会话撤销权威在 DB）
    const before = await loginViaApi(page, code, OLD_PASSWORD);
    expect(before.ok()).toBeTruthy();
    const oldToken = ((await before.json()) as { token: string }).token;

    await page.reload();
    // 双形态容器锚点：桌面表格 tr / 移动卡片（#897 卡片形态；tag+testid 符合定位器契约）
    const targetRow = page.locator('tr, [data-testid="investor-card"]').filter({ hasText: code });
    await expect(targetRow).toBeVisible();

    // 本人（ADMIN）行不渲染重置按钮：重置自己需旧密码，自助改密另有入口
    const adminRow = page.locator('tr, [data-testid="investor-card"]').filter({ hasText: 'ADMIN' });
    await expect(adminRow.getByRole('button', { name: '重置密码' })).toHaveCount(0);

    await targetRow.getByRole('button', { name: '重置密码' }).click();
    const dlg = dialogByTitle(page, '重置密码');
    await expect(dlg).toBeVisible();
    await expect(dlg.getByLabel('原密码')).toHaveCount(0);
    await dlg.getByLabel('新密码', { exact: true }).fill(NEW_PASSWORD);
    await dlg.getByLabel('确认新密码').fill(NEW_PASSWORD);
    await dlg.getByRole('button', { name: '重置密码' }).click();

    // 成功 toast（Dialog 关闭竞态期用 DOM 口径，#640）；不登出、不跳转
    await expect(page.getByTestId('toast-card').filter({ hasText: '密码重置成功' })).toBeVisible();
    await expect(dlg).toBeHidden();
    await expect(page.getByRole('heading', { name: '投资人管理' })).toBeVisible();
    expect(page.url()).toContain('/investors');

    // 新密码可登录、旧密码失败、旧 token 失效；操作者会话不受影响
    expect((await loginViaApi(page, code, NEW_PASSWORD)).ok()).toBeTruthy();
    expect((await loginViaApi(page, code, OLD_PASSWORD)).status()).toBe(401);
    const revoked = await page.request.get('/api/auth/status', {
      headers: { Authorization: `Bearer ${oldToken}` },
    });
    expect(revoked.status()).toBe(401);
    const adminStatus = await page.request.get('/api/auth/status', { headers });
    expect(adminStatus.ok()).toBeTruthy();

    const del = await page.request.delete(`/api/investors/${code}`, { headers });
    expect(del.ok()).toBeTruthy();
  });

  test('短密码与两次不一致被前端拦截，不发请求', async ({ page }, testInfo) => {
    const code = tempInvestorCode(testInfo, 'B');
    await page.goto('/investors');
    await expect(page.getByRole('heading', { name: '投资人管理' })).toBeVisible();
    const headers = await authHeaders(page);
    await createTempInvestor(page, headers, code);
    await page.reload();

    const requests: string[] = [];
    page.on('request', (request) => {
      if (request.url().includes('/api/auth/password')) requests.push(request.url());
    });

    await page.locator('tr, [data-testid="investor-card"]').filter({ hasText: code })
      .getByRole('button', { name: '重置密码' }).click();
    const dlg = dialogByTitle(page, '重置密码');
    await expect(dlg).toBeVisible();

    // 校验提示走弹窗内 destructive inline Alert（visual-spec §14，#893 L2 S1），
    // 不走 toast；编辑输入即清除，弹窗保持打开且零请求
    const alert = dlg.getByTestId('reset-password-validation-alert');
    await dlg.getByLabel('新密码', { exact: true }).fill('abc');
    await dlg.getByLabel('确认新密码').fill('abc');
    await dlg.getByRole('button', { name: '重置密码' }).click();
    await expect(alert).toBeVisible();
    await expect(alert).toContainText('至少 6 位');
    await expect(dlg).toBeVisible();

    await dlg.getByLabel('新密码', { exact: true }).fill(NEW_PASSWORD);
    await expect(alert).toBeHidden();
    await dlg.getByLabel('确认新密码').fill('e893other@2026');
    await dlg.getByRole('button', { name: '重置密码' }).click();
    await expect(alert).toBeVisible();
    await expect(alert).toContainText('不一致');
    await expect(dlg).toBeVisible();
    expect(requests).toEqual([]);

    await dlg.getByRole('button', { name: '取消' }).click();
    await expect(dlg).toBeHidden();

    const del = await page.request.delete(`/api/investors/${code}`, { headers });
    expect(del.ok()).toBeTruthy();
  });
});