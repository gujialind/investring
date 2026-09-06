/**
 * 前端 E2E 测试：弹窗内 DatePicker（防 #191 复发）
 *
 * 背景（issue #191）：modal Dialog 会给 body 置 pointer-events:none（仅 DialogContent
 * 子树恢复 auto），Popover 弹层默认 Portal 到 body 时日历日按钮点击不可达——
 * 点击落在 DialogContent（弹窗保留但日期不写入）或 Overlay（弹窗被整体误关）上。
 * 修复（方案 C）：DialogContent 经 context 暴露 DOM 节点，PopoverContent 在 Dialog 内时
 * 把弹层 Portal 注入 DialogContent。本文件守护该行为不再复发。
 *
 * 数据说明：全部用例经 helpers.ts 按组合 code 直达种子活跃组合 E2E_ACTIVE
 * （#354），不再依赖列表 .first() 的无序命中。E2E_ACTIVE 是种子契约：缺组合即
 * 硬失败，不再优雅 skip。编辑交易用例（用例 5/8）依赖 D4 那笔 pending 场内买入，
 * 故禁止对 E2E_ACTIVE 跑 recalculate/catch-up/generate-next——auto_confirm 会吃掉
 * 该 pending 交易、破坏「编辑按钮可见」契约。用例 4 用 dry_run 探针（零副作用）
 * 取真实 count 后对 UI 做精确单分支断言，不再 OR 弱断言。
 */
import { test, expect, type Page, type Locator } from '@playwright/test';
import { E2E_ACTIVE, authHeaders, dialogByTitle, gotoPortfolioSubpage } from './helpers';

/** 日历中「当月 18 号」按钮（data-day=yyyy-MM-18，当月视图内唯一） */
const DAY_18 = 'button.rdp-day_button[data-day$="-18"]';
const DAY_17 = 'button.rdp-day_button[data-day$="-17"]';

/** DatePicker trigger：占位文案或已选日期 */
function pickerTrigger(dlg: Locator, extra?: RegExp): Locator {
  const pat = extra ?? /选择日期|起始日期|结束日期|申请日期|交易日期|除息日|\d{4}-\d{2}-\d{2}/;
  return dlg.locator('button').filter({ hasText: pat }).first();
}

/** 打开日历并点选当日视图中的某日（day 选择器见 DAY_18/DAY_17），断言日历关闭 */
async function pickDay(page: Page, dlg: Locator, daySelector: string, trigger?: Locator): Promise<Locator> {
  const trig = trigger ?? pickerTrigger(dlg);
  await trig.click();
  await page.locator('button.rdp-day_button').first().waitFor();
  await page.locator(daySelector).click();
  await expect(page.locator('button.rdp-day_button')).toHaveCount(0);
  return trig;
}

