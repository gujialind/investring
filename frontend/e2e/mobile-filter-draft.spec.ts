import { expect, test, type Locator, type Page } from '@playwright/test';
import {
  E2E_ACTIVE,
  gotoPortfolioSubpage,
  openFilterPanelIfMobile,
  openPopover,
  settlePopovers,
  toISODate,
  type PortfolioSub,
} from './helpers';

const STATUS_CASES = [
  { sub: 'trades', path: '/api/trades', param: 'status', value: 'confirmed', option: '已确认' },
  { sub: 'subscriptions', path: '/api/subscriptions', param: 'status', value: 'confirmed', option: '已确认' },
  { sub: 'share-change-events', path: '/api/share-change-events', param: 'status', value: 'confirmed', option: '已确认' },
  { sub: 'cash-flows', path: `/api/portfolios/${E2E_ACTIVE}/cash-flows`, param: 'status', value: 'in_transit', option: '在途' },
] as const;

function isListRequest(request: import('@playwright/test').Request, path: string): boolean {
  return new URL(request.url()).pathname === path && request.method() === 'GET';
}

async function openSheet(page: Page): Promise<{ trigger: Locator; sheet: Locator }> {
  const trigger = page.getByRole('button', { name: /^筛选/, includeHidden: true });
  await trigger.click();
  const sheet = page.getByRole('dialog', { name: '筛选' });
  await expect(sheet).toBeVisible();
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  const controls = await trigger.getAttribute('aria-controls');
  expect(controls).toBeTruthy();
  await expect(page.locator(`[id="${controls}"]`)).toBeVisible();
  return { trigger, sheet };
}

async function chooseStatus(page: Page, sheet: Locator, option: string): Promise<void> {
  const trigger = sheet.getByRole('combobox').first();
  const target = page.getByRole('option', { name: option, exact: true });
  await openPopover(page, trigger, target);
  await target.click();
  await settlePopovers(page);
  await expect(trigger).toContainText(option);
}

async function chooseQuickRange(page: Page, sheet: Locator, label: string): Promise<void> {
  const trigger = sheet.getByRole('button', { name: /~/ }).first();
  const quick = page.getByRole('button', { name: label, exact: true });
  await openPopover(page, trigger, quick);
  await quick.click();
  await page.getByRole('button', { name: '确定', exact: true }).click();
  await settlePopovers(page);
}

