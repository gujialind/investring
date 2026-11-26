import { test, expect } from "@playwright/test";
import { E2E_ACTIVE, portfolioPath } from "./helpers";

const portfolioApi = /\/api\/portfolios\/E2E_ACTIVE$/;
const portfolioName = "E2E 活跃组合";

test.describe("组合子页面包屑（#943 S1）", () => {
  test("长组合名完整可访问、href 仍用 code，窄栏不产生页面横向溢出", async ({ page }, testInfo) => {
    const longName = "这是一个用于验证组合子页面包屑长名称换行与完整可访问语义的家庭投资组合";
    await page.route(portfolioApi, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.name = longName;
      await route.fulfill({ response, json: body });
    });

    await page.goto(portfolioPath(E2E_ACTIVE, "trades"));
    const crumb = page.getByRole("link", { name: "返回组合详情" });
    await expect(crumb).toHaveText(longName);
    const prefix = testInfo.project.name === "mobile" ? "/m" : "";
    await expect(crumb).toHaveAttribute("href", `${prefix}/portfolio/${E2E_ACTIVE}`);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow, "长组合名应换行而不是撑破页面").toBeLessThanOrEqual(1);
  });

  test("组合查询加载中不渲染 code 标签，完成后显示真实名称", async ({ page }) => {
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(portfolioApi, async (route) => {
      await gate;
      await route.continue();
    });

    await page.goto(portfolioPath(E2E_ACTIVE, "trades"));
    await expect(page.getByTestId("page-header")).toHaveCount(0);
    await expect(page.getByText(E2E_ACTIVE, { exact: true })).toHaveCount(0);

    release();
    await expect(page.getByRole("link", { name: "返回组合详情" })).toHaveText(portfolioName);
  });

  test("组合查询失败进入可重试失败态，不伪装 code 或空业务数据", async ({ page }) => {
    let fail = true;
    await page.route(portfolioApi, async (route) => {
      if (fail) {
        return route.fulfill({
          status: 404,
          json: { detail: `组合 ${E2E_ACTIVE} 不存在` },
        });
      }
      return route.continue();
    });

    await page.goto(portfolioPath(E2E_ACTIVE, "trades"));
    await expect(page.getByText(`加载失败：组合 ${E2E_ACTIVE} 不存在`)).toBeVisible();
    await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
    await expect(page.getByText(E2E_ACTIVE, { exact: true })).toHaveCount(0);
    await expect(page.getByText("暂无交易记录")).toHaveCount(0);

    fail = false;
    await page.getByRole("button", { name: "重试" }).click();
    await expect(page.getByRole("link", { name: "返回组合详情" })).toHaveText(portfolioName);
  });
});
