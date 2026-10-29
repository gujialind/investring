/** #797：独立密码会话，双端续期、迟到响应与密码修改。
 * 每条用例新登录，不撤销 setup 或其他用例的会话；不改共享种子密码。
 */
import { test, expect, type Page } from '@playwright/test';
import type { LoginResponse } from '../src/types/auth';

async function installCredential(page: Page, data: LoginResponse) {
  await page.evaluate((credential) => {
    localStorage.setItem('token', credential.token);
    localStorage.setItem('token-expires-at', credential.expires_at);
    localStorage.setItem('auth-storage', JSON.stringify({ state: {
      token: credential.token, expiresAt: credential.expires_at,
      user: credential.user, isAuthenticated: true,
    }, version: 0 }));
    document.cookie = `token=${encodeURIComponent(credential.token)}; path=/; SameSite=Lax`;
  }, data);
}

async function ownSession(page: Page, nearExpiry = true) {
  const response = await page.request.post('/api/auth/login', {
    data: { code: 'ADMIN', password: 'admin@2026' },
  });
  expect(response.ok()).toBeTruthy();
  const data: LoginResponse = await response.json();
  await page.goto('/login');
  if (nearExpiry) data.expires_at = new Date(Date.now() + 3600000).toISOString();
  await installCredential(page, data);
  return data;
}

