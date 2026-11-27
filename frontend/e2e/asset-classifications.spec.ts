import { test, expect, type Page } from "@playwright/test";
import {
  collectPageErrors,
  dialogByTitle,
  dialogTestViewports,
  expectDialogSingleScroll,
} from "./helpers";

/**
 * #910 资产分类「新建/编辑维度值」弹窗回归（SPEC S4 新增落点）。
 *
 * 只承载本次必要覆盖：DialogContent 外层限高/overflow 移除后的单滚动容器契约
 * （四档视口）、原生选择框/适用关联/禁用与提交状态、真实 UI + 精确接口 mock 的
 * 新建/编辑提交链路。不扩展矩阵管理全量测试。
 *
 * 数据边界：字典读取走真实种子（ASSET_STOCK 股票恒存在）；写操作全部 route mock
 * （POST/PUT /api/asset-classifications），不修改共享种子分类的规则或排序。
 * 表单均为原生 <select>（selectOption），无 Popover/DatePicker 场景。
 * 两端共用 AssetClassificationsContent，mobile project 经 proxy 落 /m 前缀同跑。
 */

/** 打开「新建维度值」弹窗（页头主动作，双端同名） */
async function openCreateDialog(page: Page) {
  await page.goto("/asset-classifications");
  const trigger = page.getByRole("button", { name: "新建维度值" });
  await expect(trigger).toBeVisible({ timeout: 15_000 });
  await trigger.click();
  return dialogByTitle(page, "新建维度值");
}

test.describe("资产分类弹窗滚动契约（#910）", () => {
  test("新建：默认与大类长表单仅内层滚动，原生控件与适用关联可用（四档视口）", async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    await page.goto("/asset-classifications");
    await expect(page.getByRole("button", { name: "新建维度值" })).toBeVisible({ timeout: 15_000 });
    if (testInfo.project.name === "chromium") {
      const row = page.getByRole("row").filter({ hasText: "ASSET_STOCK" });
      await expect(row.getByText("启用", { exact: true })).toHaveClass(/\bbg-muted\b/);
    } else {
      const card = page.getByTestId("asset-class-card").filter({ hasText: "ASSET_STOCK" });
      await expect(card.getByText("启用", { exact: true })).toHaveCount(0);
    }
    for (const vp of dialogTestViewports(testInfo, page)) {
      await page.setViewportSize(vp);
      const label = `新建维度值 ${vp.width}×${vp.height}`;
      await page.getByRole("button", { name: "新建维度值" }).click();
      const dlg = dialogByTitle(page, "新建维度值");
      // 默认 region 表单：外层不裁剪不滚动（须在自动滚动动作前断言）
      await expectDialogSingleScroll(dlg, `${label} 默认表单`);
      // 非大类维度未选适用大类：前置提示 + 提交禁用
      await expect(dlg.getByText("非大类维度必须选择至少一个适用大类")).toBeVisible();
      await expect(dlg.getByRole("button", { name: "创建", exact: true })).toBeDisabled();
      await dlg.getByRole("button", { name: "股票", exact: true }).click();
      await expect(dlg.getByRole("button", { name: "创建", exact: true })).toBeEnabled();

      // 切大类维度：四条原生规则 select 展开为长表单，整体滚动只归内层
      await dlg.locator("select#ac-dimension").selectOption("asset_class");
      const ruleSelects = dlg.locator("select:not([id])");
      await expect(ruleSelects).toHaveCount(4);
      await ruleSelects.first().selectOption("required");
      await expect(ruleSelects.first()).toHaveValue("required");
      await expectDialogSingleScroll(dlg, `${label} 大类长表单`, {
        bottomAction: dlg.getByRole("button", { name: "创建", exact: true }),
      });
      await expect(dlg.getByRole("button", { name: "Close" })).toBeVisible();
      // 不提交（共享种子不可改），Escape 关闭
      await page.keyboard.press("Escape");
      await expect(dlg).toHaveCount(0);
    }
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });
});

