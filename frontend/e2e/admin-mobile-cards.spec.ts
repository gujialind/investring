import { test, expect } from '@playwright/test';
import { dialogByTitle, expectSingleLine } from './helpers';

/**
 * #897 方案 A：投资人/平台管理页移动卡片形态门禁（双 project）。
 * 数据说明：只消费种子——投资人 ADMIN（测试管理员）/VIEWER（测试投资人）均无电话/邮箱
 * （承载位断言靠 `--` 兜底），平台 MYCF/HBZQ/TTJJ/ZB（seed_base.py）。不造数、不写库：
 * 编辑/删除只开弹窗后取消，验证操作入口可达与二次确认沿用。
 * 形态断言按 project 分支：mobile 断卡片容器存在 + table 不存在 + 姓名/用户名（平台：名称/代码·类型）
 * 单行 + 卡片内大标题/描述文案不渲染；chromium 断桌面表格列结构不变 + CardHeader 保留（#897 验收断言 5/7 定案口径）。
 * 桌面行集锚点顺序无关（getByRole cell exact；列表顺序非契约，#354 同口径，L2 #901 Blocker 修复）。
 * 邮箱**不做单行断言**（维护者 2026-11-17 定案：允许换两行）。
 */

test('投资人管理页：移动卡片形态 / 桌面表格形态，字段承载与单行门禁 (#897)', async ({ page }, testInfo) => {
  const isMobile = testInfo.project.name === 'mobile';
  await page.goto('/investors');

  if (isMobile) {
    await expect(page).toHaveURL(/\/m\/investors/);
    await expect(page.getByTestId('investor-card-list')).toBeVisible();
    await expect(page.locator('table')).toHaveCount(0);
    // 描述文案与卡片内大标题仅移动收掉（定案②④⑤），桌面保留见 else 分支
    await expect(page.getByText('所有投资人的详细信息')).toHaveCount(0);
    await expect(page.getByText('投资人列表', { exact: true })).toHaveCount(0);

    const adminCard = page.getByTestId('investor-card').filter({ hasText: '测试管理员' });
    await expect(adminCard).toBeVisible();
    await expectSingleLine(adminCard.getByTestId('investor-card-name'), '投资人卡姓名');
    await expectSingleLine(adminCard.getByTestId('investor-card-code'), '投资人卡用户名行');
    // 定案①：用户名带标签，与电话/邮箱同口径；种子无电话/邮箱 → -- 承载位
    await expect(adminCard.getByText('用户名：ADMIN', { exact: true })).toBeVisible();
    await expect(adminCard.getByText('电话：--', { exact: true })).toBeVisible();
    await expect(adminCard.getByText('邮箱：--', { exact: true })).toBeVisible();
    await expect(adminCard.getByText('管理员', { exact: true })).toBeVisible();

    const viewerCard = page.getByTestId('investor-card').filter({ hasText: '测试投资人' });
    await expect(viewerCard).toBeVisible();
    await expect(viewerCard.getByText('投资人', { exact: true })).toBeVisible();
    await expectSingleLine(viewerCard.getByTestId('investor-card-name'), '投资人卡姓名（viewer）');

    // 操作入口可达 + 既有二次确认沿用（只取消，不写库）
    await adminCard.getByRole('button', { name: '编辑' }).click();
    const editDlg = dialogByTitle(page, '编辑投资人');
    await expect(editDlg).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(editDlg).toBeHidden();
    await adminCard.getByRole('button', { name: '删除' }).click();
    const confirmDlg = page.getByRole('alertdialog').filter({ hasText: '移除投资人' });
    await expect(confirmDlg).toBeVisible();
    await confirmDlg.getByRole('button', { name: '取消' }).click();
    await expect(confirmDlg).toBeHidden();
  } else {
    await expect(page.locator('table')).toBeVisible();
    await expect(page.getByTestId('investor-card-list')).toHaveCount(0);
    for (const head of ['用户名', '姓名', '角色', '电话', '邮箱', '操作']) {
      await expect(page.locator('thead').getByText(head, { exact: true })).toBeVisible();
    }
    await expect(page.getByText('所有投资人的详细信息')).toBeVisible();
    await expect(page.getByText('投资人列表', { exact: true })).toBeVisible();
    // 顺序无关定位（#354 口径；列表顺序非契约，L2 #901 Blocker 修复）
    await expect(page.getByRole('cell', { name: 'ADMIN', exact: true })).toBeVisible();
  }
});

test('平台管理页：移动卡片形态 / 桌面表格形态，名称与代码·类型单行门禁 (#897)', async ({ page }, testInfo) => {
  const isMobile = testInfo.project.name === 'mobile';
  await page.goto('/platforms');

  if (isMobile) {
    await expect(page).toHaveURL(/\/m\/platforms/);
    await expect(page.getByTestId('platform-card-list')).toBeVisible();
    await expect(page.locator('table')).toHaveCount(0);
    await expect(page.getByText('所有投资平台')).toHaveCount(0);
    await expect(page.getByText('平台列表', { exact: true })).toHaveCount(0);

    const mycfCard = page.getByTestId('platform-card').filter({ hasText: '蚂蚁财富' });
    await expect(mycfCard).toBeVisible();
    await expectSingleLine(mycfCard.getByTestId('platform-card-name'), '平台卡名称');
    await expectSingleLine(mycfCard.getByTestId('platform-card-meta'), '平台卡代码·类型次行');
    await expect(mycfCard.getByText('MYCF · 第三方平台', { exact: true })).toBeVisible();
    await expect(page.getByTestId('platform-card').filter({ hasText: '华宝证券' })).toBeVisible();

    await mycfCard.getByRole('button', { name: '编辑' }).click();
    const editDlg = dialogByTitle(page, '编辑平台');
    await expect(editDlg).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(editDlg).toBeHidden();
    await mycfCard.getByRole('button', { name: '删除' }).click();
    const confirmDlg = page.getByRole('alertdialog').filter({ hasText: '删除平台' });
    await expect(confirmDlg).toBeVisible();
    await confirmDlg.getByRole('button', { name: '取消' }).click();
    await expect(confirmDlg).toBeHidden();
  } else {
    await expect(page.locator('table')).toBeVisible();
    await expect(page.getByTestId('platform-card-list')).toHaveCount(0);
    for (const head of ['代码', '名称', '类型', '操作']) {
      await expect(page.locator('thead').getByText(head, { exact: true })).toBeVisible();
    }
    await expect(page.getByText('所有投资平台')).toBeVisible();
    await expect(page.getByText('平台列表', { exact: true })).toBeVisible();
    // 顺序无关定位（#354 口径；MySQL 聚簇首行 HBZQ ≠ SQLite 插入首行 MYCF，L2 #901 Blocker 修复）
    await expect(page.getByRole('cell', { name: 'MYCF', exact: true })).toBeVisible();
  }
});