test.describe('弹窗内 DatePicker（防 #191 复发）', () => {
  // ---- 用例 1：追平至日期 → 选日写入、「开始追平」启用（issue 原断言 1）----
  test('追平快照弹窗：选日写入且「开始追平」启用', async ({ page }) => {
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'snapshots');
    await page.getByRole('button', { name: '追平至日期' }).click();
    const dlg = dialogByTitle(page, '追平快照');
    await dlg.waitFor();

    const trig = await pickDay(page, dlg, DAY_18);
    await expect(dlg).toBeVisible();
    await expect(trig).toHaveText(/20\d{2}-\d{2}-18/);
    await expect(dlg.getByRole('button', { name: '开始追平' })).toBeEnabled();
  });

  // ---- 用例 2：单日生成 → 选日写入、「预检验证」启用（issue 原断言 2）----
  test('单日生成弹窗：选日写入且「预检验证」启用', async ({ page }) => {
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'snapshots');
    await page.getByRole('button', { name: '单日生成' }).click();
    const dlg = dialogByTitle(page, '生成单日快照');
    await dlg.waitFor();

    const trig = await pickDay(page, dlg, DAY_18);
    await expect(dlg).toBeVisible();
    await expect(trig).toHaveText(/20\d{2}-\d{2}-18/);
    await expect(dlg.getByRole('button', { name: '预检验证' })).toBeEnabled();
  });

  // ---- 用例 3：区间重算 → 起止两日期写入、勾选确认后「提交重算任务」启用
  //      （issue 原断言 3 + 症状 2「Dialog 保留但日期不写入」修复实证）----
  test('区间重算弹窗：起止两日期写入、勾选后「提交重算任务」启用', async ({ page }) => {
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'snapshots');
    await page.getByRole('button', { name: '区间重算' }).click();
    const dlg = dialogByTitle(page, '区间重算快照');
    await dlg.waitFor();

    await pickDay(page, dlg, DAY_17, pickerTrigger(dlg, /起始日期/));
    await expect(dlg).toBeVisible();
    // 写入后 trigger 文案变为日期、占位文案失配，按日期文案断言（locator 惰性求值）
    await expect(dlg.getByRole('button', { name: /20\d{2}-\d{2}-17/ })).toBeVisible();

    await pickDay(page, dlg, DAY_18, pickerTrigger(dlg, /结束日期/));
    await expect(dlg).toBeVisible();
    await expect(dlg.getByRole('button', { name: /20\d{2}-\d{2}-18/ })).toBeVisible();

    await dlg.getByText('我已了解重算将删除区间内全部快照并重新生成').click();
    await expect(dlg.getByRole('button', { name: '提交重算任务' })).toBeEnabled();
  });

  // ---- 用例 4：批量删除 → 起始日期写入、可触发 dry-run 预览（issue 原断言 4）----
  test('批量删除弹窗：起始日期写入、dry-run 预览可触发', async ({ page }) => {
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'snapshots');
    await page.getByRole('button', { name: '批量删除' }).click();
    const dlg = dialogByTitle(page, '批量删除快照');
    await dlg.waitFor();

    await pickDay(page, dlg, DAY_17, pickerTrigger(dlg, /选择起始日期/));
    await expect(dlg).toBeVisible();
    const trigger = dlg.getByRole('button', { name: /20\d{2}-\d{2}-17/ });
    await expect(trigger).toBeVisible();

    // 收紧（#354）：旧断言 OR 两分支恒过、无判别力。改为回读 trigger 实际选中的起始日，
    // 经 API dry_run（零副作用、并发安全）取同一 from_date 的真实快照数，再断言 UI 精确
    // 命中对应单分支——UI 与后端就同一日期达成一致才算预览契约成立。
    const fromDate = (await trigger.innerText()).trim().match(/\d{4}-\d{2}-\d{2}/)![0];
    const headers = await authHeaders(page);
    const probe = await page.request.delete(
      `/api/snapshots/${E2E_ACTIVE}/bulk/${fromDate}?dry_run=true`,
      { headers },
    );
    expect(probe.ok(), `dry_run 预览探针失败 ${probe.status()}`).toBeTruthy();
    const { count } = (await probe.json()) as { count: number };

    await dlg.getByRole('button', { name: '预览影响' }).click();
    if (count > 0) {
      await expect(dlg.getByText(`将删除 ${count} 张快照`)).toBeVisible({ timeout: 10_000 });
    } else {
      await expect(dlg.getByText('该日期及之后无快照可删除')).toBeVisible({ timeout: 10_000 });
    }
  });

  // ---- 用例 5：编辑交易弹窗（pending 交易）选日写入（issue 原断言 5）----
  test('编辑交易弹窗：交易日期选日写入', async ({ page }) => {
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'trades');
    // E2E_ACTIVE 种子契约：D4 有一笔 pending 场内买入（编辑按钮在结对主行=基金腿，
    // CASH 子行无），trade_date > 最新快照日且落在「近1年」过滤窗内 → 必现，缺即硬失败
    const editBtn = page.locator('button[title="编辑"]').first();
    await expect(editBtn).toBeVisible({ timeout: 10_000 });
    await editBtn.click();
    const dlg = dialogByTitle(page, '编辑交易');
    await dlg.waitFor();

    // 交易日期默认预填，改选 18 号
    const trig = await pickDay(page, dlg, DAY_18);
    await expect(dlg).toBeVisible();
    await expect(trig).toHaveText(/20\d{2}-\d{2}-18/);
  });

  // ---- 用例 6：modal={false} 弹窗选日写入不回归（issue 原断言 6；
  //      Task 0.3 实测修正：modal={false} 弹窗选日本来就可用，此处为防回归）----
  test('modal={false} 弹窗：提交交易/申购/事件/现金修正/转移选日写入', async ({ page }, testInfo) => {
    const isMobile = testInfo.project.name === 'mobile';
    // 提交交易（TradesContent L575）
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'trades');
    await page.getByRole('button', { name: '提交交易' }).first().click();
    let dlg = dialogByTitle(page, '提交交易');
    await dlg.waitFor();
    let trig = await pickDay(page, dlg, DAY_18);
    await expect(dlg).toBeVisible();
    await expect(trig).toHaveText(/20\d{2}-\d{2}-18/);
    await page.keyboard.press('Escape');

    // 申购（SubscriptionsContent L376）
    await page.goto(page.url().replace(/\/trades.*$/, '/subscriptions'));
    await page.getByRole('button', { name: /提交申请|首次申购激活/ }).first().click();
    dlg = dialogByTitle(page, /提交申请|首次申购激活/);
    await dlg.waitFor();
    trig = await pickDay(page, dlg, DAY_18);
    await expect(dlg).toBeVisible();
    await expect(trig).toHaveText(/20\d{2}-\d{2}-18/);
    await page.keyboard.press('Escape');

    // 份额变动事件：验证**权益登记日**。该弹窗两个 DatePicker 都预填今日，pickerTrigger 的 .first()
    // 无法区分二者、会随表单字段顺序漂移（#355 把顺序改为 登记日 → 除息日，覆盖会无声换目标），
    // 故按 label 邻近显式定位。DatePicker 无 id prop（表单 htmlFor 悬空），getByLabel 用不了。
    // 移动端无此子路由（app/m/portfolio/[code]/ 下不存在），仅桌面断言
    if (!isMobile) {
      await page.goto(page.url().replace(/\/subscriptions.*$/, '/share-change-events'));
      await page.getByRole('button', { name: '新建事件' }).click();
      dlg = dialogByTitle(page, '新建份额变动事件');
      await dlg.waitFor();
      const entitlementTrigger = dlg
        .getByText('权益登记日', { exact: true })
        .locator('xpath=..')
        .locator('button')
        // 过滤掉 DatePicker 图标 only 的「清除日期」按钮，只留日期触发器
        .filter({ hasText: /\d{4}-\d{2}-\d{2}|选择日期/ })
        .first();
      trig = await pickDay(page, dlg, DAY_18, entitlementTrigger);
      await expect(dlg).toBeVisible();
      await expect(trig).toHaveText(/20\d{2}-\d{2}-18/);
      await page.keyboard.press('Escape');
      await page.goto(page.url().replace(/\/share-change-events.*$/, '/positions'));
    } else {
      await page.goto(page.url().replace(/\/subscriptions.*$/, '/positions'));
    }

    // 持仓页：现金修正 + 平台间现金转移（positions L340/L409）
    // 移动端 positions 为独立实现（m/positions/page.tsx L194 弹窗不同源于桌面）：
    // 「更新非净值资产」触发器是纯图标按钮（RefreshCw），且无现金转移功能
    if (isMobile) {
      await page.locator('button:has(.lucide-refresh-cw)').click();
      dlg = dialogByTitle(page, '更新非净值资产');
      await dlg.waitFor();
      trig = await pickDay(page, dlg, DAY_18);
      await expect(dlg).toBeVisible();
      await expect(trig).toHaveText(/20\d{2}-\d{2}-18/);
      return;
    }
    await page.getByRole('button', { name: '更新非净值资产' }).click();
    dlg = dialogByTitle(page, '更新非净值资产');
    await dlg.waitFor();
    trig = await pickDay(page, dlg, DAY_18);
    await expect(dlg).toBeVisible();
    await expect(trig).toHaveText(/20\d{2}-\d{2}-18/);
    await page.keyboard.press('Escape');

    await page.getByRole('button', { name: '现金转移' }).click();
    dlg = dialogByTitle(page, '平台间现金转移');
    await dlg.waitFor();
    trig = await pickDay(page, dlg, DAY_18);
    await expect(dlg).toBeVisible();
    await expect(trig).toHaveText(/20\d{2}-\d{2}-18/);
  });

  // ---- 用例 9：键盘层级——Esc 逐层关闭与 Tab 焦点不逃逸（评审新增）----
  // Radix react-dismissable-layer 单实例下 layers 栈互通，Esc 只关最高层：第一次关
  // 日历弹层、Dialog 保留（焦点回落到 DialogContent），第二次才关 Dialog。
  // 历史上此处是双实例——react-popover 自带嵌套 dismissable-layer@1.1.11 副本、与
  // react-dialog 用的 hoisted 1.1.19 不同实例，两个 layer 各自认为自己是最高层，
  // 一次 Esc 同关两层（#191 评审时按当时现状断言，并预留了「依赖对齐为单实例后
  // 恢复逐层断言」的迁移条件）。#315：popover 升到 1.1.23 后嵌套副本消失、与 dialog
  // 共用单实例，该条件达成，下面按逐层语义守护。
  test('键盘层级：Esc 逐层关闭弹层、Tab 焦点不逃逸', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '移动端无物理键盘语义，仅桌面项目断言');
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'snapshots');
    await page.getByRole('button', { name: '追平至日期' }).click();
    let dlg = dialogByTitle(page, '追平快照');
    await dlg.waitFor();

    await pickerTrigger(dlg).click();
    await page.locator('button.rdp-day_button').first().waitFor();

    // Esc 1：仅关日历弹层，Dialog 必须保留（layers 栈互通的逐层语义）
    await page.keyboard.press('Escape');
    await expect(page.locator('button.rdp-day_button')).toHaveCount(0);
    await expect(dlg).toBeVisible();

    // Esc 2：弹层已关，此时 Dialog 是最高层，再按才关闭
    await page.keyboard.press('Escape');
    await expect(dlg).toHaveCount(0);

    // Tab 焦点应始终在 Dialog（含注入其中的日历弹层）体系内
    await page.getByRole('button', { name: '追平至日期' }).click();
    dlg = dialogByTitle(page, '追平快照');
    await dlg.waitFor();
    await pickerTrigger(dlg).click();
    await page.locator('button.rdp-day_button').first().waitFor();
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Tab');
      const inside = await page.evaluate(() => {
        const el = document.activeElement;
        return !!el && el !== document.body && !!el.closest('[role="dialog"]');
      });
      expect(inside, `第 ${i + 1} 次 Tab 后焦点逃逸出弹层体系`).toBe(true);
    }
    // Tab 循环结束时日历弹层仍开着：Esc 1 只关弹层、Dialog 保留，Esc 2 才关 Dialog
    await page.keyboard.press('Escape');
    await expect(page.locator('button.rdp-day_button')).toHaveCount(0);
    await expect(dlg).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dlg).toHaveCount(0);
  });

  // ---- 用例 10：弹窗外回归——筛选栏 DateRangePicker 交互不变（issue 原断言 8，
  //      context 为空时保持 body Portal 默认行为）----
  test('弹窗外 DateRangePicker（交易页筛选栏）区间选择不回归', async ({ page }, testInfo) => {
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'trades');
    // 移动端筛选栏默认折叠（TradesContent L752：「筛选」按钮展开）
    if (testInfo.project.name === 'mobile') {
      await page.getByRole('button', { name: '筛选' }).click();
    }
    // 筛选栏 DateRangePicker 默认区间「近1年」（#126）。v10 range 语义下
    // 完整区间再点击只移动端点，故先清空再开弹层选新区间（空草稿首击
    // 得单日区间、再击得完整区间，见 date-range-picker.tsx handleSelect 注释）
    await page.getByRole('button', { name: '清除日期区间' }).click();
    await page.getByRole('button', { name: '交易日期', exact: true }).click();
    await page.locator('button.rdp-day_button').first().waitFor();
    // 桌面双月视图同日期按钮有两个（当月+下月），取当月首个
    await page.locator(DAY_17).first().click();
    await page.locator(DAY_18).first().click();
    await page.getByRole('button', { name: '确定' }).click();
    // 弹层关闭、新区间写入 trigger 即不回归
    await expect(page.locator('button.rdp-day_button')).toHaveCount(0);
    await expect(
      page.getByRole('button').filter({ hasText: /20\d{2}-\d{2}-17 ~ 20\d{2}-\d{2}-18/ }).first()
    ).toBeVisible();
  });
});