test.describe("资产分类提交链路（#910，真实 UI + 精确 mock）", () => {
  test("新建非大类维度值：拒绝保留表单与提示，成功关闭并断请求体", async ({ page }) => {
    const errors = collectPageErrors(page);
    let reject = true;
    const bodies: Record<string, unknown>[] = [];
    await page.route(/\/api\/asset-classifications(\?|$)/, (route) => {
      if (route.request().method() !== "POST") return route.continue();
      const body = route.request().postDataJSON();
      bodies.push(body);
      if (reject) {
        return route.fulfill({
          status: 422,
          json: { detail: { error: "CONFLICT", message: "代码已存在（mock 拒绝）" } },
        });
      }
      return route.fulfill({
        status: 200,
        json: {
          code: body.code, dimension: body.dimension, name: body.name,
          sort_order: body.sort_order, description: null, is_active: true,
          applicable_asset_classes: body.applicable_asset_classes,
        },
      });
    });

    const dlg = await openCreateDialog(page);
    await dlg.locator("#ac-code").fill("REGION_E2E910");
    await dlg.locator("#ac-name").fill("E2E910测试地区");
    await dlg.getByRole("button", { name: "股票", exact: true }).click();

    // 拒绝路径：toast 透传后端消息（modal Dialog 开着走 DOM testid，#640 口径），
    // 对话框不关、已填表单保留
    await dlg.getByRole("button", { name: "创建", exact: true }).click();
    const toast = page.getByTestId("toast-card").filter({ hasText: "创建失败" });
    await expect(toast).toBeVisible({ timeout: 10_000 });
    await expect(toast).toContainText("代码已存在（mock 拒绝）");
    await expect(dlg).toBeVisible();
    await expect(dlg.locator("#ac-code")).toHaveValue("REGION_E2E910");

    // 成功路径：关闭对话框，请求体含维度与适用关联
    reject = false;
    const posted = page.waitForRequest(
      (r) => r.method() === "POST" && /\/api\/asset-classifications(\?|$)/.test(r.url())
    );
    await dlg.getByRole("button", { name: "创建", exact: true }).click();
    await posted;
    await expect(dlg).toHaveCount(0);
    expect(bodies).toHaveLength(2);
    expect(bodies[1].code).toBe("REGION_E2E910");
    expect(bodies[1].dimension).toBe("region");
    expect(bodies[1].name).toBe("E2E910测试地区");
    expect(bodies[1].applicable_asset_classes).toEqual(["ASSET_STOCK"]);
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("编辑大类：长表单滚动契约，code/维度不可改，规则经 PUT 全量下发", async ({ page }) => {
    const errors = collectPageErrors(page);
    let putBody: Record<string, unknown> | null = null;
    await page.route(/\/api\/asset-classifications\/ASSET_STOCK(\?|$)/, (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      putBody = route.request().postDataJSON();
      return route.fulfill({
        status: 200,
        json: {
          code: "ASSET_STOCK", dimension: "asset_class", name: "股票",
          sort_order: putBody!.sort_order, description: null,
          is_active: putBody!.is_active, applicable_asset_classes: [],
        },
      });
    });

    await page.goto("/asset-classifications");
    await expect(page.getByRole("button", { name: "新建维度值" })).toBeVisible({ timeout: 15_000 });
    // 桌面表格行 / 移动卡片（code 在「ASSET_STOCK · 排序 …」行内）双形态定位
    const editButton = page
      .getByRole("row")
      .filter({ hasText: "ASSET_STOCK" })
      .getByRole("button", { name: "编辑" })
      .or(
        page
          .getByText(/ASSET_STOCK · 排序/)
          .locator("xpath=ancestor::div[1]")
          .getByRole("button", { name: "编辑" })
      );
    await editButton.click();
    const dlg = dialogByTitle(page, "编辑 ASSET_STOCK");
    await expect(dlg).toBeVisible();
    // 编辑态：code 与维度不可改（不渲染输入），启用开关存在
    await expect(dlg.locator("#ac-code")).toHaveCount(0);
    await expect(dlg.locator("#ac-dimension")).toHaveCount(0);
    await expect(dlg.locator("#ac-is-active")).toBeChecked();
    await expect(dlg.locator("#ac-name")).toHaveValue("股票");

    const ruleSelects = dlg.locator("select:not([id])");
    await expect(ruleSelects).toHaveCount(4);
    await expectDialogSingleScroll(dlg, "编辑大类长表单", {
      bottomAction: dlg.getByRole("button", { name: "保存修改" }),
    });

    // 改一条规则（不预设种子现值，取与当前不同的合法档），保存经 PUT 全量下发
    const last = ruleSelects.last();
    const current = await last.inputValue();
    const next = current === "required" ? "optional" : "required";
    await last.selectOption(next);
    const put = page.waitForRequest(
      (r) => r.method() === "PUT" && /\/api\/asset-classifications\/ASSET_STOCK(\?|$)/.test(r.url())
    );
    await dlg.getByRole("button", { name: "保存修改" }).click();
    await put;
    await expect(dlg).toHaveCount(0);
    expect(putBody).not.toBeNull();
    expect(putBody!.name).toBe("股票");
    const rules = putBody!.dimension_rules as Record<string, string>;
    expect(rules, "编辑大类应全量下发 dimension_rules").toBeTruthy();
    expect(rules.segment).toBe(next);
    expect(errors, `页面抛出未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
  });
});