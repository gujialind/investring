import { test, expect } from "@playwright/test";
import { E2E_ACTIVE, collectPageErrors, openPopover, settlePopovers } from "./helpers";

const PRODUCT_PATH = `/portfolio/${E2E_ACTIVE}/product/510300.SH?market=CN_EXCHANGE`;
const CUMULATIVE_HINT = "累计收益含已卖出实现盈亏，与持仓卡「累计收益」（持有收益口径）不同";
const AUTO_SNAPSHOT_HINT = "开启后每个交易日由定时任务自动补齐快照；关闭不影响手动生成";
const PERFORMANCE_HINT = "区间收益与风险指标，历史不足窗口期的区间收益不展示";

const metricHints = [
  ["近 6 月", "以 6 个月前当日或之后首个快照净值为基准；组合成立不足 6 个月时不展示"],
  ["近 1 年", "以 1 年前当日或之后首个快照净值为基准；组合成立不足 1 年时不展示"],
  ["近 3 年", "以 3 年前当日或之后首个快照净值为基准；组合成立不足 3 年时不展示"],
  ["MWR 年化", "资金加权收益率（XIRR）：把每笔申赎按发生时点计入现金流求解年化回报，衡量实际投入的钱赚了多少；短期大额申购会使其大幅失真。"],
  ["最大回撤", "历史最高净值到之后最低点的最大跌幅"],
  ["年化波动率", "日净值收益率的标准差按 252 交易日年化。数值越大净值波动越剧烈。"],
] as const;