test.describe('移动筛选草稿（#944 D2/A9）', () => {
  for (const item of STATUS_CASES) {
    test(`${item.sub}：修改不请求，应用才提交，取消与重置不污染已应用条件`, async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== 'mobile', '移动端草稿交互，桌面即时应用由单独用例覆盖');
      const initial = page.waitForResponse((response) =>
        new URL(response.url()).pathname === item.path && response.status() === 200,
      );
      await gotoPortfolioSubpage(page, E2E_ACTIVE, item.sub as PortfolioSub);
      await initial;
      await page.clock.install();
      const requests: string[] = [];
      page.on('request', (request) => {
        if (isListRequest(request, item.path)) requests.push(request.url());
      });

      let { trigger, sheet } = await openSheet(page);
      await chooseStatus(page, sheet, item.option);
      await page.clock.fastForward(400);
      expect(requests).toHaveLength(0);

      await sheet.getByRole('button', { name: '取消', exact: true }).click();
      await expect(sheet).toBeHidden();
      await expect(trigger).toBeFocused();
      await page.clock.fastForward(400);
      expect(requests).toHaveLength(0);

      ({ trigger, sheet } = await openSheet(page));
      await expect(sheet.getByRole('combobox').filter({ hasText: '全部状态' })).toBeVisible();
      await chooseStatus(page, sheet, item.option);
      const applied = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return url.pathname === item.path && url.searchParams.get(item.param) === item.value && response.status() === 200;
      });
      await sheet.getByRole('button', { name: '应用筛选', exact: true }).click();
      await applied;
      await expect(sheet).toBeHidden();
      await expect(trigger).toHaveText(/筛选1/);

      if (item.sub === 'trades') {
        ({ trigger, sheet } = await openSheet(page));
        await chooseQuickRange(page, sheet, '近3年');
        const ranged = page.waitForResponse((response) => {
          const url = new URL(response.url());
          return url.pathname === item.path && url.searchParams.get('status') === 'confirmed' &&
            url.searchParams.has('trade_date_start') && response.status() === 200;
        });
        await sheet.getByRole('button', { name: '应用筛选', exact: true }).click();
        await ranged;
        await expect(sheet).toBeHidden();
        await expect(trigger).toHaveText(/筛选2/);
        await page.getByTestId('trade-card').first().getByRole('button', { name: '详情' }).click();
        await expect(page).toHaveURL(/[?&]trade=/);
        await page.goBack();
        await expect(page).not.toHaveURL(/[?&]trade=/);
        await expect(trigger).toHaveText(/筛选2/);
      }

      ({ trigger, sheet } = await openSheet(page));
      await expect(sheet.getByRole('combobox').filter({ hasText: item.option })).toBeVisible();
      await sheet.getByRole('button', { name: '重置', exact: true }).click();
      await expect(sheet.getByRole('combobox').filter({ hasText: '全部状态' })).toBeVisible();
      await page.clock.fastForward(400);
      expect(requests).toHaveLength(item.sub === 'trades' ? 2 : 1);
      await sheet.getByRole('button', { name: '应用筛选', exact: true }).click();
      await expect(sheet).toBeHidden();
      await expect(trigger).toHaveText('筛选');
      const reopened = await openSheet(page);
      await expect(reopened.sheet.getByRole('combobox').filter({ hasText: '全部状态' })).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(reopened.sheet).toBeHidden();
    });
  }

  test('snapshots：日期草稿取消后恢复，应用才生成新区间请求', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '移动端草稿交互，桌面即时应用由单独用例覆盖');
    const path = `/api/snapshots/portfolios/${E2E_ACTIVE}/list`;
    const initial = page.waitForResponse((response) => new URL(response.url()).pathname === path && response.status() === 200);
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'snapshots');
    await initial;
    await page.clock.install();
    const requests: string[] = [];
    page.on('request', (request) => {
      if (isListRequest(request, path)) requests.push(request.url());
    });
    let { sheet } = await openSheet(page);
    const originalLabel = await sheet.getByRole('button', { name: /~/ }).first().textContent();
    await chooseQuickRange(page, sheet, '近7天');
    await page.clock.fastForward(400);
    expect(requests).toHaveLength(0);
    await sheet.getByRole('button', { name: '取消', exact: true }).click();
    await expect(sheet).toBeHidden();

    ({ sheet } = await openSheet(page));
    await expect(sheet.getByRole('button', { name: /~/ }).first()).toHaveText(originalLabel ?? '');
    await chooseQuickRange(page, sheet, '近7天');
    const applied = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === path && url.searchParams.has('start_date') && url.searchParams.has('end_date') && response.status() === 200;
    });
    await sheet.getByRole('button', { name: '应用筛选', exact: true }).click();
    await applied;
    await expect(sheet).toBeHidden();
    expect(requests).toHaveLength(1);
  });

  test('关闭、遮罩与 Esc 均放弃草稿，重开恢复已应用状态', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '移动端草稿交互，桌面即时应用由单独用例覆盖');
    test.setTimeout(120_000);
    const path = '/api/trades';
    const initial = page.waitForResponse((response) => new URL(response.url()).pathname === path && response.status() === 200);
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'trades');
    await initial;
    await page.clock.install();
    const requests: string[] = [];
    page.on('request', (request) => {
      if (isListRequest(request, path)) requests.push(request.url());
    });

    for (const method of ['取消', '关闭', 'Esc', '遮罩'] as const) {
      const { sheet } = await openSheet(page);
      await chooseStatus(page, sheet, '待确认');
      if (method === '取消') await sheet.getByRole('button', { name: '取消', exact: true }).click();
      else if (method === '关闭') await sheet.getByRole('button', { name: '关闭', exact: true }).click();
      else if (method === 'Esc') await page.keyboard.press('Escape');
      else await page.mouse.click(5, 5);
      await expect(sheet).toBeHidden();
      await page.clock.fastForward(400);
      expect(requests, `${method} 不得提交草稿`).toHaveLength(0);
      const reopened = await openSheet(page);
      await expect(reopened.sheet.getByRole('combobox').filter({ hasText: '全部状态' })).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(reopened.sheet).toBeHidden();
    }
  });

  test('倒置日期选择被归一为有效区间，应用前不发送列表请求', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', '移动端草稿交互，桌面即时应用由单独用例覆盖');
    const path = '/api/trades';
    const initial = page.waitForResponse((response) => new URL(response.url()).pathname === path && response.status() === 200);
    await gotoPortfolioSubpage(page, E2E_ACTIVE, 'trades');
    await initial;
    await page.clock.install();
    const requests: string[] = [];
    page.on('request', (request) => {
      if (isListRequest(request, path)) requests.push(request.url());
    });
    const { sheet } = await openSheet(page);
    await sheet.getByRole('button', { name: '清除日期区间', exact: true }).click();
    const dateTrigger = sheet.getByRole('button', { name: '交易日期', exact: true });
    const firstDay = page.locator('button.rdp-day_button').first();
    await openPopover(page, dateTrigger, firstDay);
    const now = new Date();
    const later = toISODate(new Date(now.getFullYear(), now.getMonth(), 20));
    const earlier = toISODate(new Date(now.getFullYear(), now.getMonth(), 10));
    await page.locator(`button.rdp-day_button[data-day="${later}"]`).click();
    await page.locator(`button.rdp-day_button[data-day="${earlier}"]`).click();
    await page.getByRole('button', { name: '确定', exact: true }).click();
    await settlePopovers(page);
    await expect(sheet.getByRole('button', { name: `${earlier} ~ ${later}`, exact: true })).toBeVisible();
    await expect(sheet.getByRole('alert')).toHaveCount(0);
    await expect(sheet.getByRole('button', { name: '应用筛选', exact: true })).toBeEnabled();
    await page.clock.fastForward(400);
    expect(requests).toHaveLength(0);
    const applied = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === path && url.searchParams.get('trade_date_start') === earlier &&
        url.searchParams.get('trade_date_end') === later && response.status() === 200;
    });
    await sheet.getByRole('button', { name: '应用筛选', exact: true }).click();
    await applied;
    await expect(sheet).toBeHidden();
  });
});

test('桌面状态筛选仍即时应用，不出现草稿 sheet', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', '桌面即时筛选，移动草稿由移动用例覆盖');
  const path = '/api/trades';
  const initial = page.waitForResponse((response) => new URL(response.url()).pathname === path && response.status() === 200);
  await gotoPortfolioSubpage(page, E2E_ACTIVE, 'trades');
  await initial;
  await openFilterPanelIfMobile(page, testInfo);
  const query = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === path && url.searchParams.get('status') === 'confirmed' && response.status() === 200;
  });
  const trigger = page.getByRole('combobox').filter({ hasText: '全部状态' });
  const option = page.getByRole('option', { name: '已确认', exact: true });
  await openPopover(page, trigger, option);
  await option.click();
  await query;
  await expect(page.getByRole('dialog', { name: '筛选' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '应用筛选', exact: true })).toHaveCount(0);
});
