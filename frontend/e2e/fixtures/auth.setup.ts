/**
 * Playwright 认证 Setup
 * 登录一次，保存 auth state 供所有测试复用
 */
import { test as setup } from '@playwright/test';

const ADMIN_AUTH_FILE = process.env.E2E_AUTH_FILE || 'e2e/.auth/admin.json';

setup('以管理员身份登录并保存状态', async ({ page }) => {
  // 访问登录页
  await page.goto('/login');

  // 等待 React hydration 完成（form 挂上 __reactProps）后再填表：
  // hydration 前 fill 的值会被 controlled input 重渲染清空，导致静默登录失败
  // （PR #169 遗留问题根因之二，JS 较慢的引擎如 webkit 必现）
  await page.waitForFunction(() => {
    const form = document.querySelector('form');
    return form && Object.keys(form).some((k) => k.startsWith('__reactProps'));
  });

  // 填写登录表单（label 文案为「用户名」，见 app/login/page.tsx）
  await page.getByLabel('用户名').fill('ADMIN');
  await page.getByLabel('密码').fill('admin@2026');

  // 提交登录
  await page.getByRole('button', { name: '登录' }).click();

  // 等待跳转到 dashboard（/m/dashboard 也匹配）。
  // 用 domcontentloaded 而非默认 load：dashboard 流式渲染依赖多个 API，
  // load 事件会被慢请求拖到超时；此处只需 cookie 已写入即可存 storageState
  await page.waitForURL(/\/(m\/)?dashboard/, { timeout: 10_000, waitUntil: 'domcontentloaded' });

  // 保存认证状态
  await page.context().storageState({ path: ADMIN_AUTH_FILE });
});
