/** #878 L2 修后回归：设置角色门控 / 跨 UA 设置子页映射 / 改密弹窗关闭收口 / 收起侧栏可访问名称。
 * 默认 admin 会话（auth.setup）；viewer 用例清凭证后走真实登录流（VIEWER/viewer123 种子）。
 * 改密用例 mock /api/auth/password，不真改共享种子密码。
 */
import { test, expect, type Page } from '@playwright/test';
import { collectPageErrors } from './helpers';

const SECTIONS = [
  { key: 'appearance', title: '外观与主题', tab: '通用' },
  { key: 'datasource', title: '数据源', tab: '通用' },
  { key: 'system', title: '系统信息', tab: '通用' },
  { key: 'calendar', title: '交易日历同步', tab: '交易日历同步' },
  { key: 'logs', title: '日志管理', tab: '日志管理' },
] as const;

/** 换 viewer 会话（沿用 portfolio-delete / cash-flows 的既有形态：清凭证后真实登录流）。
 * 新页默认 about:blank（不透明源），直接 evaluate 读 localStorage 会 SecurityError，
 * 故先落到同源页再清凭证。 */
async function loginAsViewer(page: Page): Promise<void> {
  await page.goto('/');
  await page.context().clearCookies();
  await page.evaluate(() => window.localStorage.clear());
  await page.goto('/login');
  await page.waitForFunction(() => {
    const form = document.querySelector('form');
    return form && Object.keys(form).some((k) => k.startsWith('__reactProps'));
  });
  await page.getByLabel('用户名').fill('VIEWER');
  await page.getByLabel('密码').fill('viewer123');
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForURL(/\/(m\/)?dashboard/, { waitUntil: 'domcontentloaded' });
}

test.describe('设置角色门控（#878 L2 Blocker 1）', () => {
  test('viewer 深链设置首页与钻取子页均被拦截，不渲染管理卡片', async ({ page }) => {
    const errors = collectPageErrors(page, { tolerateLoginRaceNoise: true });
    await loginAsViewer(page);
    // 设置首页（桌面 /settings、移动 /m/settings 由 proxy 分流）+ 两个非 logs 钻取子页：
    // 修复前钻取页只拦 logs，viewer 可直达数据源/日历；修复后整体回退到首页 AdminGuard。
    for (const path of ['/settings', '/m/settings/datasource', '/m/settings/calendar']) {
      await page.goto(path);
      await expect(page.getByText('无权限访问', { exact: true })).toBeVisible();
      await expect(page.getByRole('heading', { name: '设置', exact: true })).toHaveCount(0);
      await expect(page.getByText('数据源', { exact: true })).toHaveCount(0);
    }
    expect(errors).toEqual([]);
  });

  test('viewer 仍可从账户菜单修改密码（设置外全角色入口）', async ({ page }) => {
    await loginAsViewer(page);
    await page.getByRole('button', { name: '用户菜单', exact: true }).click();
    await page.getByRole('menuitem', { name: '修改密码' }).click();
    await expect(
      page.getByRole('dialog').filter({ hasText: '修改您的登录密码' }),
    ).toBeVisible();
  });

  test('admin 五个移动钻取分区保持可达', async ({ page }, testInfo) => {
    test.skip(
      testInfo.project.name !== 'mobile',
      '移动钻取分区仅移动项目（桌面为三 Tab，由跨 UA 映射用例覆盖）',
    );
    for (const s of SECTIONS) {
      await page.goto(`/m/settings/${s.key}`);
      await expect(page.getByRole('link', { name: '返回设置' })).toBeVisible();
      await expect(page.getByRole('heading', { level: 1, name: s.title, exact: true })).toBeVisible();
    }
  });
});

test.describe('跨 UA 设置子页映射（#878 L2 Blocker 2）', () => {
  test('桌面 UA 打开移动设置子页落到 /settings 对应 Tab，不 404', async ({ page }, testInfo) => {
    test.skip(
      testInfo.project.name !== 'chromium',
      '桌面 UA 映射仅桌面项目（移动 UA 保持钻取，由另一条覆盖）',
    );
    for (const s of SECTIONS) {
      await page.goto(`/m/settings/${s.key}`);
      await page.waitForURL(/\/settings\?tab=/);
      await expect(page.getByRole('tab', { name: s.tab, exact: true })).toHaveAttribute(
        'aria-selected',
        'true',
      );
    }
    // 非法 section 落通用 Tab，不 404
    await page.goto('/m/settings/bogus');
    await page.waitForURL(/\/settings\?tab=/);
    await expect(page.getByRole('tab', { name: '通用', exact: true })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  test('移动 UA 打开设置子页保持钻取子页（行为不变）', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '移动钻取仅移动项目');
    for (const s of SECTIONS) {
      await page.goto(`/m/settings/${s.key}`);
      await expect(page.getByRole('link', { name: '返回设置' })).toBeVisible();
      await expect(page.getByRole('heading', { level: 1, name: s.title, exact: true })).toBeVisible();
    }
  });
});

test.describe('改密弹窗关闭收口（#878 L2 Suggestion 3）', () => {
  test('取消后重开表单为空；pending 时取消禁用且 Esc 不关闭', async ({ page }) => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route('**/api/auth/password', async (route) => {
      await gate;
      await route.fulfill({ status: 200, json: { message: '密码修改成功' } });
    });
    await page.goto('/dashboard');
    const openDialog = async () => {
      await page.getByRole('button', { name: '用户菜单', exact: true }).click();
      await page.getByRole('menuitem', { name: '修改密码' }).click();
      await expect(page.getByLabel('原密码', { exact: true })).toBeVisible();
    };
    const fill = async () => {
      await page.getByLabel('原密码', { exact: true }).fill('admin@2026');
      await page.getByLabel('新密码', { exact: true }).fill('test-next-password');
      await page.getByLabel('确认新密码', { exact: true }).fill('test-next-password');
    };

    // 取消 → 重开应为空表单（修复前取消绕过清空逻辑，重开残留）
    await openDialog();
    await fill();
    await page.getByRole('button', { name: '取消', exact: true }).click();
    await expect(page.getByLabel('原密码', { exact: true })).toHaveCount(0);
    await openDialog();
    await expect(page.getByLabel('原密码', { exact: true })).toHaveValue('');
    await expect(page.getByLabel('新密码', { exact: true })).toHaveValue('');
    await expect(page.getByLabel('确认新密码', { exact: true })).toHaveValue('');

    // 提交进入 pending：取消禁用、Esc 不关闭（mutation 不可中止，避免误解为撤销）
    await fill();
    await page.getByRole('button', { name: '修改密码', exact: true }).click();
    await expect(page.getByRole('button', { name: '取消', exact: true })).toBeDisabled();
    await page.keyboard.press('Escape');
    await expect(page.getByLabel('原密码', { exact: true })).toBeVisible();
    release();
    await page.waitForURL(/\/(m\/)?login/);
  });
});

test.describe('收起侧栏导航可访问名称（#878 L2 Suggestion 4）', () => {
  test('收起后主导航链接仍按名称可访问（aria-label，非仅 Tooltip）', async ({ page }, testInfo) => {
    test.skip(
      testInfo.project.name !== 'chromium',
      '桌面侧栏仅桌面项目（移动无侧栏，账户/导航走底部 Tab）',
    );
    await page.goto('/dashboard');
    await page.getByRole('button', { name: '收起侧栏', exact: true }).click();
    for (const label of ['首页', '组合', '产品', '设置']) {
      const link = page.getByRole('link', { name: label, exact: true });
      await expect(link).toBeVisible();
      await expect(link).toHaveAttribute('aria-label', label);
    }
  });
});