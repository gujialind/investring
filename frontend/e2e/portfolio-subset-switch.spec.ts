import { test, expect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { E2E_ACTIVE, authHeaders, collectPageErrors, gotoPortfolioDetail } from './helpers';

/**
 * #915 B2 页面级子集快捷切换（双端）：分区头下切换 chip 行 + URL ?sub= 持久化，
 * 不写回 display_config（SPEC §5.2/§7.3：切换后分组口径变化、零写请求、刷新保持）。
 *
 * 数据说明（种子契约，backend/tests/seed_base.py）：
 * - E2E_ACTIVE：510300.SH（中国/平衡/大盘/综合）+ 现金。股票分区默认组合键
 *   「中国·综合」（D3 region,segment）；切风格→「平衡」、规模→「大盘」、
 *   风格+规模→「平衡·大盘」。现金无规则行 → 不渲染切换行。
 * - 全部用例对共享种子只读：切换只写 URL（router.replace），「不写回配置」以
 *   请求监听（零组合写请求）+ API 回读 display_config 双重钉住。
 * - 「显式配置 + 覆盖优先级」用例经 page.route 给详情响应注入 display_config
 *   （读侧 mock，先例 display-config-dialog.spec.ts 的规则收紧 mock）；
 *   后端零写入，mock 须装于首次导航前（react-query 缓存命中后 route 不生效）。
 * - 断言为组名/aria-pressed/URL 关系式，不硬绑定快照数字；两端共用组件，mobile 同跑。
 */

/** 股票分区切换行与 chip（组件侧 data-testid 锚点，#382 口径） */
function switchRow(page: Page, classCode = 'ASSET_STOCK'): Locator {
  return page.getByTestId(`sub-dim-switch-${classCode}`);
}
function chip(page: Page, dim: string, classCode = 'ASSET_STOCK'): Locator {
  return page.getByTestId(`sub-dim-chip-${classCode}-${dim}`);
}
/** 股票分区第一个维度组 chip 头（E2E_ACTIVE 单产品，切换后仍恰一组） */
function stockGroupHeader(page: Page): Locator {
  return page.getByTestId('asset-group-header').first();
}

/** 监听组合写请求（不写回配置的请求侧防线） */
function collectPortfolioWrites(page: Page): string[] {
  const writes: string[] = [];
  page.on('request', (r) => {
    if (['PUT', 'POST', 'PATCH', 'DELETE'].includes(r.method()) && r.url().includes('/api/portfolios')) {
      writes.push(`${r.method()} ${r.url()}`);
    }
  });
  return writes;
}

/** mobile webkit 点击 stable 等待按帧率计费（#763），长链路按 project 放宽预算 */
function mobileTimeout(testInfo: TestInfo, ms: number): void {
  if (testInfo.project.name === 'mobile') test.setTimeout(ms);
}

/** 移动端 sticky 页头/底部导航可能盖住视口边缘的 chip：显式滚到视口中央再点
 * （先例 display-config-dialog.spec.ts::openDisplayConfigDialog） */
async function clickChip(page: Page, dim: string, classCode = 'ASSET_STOCK'): Promise<void> {
  const target = chip(page, dim, classCode);
  await target.evaluate((el) => el.scrollIntoView({ block: 'center' }));
  await target.click();
}

test.describe('#915 B2 子集快捷切换（双端）', () => {
  test('默认态：切换行枚举非禁止维、默认项激活、URL 无 sub；现金无切换行', async ({ page }) => {
    const errors = collectPageErrors(page);
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(page.getByTestId('holdings-view-tabs')).toBeVisible({ timeout: 10_000 });

    const row = switchRow(page);
    await expect(row).toBeVisible();
    // 股票四维全登记（required×3 + optional×1）+ 默认项
    for (const dim of ['region', 'style', 'size', 'segment']) {
      await expect(chip(page, dim)).toBeVisible();
      await expect(chip(page, dim)).toHaveAttribute('aria-pressed', 'false');
    }
    await expect(chip(page, 'default')).toHaveAttribute('aria-pressed', 'true');
    expect(page.url()).not.toContain('sub=');
    // 默认组合键（D3）不受切换行影响
    await expect(stockGroupHeader(page)).toContainText('中国·综合');
    // 现金 0 维：不渲染切换行（平铺语义不变）
    await expect(switchRow(page, 'ASSET_CASH')).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('快捷切换单维：分组口径变化、URL 写入、零写请求、配置不写回', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 90_000);
    const errors = collectPageErrors(page);
    const writes = collectPortfolioWrites(page);
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(switchRow(page)).toBeVisible({ timeout: 10_000 });
    const headers = await authHeaders(page);

    await clickChip(page, 'style');
    // 分组口径：组合键「中国·综合」→ 风格单维「平衡」
    await expect(stockGroupHeader(page)).toContainText('平衡');
    await expect(stockGroupHeader(page)).not.toContainText('中国·综合');
    await expect(chip(page, 'style')).toHaveAttribute('aria-pressed', 'true');
    await expect(chip(page, 'default')).toHaveAttribute('aria-pressed', 'false');
    await expect(page).toHaveURL(/[?&]sub=ASSET_STOCK\.style/);

    // 不写回配置：零组合写请求 + 后端 display_config 仍为 null（种子未配置）
    expect(writes).toEqual([]);
    const resp = await page.request.get(`/api/portfolios/${E2E_ACTIVE}`, { headers });
    await expect(resp).toBeOK();
    expect(((await resp.json()) as { display_config: unknown }).display_config).toBeNull();
    expect(errors).toEqual([]);
  });

  test('页面直切子集：多选成组合键、取消回退、取消最后一维回默认', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 120_000);
    const errors = collectPageErrors(page);
    const writes = collectPortfolioWrites(page);
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(switchRow(page)).toBeVisible({ timeout: 10_000 });

    // 默认态首击 = 快捷切换到该单维（不是在默认子集 region,segment 上追加）
    await clickChip(page, 'style');
    await expect(stockGroupHeader(page)).toContainText('平衡');
    // 覆盖态再点 = 勾选追加 → 风格·规模组合键（全序 style→size）
    await clickChip(page, 'size');
    await expect(stockGroupHeader(page)).toContainText('平衡·大盘');
    await expect(page).toHaveURL(/[?&]sub=ASSET_STOCK\.style\.size/);
    await expect(chip(page, 'style')).toHaveAttribute('aria-pressed', 'true');
    await expect(chip(page, 'size')).toHaveAttribute('aria-pressed', 'true');
    // 取消一维 → 回单维
    await clickChip(page, 'size');
    await expect(stockGroupHeader(page)).toContainText('平衡');
    await expect(page).toHaveURL(/[?&]sub=ASSET_STOCK\.style(?![.\w])/);
    // 取消最后一维 = 回默认（Q8 不支持强制平铺）：sub 参数移除、默认项激活
    await clickChip(page, 'style');
    await expect(stockGroupHeader(page)).toContainText('中国·综合');
    await expect(page).toHaveURL((url) => !url.search.includes('sub='));
    await expect(chip(page, 'default')).toHaveAttribute('aria-pressed', 'true');
    expect(writes).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('默认项恢复组合配置/默认子集分组', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 90_000);
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(switchRow(page)).toBeVisible({ timeout: 10_000 });

    await clickChip(page, 'region');
    await expect(stockGroupHeader(page)).toContainText('中国');
    await expect(page).toHaveURL(/[?&]sub=ASSET_STOCK\.region/);
    await clickChip(page, 'default');
    await expect(stockGroupHeader(page)).toContainText('中国·综合');
    await expect(page).toHaveURL((url) => !url.search.includes('sub='));
    await expect(chip(page, 'default')).toHaveAttribute('aria-pressed', 'true');
  });

  test('URL 持久化：刷新保持；深链直达；非法条目防御回默认', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 120_000);
    const errors = collectPageErrors(page);
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(switchRow(page)).toBeVisible({ timeout: 10_000 });

    // 切换后刷新保持（URL 驱动，与 ?view= 先例同构）
    await clickChip(page, 'style');
    await expect(page).toHaveURL(/[?&]sub=ASSET_STOCK\.style/);
    await page.reload();
    await expect(stockGroupHeader(page)).toContainText('平衡', { timeout: 10_000 });
    await expect(chip(page, 'style')).toHaveAttribute('aria-pressed', 'true');

    // 深链直达（gotoPortfolioDetail 收窄为种子字面量，深链直导航 + 同款就绪信号）
    await page.goto(`/portfolio/${E2E_ACTIVE}?sub=ASSET_STOCK.size`);
    await page
      .getByRole('heading', { level: 1 })
      .first()
      .waitFor({ state: 'visible', timeout: 15_000 });
    await expect(stockGroupHeader(page)).toContainText('大盘', { timeout: 10_000 });
    await expect(chip(page, 'size')).toHaveAttribute('aria-pressed', 'true');

    // 非法条目（未知维度）整条丢弃 → 回默认组合键，不抛错不白屏
    await page.goto(`/portfolio/${E2E_ACTIVE}?sub=ASSET_STOCK.bogus`);
    await page
      .getByRole('heading', { level: 1 })
      .first()
      .waitFor({ state: 'visible', timeout: 15_000 });
    await expect(stockGroupHeader(page)).toContainText('中国·综合', { timeout: 10_000 });
    await expect(chip(page, 'default')).toHaveAttribute('aria-pressed', 'true');
    expect(errors).toEqual([]);
  });

  test('与 ?view= 共存：切按平台保留 sub，切回按产品仍生效', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 120_000);
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(switchRow(page)).toBeVisible({ timeout: 10_000 });

    await clickChip(page, 'style');
    await expect(page).toHaveURL(/[?&]sub=ASSET_STOCK\.style/);
    await page.getByTestId('holdings-tab-platform').click();
    await expect(page).toHaveURL(/[?&]view=platform/);
    await expect(page).toHaveURL(/[?&]sub=ASSET_STOCK\.style/);
    // 按平台视图无分区结构 → 切换行不渲染
    await expect(switchRow(page)).toHaveCount(0);
    await page.getByTestId('holdings-tab-product').click();
    await expect(stockGroupHeader(page)).toContainText('平衡');
    await expect(chip(page, 'style')).toHaveAttribute('aria-pressed', 'true');
  });

  test('显式配置组合：覆盖优先于配置、默认项回配置口径（读侧 mock，零写入）', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 120_000);
    const errors = collectPageErrors(page);
    const writes = collectPortfolioWrites(page);
    // 读侧 mock 须在首次导航前装好：给详情响应注入 display_config（股票=风格单维），
    // 后端零写入（先例：display-config-dialog.spec.ts 规则收紧 mock）
    await page.route(/\/api\/portfolios\/E2E_ACTIVE(\?|$)/, async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const resp = await route.fetch();
      const json = (await resp.json()) as Record<string, unknown>;
      json.display_config = { ASSET_STOCK: 'style' };
      const headers = { ...resp.headers() };
      delete headers['content-length'];
      delete headers['content-encoding'];
      await route.fulfill({ status: resp.status(), headers, json });
    });
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(switchRow(page)).toBeVisible({ timeout: 10_000 });

    // 默认态 = 跟随显式配置（风格单维「平衡」），默认项激活
    await expect(stockGroupHeader(page)).toContainText('平衡');
    await expect(chip(page, 'default')).toHaveAttribute('aria-pressed', 'true');
    // 覆盖优先于配置：切规模 → 「大盘」
    await clickChip(page, 'size');
    await expect(stockGroupHeader(page)).toContainText('大盘');
    await expect(page).toHaveURL(/[?&]sub=ASSET_STOCK\.size/);
    // 默认项 = 回配置口径（不是回 D3 默认表）
    await clickChip(page, 'default');
    await expect(stockGroupHeader(page)).toContainText('平衡');
    await expect(page).toHaveURL((url) => !url.search.includes('sub='));
    expect(writes).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('失效覆盖不死胡同：规则收紧后 URL 覆盖含未登记维——按默认态渲染、首击重建子集（ocr r1 反例）', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 120_000);
    const errors = collectPageErrors(page);
    const writes = collectPortfolioWrites(page);
    // 读侧 mock 摘掉股票 style 登记（先例 display-config-dialog.spec.ts 规则收紧），
    // 深链覆盖 ?sub=ASSET_STOCK.style 遂含未登记维：消费侧整条回退默认
    await page.route(/\/api\/asset-classifications(\?|$)/, async (route) => {
      const resp = await route.fetch();
      const json = (await resp.json()) as {
        dimension_rules?: Record<string, Record<string, string>>;
      };
      if (json.dimension_rules?.ASSET_STOCK) delete json.dimension_rules.ASSET_STOCK.style;
      const headers = { ...resp.headers() };
      delete headers['content-length'];
      delete headers['content-encoding'];
      await route.fulfill({ status: resp.status(), headers, json });
    });
    await page.goto(`/portfolio/${E2E_ACTIVE}?sub=ASSET_STOCK.style`);
    await page
      .getByRole('heading', { level: 1 })
      .first()
      .waitFor({ state: 'visible', timeout: 15_000 });

    // 分组回退默认组合键；切换行按默认态渲染（默认项激活），失效维不枚举
    await expect(stockGroupHeader(page)).toContainText('中国·综合', { timeout: 10_000 });
    await expect(chip(page, 'default')).toHaveAttribute('aria-pressed', 'true');
    await expect(chip(page, 'style')).toHaveCount(0);
    // 首击已登记维 = 重建子集（不是追加到失效覆盖上）：分组立即生效、URL 替换
    await clickChip(page, 'region');
    await expect(stockGroupHeader(page)).toContainText('中国');
    await expect(stockGroupHeader(page)).not.toContainText('中国·综合');
    await expect(page).toHaveURL(/[?&]sub=ASSET_STOCK\.region(?![.\w])/);
    await expect(chip(page, 'region')).toHaveAttribute('aria-pressed', 'true');
    expect(writes).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('连点不丢更新：两次快速点击同步累积（ocr r2 竞态反例）', async ({ page }, testInfo) => {
    mobileTimeout(testInfo, 90_000);
    const errors = collectPageErrors(page);
    await gotoPortfolioDetail(page, E2E_ACTIVE);
    await expect(switchRow(page)).toBeVisible({ timeout: 10_000 });

    // 不等 URL/重渲染收敛就连点两维：旧渲染期快照合并会丢首写（终态只剩 size）
    await clickChip(page, 'style');
    await clickChip(page, 'size');
    await expect(page).toHaveURL(/[?&]sub=ASSET_STOCK\.style\.size/);
    await expect(stockGroupHeader(page)).toContainText('平衡·大盘');
    await expect(chip(page, 'style')).toHaveAttribute('aria-pressed', 'true');
    await expect(chip(page, 'size')).toHaveAttribute('aria-pressed', 'true');
    // 再连点「默认」+ 单维：清除与重建也不互相丢失
    await clickChip(page, 'default');
    await clickChip(page, 'region');
    await expect(page).toHaveURL(/[?&]sub=ASSET_STOCK\.region(?![.\w])/);
    await expect(stockGroupHeader(page)).toContainText('中国');
    expect(errors).toEqual([]);
  });
});