// ---- 用例 7：移动端 project 跑用例 1–4（/m/portfolio/[code]/snapshots）----
// 移动端与桌面共用 SnapshotsContent（variant=mobile），上述用例在 mobile project
// 会经 middleware 重定向到 /m 路由，天然双端覆盖；本 describe 仅补移动端专属断言。
test.describe('弹窗内 DatePicker 移动端（防 #191 复发）', () => {
  test('移动端快照页四弹窗选日写入', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '仅移动端项目');
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'snapshots');
    await expect(page).toHaveURL(/\/m\/portfolio\//);

    for (const [triggerName, title, daySel, extra] of [
      ['追平至日期', '追平快照', DAY_18, undefined],
      ['单日生成', '生成单日快照', DAY_18, undefined],
      ['区间重算', '区间重算快照', DAY_17, /起始日期/],
      ['批量删除', '批量删除快照', DAY_17, /选择起始日期/],
    ] as const) {
      await page.getByRole('button', { name: triggerName }).click();
      const dlg = dialogByTitle(page, title);
      await dlg.waitFor();
      await pickDay(page, dlg, daySel, extra ? pickerTrigger(dlg, extra) : undefined);
      await expect(dlg).toBeVisible();
      // 写入后 trigger 文案变为日期、占位文案失配，按日期文案断言
      await expect(dlg.getByRole('button', { name: /20\d{2}-\d{2}-1[78]/ }).first()).toBeVisible();
      // 单日生成弹窗无「取消」按钮，统一 Esc 关闭（日历已关，Esc 只余 Dialog 一层）
      await page.keyboard.press('Escape');
      await expect(dlg).toHaveCount(0);
    }
  });
});