test.describe('凭证生命周期', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('并发读取共享一次续期，四处登录状态保持一致', async ({ page }) => {
    const original = await ownSession(page);
    let renewals = 0;
    const businessTokens: string[] = [];
    await page.route('**/api/auth/renew', async (route) => {
      renewals++;
      expect(route.request().headers().authorization).toBe(`Bearer ${original.token}`);
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      await route.fulfill({ response });
    });
    page.on('request', (request) => {
      if (/\/api\/(portfolios|investors|subscriptions|trades)(\?|$)/.test(request.url())) {
        businessTokens.push(request.headers().authorization);
      }
    });
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: '首页', exact: true })).toBeVisible();
    const state = await page.evaluate(() => ({
      token: localStorage.getItem('token'), expires: localStorage.getItem('token-expires-at'),
      persisted: JSON.parse(localStorage.getItem('auth-storage')!).state,
    }));
    expect(renewals).toBe(1);
    expect(state.token).not.toBe(original.token);
    expect(state.persisted.token).toBe(state.token);
    expect(state.persisted.expiresAt).toBe(state.expires);
    expect(state.persisted.isAuthenticated).toBe(true);
    expect(Date.parse(state.expires!) - Date.now()).toBeGreaterThan(6 * 86400000);
    const cookie = (await page.context().cookies()).find((item) => item.name === 'token');
    expect(cookie?.value).toBe(state.token);
    expect(cookie!.expires * 1000).toBeCloseTo(Date.parse(state.expires!), -3);
    expect(businessTokens.length).toBeGreaterThan(1);
    expect(businessTokens.every((value) => value === `Bearer ${state.token}`)).toBe(true);
  });

  test('续期暂时失败时继续使用有效旧凭证', async ({ page }) => {
    const original = await ownSession(page);
    await page.route('**/api/auth/renew', (route) => route.fulfill({ status: 503, json: { detail: '暂时不可用' } }));
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: '首页', exact: true })).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBe(original.token);
  });

  test('已撤销会话续期失败后清理状态并返回登录页', async ({ page }) => {
    const original = await ownSession(page);
    const revoked = await page.request.post('/api/auth/logout', { headers: { Authorization: `Bearer ${original.token}` } });
    expect(revoked.ok()).toBeTruthy();
    await page.goto('/dashboard');
    await page.waitForURL(/\/(m\/)?login/);
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull();
    expect((await page.context().cookies()).some((item) => item.name === 'token')).toBe(false);
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('auth-storage')!).state.isAuthenticated)).toBe(false);
  });

  for (const replace of [false, true]) {
    test(`在途续期不能覆盖其他标签页的${replace ? '新登录' : '退出'}`, async ({ page }) => {
      await ownSession(page);
      let started!: () => void;
      const ready = new Promise<void>((resolve) => { started = resolve; });
      let release!: () => void;
      const gate = new Promise<void>((resolve) => { release = resolve; });
      await page.route('**/api/auth/renew', async (route) => {
        const response = await route.fetch();
        expect(response.ok()).toBeTruthy();
        started();
        await gate;
        await route.fulfill({ response });
      });
      await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
      await ready;
      let replacement: LoginResponse | null = null;
      if (replace) {
        const response = await page.request.post('/api/auth/login', { data: { code: 'ADMIN', password: 'admin@2026' } });
        replacement = await response.json();
        await installCredential(page, replacement!);
      } else {
        await page.evaluate(() => {
          localStorage.removeItem('token');
          localStorage.removeItem('token-expires-at');
          localStorage.setItem('auth-storage', JSON.stringify({ state: { token: null, expiresAt: null, user: null, isAuthenticated: false }, version: 0 }));
          document.cookie = 'token=; Max-Age=0; path=/';
        });
      }
      const completed = page.waitForResponse('**/api/auth/renew');
      release();
      await completed;
      await expect(page.getByRole('heading', { name: '首页', exact: true })).toBeVisible();
      expect(await page.evaluate(() => localStorage.getItem('token'))).toBe(replacement?.token ?? null);
      expect(await page.evaluate(() => JSON.parse(localStorage.getItem('auth-storage')!).state.token)).toBe(replacement?.token ?? null);
    });
  }

  test('旧请求迟到的 401 不清除新登录', async ({ page }) => {
    const original = await ownSession(page, false);
    let started!: () => void;
    const ready = new Promise<void>((resolve) => { started = resolve; });
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route(/\/api\/portfolios(\?|$)/, async (route) => {
      if (route.request().headers().authorization !== `Bearer ${original.token}`) {
        await route.continue();
        return;
      }
      started();
      await gate;
      await route.fulfill({ status: 401, json: { detail: '旧会话已失效' } });
    });
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await ready;
    const response = await page.request.post('/api/auth/login', { data: { code: 'ADMIN', password: 'admin@2026' } });
    const replacement: LoginResponse = await response.json();
    await installCredential(page, replacement);
    const completed = page.waitForResponse(/\/api\/portfolios(\?|$)/);
    release();
    await completed;
    await expect(page.getByRole('heading', { name: '首页', exact: true })).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBe(replacement.token);
    await expect(page).toHaveURL(/dashboard/);
  });

  test('无效续期响应保留原凭证且不发送业务请求', async ({ page }) => {
    const original = await ownSession(page);
    let businessRequests = 0;
    await page.route('**/api/auth/renew', (route) => route.fulfill({
      status: 200,
      json: { ...original, token: 'wrong-owner', user: { ...original.user, code: 'OTHER' } },
    }));
    page.on('request', (request) => {
      if (/\/api\/(portfolios|investors|subscriptions|trades)(\?|$)/.test(request.url())) businessRequests++;
    });
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: '首页', exact: true })).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBe(original.token);
    expect(businessRequests).toBe(0);
  });

  test('登出立即返回登录页，迟到回执不打断新登录', async ({ page, isMobile }) => {
    test.skip(isMobile, '移动端没有 Navbar 的退出登录入口；双端存储竞态由续期用例覆盖');
    const original = await ownSession(page, false);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route('**/api/auth/logout', async (route) => {
      expect(route.request().headers().authorization).toBe(`Bearer ${original.token}`);
      const response = await route.fetch();
      expect(response.ok()).toBeTruthy();
      await gate;
      await route.fulfill({ response });
    });
    await page.goto('/dashboard');
    await page.getByRole('button', { name: '用户菜单', exact: true }).click();
    await page.getByRole('menuitem', { name: '退出登录' }).click();
    await page.waitForURL(/\/login/);
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull();
    const otherTab = await page.context().newPage();
    await otherTab.goto('/login');
    await otherTab.waitForFunction(() => {
      const form = document.querySelector('form');
      return form && Object.keys(form).some((key) => key.startsWith('__reactProps'));
    });
    await otherTab.getByLabel('用户名').fill('ADMIN');
    await otherTab.getByLabel('密码').fill('admin@2026');
    await otherTab.getByRole('button', { name: '登录', exact: true }).click();
    await otherTab.waitForURL(/\/dashboard/);
    const replacement = await otherTab.evaluate(() => localStorage.getItem('token'));
    expect(replacement).not.toBe(original.token);
    const completed = page.waitForResponse('**/api/auth/logout');
    release();
    await completed;
    await expect(otherTab.getByRole('heading', { name: '首页', exact: true })).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBe(replacement);
    await expect(otherTab).toHaveURL(/\/dashboard/);
  });

  test('修改自身密码成功后清除本地凭证', async ({ page }) => {
    await ownSession(page, false);
    let writes = 0;
    await page.route('**/api/auth/password', async (route) => {
      writes++;
      expect(route.request().method()).toBe('PUT');
      await route.fulfill({ status: 200, json: { message: '密码修改成功' } });
    });
    await page.goto('/settings');
    await page.getByRole('tab', { name: '修改密码', exact: true }).click();
    await page.getByLabel('原密码', { exact: true }).fill('admin@2026');
    await page.getByLabel('新密码', { exact: true }).fill('test-next-password');
    await page.getByLabel('确认新密码', { exact: true }).fill('test-next-password');
    await page.getByRole('button', { name: '修改密码', exact: true }).click();
    await page.waitForURL(/\/(m\/)?login/);
    expect(writes).toBe(1);
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull();
  });
});
