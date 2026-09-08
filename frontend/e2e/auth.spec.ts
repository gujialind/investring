/**
 * 前端 E2E 测试：认证流程
 */
import { test, expect } from '@playwright/test';

test.describe('登录认证', () => {
  // 本组用例需从未登录态开始，不复用 setup 保存的 storageState
  test.use({ storageState: { cookies: [], origins: [] } });

  test('登录成功应跳转到 dashboard', async ({ page }) => {
    await page.goto('/login');
    // 等 React hydration 完成再填表：hydration 前 fill 的值会被
    // controlled input 重渲染清空（JS 较慢的引擎如 webkit 必现，见 auth.setup.ts）
    await page.waitForFunction(() => {
      const form = document.querySelector('form');
      return form && Object.keys(form).some((k) => k.startsWith('__reactProps'));
    });
    await page.getByLabel('用户名').fill('ADMIN');
    await page.getByLabel('密码').fill('admin@2026');
    await page.getByRole('button', { name: '登录' }).click();
    // waitForURL 而非 toHaveURL：后者内部按 'load' 等导航完成，
    // dashboard 流式渲染依赖多个 API，后端慢时 load 会拖过断言超时
    await page.waitForURL(/\/(m\/)?dashboard/, { waitUntil: 'domcontentloaded' });
  });

  test('登录失败应停留在登录页', async ({ page }) => {
    await page.goto('/login');
    // 同上：等水合完成再填表，确保测的是「错误密码」而非被清空的空输入
    await page.waitForFunction(() => {
      const form = document.querySelector('form');
      return form && Object.keys(form).some((k) => k.startsWith('__reactProps'));
    });
    await page.getByLabel('用户名').fill('ADMIN');
    await page.getByLabel('密码').fill('wrong_password');
    await page.getByRole('button', { name: '登录' }).click();
    await expect(page).toHaveURL(/login/);
  });

  test('未登录访问受保护页应重定向到登录页', async ({ page }) => {
    await page.context().clearCookies();
    await page.goto('/dashboard');
    await expect(page).toHaveURL(/login/);
  });

  // [dogfood] 临时 skip，验证 e2e-morph-expected 标签放行路径，验完即删
  test.skip('dogfood: dummy skip for morph-expected label verification', async ({ page }) => {
    // 此用例不执行，只制造 baseline/candidate 形态差异
  });
});
