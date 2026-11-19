import { test, expect } from '@playwright/test';
import {
  E2E_ACTIVE,
  E2E_PORT,
  dialogByTitle,
  expectSingleLine,
  gotoPortfolioSubpage,
  nearestTradingDay,
  nextTradingDays,
  openAppAndAuth,
} from './helpers';

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

    // 重置密码入口双端一致（#898 判据沿用）：他人卡有、本人卡无；dialog 共享挂载移动实开
    await expect(viewerCard.getByRole('button', { name: '重置密码' })).toBeVisible();
    await expect(adminCard.getByRole('button', { name: '重置密码' })).toHaveCount(0);
    await viewerCard.getByRole('button', { name: '重置密码' }).click();
    const resetDlg = dialogByTitle(page, '重置密码');
    await expect(resetDlg).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(resetDlg).toBeHidden();
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

/**
 * #907 移动流水列表卡化：调仓/申赎/事件/快照四页移动改流水卡（FlowCard 解剖单点），
 * 桌面表格零变化。门禁形态（SPEC §7）：mobile 断卡容器在 + table 不在 + kv 字段集合与
 * 桌面列一致；chromium 断桌面表格列结构不变 + 卡容器不在。
 * 与 #897 管理页不同：流水页保留 Card/CardHeader（标题/描述不收起），故不做 CardHeader-gone 断言。
 * 数据：调仓/申赎/快照消费 E2E_ACTIVE 种子（已确认场内交易 / 首购 / 2 日快照，只读不推进）；
 * 事件种子不保证存在，经 REST 造一条 pending 现金分红（E2E_PORT draft，零快照 ⇒ 无除息日约束），
 * finally 删除（手法照 share-change-event-cancel.spec.ts::createPendingEvent）。
 */