// ---- 用例 8：矮视口可达性（评审新增）：桌面 800×500 / 移动 390×700，
//      四弹窗 + 编辑交易弹窗的执行按钮在视口内可点击 ----
test.describe('弹窗内 DatePicker 矮视口（评审新增）', () => {
  test('矮视口下弹窗执行按钮在视口内可达', async ({ page }, testInfo) => {
    const isMobile = testInfo.project.name === 'mobile';
    await page.setViewportSize(isMobile ? { width: 390, height: 700 } : { width: 800, height: 500 });
    const viewportH = isMobile ? 700 : 500;

    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'snapshots');
    const dialogs: Array<[string, string, string]> = [
      ['追平至日期', '追平快照', '开始追平'],
      ['单日生成', '生成单日快照', '预检验证'],
      ['区间重算', '区间重算快照', '提交重算任务'],
      ['批量删除', '批量删除快照', '预览影响'],
    ];
    for (const [triggerName, title, actionName] of dialogs) {
      await page.getByRole('button', { name: triggerName }).click();
      const dlg = dialogByTitle(page, title);
      await dlg.waitFor();
      // 矮视口下日历弹层可用（#161 max-h/overflow 在 Dialog 内不撑破）
      await pickDay(page, dlg, DAY_18);
      // 执行按钮在视口内
      const action = dlg.getByRole('button', { name: actionName });
      await expect(action).toBeVisible();
      const box = await action.boundingBox();
      expect(box, `${title}「${actionName}」无 boundingBox`).not.toBeNull();
      if (box) {
        expect(box.y, `${title}「${actionName}」上缘超出视口`).toBeGreaterThanOrEqual(0);
        expect(box.y + box.height, `${title}「${actionName}」下缘超出 ${viewportH}px 视口`).toBeLessThanOrEqual(viewportH);
      }
      await page.keyboard.press('Escape');
      if (!isMobile) await expect(dlg).toHaveCount(0);
      else await page.waitForTimeout(300);
    }

    // 编辑交易弹窗（字段最多的弹窗）：E2E_ACTIVE 有 D4 pending 场内买入 → 编辑按钮必现。
    // 断言内容超高时经 DialogContent 内层滚动容器可达（#191 高度兜底），滚动到位后执行
    // 按钮落在视口内。
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'trades');
    const editBtn = page.locator('button[title="编辑"]').first();
    await expect(editBtn).toBeVisible({ timeout: 10_000 });
    await editBtn.click();
    const dlg = dialogByTitle(page, '编辑交易');
    await dlg.waitFor();
    const save = dlg.getByRole('button', { name: '保存修改' });
    await save.scrollIntoViewIfNeeded();
    await expect(save).toBeVisible();
    const box = await save.boundingBox();
    expect(box, '编辑交易「保存修改」无 boundingBox').not.toBeNull();
    if (box) {
      expect(box.y + box.height, `编辑交易「保存修改」下缘超出 ${viewportH}px 视口`).toBeLessThanOrEqual(viewportH);
    }
  });
});