test.describe("共享说明触发器（#943 S2）", () => {
  test("产品累计收益说明支持桌面悬停/聚焦与移动点按，触控区至少 44px", async ({ page }, testInfo) => {
    test.setTimeout(testInfo.project.name === "mobile" ? 90_000 : 60_000);
    const errors = collectPageErrors(page);
    await page.goto(PRODUCT_PATH);

    const trigger = page.getByRole("button", { name: "查看累计收益说明" });
    const content = page.getByTestId("info-tip-content");
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(await trigger.getAttribute("aria-controls")).toBeNull();
    await expect(page.getByText(CUMULATIVE_HINT, { exact: true })).toHaveCount(0);

    const box = await trigger.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThanOrEqual(44);
    expect(box!.height).toBeGreaterThanOrEqual(44);

    if (testInfo.project.name === "chromium") {
      await page.evaluate(() => {
        const input = document.createElement("input");
        input.id = "info-tip-focus-probe";
        input.style.position = "fixed";
        input.style.left = "-10000px";
        document.body.append(input);
      });
      const focusProbe = page.locator("#info-tip-focus-probe");
      await focusProbe.focus();
      await trigger.hover();
      await expect(content).toBeVisible();
      await trigger.click();
      await expect(content).toBeVisible();
      const contentId = await trigger.getAttribute("aria-controls");
      expect(contentId).not.toBeNull();
      await expect(content).toHaveAttribute("id", contentId!);
      await expect(content).toHaveAttribute("aria-label", "查看累计收益说明");
      await content.hover();
      await expect(content).toBeVisible();

      await page.getByRole("heading", { name: "产品详情", exact: true }).hover();
      await expect(content).toHaveCount(0);

      await trigger.hover();
      await expect(content).toBeVisible();
      await content.click();
      await trigger.hover();
      await page.getByRole("heading", { name: "产品详情", exact: true }).hover();
      await expect(content).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(content).toHaveCount(0);

      await focusProbe.focus();
      await trigger.hover();
      await expect(content).toBeVisible();
      await page.getByRole("heading", { name: "产品详情", exact: true }).hover();
      await expect(content).toHaveCount(0);
      await expect(focusProbe).toBeFocused();

      await page.keyboard.press("Tab");
      await trigger.focus();
      await expect(content).toBeVisible();
      const shadow = await trigger.evaluate((element) => getComputedStyle(element).boxShadow);
      expect(shadow).not.toBe("none");
      await page.keyboard.press("Enter");
      await expect(content).toHaveCount(0);
      await page.keyboard.press("Tab");
      await trigger.focus();
      await expect(content).toBeVisible();
      await trigger.hover();
      await content.hover();
      await page.getByRole("heading", { name: "产品详情", exact: true }).hover();
      await expect(content).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(content).toHaveCount(0);
      await page.keyboard.press("Tab");
      await trigger.focus();
      await expect(content).toBeVisible();
      await page.keyboard.press("Tab");
      await expect(content).toHaveCount(0);
      await expect(trigger).not.toBeFocused();

      await trigger.focus();
      await expect(content).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(content).toHaveCount(0);
    } else {
      await openPopover(page, trigger, content);
      await content.click();
      await expect(content).toBeVisible();
      await trigger.click();
      await expect(content).toHaveCount(0);

      await openPopover(page, trigger, content);
      await page.getByRole("heading", { name: "产品详情", exact: true }).click();
      await expect(content).toHaveCount(0);
    }

    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("自动快照说明默认收起，点说明文字不误关且不触发开关写入", async ({ page }, testInfo) => {
    test.setTimeout(testInfo.project.name === "mobile" ? 90_000 : 60_000);
    await page.goto(`/portfolio/${E2E_ACTIVE}/snapshots`);

    const toggle = page.getByRole("switch", { name: "自动生成快照" });
    const trigger = page.getByRole("button", { name: "查看自动生成快照说明" });
    const content = page.getByTestId("info-tip-content");
    const label = page.locator('label[for="auto-snapshot-switch"]');
    const labelBox = await label.boundingBox();
    const triggerBox = await trigger.boundingBox();
    expect(labelBox).not.toBeNull();
    expect(triggerBox).not.toBeNull();
    expect(triggerBox!.x).toBeGreaterThanOrEqual(labelBox!.x + labelBox!.width - 1);
    const checked = await toggle.isChecked();
    const updates: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "PUT" && request.url().includes("/api/portfolios/E2E_ACTIVE")) {
        updates.push(request.url());
      }
    });

    await expect(page.getByText(AUTO_SNAPSHOT_HINT, { exact: true })).toHaveCount(0);
    if (testInfo.project.name === "chromium") {
      await trigger.hover();
    } else {
      await openPopover(page, trigger, content);
    }
    await expect(content).toHaveText(AUTO_SNAPSHOT_HINT);
    await content.click();
    await expect(content).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-checked", checked ? "true" : "false");
    expect(updates).toHaveLength(0);

    if (testInfo.project.name === "chromium") {
      await page.keyboard.press("Escape");
    } else {
      await trigger.click();
    }
    await expect(content).toHaveCount(0);
    await settlePopovers(page);
  });

  test("绩效说明与六项指标同交互，持有期和风险提示常显且 320px 不溢出", async ({ page }, testInfo) => {
    test.setTimeout(testInfo.project.name === "mobile" ? 120_000 : 90_000);
    await page.setViewportSize({ width: 320, height: 720 });
    await page.goto(`/portfolio/${E2E_ACTIVE}`);

    await expect(page.getByText(PERFORMANCE_HINT, { exact: true })).toHaveCount(0);
    await expect(page.getByText(/^持有 \d+ 天$/).first()).toBeVisible();
    await expect(page.getByText(/持有期仅 \d+ 天/).first()).toBeVisible();
    const baselineOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );

    const overviewTrigger = page.getByRole("button", { name: "查看绩效指标说明" });
    const content = page.getByTestId("info-tip-content");
    if (testInfo.project.name === "chromium") {
      await overviewTrigger.hover();
    } else {
      await openPopover(page, overviewTrigger, content);
    }
    await expect(content).toHaveText(PERFORMANCE_HINT);
    const box = await content.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(321);
    await page.keyboard.press("Escape");
    await expect(content).toHaveCount(0);
    await settlePopovers(page);

    for (const [label, hint] of metricHints) {
      const trigger = page.getByRole("button", { name: `查看${label}说明` });
      if (testInfo.project.name === "chromium") {
        await trigger.hover();
      } else {
        await openPopover(page, trigger, content);
      }
      await expect(content).toContainText(hint);
      await page.keyboard.press("Escape");
      await expect(content).toHaveCount(0);
      await settlePopovers(page);
    }

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(
      overflow,
      `说明浮层不应新增横向溢出（基线 ${baselineOverflow}px，操作后 ${overflow}px）`,
    ).toBeLessThanOrEqual(baselineOverflow + 1);
  });
});