test.describe('#907 流水页移动卡化形态门禁（双 project）', () => {
  test('调仓流水页：移动流水卡 / 桌面表格', async ({ page }, testInfo) => {
    const isMobile = testInfo.project.name === 'mobile';
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'trades');
    if (isMobile) {
      await expect(page.getByTestId('trade-card-list')).toBeVisible();
      await expect(page.locator('table')).toHaveCount(0);
      const card = page.getByTestId('trade-card').first();
      await expect(card).toBeVisible();
      // kv 字段集合与桌面数值列一致（§6）：金额/份额/手续费/价格
      for (const label of ['金额', '份额', '手续费', '价格']) {
        await expectSingleLine(card.getByText(label, { exact: true }).first(), `调仓卡 kv 标签「${label}」`, { nowrap: true });
      }
      // 日期行可见标签（#907 showLabels）：移动卡无表头/无悬停，交易日/确认日须可见而非仅 title
      await expect(card.getByText('交易日期', { exact: true })).toBeVisible();
      await expect(card.getByText('确认日期', { exact: true })).toBeVisible();
    } else {
      await expect(page.locator('table')).toBeVisible();
      await expect(page.getByTestId('trade-card-list')).toHaveCount(0);
      for (const head of ['产品', '平台', '类型', '金额', '份额', '手续费', '价格', '状态', '操作']) {
        await expect(page.locator('thead').getByText(head, { exact: true })).toBeVisible();
      }
    }
  });

  test('申赎流水页：移动流水卡 / 桌面表格', async ({ page }, testInfo) => {
    const isMobile = testInfo.project.name === 'mobile';
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'subscriptions');
    if (isMobile) {
      await expect(page.getByTestId('subscription-card-list')).toBeVisible();
      await expect(page.locator('table')).toHaveCount(0);
      const card = page.getByTestId('subscription-card').first();
      await expect(card).toBeVisible();
      for (const label of ['金额', '份额', '净值']) {
        await expectSingleLine(card.getByText(label, { exact: true }).first(), `申赎卡 kv 标签「${label}」`, { nowrap: true });
      }
      // 日期行可见标签（#907 showLabels）：申请日/确认日须可见
      await expect(card.getByText('申请日期', { exact: true })).toBeVisible();
      await expect(card.getByText('确认日期', { exact: true })).toBeVisible();
    } else {
      await expect(page.locator('table')).toBeVisible();
      await expect(page.getByTestId('subscription-card-list')).toHaveCount(0);
      for (const head of ['投资人', '平台', '类型', '金额', '份额', '净值', '状态', '操作']) {
        await expect(page.locator('thead').getByText(head, { exact: true })).toBeVisible();
      }
    }
  });

  test('快照流水页：移动流水卡 / 桌面表格，删除仅最新日可用', async ({ page }, testInfo) => {
    const isMobile = testInfo.project.name === 'mobile';
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'snapshots');
    if (isMobile) {
      await expect(page.getByTestId('snapshot-card-list')).toBeVisible();
      await expect(page.locator('table')).toHaveCount(0);
      const card = page.getByTestId('snapshot-card').first();
      await expect(card).toBeVisible();
      for (const label of ['净值', '份额', '市值', '在途']) {
        await expectSingleLine(card.getByText(label, { exact: true }).first(), `快照卡 kv 标签「${label}」`, { nowrap: true });
      }
      // 首卡 = 最新日（倒序 i===0）：删除可用；非最新日禁用 + 连续原则 hint（SnapshotsContent 承重）
      await expect(card.getByRole('button', { name: '删除' })).toBeEnabled();
    } else {
      await expect(page.locator('table')).toBeVisible();
      await expect(page.getByTestId('snapshot-card-list')).toHaveCount(0);
      for (const head of ['日期', '净值', '涨跌', '份额', '市值', '在途', '操作']) {
        await expect(page.locator('thead').getByText(head, { exact: true })).toBeVisible();
      }
    }
  });

  test('份额变动事件页：移动流水卡 / 桌面表格', async ({ page }, testInfo) => {
    const isMobile = testInfo.project.name === 'mobile';
    // 事件种子不保证存在：经 REST 造一条 pending 现金分红（createPendingEvent 手法）
    const headers = await openAppAndAuth(page);
    const products = await (await page.request.get('/api/products?page_size=1', { headers })).json();
    const platforms = await (await page.request.get('/api/platforms?page_size=1', { headers })).json();
    const product = products.items?.[0];
    const platform = platforms.items?.[0];
    if (!product || !platform) test.skip(true, '环境中没有产品/平台数据');
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
    try {
      await gotoPortfolioSubpage(page, E2E_PORT, 'share-change-events');
      // 行 testid 双端一致（桌面 tr / 移动卡），按事件 id 精确区分（两 project 各造一行）
      const card = page.getByTestId(`share-event-${created.id}`);
      await expect(card).toBeVisible({ timeout: 15_000 });
      if (isMobile) {
        await expect(page.getByTestId('event-card-list')).toBeVisible();
        await expect(page.locator('table')).toHaveCount(0);
        // 自动计算型 pending：两列恒 NULL → --（#424 口径，与桌面单元格同值）
        await expect(card.getByTestId('event-shares-change')).toHaveText('--');
        await expect(card.getByTestId('event-cash-change')).toHaveText('--');
        for (const label of ['份额变化', '现金变化']) {
          await expectSingleLine(card.getByText(label, { exact: true }).first(), `事件卡 kv 标签「${label}」`, { nowrap: true });
        }
        // 日期行可见标签（#907 showLabels）：权益登记日/除息日须可见
        await expect(card.getByText('权益登记日', { exact: true })).toBeVisible();
        await expect(card.getByText('除息日', { exact: true })).toBeVisible();
      } else {
        await expect(page.locator('table')).toBeVisible();
        await expect(page.getByTestId('event-card-list')).toHaveCount(0);
        for (const head of ['事件类型', '产品', '平台', '份额变化', '现金变化', '状态', '操作']) {
          await expect(page.locator('thead').getByText(head, { exact: true })).toBeVisible();
        }
      }
    } finally {
      await page.request.delete(`/api/share-change-events/${created.id}`, { headers });
    }
  });
});
